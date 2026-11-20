"""Transactional purchase-order workflow. Prices are integer paise."""
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path=":memory:"):
    db = sqlite3.connect(path, isolation_level="IMMEDIATE")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init(db):
    db.executescript(SCHEMA.read_text())


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def one(db, query, args=()):
    return db.execute(query, args).fetchone()


def insert(db, table, *values):
    # Table names are constants from this application; all values are bound.
    placeholders = ",".join("?" for _ in values)
    return db.execute(f"INSERT INTO {table} VALUES ({placeholders})", values)


def create_order(db, supplier, ordered, item, quantity, price, requested, po_id=None):
    if date.fromisoformat(requested) < date.fromisoformat(ordered):
        raise ValueError("Requested date must be on or after order date.")
    if not item.strip() or quantity <= 0 or price <= 0:
        raise ValueError("Enter an item, positive quantity and positive price.")
    if po_id is None:
        year = ordered[:4]
        last = one(db, "SELECT MAX(id) FROM purchase_order WHERE id LIKE ?", (f"PO-{year}-%",))[0]
        sequence = int(last.rsplit("-", 1)[1]) + 1 if last else 1
        po_id = f"PO-{year}-{sequence:06d}"
    insert(db, "purchase_order", po_id, supplier, ordered)
    line = add_line(db, po_id, item, quantity, price, requested)
    return po_id, line


def add_line(db, po, item, quantity, price, requested):
    order = one(db, "SELECT ordered FROM purchase_order WHERE id=?", (po,))
    if not order or date.fromisoformat(requested) < date.fromisoformat(order["ordered"]):
        raise ValueError("Requested date must be on or after order date.")
    if not item.strip() or quantity <= 0 or price <= 0:
        raise ValueError("Enter an item, positive quantity and positive price.")
    count = one(db, "SELECT COUNT(*) FROM po_line WHERE po_id=?", (po,))[0]
    line = f"{po}-L{count + 1:03d}"
    insert(db, "po_line", line, po, item, quantity, price, requested)
    insert(db, "inventory_plan", None, line, None, quantity, requested, order["ordered"] + "T00:00:00+00:00")
    return line


def current_plan(db, line):
    return one(db, "SELECT * FROM inventory_plan WHERE line_id=? ORDER BY id DESC LIMIT 1", (line,))


def latest_revision(db, line):
    return one(db, "SELECT COALESCE(MAX(revision),0) FROM acknowledgement WHERE line_id=?", (line,))[0]


def review_required(line, quantity_delta, price_delta, days_delta):
    return (abs(quantity_delta) * 100 > line["quantity"] * 10
            or abs(price_delta) * 100 > line["price"] * 5
            or abs(days_delta or 0) > 2)


def receive(db, po, line_id, revision, accepted, rejected, price, promised, received):
    line = one(db, "SELECT * FROM po_line WHERE id=?", (line_id,))
    order = one(db, "SELECT * FROM purchase_order WHERE id=?", (po,))
    if not line or not order:
        raise ValueError("Purchase order and line must exist.")
    if revision < 1 or min(accepted, rejected) < 0 or price <= 0:
        raise ValueError("Revision and price must be positive and quantities nonnegative.")
    reason = None
    if line["po_id"] != po:
        reason = "WRONG_PO_LINE"
    elif promised and date.fromisoformat(promised) < date.fromisoformat(order["ordered"]):
        reason = "PROMISE_BEFORE_ORDER"
    elif revision <= latest_revision(db, line_id):
        reason = "STALE_REVISION"
    elif received[:10] < order["ordered"]:
        reason = "REPLY_BEFORE_ORDER"
    elif accepted + rejected != line["quantity"] or bool(accepted) != bool(promised):
        reason = "INVALID_ACCEPTANCE"
    if reason:
        insert(db, "exception_event", None, po, line_id, revision, accepted, rejected, price, promised, received, reason)
        return None, reason
    sequence = one(db, "SELECT COUNT(*) FROM acknowledgement WHERE po_id=?", (po,))[0] + 1
    ack = f"ACK-{po}-{sequence:02d}"
    insert(db, "acknowledgement", ack, po, line_id, revision, accepted, rejected, price, received)
    if promised:
        insert(db, "promise_date", f"PRM-{ack}", ack, promised)
    quantity_delta = accepted - line["quantity"]
    price_delta = price - line["price"]
    days_delta = (date.fromisoformat(promised) - date.fromisoformat(line["requested"])).days if promised else None
    plan = current_plan(db, line_id)
    previous = one(db, """SELECT a.price FROM approval ap JOIN change_request c ON c.id=ap.change_id
                          JOIN acknowledgement a ON a.id=c.ack_id WHERE ap.id=?""", (plan["approval_id"],))
    effective_price = previous["price"] if previous else line["price"]
    if not any((quantity_delta, price_delta, days_delta)) and (accepted, promised, price) == (plan["quantity"], plan["arrival"], effective_price):
        return ack, "CONFIRMED"
    change = f"CHG-{po}-{sequence:06d}"
    needs_review = review_required(line, quantity_delta, price_delta, days_delta)
    insert(db, "change_request", change, ack, quantity_delta, price_delta, days_delta, int(needs_review))
    if not needs_review:
        decide(db, change, "APPROVED", "Policy", "Within approval thresholds", received)
    return change, "PENDING" if needs_review else "APPROVED"


