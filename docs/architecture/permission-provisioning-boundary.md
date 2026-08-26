# Permission provisioning boundary

## As-built authority

ERP owns its runtime roles, permission catalogue, role memberships, and
role-permission links in `public.roles`, `public.permissions`,
`public.person_roles`, and `public.role_permissions`. Routes and services
reference permission codes; the database catalogue must materialize those
codes before a grant can authorize a principal.

`app/authz/expense.py` is the authored owner of the 35
Expense permission definitions.
`app/authz/payment_execution.py` separately owns the three
payout permissions Expense consumes: read provider data, prepare a payout, and
execute a transfer. `app/authz/profile.py` is the
product/assembly owner of the Expense baseline roles and both grant bundles.
These are pure declarations: they import no persistence layer and perform no
I/O.

The deployment path runs `alembic upgrade heads`. It does not run
`scripts/seed_rbac.py`. The seed imports those declarations and remains a
development/bootstrap writer, not a production reconciliation engine or a
second catalogue owner.

For the Expense module, the first governed baseline is frozen into
`20260826_provision_expense_permissions.py`:

- the Expense catalogue owns 35 stable `expense:*` permission definitions;
- Payment Execution owns three stable, separately grantable payout
  permissions consumed by reimbursement;
- ERP owns the mapping from those permissions to its nine baseline product
  roles;
- the migration creates missing baseline roles and permissions and adds
  missing links;
- existing descriptions, custom roles, custom permissions, memberships,
  direct grants, and role-permission links are preserved;
- an existing inactive baseline role or permission is a conflict. The
  migration fails instead of silently reversing an operator's decision.

The migration is deliberately self-contained. Importing the mutable seed from
historical migration code would make a future edit change the meaning of an
already-issued database transition.

## Reusable contract boundary

The shared kernel may describe and preview permission provisioning, but it
does not own ERP persistence or ERP role policy:

- installable modules declare permission definitions;
- the product assembly declares versioned role-grant profiles and an explicit
  missing-role creation policy using opaque role and permission keys;
- a storage-neutral plan reports additive inserts, preserved extra state, and
  conflicts;
- an ERP migration adapter applies the approved plan inside the migration
  transaction;
- application startup validates and reports; it never writes RBAC state.

The first contract supports additive convergence only. `role_permissions` has
no policy-owner provenance, so a baseline reconciler cannot tell whether an
effective link is wanted by the assembly, an operator, or both. Revocation,
rename, retirement, and subtractive reconciliation therefore require an
explicit reviewed migration.

Future generic managed revocation requires a multi-owner claim ledger keyed by
role, permission, owner kind, profile, and profile version. The effective link
may disappear only when no claim remains. A single `managed_by` column on the
effective link is insufficient because multiple owners can want the same
grant.

## Enforcement

`tests/migrations/test_expense_permission_provisioning.py` proves that the seed
consumes the authored declarations, the migration remains an exact frozen copy
of them, every grant references declared codes and roles, application is
idempotent, operator-owned extras and descriptions survive, and inactive
desired state is refused. The normal migration rehearsal remains the proof
that the SQL applies to the real PostgreSQL catalogue.
`tests/architecture/test_authz_declarations_are_pure.py` prevents the authored
catalogues from acquiring a web, service, database, ORM, or persistence import.
