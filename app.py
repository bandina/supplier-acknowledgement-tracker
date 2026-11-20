"""Local browser interface using only Python's standard library."""
import argparse
import json
import secrets
import sqlite3
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote
from wsgiref.simple_server import make_server

from evaluate import metrics
from generate import demo
from tracker import add_line, connect, create_order, decide, init, insert, now, one, receive

STYLE = """
:root{font-family:system-ui,sans-serif;background:#f5f7fa;color:#18253b}
*{box-sizing:border-box}body{margin:0}header{background:#14283a;color:white;padding:1rem 5vw}
nav{display:flex;align-items:center;gap:1.2rem;flex-wrap:wrap}nav strong{margin-right:1rem}
nav a{color:#d7eaf7}main{max-width:1160px;margin:auto;padding:1.5rem}
section,form,.notice{background:white;border:1px solid #dbe3eb;border-radius:12px;padding:1rem;margin:1rem 0}
h1{font-size:2rem}h2{font-size:1.2rem;margin-top:0}a{color:#075e80}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.6rem;border-bottom:1px solid #dbe3eb}
th{background:#eef3f7}.scroll{overflow-x:auto}label{display:inline-flex;flex-direction:column;margin:.35rem;gap:.25rem}
input,select,button{font:inherit;padding:.42rem;border-radius:6px;border:1px solid #8193a3}
button{background:#075e80;color:white;cursor:pointer;margin:.35rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.8rem}
.grid section{margin:0}.value{font-weight:700;font-size:1.5rem}small{color:#466175}
"""


def safe(value):
    return escape("" if value is None else str(value), quote=True)


