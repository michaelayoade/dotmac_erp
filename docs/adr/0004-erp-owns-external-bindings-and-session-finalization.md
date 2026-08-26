# ADR-0004 — ERP owns external bindings and session finalization

- **Status:** Accepted
- **Date:** 2026-08-17
- **Decider:** Michael
- **Supersedes:** ADR-0003 decision 5

## Context

ADR-0003 correctly refused managed activation while ERP had no canonical
external-binding/session finalizer. Production inspection established a
zero-row cutover: the retained `federated_identities` table held no data and the
deleted local OIDC implementation had never been enabled. Hard rule 28 still
requires ERP to own its runtime, database, authorization and sessions. Adopting
kernel `Party` or `AuthSession` would create a second identity/session authority.

## Decision

1. `ERPExternalIdentityAuthority` is ERP's only writer of the exact
   `(organization_id, provider_binding, issuer, subject) -> Person` binding. The
   existing table is migrated from global uniqueness to organization-scoped
   uniqueness and FORCE RLS. Login never creates a Person or binding and never
   matches email.
2. The same authority locks the binding during finalization. `AuthFlow` issues
   an ERP `Session` carrying that binding id in the same caller-owned database
   transaction. Disable locks the same row, disables it and selectively revokes
   sessions carrying its provenance before that transaction commits.
3. `dotmac-auth-oidc` `0.1.0a1` is the sole protocol adapter. ERP supplies its
   own PostgreSQL `StateStore`, whose `DELETE ... RETURNING` claim is atomic.
   ERP consumes only issuer and subject; it never consumes email, claims,
   groups, scopes or provider roles.
4. ERP remains the owner of Person, RBAC, sessions, cookies, logout and tenant
   isolation. Kernel Party/AuthSession and kernel session factories stay
   prohibited. This preserves hard rule 28.
5. OIDC client material is resolved and installed once at startup. Incomplete
   enablement fails startup; an explicit refresh constructs a replacement
   before swapping it and retains the working client on failure. Request
   handlers use the held client and never read environment variables or secret
   stores.
6. ERP owns the value-free composition
   `erp.identity-user-application-lifecycle.v1`. It maps only the managed
   identity APPLY receipt's public `/issuer_url` and immutable `/subject` into
   ERP's `/external_subject/issuer` and `/external_subject/subject` desired
   input. `identity_ref` remains the upstream account's stable lookup key and
   receipt correlation; ERP does not replace Person with it. `provider_binding`
   remains ERP's approved local registration and is never copied from provider
   evidence.

## Cutover, rollback, and retirement

The old runtime owner is “none”: production had zero bindings and no active
route. The migration backfills organization ids defensively, adds provenance,
and enables the new route only when explicit `ERP_OIDC_*` configuration is
complete. Rollback before first adoption disables the setting and reverses the
migration. After bindings or sessions exist, rollback first disables every
binding and revokes provenance-bearing sessions; there is no kernel or email
fallback. The deleted ERP protocol code and legacy `OIDC_*` settings remain
retired permanently; only the shared adapter is admissible.

The composition and executable capability-input grammar require exact published
kernel `0.1.0a69` and managed-identity-contracts `0.1.0a1` artifacts. Until the
lock resolves those immutable releases, product capability activation remains a
release gate; a path dependency or caller-supplied subject is not a fallback.

## Consequences

Managed activation reports ready only after both the exact binding and matching
held registration exist. Disabling one binding revokes only sessions issued
through it; local password sessions remain separately attributable.

## Alternatives rejected

- Kernel Party/AuthSession composition: duplicates ERP authorities.
- Email or JIT linking: turns mutable display evidence into identity authority.
- A generalized kernel finalizer: larger than the proven product-local seam.
- Restoring the deleted ERP protocol implementation: duplicates the released
  adapter and revives its missing-audience and shared-signing-key defects.
