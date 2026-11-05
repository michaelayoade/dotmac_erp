# ADR-0015 — Kernel identity effects are ERP-hosted, with a gated revisit

- **Status:** Accepted
- **Date:** 2026-10-26
- **Decider:** Michael
- **Program slice:** E8 close-out / kernel lineage disposition
  (`docs/architecture/kernel-0001-dispositions.md`)

## Context

Kernel revision `0001_initial_tenant_schema` is atomic: roles, eight
tables, RLS, grants. It can never run in ERP — its first statement
creates `public.tenants`, which ERP intentionally hosts itself
(`20260813_tenant_projection`), and its identity tables (`people`,
`user_credentials`, `auth_sessions`, `roles`, `person_roles`,
`audit_events`) collide with ERP's incompatible incumbent authorities.
The executable canary (`tests/integration/test_kernel_lineage_rehearsal.py`)
proves this permanently, on purpose.

So ERP's kernel adoption has proceeded effect-by-effect in ERP's own
lineage: tenant catalogue as an Organization projection, dual-GUC tenant
scoping, module database roles, the idempotency ledger (ADR-0001).
The disposition matrix leaves identity, sessions, RBAC and audit marked
"Blocked" with no named endpoint. An indefinite "blocked" is not a
decision: it lets every future slice re-litigate whether ERP identity is
temporary, and it misstates the thin-client target (replace-not-adapt,
2026-10-16) as either already satisfied or silently abandoned.

Sub is on the other trajectory: it does not host `tenants`, and its
kernel-lineage rehearsal is a forward ratchet toward stamping the kernel
lineage. Starter identity will therefore be *proven* in Sub before ERP
would ever need to attempt it.

## Decision

1. **The current endpoint is accepted, on the record.** ERP hosts the
   kernel-shaped effects it needs (tenants, tenant domains, scope
   function, module roles, idempotency storage) in its own lineage, and
   ERP's incumbent identity, session, RBAC and audit authorities remain
   the authorities. No slice may stamp, stub-stamp, or partially run
   kernel revision 0001, and no second identity authority may be created
   alongside ERP's.
2. **This is a deviation from replace-not-adapt, recorded as one** —
   accepted because identity replacement would migrate authentication,
   live sessions, a global-shaped RBAC model and audit history in one
   programme, against a kernel contract not yet proven in production
   anywhere.
3. **Revisit is gated, not calendared.** This ADR must be re-opened when
   both hold: (a) Starter/kernel identity is proven in production in Sub
   (lineage stamped, identity live through a full release cycle), and
   (b) ERP accounting has completed its Gate D clean-instance cutover
   (ADR-0003), so identity is the last major incumbent subsystem rather
   than one of several in flight. At the revisit, the default is the
   thin-client rule: replace, unless a new ADR argues product-uniqueness
   with evidence.
4. The runtime database-role programme
   (`docs/architecture/erp-runtime-identity-cutover.md`,
   `dotmac_erp_app` -> `app_user`) is a separate concern — a login-role
   migration, not an identity-model migration — and proceeds on its own
   gates regardless of this ADR.

## Consequences

- `docs/architecture/kernel-0001-dispositions.md` stays the executable
  gate; its "Blocked" rows now mean "deliberately not adopted under this
  ADR", and the canary's permanent failure at `tenants` remains correct
  behaviour, not an open defect.
- New Starter modules may not require kernel identity tables in ERP
  until the revisit; a module that does is inadmissible for ERP
  composition or must declare an ERP-hostable effect instead (the
  `idempotency_ledger.v1` precedent).
- ERP identity code is maintained as incumbent product code under normal
  standards until the revisit — maintained, not extended with new
  identity concepts that a future kernel replacement would have to
  re-migrate. New identity-shaped features need an explicit
  build-in-ERP vs wait-for-kernel call, recorded where they land.
- Nothing in this ADR blocks the runtime-role cutover, accounting Gate D,
  tax C3, or the files/numbering close-out.
