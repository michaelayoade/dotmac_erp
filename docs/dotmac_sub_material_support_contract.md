# Dotmac Sub to ERP material-support source-of-truth contract

## Purpose

Dotmac Sub owns service delivery and the operational reason material is
needed. Dotmac ERP owns warehouse stock and the backoffice decision to issue
it. The exchange is a versioned request and outcome contract between two
independent applications; neither application reads or writes the other's
database.

## Named owners

| Decision or state | Owner | Canonical record/service |
|---|---|---|
| Service work order and customer outcome | Dotmac Sub | Sub work-order owner |
| Material need, contextual submission, delivery retry, and reconciliation | Dotmac Sub | Sub material-request owner and outbox |
| Material-support admission and authoritative ERP outcome | Dotmac ERP | `inventory.material_support` |
| Warehouse, stock, serial, fiscal-period, and issue decision | Dotmac ERP | ERP inventory transaction and posting services |
| Projection of the ERP outcome into the service workflow | Dotmac Sub | Sub material-outcome resolver |

ERP must not change the Sub service work order. Sub must not allocate an ERP
serial, post inventory, or decide that ERP stock has been issued.

## Request contract

Sub delivers `POST /api/v1/sync/sub/material-requests` with explicit API-key
scope `sub:material:write`. The strict request is:

| Field | Meaning |
|---|---|
| `source_request_id` | Immutable Sub material-request identity |
| `request_type` | `ISSUE`; other request types fail closed |
| `status` | Requested ERP state; the operational sender normally submits `submitted` |
| `requested_by_email` | Staff identity used to resolve the ERP employee |
| `schedule_date` | Approved operational need date, in `YYYY-MM-DD` form |
| `project_source_id` | Optional Sub project identity resolved through the Sub projection ledger |
| `items` | Item code, quantity, UOM, source warehouse, and selected serials |
| `remarks` | Operational context, never an inventory decision |

The request is idempotent on `(organization_id, source_system="sub",
source_id=source_request_id)` and on its immutable body. An identical replay
returns the existing ERP record with HTTP 200; first acceptance returns HTTP
201. A different body reusing the same identity fails closed, except an
allowed status advance whose non-status body is unchanged.

`MaterialRequest.source_id` is the only live source identity. There is no
`omni_id`, `crm_id`, CRM mapping, or CRM fallback in the request path.

## Outcome contract

ERP's material-request state is authoritative for the backoffice result. Sub
reads `GET /api/v1/sync/sub/material-requests/{source_request_id}` with
`sub:material:read` (or the write scope) and consumes signed outcome events
emitted for Sub-originated requests. Both paths feed one idempotent Sub
resolver.

| ERP outcome | Sub material dependency | Service-workflow consequence |
|---|---|---|
| `draft`, `submitted`, `pending_stock`, `partially_ordered`, `ordered` | remains waiting | workflow remains blocked |
| `issued`, `fulfilled`, `complete`, `completed` | fulfilled | material allocation is recorded and the owning workflow may resume |
| `cancelled`, `canceled`, `rejected`, `declined`, `denied` | canceled | workflow remains blocked and requires operational replanning |

A changed ERP request identity fails closed. Replaying an outcome cannot
duplicate work-order material.

## Reliability and repair

1. Sub commits its request and outbound delivery record together.
2. Delivery retries with a stable source key and dead-letters exhausted or
   permanent failures.
3. ERP records acceptance and inventory effects under the same immutable Sub
   source identity.
4. Sub records delivery evidence and applies the authoritative outcome through
   one resolver; an application failure leaves the event retryable.
5. Polling repairs a lost or delayed signed outcome.
6. Neither a cache nor an imported identifier is the only copy of source
   identity.

## Retirement and activation state

The code-level CRM retirement is complete in this change: CRM routes, clients,
tasks, settings, scopes, mappings, templates, and runtime services are removed.
Historical CRM-shaped rows are copied to the sealed
`archive.retired_crm_records` relation and then removed from live operational
tables. They are evidence only and are never relabelled as Sub records.

This change does not deploy or activate production. Before enabling Sub
traffic, operators must:

1. install a clean ERP database where practicable; for a legacy database use
   the destructive procedure in `docs/runbooks/CRM_RETIREMENT_CUTOVER.md`;
2. deploy compatible ERP and Sub contract revisions;
3. provision only the required `sub:*` scopes and signed outcome binding;
4. prove item, warehouse, employee, project, and serial resolution;
5. prove create, replay, identity-conflict, pending-stock, issue, signed
   outcome, polling repair, and dead-letter alert paths end to end; and
6. reconcile every already accepted ERP request to completion.

Rollback never restores CRM authority or reopens its runtime. An accepted ERP
request remains ERP-owned. A failed activation stops Sub delivery, repairs the
forward state, and reconciles existing requests; the database migration has no
automatic downgrade.
