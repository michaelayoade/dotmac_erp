# SOT Relationship Map — dotmac_erp

The source-of-truth architecture standard (established in `dotmac_sub`, adopted
fleet-wide per ADR-0003 in `dotmac_starter_mt`) applied to this repo. The
executable registry is `app/services/sot_relationships.py`, guarded by
`tests/architecture/test_sot_registry_liveness.py`. The Phase-0 authority
inventory behind it is `docs/PLATFORM_ADOPTION_LEDGER.md`; the GL-specific
contract remains `docs/gl_source_of_truth.md`.

## The rule

Every business decision, state transition, projection, and side effect has one
named owning service. Routes, web handlers, Celery tasks, webhooks, and
integrations are thin adapters around the owner. Observations are separated
from decisions and consequences. A change that adds, moves, or splits an owner
updates the registry (and this map) in the same change — one coherent domain
slice per change.

HTTP is one such adapter boundary. Domain and application services do not
import FastAPI or Starlette request/response types and do not raise
`HTTPException`. They return domain values or raise transport-neutral errors;
the HTTP route maps those outcomes to status codes. This keeps the same owner
callable from tasks, jobs, webhooks, commands, and reconcilers without HTTP
semantics.

## Domains

| Domain | Owns | Rule in one line |
|---|---|---|
| `organization_tenancy` | org context priming, ORM filter, RLS GUCs | Both enforcement layers are primed together or not at all |
| `identity_access` | auth flows, guards, RBAC catalogue, Person→Party catalogue projection | Person is the single login identity and the person authority; the kernel party catalogue is a rebuildable projection of it, never a second identity; RBAC scope decision pending (ledger finding 2) |
| `configuration_control` | settings writes + history, specs, flags | One canonical settings writer; flags never substitute for authorization |
| `audit_trail` | manual business audit (as-built; fragmented) | No NEW audit writer until the four existing mechanisms consolidate (finding 1) |
| `general_ledger` | single poster, period guards, sequences, FX, tax policy | GL only via posting adapters; posted lines immutable; balances are cache |
| `platform_events` | transactional outbox (claim/lease, retry, dead-letter, replay), service hooks | Consequences ride the outbox; the relay owns commits (claim/deliver/settle, token-gated); unknown events dead-letter unless declared no-consequence; handlers never commit |
| `payment_execution` | payment-intent status (every transition), transfer initiation/completion/failure/reversal, scheduled reconciliation | One service decides what a payment intent's status is; webhooks, routes and schedulers validate, authorize and delegate |
| `commercial_licensing` | license gates | Gates module availability, never data integrity (placeholder-key finding 3 pending) |
| `external_sync` | Sub AR ingestion, Sub operational-context projections, ERP material support and source-qualified correlations | External systems are transports or contracted authorities; mirrors are rebuildable |
| `bulk_imports` | durable run/partition ledger; customer field, validation and mutation port | Shared mechanics own progress and evidence; ERP owns what a row means |
| `platform_services` | storage, secrets (OpenBao pointers), notifications | One owner per capability |

Service API keys authenticate an identity but receive no authority unless an
operator assigns at least one explicit leaf scope. NULL or empty scope lists are
legacy audit findings, not a full-access compatibility mode; every service
operation fails closed for them. The API-key read surface returns scope names so
operators can locate, replace, or revoke legacy keys without exposing key
material. Wildcard scopes are refused for newly created and updated keys.

## Payment execution (ADR-0005)

`app.services.finance.payments.payment_service.PaymentService` is the **sole
writer** of `payments.payment_intent.status`. It had three writers until
2026-08-24: the service, the two-minute Celery job
`app.tasks.expense.poll_stuck_expense_transfers` (untested, and writing from a
read taken in a different session), and the dead `BatchTransferService`, which
was deleted.

The job is now an adapter: cross-tenant discovery, one `session_for_org` per
tenant, and aggregation. Selection predicates, credential resolution, the
PENDING-to-PROCESSING promotion, attempt counting and the circuit breaker are
`PaymentService`'s. Both scheduled entry points
(`expire_stale_pending_transfer`, `reconcile_stuck_transfer`) take an intent
**by id**, lock it `FOR UPDATE` with `populate_existing=True`, and re-prove the
premise the selection was made under — a transfer started, or settled by a
webhook, between the select and the write is skipped rather than overwritten.

`payments.transfer_batch` and `payments.transfer_batch_item` deliberately
survive the batch service's deletion: existing rows are payout history, and
`PaymentService._update_batch_item_status` still maintains them. Deleting dead
service code is not a destructive migration.

Enforced by `tests/architecture/test_payment_intent_status_single_owner.py`.
Note that `PaymentService` raises `HTTPException` throughout — a pre-existing
deviation from the HTTP-adapter rule above, predating this slice and not
resolved by it.

Implemented and tested; production enablement unconfirmed.

## Durable customer imports