def table(headers, rows):
    heading = "".join(f"<th>{safe(label)}</th>" for label in headers)
    body = "".join("<tr>" + "".join(f"<td>{safe(value)}</td>" for value in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>'


def field(name, label, kind="text", value="", required=True):
    return f'<label>{safe(label)}<input name="{name}" type="{kind}" value="{safe(value)}" {"required" if required else ""}></label>'


def select(name, label, options):
    values = "".join(f'<option value="{safe(key)}">{safe(text)}</option>' for key, text in options)
    return f'<label>{safe(label)}<select name="{name}">{values}</select></label>'


def form(token, action, label, fields, **hidden):
    values = "".join(f'<input type="hidden" name="{key}" value="{safe(value)}">' for key, value in
                     {"token": token, "action": action, **hidden}.items())
    return f'<form method="post" action="/actions"><h2>{label}</h2>{values}{fields}<button>Save {label.lower()}</button></form>'


def home(db, token, query):
    term = query.get("q", [""])[0].strip()
    orders = list(db.execute("""SELECT o.id,s.name,o.ordered,
        CASE WHEN EXISTS(SELECT 1 FROM acknowledgement a WHERE a.po_id=o.id)
             THEN 'Reply received' WHEN julianday('now')-julianday(o.ordered)>3
             THEN 'Overdue' ELSE 'Awaiting reply' END AS status
        FROM purchase_order o JOIN supplier s ON s.id=o.supplier_id
        WHERE o.id LIKE ? OR s.name LIKE ? ORDER BY o.id LIMIT 101 OFFSET ?""",
        (f"%{term}%", f"%{term}%", (page_number(query) - 1) * 100)))
    links = "".join(f'<li><a href="/order?id={quote(row[0])}">{safe(row[0])}</a> · {safe(row[1])} · {safe(row[2])} · {safe(row[3])}</li>'
                    for row in orders[:100])
    suppliers = db.execute("SELECT id,name FROM supplier ORDER BY name")
    today = date.today().isoformat()
    fields = (select("supplier", "Supplier", suppliers) + field("ordered", "Order date", "date", today)
              + line_fields(today))
    return ('<h1>Purchase orders</h1><form method="get"><label>Find an order or supplier'
            f'<input name="q" value="{safe(term)}"></label><button>Search</button></form>'
            f'<section><ul>{links or "<li>No purchase orders found.</li>"}</ul>{pager("/", query, len(orders)>100, q=term)}</section>'
            + form(token, "supplier", "supplier", field("name", "Supplier name"))
            + form(token, "order", "purchase order", fields))


def page_number(query):
    try:
        return max(1, min(10000, int(query.get("page", ["1"])[0])))
    except ValueError:
        return 1


def pager(path, query, more, **params):
    page = page_number(query)
    prefix = path + "?" + "".join(f"{key}={quote(value)}&amp;" for key, value in params.items())
    previous = f'<a href="{prefix}page={page-1}">Previous</a> ' if page > 1 else ""
    following = f' <a href="{prefix}page={page+1}">Next</a>' if more else ""
    return f"<p>{previous}Page {page}{following}</p>"


def line_fields(requested):
    return (field("item", "Item") + field("quantity", "Requested units", "number")
            + field("price", "Unit price in paise", "number") + field("requested", "Requested date", "date", requested))


def order_page(db, token, po):
    order = one(db, """SELECT o.*,s.name FROM purchase_order o JOIN supplier s ON s.id=o.supplier_id WHERE o.id=?""", (po,))
    if not order:
        return None
    lines = list(db.execute("SELECT * FROM po_line WHERE po_id=? ORDER BY id", (po,)))
    body = f'<h1>{safe(po)}</h1><p>{safe(order["name"])} · Ordered {safe(order["ordered"])}</p>'
    body += form(token, "line", "PO line", line_fields(order["ordered"]), po=po)
    for line in lines:
        line_id = line["id"]
        body += f'<section><h2>{safe(line["item"])} · {safe(line_id)}</h2>'
        body += f'<p>Requested {line["quantity"]} units on {safe(line["requested"])} at {line["price"]} paise per unit.</p>'
        replies = list(db.execute("""SELECT a.revision,a.accepted,a.rejected,a.price,p.promised,a.received,
                CASE WHEN a.accepted=0 THEN 'Rejected' WHEN a.rejected=0 THEN 'Full' ELSE 'Partial' END,
                c.id,c.quantity_delta,c.price_delta,c.days_delta,
                CASE WHEN c.id IS NULL THEN 'Confirmed' WHEN ap.id IS NOT NULL THEN ap.decision
                     WHEN a.revision<(SELECT MAX(b.revision) FROM acknowledgement b WHERE b.line_id=a.line_id)
                     THEN 'Superseded' ELSE 'Pending' END,ap.actor,ap.reason
                FROM acknowledgement a LEFT JOIN promise_date p ON p.ack_id=a.id
                LEFT JOIN change_request c ON c.ack_id=a.id LEFT JOIN approval ap ON ap.change_id=c.id
                WHERE a.line_id=? ORDER BY a.revision DESC""", (line_id,)))
        body += '<h2>Supplier replies and decisions</h2>' + table(
            ("Revision", "Accepted", "Rejected", "Price (paise)", "Promised", "Received", "Reply", "Change",
             "Units Δ", "Paise Δ", "Days Δ", "Decision", "Reviewer", "Reason"), replies)
        history = db.execute("""SELECT p.id,p.quantity,p.arrival,p.effective,COALESCE(p.approval_id,'Original order'),
            CASE WHEN p.id=(SELECT MAX(x.id) FROM inventory_plan x WHERE x.line_id=p.line_id)
                 THEN 'Current' ELSE 'Historical' END FROM inventory_plan p WHERE p.line_id=? ORDER BY p.id DESC""", (line_id,))
        body += '<h2>Projected supply history</h2>' + table(("Version", "Units", "Arrival", "Effective", "Source", "Status"), history)
        next_revision = int(replies[0][0]) + 1 if replies else 1
        fields = (field("revision", "Revision", "number", next_revision)
                  + field("accepted", "Accepted units", "number", line["quantity"]) + field("rejected", "Rejected units", "number", 0)
                  + field("price", "Price in paise", "number", line["price"])
                  + field("promised", "Promised date", "date", line["requested"], required=False))
        body += form(token, "reply", "supplier reply", fields, po=po, line=line_id)
        fields = (field("demand", "Required units", "number") + field("needed", "Needed by", "date", line["requested"])
                  + field("planner", "Planner"))
        body += form(token, "job", "linked production order", fields, po=po, line=line_id) + '</section>'
    events = db.execute("""SELECT line_id,revision,accepted,rejected,price,promised,received,reason
                           FROM exception_event WHERE po_id=? ORDER BY id DESC""", (po,))
    return body + '<section><h2>Rejected input events</h2>' + table(
        ("Line", "Revision", "Accepted", "Rejected", "Price", "Promised", "Received", "Reason"), events) + '</section>'


def approvals(db, token, query):
    pending = list(db.execute("""SELECT c.id,a.po_id,a.line_id,c.quantity_delta,c.price_delta,c.days_delta
        FROM change_request c JOIN acknowledgement a ON a.id=c.ack_id
        LEFT JOIN approval ap ON ap.change_id=c.id WHERE ap.id IS NULL
        AND a.revision=(SELECT MAX(b.revision) FROM acknowledgement b WHERE b.line_id=a.line_id)
        ORDER BY a.received,c.id LIMIT 26 OFFSET ?""", ((page_number(query)-1)*25,)))
    body = '<h1>Approval queue</h1>'
    for key, po, line, qty, price, days in pending[:25]:
        fields = (select("decision", "Decision", (("APPROVED", "Approve"), ("REJECTED", "Reject")))
                  + field("actor", "Reviewer") + field("reason", "Reason"))
        body += (f'<section><h2>{safe(key)}</h2><p><a href="/order?id={quote(po)}">{safe(line)}</a>'
                 f' · Units Δ {qty} · Price Δ {price} paise · Days Δ {safe(days)}</p>'
                 + form(token, "decision", "approval decision", fields, change=key) + '</section>')
    return body + ('<p>No pending changes.</p>' if not pending else pager("/approvals", query, len(pending)>25))


def supply(db, query):
    plans = list(db.execute("""SELECT l.id,l.item,p.quantity,p.arrival,p.effective,p.approval_id
        FROM po_line l JOIN inventory_plan p ON p.id=(SELECT MAX(x.id) FROM inventory_plan x WHERE x.line_id=l.id)
        ORDER BY l.id LIMIT 101 OFFSET ?""", ((page_number(query)-1)*100,)))
    jobs = db.execute("""SELECT p.id,p.item,p.demand,p.needed,p.planner,
        GROUP_CONCAT(a.line_id, ', ') FROM production_order p LEFT JOIN allocation a ON a.production_id=p.id
        GROUP BY p.id ORDER BY p.id LIMIT 100 OFFSET ?""", ((page_number(query)-1)*100,))
    from tracker import supply_impact

    job_rows = [(*row, *supply_impact(db, row[0])) for row in jobs]
    return ('<h1>Expected supply and production</h1><section><h2>Current expected receipts</h2>'
            + table(("PO line", "Item", "Units", "Arrival", "Effective", "Approval"), plans[:100])
            + '</section><section><h2>Production risk</h2>'
            + table(("Order", "Item", "Required units", "Need date", "Planner", "Source lines", "Units short by need date", "Expected late", "Supply gap"), job_rows)
            + '</section>' + pager("/supply", query, len(plans)>100))


def notifications(db, query):
    rows = list(db.execute("""SELECT n.id,p.planner,p.id,i.line_id,i.approval_id,n.shortfall,n.created
                         FROM notification n JOIN production_order p ON p.id=n.production_id
                         JOIN inventory_plan i ON i.id=n.plan_id ORDER BY n.id DESC LIMIT 101 OFFSET ?""",
                         ((page_number(query)-1)*100,)))
    return ('<h1>Planner notifications</h1><section>' + table(
        ("Alert", "Planner", "Production order", "Source line", "Approval", "Units short by need date", "Created"), rows[:100])
        + '</section>' + pager("/notifications", query, len(rows)>100))


def handle_action(db, data):
    action = data["action"]
    po = data.get("po", "")
    location = "/order?id=" + quote(po) if po else "/"
    if action == "supplier":
        name = data["name"].strip()
        if not name:
            raise ValueError("Enter a supplier name.")
        index = one(db, "SELECT COUNT(*) FROM supplier")[0] + 1
        insert(db, "supplier", f"SUP-{index:04d}", name)
    elif action == "order":
        po, _ = create_order(db, data["supplier"], data["ordered"], data["item"],
                             int(data["quantity"]), int(data["price"]), data["requested"])
        location = "/order?id=" + quote(po)
    elif action == "line":
        add_line(db, po, data["item"], int(data["quantity"]), int(data["price"]), data["requested"])
    elif action == "reply":
        key, status = receive(db, po, data["line"], int(data["revision"]), int(data["accepted"]),
                              int(data["rejected"]), int(data["price"]), data["promised"] or None, now())
        return location, f"{key + ' · ' if key else ''}{status.replace('_', ' ')}"
    elif action == "decision":
        decide(db, data["change"], data["decision"], data["actor"], data["reason"], now())
        location = "/approvals"
    elif action == "job":
        line = one(db, "SELECT * FROM po_line WHERE id=? AND po_id=?", (data["line"], po))
        allocated = one(db, "SELECT COALESCE(SUM(quantity),0) FROM allocation WHERE line_id=?", (data["line"],))[0]
        demand = int(data["demand"])
        if line is None or not 0 < demand <= line["quantity"] - allocated or not data["planner"].strip():
            raise ValueError("Enter a planner and allocate units within the line's original unallocated quantity.")
        needed = date.fromisoformat(data["needed"]).isoformat()
        index = one(db, "SELECT COUNT(*) FROM production_order")[0] + 1
        job = f"PROD-{index:06d}"
        insert(db, "production_order", job, line["item"], demand, needed, data["planner"].strip())
        insert(db, "allocation", line["id"], job, demand)
    else:
        raise ValueError("Unknown action.")
    return location, "Saved."


def create_app(database):
    token = secrets.token_urlsafe(32)

    def application(environ, start_response):
        path = environ["PATH_INFO"]
        query = parse_qs(environ.get("QUERY_STRING", ""))
        method = environ["REQUEST_METHOD"]
        notice = query.get("message", [""])[0]
        status = "200 OK"
        db = connect(database)
        data = {}
        try:
            if method == "POST" and path == "/actions":
                try:
                    length = int(environ.get("CONTENT_LENGTH") or 0)
                    if not 0 < length <= 16384:
                        raise ValueError("Invalid form size.")
                    data = {key: value[0] for key, value in parse_qs(
                        environ["wsgi.input"].read(length).decode("utf-8"), keep_blank_values=True).items()}
                    if not secrets.compare_digest(data.get("token", ""), token):
                        raise ValueError("Form expired. Open the page and try again.")
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        location, message = handle_action(db, data)
                    start_response("303 See Other", [("Location", location + ("&" if "?" in location else "?") + "message=" + quote(message))])
                    return [b""]
                except (ValueError, KeyError, sqlite3.IntegrityError) as exc:
                    status, body = "400 Bad Request", f'<h1>Could not save</h1><p>{safe(exc)}</p>'
                    if data.get("po"):
                        body += f'<a href="/order?id={quote(data["po"])}">Back to purchase order</a>'
            elif method != "GET":
                status, body = "405 Method Not Allowed", '<h1>Method not allowed</h1>'
            elif path == "/":
                body = home(db, token, query)
            elif path == "/order":
                body = order_page(db, token, query.get("id", [""])[0])
                if body is None:
                    status, body = "404 Not Found", '<h1>Purchase order not found</h1>'
            elif path == "/approvals":
                body = approvals(db, token, query)
            elif path == "/supply":
                body = supply(db, query)
            elif path == "/notifications":
                body = notifications(db, query)
            elif path == "/metrics":
                values = metrics(db, date.today().isoformat())
                body = '<h1>Operational metrics</h1><div class="grid">' + "".join(
                    f'<section><small>{safe(key.replace("_", " ").capitalize())}</small><div class="value">{value}</div></section>'
                    for key, value in values.items()) + '</div>'
            elif path == "/health":
                start_response("200 OK", [("Content-Type", "application/json")])
                return [json.dumps({"status": "ready"}).encode()]
            else:
                status, body = "404 Not Found", '<h1>Page not found</h1>'
        finally:
            db.close()
        alert = f'<p class="notice" role="status">{safe(notice)}</p>' if notice else ""
        html = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
        <title>Supplier Tracker</title><style>{STYLE}</style><header><nav><strong>Supplier Tracker</strong>
        <a href="/">Purchase orders</a><a href="/approvals">Approvals</a><a href="/supply">Supply &amp; production</a>
        <a href="/notifications">Notifications</a><a href="/metrics">Metrics</a></nav></header>
        <main>{alert}{body}</main></html>"""
        start_response(status, [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")])
        return [html.encode("utf-8")]

    return application


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("tracker.db"))
    parser.add_argument("--port", type=int, default=8000)
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument("--demo", action="store_true")
    setup.add_argument("--seed-sql", type=Path)
    args = parser.parse_args()
    if args.db.exists() and args.seed_sql:
        parser.error("--seed-sql requires a new database path")
    if not args.db.exists():
        db = connect(args.db)
        try:
            with db:
                if args.seed_sql:
                    db.executescript(args.seed_sql.read_text(encoding="utf-8"))
                else:
                    init(db)
                    if args.demo:
                        demo(db)
        except Exception:
            db.close()
            args.db.unlink()
            raise
        finally:
            db.close()
    with make_server("127.0.0.1", args.port, create_app(args.db)) as server:
        print(f"Open http://127.0.0.1:{args.port}", flush=True)
        server.serve_forever()
