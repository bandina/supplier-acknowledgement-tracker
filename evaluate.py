"""Independent checks of persisted data, plus ground-truth scoring."""
import csv
import json
from datetime import date
from pathlib import Path

from tracker import connect, impact, one, review_required

CHECKS = {
    "WRONG_PO_LINE": """
        SELECT a.id FROM acknowledgement a JOIN po_line l ON l.id=a.line_id WHERE a.po_id<>l.po_id""",
    "PROMISE_BEFORE_ORDER": """
        SELECT p.id FROM promise_date p JOIN acknowledgement a ON a.id=p.ack_id
        JOIN purchase_order o ON o.id=a.po_id WHERE p.promised<o.ordered""",
    "UNAPPROVED_PLAN": """
        SELECT CAST(p.id AS TEXT) FROM inventory_plan p LEFT JOIN approval ap ON ap.id=p.approval_id
        JOIN po_line l ON l.id=p.line_id JOIN purchase_order o ON o.id=l.po_id
        WHERE (p.approval_id IS NOT NULL AND COALESCE(ap.decision,'')<>'APPROVED')
           OR (p.approval_id IS NULL AND (p.quantity<>l.quantity OR p.arrival IS NOT l.requested
               OR p.effective<>o.ordered||'T00:00:00+00:00'
               OR EXISTS(SELECT 1 FROM inventory_plan x WHERE x.line_id=p.line_id AND x.id<p.id)))""",
    "STALE_REVISION": """
        SELECT CAST(p.id AS TEXT) FROM inventory_plan p JOIN approval ap ON ap.id=p.approval_id
        JOIN change_request c ON c.id=ap.change_id JOIN acknowledgement a ON a.id=c.ack_id
        WHERE EXISTS(SELECT 1 FROM acknowledgement newer
                     WHERE newer.line_id=a.line_id AND newer.revision>a.revision
                       AND newer.received<ap.decided)
           OR EXISTS(SELECT 1 FROM inventory_plan old JOIN approval oa ON oa.id=old.approval_id
                     JOIN change_request oc ON oc.id=oa.change_id JOIN acknowledgement prior ON prior.id=oc.ack_id
                     WHERE old.line_id=p.line_id AND old.id<p.id AND prior.revision>=a.revision)""",
    "ACCEPTANCE_MISMATCH": """
        SELECT a.id FROM acknowledgement a JOIN po_line l ON l.id=a.line_id
        LEFT JOIN promise_date p ON p.ack_id=a.id
        WHERE a.accepted+a.rejected<>l.quantity OR (a.accepted>0)<>(p.id IS NOT NULL)""",
    "INVALID_DATE_ORDER": """
        SELECT l.id FROM po_line l JOIN purchase_order o ON o.id=l.po_id WHERE l.requested<o.ordered
        UNION ALL SELECT a.id FROM acknowledgement a JOIN purchase_order o ON o.id=a.po_id
        WHERE substr(a.received,1,10)<o.ordered
        UNION ALL SELECT ap.id FROM approval ap JOIN change_request c ON c.id=ap.change_id
        JOIN acknowledgement a ON a.id=c.ack_id WHERE ap.decided<a.received""",
    "PLAN_MISMATCH": """
        SELECT CAST(p.id AS TEXT) FROM inventory_plan p JOIN approval ap ON ap.id=p.approval_id
        JOIN change_request c ON c.id=ap.change_id JOIN acknowledgement a ON a.id=c.ack_id
        LEFT JOIN promise_date d ON d.ack_id=a.id
        WHERE p.line_id<>a.line_id OR p.quantity<>a.accepted OR p.arrival IS NOT d.promised
           OR p.effective<>ap.decided""",
    "ALLOCATION_MISMATCH": """
        SELECT a.line_id||'/'||a.production_id FROM allocation a JOIN po_line l ON l.id=a.line_id
        JOIN production_order p ON p.id=a.production_id WHERE l.item<>p.item
        UNION ALL SELECT a.line_id FROM allocation a JOIN po_line l ON l.id=a.line_id
        GROUP BY a.line_id HAVING SUM(a.quantity)>l.quantity""",
    "APPROVAL_LIFECYCLE": """
        SELECT c.id FROM change_request c LEFT JOIN approval a ON a.change_id=c.id
        LEFT JOIN inventory_plan p ON p.approval_id=a.id
        WHERE (c.review=0 AND (a.decision IS NOT 'APPROVED' OR a.actor IS NOT 'Policy'))
           OR (a.decision='APPROVED' AND p.id IS NULL)
           OR (c.review=1 AND a.actor='Policy')""",
}


