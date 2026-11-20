"""Create reproducible baseline data, isolated negative fixtures, and labels."""
import argparse
import csv
import hashlib
import json
import random
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluate import metrics, score, validate
from tracker import add_line, connect, create_order, decide, init, insert, one, receive

SEED = 20261022
AS_OF = "2026-11-20"
ITEMS = ("Motor", "Cable", "Bearing", "Gasket", "Valve")
DEMO_TIME = "2026-11-20T09:00:00+00:00"


def stamp(day, hour):
    return f"{day}T{hour:02d}:00:00+00:00"


def demo(db):
    insert(db, "supplier", "SUP-0001", "Kaveri Industrial Supply")
    po, line = create_order(db, "SUP-0001", "2026-11-20", "Motor", 100, 50000, "2026-11-25")
    insert(db, "production_order", "PROD-000001", "Motor", 90, "2026-11-26", "Planner 01")
    insert(db, "allocation", line, "PROD-000001", 90)
    return po, line


def baseline(db, size, seed):
    rng = random.Random(seed)
    supplier_count = min(120, max(1, size // 10))
    for index in range(1, supplier_count + 1):
        insert(db, "supplier", f"SUP-{index:04d}", f"Synthetic Industrial Supplier {index:03d}")

    order_indices = list(range(1, size + 1))
    rng.shuffle(order_indices)
    silent_count = size // 10
    revised_count = size // 10
    active = set(order_indices[silent_count:])
    revised = set(order_indices[silent_count:silent_count + revised_count])
    slots = [(i, 1) for i in sorted(active)] + [(i, 2) for i in sorted(revised)]
    rng.shuffle(slots)
    # If only one revision in a pair is selected, put the change on
    # revision 2. A return to original terms would itself be a change.
    changed = set(slots[:size * 3 // 2 - len(slots)])
    for index in revised:
        if (index, 1) in changed and (index, 2) not in changed:
            changed.remove((index, 1))
            changed.add((index, 2))
    labels = []

    # Build every parent and allocation before generating replies.
    for index in range(1, size + 1):
        ordered = date(2026, 8, 1) + timedelta(days=rng.randrange(75))
        requested = ordered + timedelta(days=rng.randint(8, 24))
        item = rng.choice(ITEMS)
        quantity = rng.randrange(20, 201, 10)
        price = rng.randrange(5000, 95001, 100)
        supplier = f"SUP-{rng.randint(1, supplier_count):04d}"
        po, line = create_order(db, supplier, str(ordered), item, quantity, price, str(requested), f"PO-2026-{index:06d}")
        job = f"PROD-{index:06d}"
        demand = quantity * rng.randint(65, 100) // 100
        needed = requested + timedelta(days=rng.randrange(0, 6))
        insert(db, "production_order", job, item, demand, str(needed), f"Planner {index % 12 + 1:02d}")
        insert(db, "allocation", line, job, demand)
        if rng.random() < 0.18:
            add_line(db, po, rng.choice(ITEMS), rng.randint(10, 80), rng.randrange(5000, 20000, 100), str(requested))

    for index in sorted(active):
        po = f"PO-2026-{index:06d}"
        line = one(db, "SELECT * FROM po_line WHERE id=?", (f"{po}-L001",))
        order_day = date.fromisoformat(one(db, "SELECT ordered FROM purchase_order WHERE id=?", (po,))[0])
        requested = date.fromisoformat(line["requested"])
        for revision in range(1, 3 if index in revised else 2):
            accepted, price, promise = line["quantity"], line["price"], requested
            if (index, revision) in changed:
                scenario = rng.choices(("partial", "date", "price", "mixed", "rejected"), (28, 33, 22, 12, 5))[0]
                if scenario in ("partial", "mixed"):
                    accepted = max(1, accepted * rng.choice((60, 75, 90, 95)) // 100)
                if scenario in ("date", "mixed"):
                    promise += timedelta(days=rng.choice((1, 2, 3, 5, 8, 12)))
                if scenario in ("price", "mixed"):
                    price += price * rng.choice((2, 5, 7, 12)) // 100
                if scenario == "rejected":
                    accepted, promise = 0, None
            received = stamp(order_day + timedelta(days=revision * 2), 9)
            key, status = receive(db, po, line["id"], revision, accepted, line["quantity"] - accepted,
                                  price, str(promise) if promise else None, received)
            # Compute expected routing directly from the generated terms.
            days = abs((promise - requested).days) if promise else 0
            manual = (accepted * 10 < line["quantity"] * 9
                      or price * 20 > line["price"] * 21 or days > 2)
            expected = "PENDING" if manual else "APPROVED"
            if (index, revision) not in changed:
                expected = "CONFIRMED"
            if status != expected:
                raise RuntimeError(f"{po} revision {revision}: expected {expected}, got {status}")
            if expected == "PENDING":
                if not accepted or accepted * 2 < line["quantity"] or price * 100 > line["price"] * 110:
                    decision = "REJECTED"
                elif promise and (promise - requested).days > 7:
                    decision = "PENDING"
                else:
                    decision = "APPROVED"
                if decision != "PENDING":
                    decide(db, key, decision, "Synthetic planner", "Synthetic review policy", stamp(order_day + timedelta(days=revision * 2), 10))
                if decision == "PENDING" and revision == 1 and index in revised:
                    decision = "SUPERSEDED"
                labels.append((key, "", decision))
            elif status == "APPROVED":
                labels.append((key, "", "APPROVED"))
    return labels


def exceptions(db):
    demo_po, line = demo(db)
    other, other_line = create_order(db, "SUP-0001", "2026-11-20", "Bearing", 30, 10000, "2026-11-25")
    labels = []
    change, _ = receive(db, demo_po, line, 1, 80, 20, 52500, "2026-11-28", DEMO_TIME)
    planned = insert(db, "inventory_plan", None, line, None, 80, "2026-11-28", DEMO_TIME).lastrowid
    labels.extend(((str(planned), "UNAPPROVED_PLAN", ""), (change, "", "PENDING")))
    insert(db, "acknowledgement", f"ACK-{demo_po}-02", demo_po, other_line, 1, 30, 0, 10000, DEMO_TIME)
    insert(db, "promise_date", "PRM-WRONG", f"ACK-{demo_po}-02", "2026-11-25")
    labels.append((f"ACK-{demo_po}-02", "WRONG_PO_LINE", ""))

    dated_po, dated_line = create_order(db, "SUP-0001", "2026-11-20", "Cable", 30, 10000, "2026-11-25")
    ack = f"ACK-{dated_po}-01"
    insert(db, "acknowledgement", ack, dated_po, dated_line, 1, 30, 0, 10000, DEMO_TIME)
    insert(db, "promise_date", f"PRM-{ack}", ack, "2026-11-18")
    labels.append((f"PRM-{ack}", "PROMISE_BEFORE_ORDER", ""))

    stale_po, stale_line = create_order(db, "SUP-0001", "2026-11-20", "Valve", 100, 50000, "2026-11-25")
    stale_change, _ = receive(db, stale_po, stale_line, 1, 80, 20, 52500, "2026-11-28", DEMO_TIME)
    receive(db, stale_po, stale_line, 2, 95, 5, 50000, "2026-11-25", stamp("2026-11-20", 10))
    approval = f"APR-{stale_change}-01"
    insert(db, "approval", approval, stale_change, "APPROVED", "Fixture reviewer", "Stale decision fixture", stamp("2026-11-20", 11))
    plan = insert(db, "inventory_plan", None, stale_line, approval, 80, "2026-11-28", stamp("2026-11-20", 11)).lastrowid
    labels.append((str(plan), "STALE_REVISION", ""))
    return labels


def write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def export_sql(db, path):
    # SQLite's iterdump lists tables alphabetically. Replay its inserts in
    # schema order so foreign keys are enforced throughout the import.
    from tracker import SCHEMA

    tables = list(db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid"))
    inserts = {table[0]: [] for table in tables}
    for statement in db.iterdump():
        if statement.startswith('INSERT INTO "'):
            inserts[statement.split('"', 2)[1]].append(statement)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(SCHEMA.read_text() + "\nBEGIN;\n")
        for table in tables:
            stream.writelines(statement + "\n" for statement in inserts[table[0]])
        stream.write("COMMIT;\n")


def generate(out, size, seed):
    out.mkdir(parents=True, exist_ok=True)
    data_dir = out / "csv"
    data_dir.mkdir(exist_ok=True)
    headers = ("record_id", "issue_type", "expected_decision")
    counts = {}
    with connect() as db:
        init(db)
        labels = baseline(db, size, seed)
        if size >= 10 and one(db, "SELECT (SELECT COUNT(*) FROM acknowledgement)+(SELECT COUNT(*) FROM change_request)")[0] != size * 3 // 2:
            raise RuntimeError("Unexpected acknowledgement/change event count")
        data = {}
        for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            rows = db.execute(f"SELECT * FROM {table} ORDER BY 1, 2")
            columns = [entry[0] for entry in rows.description]
            data[table] = [dict(row) for row in rows]
            counts[table] = len(data[table])
            write_csv(data_dir / f"{table}.csv", columns, (row.values() for row in data[table]))
        write_json(out / "baseline.json", data)
        export_sql(db, out / "baseline.sql")
        write_csv(out / "ground_truth.csv", headers, labels)
        report = {"row_counts": counts, "metrics": metrics(db, AS_OF),
                  "baseline": score(db, [dict(zip(headers, row)) for row in labels])}

    with connect() as db:
        init(db)
        negative_labels = exceptions(db)
        export_sql(db, out / "exceptions.sql")
        write_csv(out / "exceptions_ground_truth.csv", headers, negative_labels)
        report["exceptions"] = score(db, [dict(zip(headers, row)) for row in negative_labels])

    write_json(out / "source_manifest.json", {"source": "Synthetic, authored for this project",
        "external_datasets": [], "seed": seed, "purchase_orders": size, "as_of": AS_OF,
        "combined_events_target": size * 3 // 2, "rules": "See docs/design.md"})
    write_json(out / "validation_summary.json", report)
    for result in (report["baseline"], report["exceptions"]):
        if result["missed"] or result["unexpected_findings"] or result["decision_mismatches"]:
            raise RuntimeError(f"Validation failed: {result}")
    return counts


def checksums(path):
    return {file.relative_to(path).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in sorted(path.rglob("*")) if file.is_file() and file.name != "reproducibility.json"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=Path("generated"))
    parser.add_argument("--verify-repeat", action="store_true")
    args = parser.parse_args()
    if not 10 <= args.size <= 10000:
        parser.error("--size must be between 10 and 10000")
    counts = generate(args.out, args.size, args.seed)
    print(json.dumps(counts, sort_keys=True))
    if args.verify_repeat:
        with TemporaryDirectory() as directory:
            second = Path(directory)
            second_counts = generate(second, args.size, args.seed)
            hashes = checksums(args.out)
            identical = counts == second_counts and hashes == checksums(second)
            write_json(args.out / "reproducibility.json", {"identical": identical, "row_counts": counts, "sha256": hashes})
            if not identical:
                raise SystemExit("Regeneration differed")
            print("Row counts and SHA-256 checksums match.")
