# Supplier Acknowledgement and Schedule Impact Tracker

A local application for recording supplier replies, reviewing purchase-order changes and tracing approved changes to expected receipts and production shortages.

## Run the demo

Python 3.11 or newer is recommended. The application uses Python's standard library and bundled SQLite. It requires no package installation, credentials or external services.

```sh
python app.py --demo
```

Open http://127.0.0.1:8000. On Windows you can use `py` in place of `python`. A fresh demo database is created on first launch. Use `--db walkthrough.db --demo` for a fresh walkthrough with a different filename.

1. Open `PO-2026-000001`. The order is for 100 motors at 50000 paise (₹500) each, requested November 25. The production order needs 90 by November 26.
2. Save revision 1 with 80 accepted, 20 rejected, price 52500 paise, and promised date 2026-11-28.
3. Check that the reply is **Pending** and the only plan version still shows 100 units on November 25.
4. Open **Approvals**, approve the request, enter a reviewer and reason, and save.
5. Reopen the PO. Both plan versions are visible. The new effective plan shows 80 units on November 28.
6. Open **Supply & production** and **Notifications**. The job is short 90 units **by its need date** because all 80 promised units arrive two days late. The notification links to the plan and approval through their IDs.
7. Use a fresh demo database and reject the same proposed change. Verify the original plan is still effective and no planner notification was created.

## Generate and verify the data

```sh
python -m unittest -v
python generate.py --size 100 --seed 20261022 --out starter --verify-repeat
python generate.py --size 10000 --seed 20261022 --out generated --verify-repeat
python evaluate.py generated/baseline.sql generated/ground_truth.csv
python evaluate.py generated/exceptions.sql generated/exceptions_ground_truth.csv
python app.py --db full.db --seed-sql generated/baseline.sql
```

The last command requires a fresh database filename. The project includes the full exports under `generated/`. The generator writes per-table CSV, combined JSON, replayable SQLite SQL, separate ground truth, a source manifest, validation metrics and SHA-256 comparison results. The scoring command exits nonzero for missed or unexpected findings or mismatched decisions. Load only `baseline.sql` into the application. `exceptions.sql` is intentionally invalid business data for the evaluator.

## Design and review

- [Workflow, rules and limitations](docs/design.md)
- [Data dictionary](docs/data-dictionary.md)
- [SQL schema](schema.sql)
- [AI prompt history and disclosure](docs/ai-prompts.md)
- [Full run validation](generated/validation_summary.json)
- [Reproducibility evidence](generated/reproducibility.json)

This is a localhost demonstration with fictional data and typed reviewer names. The workflow models expected receipts and delivery exceptions. It does not book freight, receive physical stock or deliver external email.
