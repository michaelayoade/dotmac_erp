# Dotmac Sub → ERP material-support source-of-truth contract

## Purpose

This contract is the first implementation slice of the approved operating
boundary:

- **Dotmac Sub owns service delivery and operational customer workflows.**
- **Dotmac ERP owns backoffice support workflows and resources.**

A field material need is therefore not an ERP-owned service workflow and not a
Sub-owned stock issue. It is a cross-system request: Sub owns why the material
is needed and whether the service workflow is blocked; ERP owns whether and how
stock is fulfilled.

## Named owners

| Decision or state | Owner | Canonical record/service |
|---|---|---|
| Service work order and customer outcome | Dotmac Sub | `WorkOrder` and `operations.work_order_commands` |
| Material need, contextual submission, and service dependency | Dotmac Sub | `FieldMaterialRequest` and `operations.material_dependencies` |
| Request delivery, retries, and reconciliation | Dotmac Sub | `field_erp_sync_events` and `integration.erp_material_support` |
| Material support intake and outcome contract | Dotmac ERP | `inventory.material_support` |
| Warehouse, stock, serial, fiscal-period, and issue decision | Dotmac ERP | ERP inventory models and inventory transaction/posting services |
| Projection of ERP outcome back into the service workflow | Dotmac Sub | `operations.material_dependencies.apply_backoffice_outcome` |

ERP must not change the service work order. Sub must not post inventory,
allocate an ERP serial, or decide that ERP stock has been issued.

## Request contract

When an ERP-channel material request is submitted, Sub writes an outbox event in the
same database transaction. The current compatibility payload uses:

| Field | Meaning |
|---|---|
| `omni_id` | Immutable Sub `FieldMaterialRequest.id`; the legacy name is temporary |
| `request_type` | `ISSUE` |
| `status` | Requested ERP state, `submitted`; it never asks ERP to issue automatically |
| `requested_by_email` | Staff identity used to resolve the ERP employee |
| `schedule_date` | Date the operational need was approved |
| `ticket_crm_id` | Compatibility reference only; it conveys no CRM authority |
| `items` | Item code, quantity, UOM, source warehouse, and selected serials |
| `remarks` | Operational context; never an inventory decision |

`POST /api/v1/sync/sub/material-requests` is idempotent on the source request
identifier and immutable request body. An identical replay returns the existing
ERP record. A conflicting body for the same identifier fails closed.

The `crm_id` storage column and `omni_id` wire name are compatibility debt. For
Sub requests they contain a Sub UUID, not CRM ownership. A later schema-only
provenance migration may rename them after both clients pass parity; it must not
create a second identity or writer meanwhile.

## Outcome contract

ERP's material-request state is authoritative for the backoffice result. Sub
projects it as follows:

| ERP outcome | Sub material dependency | Service-workflow consequence |
|---|---|---|
| `draft`, `submitted`, `pending_stock`, `partially_ordered`, `ordered` | remains `approved` | workflow remains blocked/waiting |
| `issued` | `fulfilled` | create/update the work-order material allocation and allow the owning workflow to resume |
| `fulfilled`, `complete`, `completed` | `fulfilled` | same compatibility terminal projection |
| `cancelled`, `canceled`, `rejected`, `declined`, `denied` | `canceled` | workflow remains blocked and requires operational replanning |

Sub applies outcomes through one idempotent resolver. The immediate POST
response and the polling reconciler call that same resolver; neither writes the
projection independently. A changed ERP request identifier fails closed.

## Reliability and repair

1. Submission and outbox enqueue are atomic after cutover; there is no separate Sub approval.
2. Delivery uses the stable key `mr-{sub_request_id}-approve-v1`.
3. The Sub outbox retries transient failures and dead-letters exhausted or
   permanent failures.
4. Recording a delivered material outcome and projecting it into the Sub
   dependency are one commit. A projection failure leaves the idempotent event
   retryable instead of acknowledging a dropped consequence.
5. The polling reconciler repairs lost/delayed responses from ERP's
   authoritative status endpoint.
6. Replaying an ERP outcome is safe and does not duplicate work-order material.
7. Neither a cache nor the compatibility external identifier is the only copy
   of the source request identity.

## Authority migration and cutover

### Old paths

- CRM can originate the ERP material-request sync path.
- Sub manager actions can locally mark a request issued or fulfilled.

### New paths

- Sub originates the material need from its service workflow.
- ERP alone decides and records material issue/cancellation.
- Sub only projects ERP's outcome into its service dependency.

### Cutover gate

Do not assign `sync_flow_ownership.material_request = sub` until all of these
are true:

1. the ERP `/sync/sub/material-requests` contract is deployed and authenticated
   with `sub:material:write`;
2. item, warehouse, employee, and serialized-stock parity checks pass;
3. the Sub outbox worker and outcome reconciler are enabled and observed;
4. no pending CRM-originated material request can race the same business need;
5. an end-to-end test proves `approved → pending_stock/issued → Sub projection`;
6. dashboards/alerts cover pending, dead-lettered, and identity-conflict cases.

The master ERP-sync setting and the per-flow owner must both be active. Before
cutover, Sub creates no material outbox backlog. After cutover, code rejects the
local Sub issue/fulfil actions. Rollback returns the ownership row to `crm`
before disabling delivery; already accepted ERP requests remain ERP-owned and
must be reconciled to completion.

### Retirement gate

After one agreed observation window with zero unexplained drift, remove the
local Sub issue/fulfil endpoints and the CRM material-request origin path. The
compatibility `omni_id`/`crm_id` naming can then be migrated separately with a
backfill, unique-key parity proof, and rollback plan.