def validate(db):
    issues = {(kind, str(row[0])) for kind, sql in CHECKS.items() for row in db.execute(sql)}
    issues.update(("INVALID_FOREIGN_KEY", f"{row[0]}/{row[1]}") for row in db.execute("PRAGMA foreign_key_check"))
    issues.update(("DATABASE_INTEGRITY", row[0]) for row in db.execute("PRAGMA integrity_check") if row[0] != "ok")
    for row in db.execute("""SELECT c.*, l.quantity, l.price, l.requested,
                            a.accepted, a.price AS proposed, p.promised
                            FROM change_request c JOIN acknowledgement a ON a.id=c.ack_id
                            JOIN po_line l ON l.id=a.line_id LEFT JOIN promise_date p ON p.ack_id=a.id"""):
        qty = row["accepted"] - row["quantity"]
        price = row["proposed"] - row["price"]
        days = (date.fromisoformat(row["promised"]) - date.fromisoformat(row["requested"])).days if row["promised"] else None
        if (qty, price, days, review_required(row, qty, price, days)) != (
            row["quantity_delta"], row["price_delta"], row["days_delta"], row["review"]
        ):
            issues.add(("CHANGE_RECONCILIATION", row["id"]))
    return issues


def score(db, labels):
    expected = {(row["issue_type"], row["record_id"]) for row in labels if row["issue_type"]}
    detected = validate(db)
    decision_errors = []
    for row in labels:
        if not row["expected_decision"]:
            continue
        actual = one(db, """SELECT COALESCE(ap.decision, CASE WHEN a.revision <
                           (SELECT MAX(b.revision) FROM acknowledgement b WHERE b.line_id=a.line_id)
                           THEN 'SUPERSEDED' ELSE 'PENDING' END)
                           FROM change_request c JOIN acknowledgement a ON a.id=c.ack_id
                           LEFT JOIN approval ap ON ap.change_id=c.id WHERE c.id=?""", (row["record_id"],))
        if not actual or actual[0] != row["expected_decision"]:
            decision_errors.append(row["record_id"])
    return {
        "expected_anomalies": len(expected),
        "detected_anomalies": len(expected & detected),
        "detected_by_type": {kind: sum(issue == kind for issue, _ in expected & detected) for kind in sorted({k for k, _ in expected})},
        "missed": sorted(expected - detected),
        "unexpected_findings": sorted(detected - expected),
        "decisions_checked": sum(bool(row["expected_decision"]) for row in labels),
        "decision_mismatches": decision_errors,
    }


def metrics(db, as_of):
    def count(table):
        return one(db, f"SELECT COUNT(*) FROM {table}")[0]

    ordered = count("purchase_order")
    acknowledged = one(db, "SELECT COUNT(DISTINCT po_id) FROM acknowledgement")[0]
    overdue = one(db, """SELECT COUNT(*) FROM purchase_order p WHERE julianday(?)-julianday(p.ordered)>3
                         AND NOT EXISTS(SELECT 1 FROM acknowledgement a WHERE a.po_id=p.id)""", (as_of,))[0]
    decisions = dict(db.execute("SELECT decision,COUNT(*) FROM approval GROUP BY decision"))
    pending, superseded = one(db, """SELECT COALESCE(SUM(a.revision=r.latest),0), COALESCE(SUM(a.revision<r.latest),0)
                                    FROM change_request c JOIN acknowledgement a ON a.id=c.ack_id
                                    JOIN (SELECT line_id, MAX(revision) latest FROM acknowledgement GROUP BY line_id) r
                                      ON r.line_id=a.line_id LEFT JOIN approval ap ON ap.change_id=c.id
                                    WHERE ap.id IS NULL""")
    full, partial, rejected = one(db, """SELECT COALESCE(SUM(rejected=0),0),
        COALESCE(SUM(accepted>0 AND rejected>0),0),COALESCE(SUM(accepted=0),0) FROM acknowledgement""")
    return {
        "purchase_orders": ordered, "acknowledgements": count("acknowledgement"),
        "change_requests": count("change_request"), "acknowledged_purchase_orders": acknowledged,
        "acknowledgement_coverage_pct": round(100 * acknowledged / ordered, 2) if ordered else 0,
        "overdue_without_acknowledgement": overdue, "full_acceptances": full,
        "partial_acceptances": partial, "full_rejections": rejected,
        "approvals": decisions.get("APPROVED", 0), "rejected_changes": decisions.get("REJECTED", 0),
        "pending_changes": pending, "superseded_changes": superseded, "notifications": count("notification"),
        "production_orders_at_risk": sum(impact(db, job[0]) > 0 for job in db.execute("SELECT id FROM production_order")),
        "unapproved_plan_versions": len(list(db.execute(CHECKS["UNAPPROVED_PLAN"]))),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("sql", type=Path)
    parser.add_argument("labels", type=Path)
    args = parser.parse_args()
    with connect() as database, args.labels.open(newline="", encoding="utf-8") as stream:
        database.executescript(args.sql.read_text(encoding="utf-8"))
        summary = score(database, list(csv.DictReader(stream)))
    print(json.dumps(summary, indent=2))
    raise SystemExit(bool(summary["missed"] or summary["unexpected_findings"] or summary["decision_mismatches"]))
