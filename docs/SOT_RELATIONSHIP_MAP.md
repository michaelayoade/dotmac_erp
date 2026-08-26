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
| `identity_access` | auth flows, exact external binding/finalization, local access lifecycle, RBAC catalogue | Person is the login identity; ERP alone writes bindings and sessions |
| `configuration_control` | settings writes + history, specs, flags | One canonical settings writer; flags never substitute for authorization |
| `audit_trail` | manual business audit (as-built; fragmented) | No NEW audit writer until the four existing mechanisms consolidate (finding 1) |
| `general_ledger` | single poster, period guards, sequences, FX, tax policy | GL only via posting adapters; posted lines immutable; balances are cache |
| `platform_events` | transactional outbox (claim/lease, retry, dead-letter, replay), service hooks | Consequences ride the outbox; the relay owns commits (claim/deliver/settle, token-gated); unknown events dead-letter unless declared no-consequence; handlers never commit |
| `commercial_licensing` | license gates | Gates module availability, never data integrity (placeholder-key finding 3 pending) |
| `external_sync` | Sub AR ingestion, Sub operational-context projections, ERP material support, legacy CRM procurement mappings | External systems are transports or contracted authorities; mirrors are rebuildable |
| `platform_services` | storage, secrets (OpenBao pointers), notifications | One owner per capability |

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
they do not call the legacy CRM route adapter.

The inherited CRM procurement implementation is an explicit compatibility
engine during migration, not a second business owner. The per-flow Sub cutover
guard prevents CRM and Sub from originating the ERP write concurrently. The
full request, outcome, reconciliation, cutover, rollback, and retirement rules
are in `docs/dotmac_sub_material_support_contract.md`.

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

Authentication preserves the product boundary. Published `dotmac-auth-oidc`
verifies the external ceremony; `ERPExternalIdentityAuthority` resolves an
exact pre-provisioned subject to Person; and `AuthFlow` issues an ERP session
carrying binding provenance. ERP does not create identities at login, link by
email, share cookies or signing keys, or accept provider roles.

`app.services.application_lifecycle` is the one ERP owner for Person login
eligibility, credential activation/deactivation orchestration, and the durable
product-local plan/receipt exposed at
`/api/v1/integrations/application-lifecycle/{plan,apply,observe,cancel}`. Admin
activation and HR offboarding delegate to that owner. Its namespaced
`erp.application.lifecycle.v1` contract is provider-neutral and carries only an
exact organization/person/desired-state/external-subject target; Integrator owns
transport commands and retries. The local operation row is ERP execution
evidence, not a second Integrator ledger. APPLY activates only when the exact
provider registration is held at startup. Provisioning or
disable/selective-revocation occurs in one ERP transaction; no kernel identity
or session table is composed.

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
