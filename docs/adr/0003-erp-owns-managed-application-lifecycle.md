# ADR-0003 — ERP owns its managed application lifecycle

- **Status:** Superseded in part by ADR-0004
- **Date:** 2026-08-17
- **Decider:** Michael
- **Supersedes:** nothing

## Context

Vendor Control Plane and Integrator need a provider-neutral way to request that
ERP make one already-known Person active or inactive. Integrator owns transport
bindings, connector retries and external I/O, but it cannot own ERP's Person,
credentials, sessions or the evidence that a local transition occurred.

ERP already had three local write paths: admin account activation, HR
offboarding credential changes, and `auth_flow` session revocation. Adding a
fourth managed path beside them would create drift. External identity is also
not currently adoptable: `docs/PLATFORM_ADOPTION_LEDGER.md` explicitly prohibits
kernel Party/AuthSession persistence, and the retained ERP
`federated_identities` table has no reader or writer after the unshipped OIDC
implementation was deleted.

## Decision

1. `app.services.application_lifecycle.ApplicationAccessLifecycle` is ERP's one
   orchestration owner for Person login eligibility and credential activation or
   deactivation. Session revocation remains owned by `auth_flow` and is called
   by the orchestrator. Admin activation and HR offboarding delegate here.
2. ERP publishes the product-owned, namespaced capability
   `erp.application.lifecycle.v1`. Its only target is
   `{organization_id, person_id, desired_state, external_subject}`. Profile,
   employment and authorization fields are not in the schema and are rejected.
3. PLAN creates an organization-scoped durable operation with the exact target,
   observed state and plan digests. APPLY must present the operation reference,
   idempotency key, target and all three digests; it locks the operation and
   Person and refuses drift. OBSERVE and CANCEL accept only the operation
   reference and derive their evidence from that row.
4. The receipt is ERP execution evidence and an idempotency boundary, not a
   duplicate of Integrator's command/retry ledger. The API performs no provider
   I/O and carries no provider branches or credentials.
5. APPLY fails closed with `external_identity_not_adopted` until a later,
   explicit identity/session authority ADR supersedes the kernel adoption
   ledger. Neither the retained legacy binding table nor an ERP shadow of kernel
   session provenance is an admissible interim owner.

## Consequences

- Integrator can bind to a stable product-owned contract without gaining direct
  write authority over ERP data.
- Retries return the same plan or receipt; an idempotency key reused for another
  target is refused.
- The operation table is organization-scoped, FORCE-RLS protected, and enforces
  Person/organization parity with a composite foreign key.
- Managed activation is deliberately not complete OIDC adoption. Its explicit
  activation check and stable blocked result prevent a contract from being
  mistaken for a working login path.

## Alternatives rejected

- **Let Vendor Control Plane or Integrator own ERP lifecycle state.** They are
  transports/control planes, not the owner of ERP Person or sessions.
- **Reuse ERP `federated_identities`.** That restores a retired parallel owner
  and its global issuer/subject uniqueness rather than adopting the kernel
  tenant-scoped owner.
- **Create ERP-local session provenance.** That would split selective
  revocation from the kernel finalization path it is defined to protect.
- **Keep a memory-only plan.** It cannot make retries, observation or tenant
  isolation durable and auditable.
