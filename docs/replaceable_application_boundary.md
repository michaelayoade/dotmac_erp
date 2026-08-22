# Replaceable Application Boundary

Dotmac ERP and Dotmac Sub are independent products with separate domain models,
identifiers, databases, release cycles, and failure domains.

Dotmac ERP currently provides backoffice collaboration for procurement,
inventory, expenses, workforce, payables, and accounting. It is not an
enterprise-wide integration platform and Dotmac Sub must not require it to make
subscriber, service, provisioning, billing, or operational-workflow decisions.
Dotmac ERP may be replaced by Zoho or another provider.

## Rules

1. Integrations use explicit, versioned API or event contracts.
2. Neither application reads the other's database, shares ORM models, creates
   cross-system foreign keys, or participates in a shared transaction.
3. Imported references include their source-system scope and serve only as
   correlation evidence.
4. Each application owns its own tax-identity system. ERP customer, supplier,
   employee, and organization tax IDs are governed and validated in ERP; Sub
   tax IDs do not overwrite them.
5. ERP-side Sub packages are provider adapters. They may map and reconcile
   contracted facts, but they do not become owners of Sub domain state.
6. Integration failure is visible and repairable without rolling back a valid
   decision in the source application.
7. Secrets and credentials remain local to each provider configuration.

These rules also govern identity integration. The shared-auth-database SSO path
was retired, and the OIDC implementation that briefly replaced it was itself
deleted on 2026-08-15 without ever being enabled. ERP therefore has no external
identity integration: it is the only writer of ERP sessions, cookies, user
status, roles, and permissions, and there is no provider whose authorization
claims could be imported. If an identity provider is added later it proves
identity only, mapping an opaque issuer/subject to a local person — see
`docs/oidc_identity_contract.md`.

## Existing tax-data remediation

Older subscriber imports may already have copied a Sub tax ID into an ERP
customer record without provenance. This change stops all future overwrites but
does not automatically clear existing ERP values: the system cannot distinguish
a copied value from one later verified by Finance. Before an ERP-owned FIRS
validation rollout, Finance must review matching Sub/ERP values, verify the ERP
record, and correct it through the ERP tax-identity owner.

## Replacement test

The boundary is acceptable only if Dotmac ERP can be replaced without changing
Sub's core domain services or database schema beyond provider-neutral
correlation data. Equally, ERP must remain usable for its own backoffice and
accounting functions when Sub is unavailable.

## Active vertical replacement programme

ERP is being replaced one domain at a time by released Starter-owned modules
composed by the thin **Dotmac ERP** product assembly (corrected 2026-08-19 —
the destination is not an internally framed `dotmac_backoffice` application;
see `dotmac-erp-recomposition-into-domain-modules`). Composition alone does not
move authority. Each domain must expose a versioned source projection, backfill
into the composed product, shadow and reconcile, switch one sole writer, and
then remove the matching ERP writer before it counts as retired.

People is the first vertical slice. Its read-only source contract, exact writer
retirement ledger, ownership boundary and cutover gates are recorded in
`docs/architecture/people-replacement-boundary.md`. ERP remains the sole People
writer at the state documented here.
