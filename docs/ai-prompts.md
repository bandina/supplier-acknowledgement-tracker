# AI prompt history and disclosure

## ChatGPT

Act as a senior Python developer and explain the work to me in beginner-friendly
language. Help me build an end-to-end Supplier Acknowledgement and Schedule
Impact Tracker for my 72-hour coding challenge.

I want the working code, an explanation of each file, a step-by-step demo,
test evidence, a troubleshooting record, and a GitHub-ready deliverable.

1. EXPLAIN THE PROBLEM AND THE TECHNOLOGY

Explain the workflow with a simple factory example. A buyer orders motors,
the supplier replies with a different quantity, price, or date, a planner
reviews the change, and the application checks whether production is affected.

Explain how the supplier, planner, warehouse or carrier operator, logistics
analyst, and operations manager use the information. State which parts are
implemented in the local demo and which are outside its scope.

Use Python's standard library, wsgiref for the local web server, SQLite and
SQL for the database, HTML and CSS for the interface, and unittest for testing.
Explain why each technology is used and list the important Python modules.
Explain the difference between a database data model and an AI model.
Keep the application rule-based and runnable without an API key.

2. DESIGN THE DATABASE AND WRITE THE CODE

Show the folder structure and provide the complete runnable code for:
- schema.sql for the database tables and relationships
- tracker.py for the business rules
- app.py for the browser interface
- generate.py for demo and full synthetic data
- evaluate.py for validation and ground-truth comparison
- test_tracker.py for automated tests

Include Supplier, PurchaseOrder, POLine, Acknowledgement, PromiseDate,
ChangeRequest, Approval, InventoryPlan, ProductionOrder, and Notification.
Use a bridge table to link PO lines to production orders. Explain primary keys,
foreign keys, cardinality, optional fields, lifecycle states, and effective dates.
Document every field's type, units, allowed values, and validation rules.
Use the challenge's zero-padded ID formats.

For each file, explain what it does and highlight the most important functions.
Show how a browser form reaches the Python logic, how the logic writes to
SQLite, and how the saved result appears on the next page.

3. BUILD AND EXPLAIN THE FEATURES

Implement purchase orders and item lines, supplier replies and revisions,
change requests, approval decisions, projected supply history, production
risk, planner notifications, rejected input events, and operational metrics.

Compare a supplier reply with the original PO line. Explain the chosen
thresholds for quantity, price, and date, including what happens exactly at
a threshold. Keep the existing plan active while a request is pending or
rejected. Record approval before adding a new supply-plan version. Preserve
previous replies, decisions, and plan versions.

Explain the difference between a valid reply that a reviewer rejects and an
invalid reply that the application blocks. Show where each appears in the UI.
Explain how the system blocks an older revision from taking effect.

Show how the app calculates units short by the production need date,
units expected late, and the supply gap. Explain how it avoids counting
the same units for two production orders. Create an in-app planner alert
when an approved change leaves a linked production order short.

4. GENERATE AND VALIDATE THE DATA

Use one generator for starter and full datasets. Use fixed seed 20261022.
Generate 10,000 purchase orders and 15,000 combined acknowledgement and
change events. Report the two event counts separately and document the
combined-count interpretation. Include partial acceptance, date slips,
price changes, supplier silence, and multiple revisions.

Build valid parent records first. Generate child records with valid references.
Calculate quantities, differences, dates, statuses, and outcomes from the
business rules. Keep ground-truth labels outside the application's inputs.

Include labelled examples of all four required exceptions:
- An unapproved change updates the inventory plan
- A supplier reply refers to the wrong PO line
- A promised date is before the order date
- An older revision overwrites a newer one

Export CSV, JSON, SQL seeds, separate ground truth, a source manifest,
a validation summary, and reproducibility evidence. Document how the
exception fixtures are packaged. Check keys, relationships, allowed values,
date ordering, arithmetic, duplicates, and lifecycle transitions.
Regenerate the full dataset twice and compare row counts and checksums.

5. SHOW ME HOW TO RUN AND TEST THE PROJECT

Give me the Windows commands, including py app.py --demo and
py -m unittest -v. Explain each command, where tracker.db is created, and
how I can open it. For each demo step, tell me which screen to open,
what values to enter, and what result to expect.

Use this walkthrough:
- Original PO: 100 motors at ₹500 each, requested November 25, 2026.
- Linked production order: 90 motors needed November 26, 2026.
- Revised reply: 80 accepted, 20 rejected, ₹525 each, promised November 24.
- While pending, the current plan still shows 100 motors on November 25.
- After approval, the current plan shows 80 motors on November 24.
  Production has 10 units short, 0 expected late, and a 10-unit supply gap.
  A planner notification is created.
- In a separate run, reject the proposal and verify the original plan stays active.
- Submit a promised date of November 19 for a PO placed November 20.
  Verify that PROMISE_BEFORE_ORDER appears under Rejected input events.

Test normal cases, threshold boundaries, invalid replies, stale revisions,
and approval and rejection. Report the exact tests you ran and their results.
Distinguish an expected result from something you actually verified.

6. RECORD THE ERRORS AND WHAT SOLVED THEM

For each real issue, show the symptom, cause, fix, and how the fix was
verified. Explain these two issues from the project:
- Flask was unavailable in the build environment. Explain the import error
  and how the project used Python's built-in wsgiref to run locally.
- The UI showed a ₹475 increase when ₹25 was expected. Trace the saved
  values: 5000 paise means ₹50, 50000 means ₹500, and 52500 means ₹525.
  Explain the correct input and calculation.

If other issues arise, document only what actually happened. Explain why
approval history, stale revisions, and production shortage calculations
were difficult and how the implementation handles them.

7. DELIVER AND REVIEW THE FINAL OUTPUT

Provide the project files, README, design notes, data dictionary, prompt
history, tests, exports, and validation results. In the README, show how
to run the app, repeat the demo, regenerate the data, and verify the outputs.

Finish with a table that maps each challenge requirement to its code,
test, or output file. State any assumptions, known limitations, and checks
that remain incomplete. Distinguish what ChatGPT built or tested from
what I personally ran and observed. Give me a short walkthrough I can
explain to the team lead in my own words.
