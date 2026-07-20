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
| `identity_access` | auth flows, guards, RBAC catalogue | Person is the single login identity; RBAC scope decision pending (ledger finding 2) |
| `configuration_control` | settings writes + history, specs, flags | One canonical settings writer; flags never substitute for authorization |
| `audit_trail` | manual business audit (as-built; fragmented) | No NEW audit writer until the four existing mechanisms consolidate (finding 1) |
| `general_ledger` | single poster, period guards, sequences, FX, tax policy | GL only via posting adapters; posted lines immutable; balances are cache |
| `platform_events` | transactional outbox, service hooks | Consequences ride the outbox; handlers never commit |
| `commercial_licensing` | license gates | Gates module availability, never data integrity (placeholder-key finding 3 pending) |
| `external_sync` | Sub AR ingestion, CRM procurement mappings | External systems are transports or contracted authorities; mirrors are rebuildable |
| `platform_services` | storage, secrets (OpenBao pointers), notifications | One owner per capability |

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

Authentication now follows the same boundary. ERP uses OIDC Authorization Code
with PKCE to accept an identity assertion, resolves the opaque issuer/subject
through an ERP-owned binding, and creates an ERP-owned session. ERP does not
query an identity-provider database, share JWT signing secrets, share cookies,
or accept provider roles as ERP permissions. See `docs/oidc_identity_contract.md`.

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
