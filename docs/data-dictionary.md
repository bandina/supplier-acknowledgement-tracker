# Data dictionary

Text dates are ISO `YYYY-MM-DD`. Timestamps use ISO 8601 UTC with seconds and `+00:00`. A dash in the unique column means the field need not be unique on its own. PK means primary key and FK means foreign key. Each table's lifecycle and optional relationships are explained in [the design notes](design.md).

| Table.field | Type | Required | Unique | Allowed values, units and validation |
| --- | --- | --- | --- | --- |
| supplier.id | TEXT | Yes | PK | Immutable `SUP-0001` style ID |
| supplier.name | TEXT | Yes | — | Nonblank company display name |
| purchase_order.id | TEXT | Yes | PK | `PO-YYYY-NNNNNN`, year of order |
| purchase_order.supplier_id | TEXT | Yes | — | FK to supplier |
| purchase_order.ordered | TEXT | Yes | — | Date the PO was issued |
| po_line.id | TEXT | Yes | PK | PO ID plus `-LNNN` |
| po_line.po_id | TEXT | Yes | — | FK to purchase_order |
| po_line.item | TEXT | Yes | — | Nonblank item name |
| po_line.quantity | INTEGER | Yes | — | Positive count in units |
| po_line.price | INTEGER | Yes | — | Positive unit price in paise |
| po_line.requested | TEXT | Yes | — | Requested delivery date, on or after order date |
| acknowledgement.id | TEXT | Yes | PK | `ACK-<PO-ID>-NN`, sequence per PO |
| acknowledgement.po_id | TEXT | Yes | — | FK to purchase_order, must match the line's PO |
| acknowledgement.line_id | TEXT | Yes | With revision | FK to po_line |
| acknowledgement.revision | INTEGER | Yes | With line_id | Positive revision, increasing for valid received replies on a line |
| acknowledgement.accepted | INTEGER | Yes | — | Nonnegative units; accepted plus rejected equals requested |
| acknowledgement.rejected | INTEGER | Yes | — | Nonnegative units; accepted plus rejected equals requested |
| acknowledgement.price | INTEGER | Yes | — | Positive proposed unit price in paise |
| acknowledgement.received | TEXT | Yes | — | Receipt timestamp, on or after order date |
| promise_date.id | TEXT | Yes | PK | `PRM-<ACK-ID>` |
| promise_date.ack_id | TEXT | Yes | Yes | FK to acknowledgement, present only for positive accepted quantity |
| promise_date.promised | TEXT | Yes | — | Promised delivery date, on or after order date |
| change_request.id | TEXT | Yes | PK | `CHG-<PO-ID>-NNNNNN`, sequence from acknowledgement |
| change_request.ack_id | TEXT | Yes | Yes | FK to acknowledgement |
| change_request.quantity_delta | INTEGER | Yes | — | Accepted minus requested units, signed |
| change_request.price_delta | INTEGER | Yes | — | Proposed minus requested unit price in paise, signed |
| change_request.days_delta | INTEGER | No | — | Promised minus requested calendar days, absent for full rejection |
| change_request.review | INTEGER | Yes | — | 0 for automatic, 1 for manual review, derived from thresholds |
| approval.id | TEXT | Yes | PK | `APR-<CHG-ID>-01` |
| approval.change_id | TEXT | Yes | Yes | FK to change_request |
| approval.decision | TEXT | Yes | — | APPROVED or REJECTED |
| approval.actor | TEXT | Yes | — | Nonblank reviewer name, or Policy for automatic decision |
| approval.reason | TEXT | Yes | — | Nonblank decision justification |
| approval.decided | TEXT | Yes | — | Decision timestamp, at or after receipt |
| inventory_plan.id | INTEGER | Yes | PK | SQLite version record number |
| inventory_plan.line_id | TEXT | Yes | — | FK to po_line |
| inventory_plan.approval_id | TEXT | No | Yes when set | FK to approval; blank only on original plan; otherwise decision must be approved |
| inventory_plan.quantity | INTEGER | Yes | — | Nonnegative projected receipt units, matches original or approved quantity |
| inventory_plan.arrival | TEXT | No | — | Original or approved arrival date, absent for zero supply |
| inventory_plan.effective | TEXT | Yes | — | Order start timestamp for original plan or decision timestamp for approved plan |
| production_order.id | TEXT | Yes | PK | `PROD-NNNNNN` |
| production_order.item | TEXT | Yes | — | Item required by this production order |
| production_order.demand | INTEGER | Yes | — | Positive required units |
| production_order.needed | TEXT | Yes | — | Date units are needed by |
| production_order.planner | TEXT | Yes | — | Nonblank fictional recipient display name |
| allocation.line_id | TEXT | Yes | Composite PK | FK to po_line |
| allocation.production_id | TEXT | Yes | Composite PK | FK to production_order; item must match the PO line |
| allocation.quantity | INTEGER | Yes | — | Positive allocated units; total per line cannot exceed originally ordered units |
| notification.id | TEXT | Yes | PK | `NOT-YYYY-NNNNNN`, year of creation |
| notification.plan_id | INTEGER | Yes | With production_id | FK to inventory_plan, identifies triggering plan |
| notification.production_id | TEXT | Yes | With plan_id | FK to production_order; planner identity comes from that record |
| notification.shortfall | INTEGER | Yes | — | Units short at need date at time of alert, positive via workflow |
| notification.created | TEXT | Yes | — | Alert timestamp, matches plan decision time |
| exception_event.id | INTEGER | Yes | PK | SQLite event number |
| exception_event.po_id | TEXT | Yes | — | FK to existing purchase_order claimed in the attempted input |
| exception_event.line_id | TEXT | Yes | — | FK to existing po_line claimed in the attempted input |
| exception_event.revision | INTEGER | Yes | — | Positive attempted revision |
| exception_event.accepted | INTEGER | Yes | — | Attempted nonnegative accepted units |
| exception_event.rejected | INTEGER | Yes | — | Attempted nonnegative rejected units |
| exception_event.price | INTEGER | Yes | — | Attempted positive unit price in paise |
| exception_event.promised | TEXT | No | — | Attempted date, if given |
| exception_event.received | TEXT | Yes | — | Timestamp of attempted input |
| exception_event.reason | TEXT | Yes | — | WRONG_PO_LINE, PROMISE_BEFORE_ORDER, STALE_REVISION, REPLY_BEFORE_ORDER, or INVALID_ACCEPTANCE |

Each ground-truth row has a text `record_id` referencing the record in the relevant seed, optional text `issue_type` for an expected anomaly, and optional text `expected_decision` from APPROVED, REJECTED, PENDING, or SUPERSEDED. These are evaluation labels held outside the application database. The pair of issue type and record ID identifies an anomaly. There is at most one decision label per change.
