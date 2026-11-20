"""Workflow regressions and a browser-facing WSGI smoke test."""
import io
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from app import create_app
from evaluate import validate
from generate import DEMO_TIME, demo, stamp
from tracker import connect, create_order, current_plan, decide, impact, init, one, receive


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.db = connect()
        self.addCleanup(self.db.close)
        init(self.db)
        self.po, self.line = demo(self.db)

    def submit(self, revision=1, quantity=80, price=52500, promised="2026-11-28"):
        return receive(self.db, self.po, self.line, revision, quantity, 100 - quantity,
                       price, promised, DEMO_TIME)

    def test_accept_pending_then_approve(self):
        change, status = self.submit()
        self.assertEqual(status, "PENDING")
        self.assertEqual(current_plan(self.db, self.line)["quantity"], 100)
        self.assertEqual(impact(self.db, "PROD-000001"), 0)
        decide(self.db, change, "APPROVED", "Planner 01", "Confirmed with production", DEMO_TIME)
        plan = current_plan(self.db, self.line)
        self.assertEqual((plan["quantity"], plan["arrival"]), (80, "2026-11-28"))
        self.assertEqual(impact(self.db, "PROD-000001"), 90)
        self.assertEqual(one(self.db, "SELECT COUNT(*) FROM inventory_plan")[0], 2)
        self.assertEqual(one(self.db, "SELECT shortfall FROM notification")[0], 90)
        self.assertEqual(validate(self.db), set())

    def test_rejection_leaves_plan_untouched(self):
        change, _ = self.submit()
        decide(self.db, change, "REJECTED", "Planner 01", "Insufficient stock", DEMO_TIME)
        self.assertEqual(current_plan(self.db, self.line)["quantity"], 100)
        self.assertEqual(one(self.db, "SELECT COUNT(*) FROM notification")[0], 0)
        with self.assertRaisesRegex(ValueError, "already has a decision"):
            decide(self.db, change, "APPROVED", "Planner 01", "Retry", DEMO_TIME)

    def test_exact_boundary_auto_approves_and_no_impact_when_on_time(self):
        _, status = self.submit(quantity=90, price=52500, promised="2026-11-25")
        self.assertEqual(status, "APPROVED")
        self.assertEqual(impact(self.db, "PROD-000001"), 0)
        self.assertEqual(one(self.db, "SELECT COUNT(*) FROM notification")[0], 0)
        self.assertEqual(validate(self.db), set())

    def test_invalid_reply_and_stale_revision(self):
        other, _ = create_order(self.db, "SUP-0001", "2026-11-20", "Motor", 100, 50000, "2026-11-25")
        self.assertEqual(receive(self.db, other, self.line, 1, 80, 20, 52500, "2026-11-28", DEMO_TIME)[1], "WRONG_PO_LINE")
        self.assertEqual(self.submit(promised="2026-11-19")[1], "PROMISE_BEFORE_ORDER")
        old, _ = self.submit()
        self.assertEqual(self.submit(2, 95, 50000, "2026-11-25")[1], "APPROVED")
        self.assertEqual(self.submit()[1], "STALE_REVISION")
        with self.assertRaisesRegex(ValueError, "newer revision"):
            decide(self.db, old, "APPROVED", "Planner 01", "Late review", stamp("2026-11-20", 10))
        self.assertEqual(current_plan(self.db, self.line)["quantity"], 95)
        self.assertEqual(one(self.db, "SELECT COUNT(*) FROM exception_event")[0], 3)

    def test_quantity_reconciliation_and_full_rejection(self):
        self.assertEqual(receive(self.db, self.po, self.line, 1, 10, 80, 50000, "2026-11-25", DEMO_TIME)[1],
                         "INVALID_ACCEPTANCE")
        change, status = self.submit(quantity=0, promised=None)
        self.assertEqual(status, "PENDING")
        decide(self.db, change, "APPROVED", "Planner 01", "Supplier unable to fulfill", DEMO_TIME)
        self.assertEqual((current_plan(self.db, self.line)["quantity"], current_plan(self.db, self.line)["arrival"]),
                         (0, None))

    def test_price_restoration_requires_a_record(self):
        self.assertEqual(self.submit(quantity=100, price=52000, promised="2026-11-25")[1], "APPROVED")
        self.assertEqual(self.submit(2, 100, 50000, "2026-11-25")[1], "APPROVED")
        self.assertEqual(one(self.db, "SELECT COUNT(*) FROM inventory_plan")[0], 3)

    def test_two_jobs_cannot_spend_same_units(self):
        from tracker import insert

        insert(self.db, "production_order", "PROD-000002", "Motor", 10, "2026-11-27", "Planner 02")
        insert(self.db, "allocation", self.line, "PROD-000002", 10)
        change, _ = self.submit(promised="2026-11-25")
        decide(self.db, change, "APPROVED", "Planner 01", "Limited supply", DEMO_TIME)
        self.assertEqual(impact(self.db, "PROD-000001"), 10)
        self.assertEqual(impact(self.db, "PROD-000002"), 10)


class WebTests(unittest.TestCase):
    def test_form_to_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tracker.db"
            db = connect(database)
            with db:
                init(db)
                po, line = demo(db)
            db.close()
            app = create_app(database)

            def request(url, fields=None):
                parts = urlsplit(url)
                payload = urlencode(fields or {}).encode()
                response = {}
                def start(status, headers):
                    response.update(status=status, headers=dict(headers))
                body = b"".join(app({
                    "PATH_INFO": parts.path, "QUERY_STRING": parts.query,
                    "REQUEST_METHOD": "POST" if fields is not None else "GET",
                    "CONTENT_LENGTH": str(len(payload)), "wsgi.input": io.BytesIO(payload),
                }, start)).decode()
                return response["status"], response["headers"], body

            status, _, page = request(f"/order?id={po}")
            self.assertTrue(status.startswith("200"))
            token = re.search('name="token" value="([^"]+)"', page)[1]
            status, headers, _ = request("/actions", {
                "token": token, "action": "reply", "po": po, "line": line, "revision": 1,
                "accepted": 80, "rejected": 20, "price": 52500, "promised": "2026-11-28",
            })
            self.assertTrue(status.startswith("303"), status)
            self.assertIn("PENDING", headers["Location"])
            change = f"CHG-{po}-000001"
            self.assertIn(change, request("/approvals")[2])
            status, _, _ = request("/actions", {"token": token, "action": "decision", "change": change,
                "decision": "APPROVED", "actor": "Planner 01", "reason": "Browser smoke test"})
            self.assertTrue(status.startswith("303"), status)
            self.assertIn("PROD-000001", request("/notifications")[2])
            self.assertIn("2026-11-28", request("/supply")[2])
            self.assertIn("APPROVED", request(f"/order?id={po}")[2])


if __name__ == "__main__":
    unittest.main()
