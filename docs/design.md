# Workflow and design decisions

## Record lifecycle

- Create supplier, purchase order and its first line in one transaction. A PO is issued on creation. Adding a line creates its initial planned arrival and units from the original request.
- A valid supplier response is immutable. Its status is derived from quantities as full acceptance, partial acceptance or full rejection. A PO with no accepted input reply is awaiting acknowledgement, and is overdue more than three calendar days after its order date. This is an order-level coverage metric, so a PO with replies for only some lines still counts as acknowledged.
- One acknowledgement represents one PO-line reply. The acknowledgement number increments within a PO, while revisions increment within a line. A line may receive a higher revision even when a lower revision never arrived. Wrong-line, impossible promise, stale revision and arithmetic failures for known orders and lines are persisted as rejected input events.
- A positive acceptance requires a promise date. A fully rejected reply has none. Accepted plus rejected units must equal originally requested units. A PO line cannot promise delivery before the order was issued.
- A reply with changed terms creates a change request. A revision that restores terms to the original order still creates a request when the effective terms differ. A request with no decision is pending, or superseded if a newer valid reply exists. Any attempt to decide a superseded request fails.
- The thresholds compare a proposal with the original terms. Manual review is required above 10% absolute quantity change, 5% absolute unit price change, or two calendar days absolute date change. Exact boundaries qualify for automatic approval. Full rejection triggers review through the quantity rule. Integer arithmetic avoids floating point errors. Price is stored as integer paise.
- A policy approval is recorded explicitly, as is a manual approval or rejection. At most one decision can be recorded per change. Approval, plan version and planner notifications commit together. A rejection records the decision and leaves projected supply intact. Plan records are append-only via application actions, with effective timestamps and links back to the approving decision.
- Production demand is linked to supply via allocation. Each supply line is shared in earliest need date order, breaking ties by production ID. Only allocations arriving by the job's need date count. A job's shortfall is its demand minus on-time allocated supply, floored at zero. A plan update records an in-app notification for each linked job with a positive shortfall under the new plan, including jobs already at risk.
- All reference IDs are immutable. SQLite foreign keys and uniqueness constraints cover structural validity. Workflow logic checks cross-record rules, and the evaluator independently inspects persisted records.

## Relationship map

| Parent | Child | Cardinality and optionality |
| --- | --- | --- |
| Supplier | PurchaseOrder | 1 to 0..N |
| PurchaseOrder | POLine | 1 to 1..N via application |
| PurchaseOrder, POLine | Acknowledgement | Each reply has one of each, each parent has 0..N replies |
| Acknowledgement | PromiseDate | 1 to 0..1, required when accepted units are positive |
| Acknowledgement | ChangeRequest | 1 to 0..1 |
| ChangeRequest | Approval | 1 to 0..1 |
| POLine | InventoryPlan | 1 to 1..N versions via application |
| Approval | InventoryPlan | 1 to 0..1, original plans have no approval |
| POLine | ProductionOrder | N:M via allocation, optional on both sides |
| InventoryPlan, ProductionOrder | Notification | Each notification has one of each, each parent has 0..N alerts |
| PurchaseOrder, POLine | ExceptionEvent | Each event has one of each, each parent has 0..N events |

## Data and evaluation

The full target is interpreted as 15,000 **combined** acknowledgements and change requests. Ten percent of POs are silent and a separate ten percent receive two revisions. About 18% have an additional line. Generated replies target the first line. The generator shuffles eligible scenario slots with a fixed seed. When only one revision of a pair changes, the change is assigned to the second revision. A weighted mix of partial, date, price, mixed and complete rejection scenarios supplies meaningful variation. The manual simulation rejects full rejection and high price increases above 10%, leaves slips above seven days pending where applicable, and approves remaining manual cases. This is a synthetic reviewer policy.

Ground-truth files are separate from the records consumed by the application. The clean seed is validated independently of the isolated exception seed. The exception seed represents invalid persisted records so the evaluator can detect failures that the application prevents. Labels identify one unapproved plan, a wrong-PO-line reply, a promise before order date and a stale plan regression. Duplicate constraints cover keys, line/revision, decisions, allocations and notifications. Date ordering, lifecycle joins, acceptance arithmetic, delta reconciliation and allowed values are checked using schema constraints and evaluator queries.

## Assumptions and limits

This is a single local instance using a lightweight WSGI server and SQLite. There is no authentication, real carrier integration, hosted environment or external notification delivery. Production orders each have one item, and the demo UI creates a job with one supply line. The schema and calculation support multi-line allocations. Schedule impact means material shortfall by need date. It does not automatically change production schedules. Full-scale results are fictional and are not a measure of real supplier performance.
