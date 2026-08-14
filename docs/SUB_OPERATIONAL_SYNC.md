# Self-Care to ERP operational sync

Dotmac Self-Care owns projects, tickets, project tasks, and work orders. ERP
stores organization-scoped copies so finance and employee expense workflows can
link costs to that work. ERP does not become the workflow owner.

## Contract and retry behavior

Self-Care sends `POST /api/v1/sync/sub/bulk` with API-key scope
`sub:domain:write`. The legacy `crm:sync:write` scope remains a transition-only
compatibility grant. The version-2 request contains these arrays, processed in
this order:

1. `projects`
2. `tickets`
3. `project_tasks`
4. `work_orders`

Every record carries its Self-Care UUID as `source_id`. Project tasks also carry
`project_source_id` and may carry `parent_task_source_id` and
`ticket_source_id`. ERP resolves these identifiers only inside the authenticated
organization.

A successful response has the real ERP shape:

```json
{
  "contract_version": 2,
  "projects_synced": 1,
  "project_tasks_synced": 1,
  "tickets_synced": 1,
  "work_orders_synced": 0,
  "errors": []
}
```

An item error contains `entity_type`, `crm_id` (the source identifier retained
for compatibility), and `error`. Self-Care validates this response and advances
its watermarks only when the complete batch has no errors.

The endpoint is safe to retry. Projects and tickets use their organization and
source mappings. Project tasks use `sync.sync_entity` under
`(organization_id, dotmac_sub, sub_project_task, source_id)`. Replaying a batch
updates the same ERP records rather than creating duplicates.

## ERP expense use

Imported projects, tickets, and tasks are available in:

- the employee expense-claim Project, Ticket, and Task fields;
- the Finance expense Project, Ticket, and Task fields; and
- Finance expense detail after the expense is saved.

Finance verifies that every selection belongs to the current organization. A
selected task must match the selected project and selected ticket. Selecting a
task can also supply its project and ticket when those fields are omitted.

## Test evidence

`tests/integration/test_sub_operational_sync_v2.py` calls the actual ERP FastAPI
route and sync service against the migration-built PostgreSQL test database. It
creates a project, ticket, and linked project task, checks the exact version-2
response, confirms both expense form data sources, creates a linked Finance
expense, and replays the request to prove idempotency.

## Limitations and deployment

- A parent project task must already exist or appear before its child.
- Incremental sync does not delete records removed in Self-Care; status changes
  are projected and orphan reconciliation remains separate.
- The expense-link migration must run before enabling the new Finance Ticket and
  Task fields.
- The CRM-named bulk route remains a compatibility path and currently shares the
  same version-2 projection implementation. New Self-Care callers use only the
  neutral `/api/v1/sync/sub/bulk` route.
- Deploy ERP contract and migration changes before enabling the Self-Care sync
  worker. A failed or older response leaves Self-Care watermarks unchanged.