def decide(db, change, decision, actor, reason, decided):
    row = one(db, """SELECT c.*, a.line_id, a.revision, a.accepted, a.received, p.promised
                      FROM change_request c JOIN acknowledgement a ON a.id=c.ack_id
                      LEFT JOIN promise_date p ON p.ack_id=a.id WHERE c.id=?""", (change,))
    if not row or decision not in ("APPROVED", "REJECTED") or not actor.strip() or not reason.strip():
        raise ValueError("Provide a valid decision, reviewer and reason.")
    if one(db, "SELECT id FROM approval WHERE change_id=?", (change,)):
        raise ValueError("That request already has a decision.")
    if row["revision"] != latest_revision(db, row["line_id"]):
        raise ValueError("A newer revision superseded this request.")
    if decided < row["received"]:
        raise ValueError("Decision cannot precede receipt.")
    approval = f"APR-{change}-01"
    insert(db, "approval", approval, change, decision, actor, reason, decided)
    if decision == "APPROVED":
        plan = insert(db, "inventory_plan", None, row["line_id"], approval, row["accepted"], row["promised"], decided).lastrowid
        notify(db, row["line_id"], plan, decided)
    return approval


def supply_impact(db, production):
    job = one(db, "SELECT * FROM production_order WHERE id=?", (production,))
    on_time = 0
    late = 0
    supplies = db.execute("""SELECT a.line_id, a.quantity FROM allocation a
                             WHERE a.production_id=? ORDER BY a.line_id""", (production,))
    for supply in supplies:
        plan = current_plan(db, supply["line_id"])
        if plan["arrival"]:
            ahead = one(db, """SELECT COALESCE(SUM(a.quantity),0) FROM allocation a
                               JOIN production_order p ON p.id=a.production_id
                               WHERE a.line_id=? AND (p.needed,p.id)<(?,?)""",
                        (supply["line_id"], job["needed"], production))[0]
            available = min(supply["quantity"], max(0, plan["quantity"] - ahead))
            if plan["arrival"] <= job["needed"]:
                on_time += available
            else:
                late += available
    shortfall = max(0, job["demand"] - on_time)
    return shortfall, min(late, shortfall), max(0, shortfall - late)


def impact(db, production):
    return supply_impact(db, production)[0]


def notify(db, line, plan, created):
    jobs = db.execute("SELECT production_id FROM allocation WHERE line_id=? ORDER BY production_id", (line,))
    for job in jobs:
        production = job["production_id"]
        shortage = impact(db, production)
        if shortage:
            year = created[:4]
            last = one(db, "SELECT MAX(id) FROM notification WHERE id LIKE ?", (f"NOT-{year}-%",))[0]
            sequence = int(last.rsplit("-", 1)[1]) + 1 if last else 1
            insert(db, "notification", f"NOT-{year}-{sequence:06d}", plan, production, shortage, created)