`dotmac-imports` owns the durable run, immutable partition plan, bounded claim,
checkpoint and minimised row outcome. ERP owns customer vocabulary, validation,
duplicate policy and mutation in
`app.services.finance.import_export.durable_customers`; valid rows reach only
`customer_service.create_customer`. `dotmac-files` owns stored-object identity
and physical lifecycle, while `app.services.storage.DotmacFilesS3Provider` is
the provider adapter. Neither shared module decides customer state.

The first cutover is deliberately shadowed: the durable dry run refuses a
partition if its row verdict differs from the retiring `CustomerImporter`.
Apply is unavailable until the durable dry run is complete and error-free.
Provider reads occur between the claim transaction and the settlement
transaction, so storage latency never extends a partition lease transaction.

## Clean-instance cutover and legacy history

ADR-0003 makes the new composable ERP and the historical ERP two systems with
non-overlapping time authority. The historical ERP owns every pre-cutover
transaction and becomes read-only at final cutover. The clean ERP owns only the
approved opening state, explicitly admitted live operational items and all
post-cutover decisions. It never reads the legacy database at runtime.

Data crosses only through an owning domain's typed, idempotent adapter with
stable source identity, content fingerprint and reconciliation evidence.
Reconciled masters, open operational items, approved accounting opening state
and continuity identities are the complete admission vocabulary. Historical
transaction tables are retained in the archive rather than copied. The clean
database therefore does not become a second owner or a competing
interpretation of old books.

For Accounting specifically, Finance owns admission of the opening trial
balance and supporting subsidiary schedules; `dotmac-accounting` owns the
balanced opening journal and every later journal/ledger transition. ERP owns
only adapters and the explicitly retained balance projection. The detailed
gates are in `docs/architecture/accounting-adoption-boundary.md`.

## Sub service workflows and ERP backoffice support

`sync.sub_operational_context` owns ERP's organization-scoped mirror of Sub
projects, tickets, project tasks, and work orders. `/sync/sub/bulk` is the
neutral version-2 entry point and currently delegates to the established bulk
projection workflow during compatibility migration. Sub remains authoritative;
ERP's copies are rebuildable and exist only for local finance and
employee-expense linking. The typed contract, retry behavior, form usage, and
limitations are documented in `docs/SUB_OPERATIONAL_SYNC.md`.

`inventory.material_support` owns the ERP side of the first cross-system
operating slice. Dotmac Sub retains its service work order, operational material
need, and customer outcome. ERP alone decides warehouse availability, serial
validity, fiscal-period eligibility, stock issue, and the material-support
outcome. The neutral `/sync/sub/material-requests` routes delegate to this owner;
they do not call a provider-named route adapter.

`sync.sub_procurement` is a provider-neutral adapter over ERP-owned procurement
and inventory decisions. The retired CRM runtime cannot originate an ERP write.
The full request, outcome, reconciliation, cutover, rollback, and retirement
rules are in `docs/dotmac_sub_material_support_contract.md`.

## Replaceable application boundary

Dotmac ERP is an independent backoffice product, not an enterprise control
plane or a runtime dependency of Dotmac Sub. Dotmac Sub owns subscribers,
services, provisioning, billing facts, and operational service workflows.
Dotmac ERP owns only the backoffice and accounting records created inside ERP.

- Collaboration uses versioned APIs or events; neither product queries the
  other's database or holds cross-system foreign keys.
- External IDs are scoped correlation evidence, not enterprise identities or
  delegated decision authority.
- Each product owns its own tax-identity records and validation policy. The Sub
  subscriber import must not populate ERP's locally governed customer tax ID.
- Provider-specific Sub endpoints and mappings are adapters. Replacing ERP with
  Zoho or another backoffice product does not require moving Sub domain state.
- Delivery failure is retried and reconciled locally; there is no shared
  transaction or required shared business-domain runtime.

Authentication follows the same boundary, in its strongest form: ERP accepts no
external identity assertion at all. Login is local username and password, and
ERP is the sole issuer of its sessions and cookies. The unshipped OIDC adapter
was deleted on 2026-08-15 (never enabled, zero rows in production), so no
protocol owner is registered in `app/services/sot_relationships.py`. ERP does
not query an identity-provider database, share JWT signing secrets, share
cookies, or accept provider roles as ERP permissions. Reintroducing external
identity means adopting the released `dotmac-auth-oidc` package under the terms
in `docs/oidc_identity_contract.md`.

The detailed local contract is `docs/replaceable_application_boundary.md`.

## Status and expansion

This is the Phase-0 seed: entries record **as-built** owners verified at the
ledger's recon pin, including honest fragmentation notes (audit, settings
writers, duplicated permission checks). It deliberately starts smaller than
dotmac_sub's registry. Expansion rules:

- Each future slice that touches ownership extends the registry in the same
  commit, with the liveness test keeping every entry real.
- The undeclared-writer baseline gate (sub's second governance layer) is added
  once coverage grows past the seed — tracked in the ledger's Phase-1 steps.
- Deviations from an owner in this map require an explicit architecture
  decision, per the fleet standard.
