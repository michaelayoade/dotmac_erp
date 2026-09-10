# Self-Care to ERP operational sync

Dotmac Self-Care owns projects, tickets, project tasks, and work orders. ERP
stores organization-scoped copies so finance and employee expense workflows can
link costs to that work. ERP does not become the workflow owner.

## Contract and retry behavior

Self-Care sends `POST /api/v1/sync/sub/bulk` with API-key scope
`sub:domain:write`. No provider or retired-application scope is accepted. The
version-2 request contains these arrays, processed in this order:

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

An item error contains `entity_type`, `source_reference`, and `error`.
Self-Care validates this response and advances
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

### Field employee claims and reimbursement

The legacy `POST /api/v1/sync/sub/expense-claims` create-and-submit contract is
retained for already-deployed callers. New Self-Care expense delivery begins
only after the Field manager has approved the authoritative request. The worker
creates or retrieves a receipt-capable `DRAFT` through
`POST /api/v1/sync/sub/expense-claims/drafts`. Every line carries a stable
`source_line_id`, and the response maps it to the ERP item identity without
making the claim visible to ERP approval processing.

Self-Care then uploads each private attachment through
`POST /api/v1/sync/sub/expense-claims/{source_claim_id}/items/{item_id}/receipts`.
The typed request supplies the source line and attachment identities, validated
filename, MIME type, byte size, SHA-256 checksum, and base64 transport content.
The same identities form the required `Idempotency-Key`. ERP validates the
decoded bytes through its expense-receipt storage policy, records checksum and
attachment evidence, and returns the existing attachment for an identical
retry. Receipt content is never written to an outbox or log.

Sub remains authoritative for the Field manager decision and delivers its
durable decision evidence to the claim-specific `/approve` or `/reject`
endpoint only after all mandatory receipt uploads succeed. Approval submits a
receipt-complete draft and then projects the trusted manager decision. ERP
verifies the manager's employee identity, monetary authority, self-approval
restriction, and category receipt rules before acceptance. Draft creation,
receipt upload, decision endpoints, and claim status polling require the exact
`sub:expense:write` service scope.

An approved claim may be paid from the Field app. Sub only stages and delivers
the command; ERP owns creation of the payment intent, Paystack transfer,
webhook/poll reconciliation, accounting consequences, and final `PAID` fact.
The payment endpoint requires the stronger exact `sub:expense:pay` scope. One
Sub command ID maps idempotently to one payment intent. An unknown provider
outcome is returned as `INDETERMINATE` and is never automatically retried.

## NCC regulatory projection

Sub reads ERP-owned NCC Section F/G evidence through
`GET /api/v1/sync/sub/ncc/financials` and
`GET /api/v1/sync/sub/ncc/staff-headcount`. The service key configured for
Sub's `ERP_REGULATORY_CAPABILITY` must be granted `sub:ncc:read` before this
contract is deployed. The material-support bootstrap does not own that key and
must not grant the regulatory scope. The retired `crm:ncc:read` scope is
refused.

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
- The retired CRM route, task, client, credentials, and mappings are absent.
  Self-Care callers use only `/api/v1/sync/sub/bulk`.
- Deploy ERP contract and migration changes before enabling the Self-Care sync
  worker. A failed or older response leaves Self-Care watermarks unchanged.
- Do not replay historical failed expense events automatically. A Self-Care
  recovery owner must revalidate and explicitly create linked replacement
  evidence before this contract is invoked.
