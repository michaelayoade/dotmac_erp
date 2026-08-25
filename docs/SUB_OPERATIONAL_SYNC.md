# Dotmac Sub to ERP operational projections

Dotmac Sub owns projects, project tasks, and work orders. ERP stores
organization-scoped projections so finance and employee-expense workflows can
link costs to that work. ERP does not become the operational workflow owner.
ERP support tickets are a separate, ERP-owned lifecycle and are not imported
from Sub.

## Contract and retry behavior

Sub sends `POST /api/v1/sync/sub/bulk` with API-key scope
`sub:domain:write`. This is the only operational bulk route. The strict
version-2 request contains these arrays, processed in order:

1. `projects`
2. `project_tasks`
3. `work_orders`

Every record carries its Sub UUID as `source_id`. Project tasks carry
`project_source_id` and may carry `parent_task_source_id`; work orders may
carry `project_source_id`. ERP resolves those identifiers only inside the
authenticated organization.

A successful response has this exact shape:

```json
{
  "contract_version": 2,
  "projects_synced": 1,
  "project_tasks_synced": 1,
  "work_orders_synced": 0,
  "errors": []
}
```

An item error contains `entity_type`, `source_id`, and `error`. Sub advances
its delivery watermark only after the complete batch returns without errors.

The endpoint is safe to retry. `sync.sync_entity` is the sole correlation
ledger, keyed by organization, source system `sub`, source doctype, and
`source_id`. Replaying a batch updates the same ERP records instead of creating
duplicates. The projection carries no CRM alias or compatibility mapping.

## ERP expense use

Imported projects and tasks are available in the employee and Finance expense
Project and Task fields and on Finance expense detail. Finance verifies that
each selection belongs to the current organization and that a selected task
belongs to the selected project. Ticket linkage, when used by an ERP expense,
refers only to an independently ERP-owned ticket.

## Test evidence

`tests/sync/test_sub_operational_contract.py` pins the strict ticket-free DTO,
the project-task status policy, and delegation from the canonical route to the
registered owner. PostgreSQL route-and-replay evidence belongs in
`tests/integration/test_sub_operational_sync_v2.py`; that test must use the
Sub service-principal dependency and `sync.sync_entity`, never a retired CRM
model.

## Limitations and activation

- A parent project task must already exist or appear before its child.
- Incremental sync does not delete records removed in Sub; status changes are
  projected and orphan reconciliation remains a separate repair operation.
- Deploy the ERP contract and migration before enabling the Sub sender. A
  failed or older response leaves the Sub watermark unchanged.
- No CRM route, scope, setting, task, client, mapping, or fallback may be used
  to activate or repair this flow.
