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
| `external_sync` | Sub AR ingestion, ERP material support, legacy CRM procurement mappings | External systems are transports or contracted authorities; mirrors are rebuildable |
| `platform_services` | storage, secrets (OpenBao pointers), notifications | One owner per capability |

## Sub service workflows and ERP backoffice support

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
