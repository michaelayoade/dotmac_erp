# Where the RLS bypass belongs

**Status:** Proposed
**Date:** 2026-08-11
**Decision owner:** Michael
**Gates:** every E8 table-family migration. Decide before moving tables, not
after — see "Why the order matters".

## Context

Every RLS policy in this repository carries the same predicate:

```sql
USING (should_bypass_rls() OR organization_id = get_current_organization_id())
```

`should_bypass_rls()` reads the `app.bypass_rls` GUC. Two facts about it are
better than its name suggests, and both were verified rather than assumed:

- It is set with **`SET LOCAL`**, so it is transaction-scoped and reverts at
  COMMIT or ROLLBACK. It cannot leak into a later transaction on a pooled
  connection.
- It is reached only through two context managers in `app/rls.py`
  (`bypass_rls`, `bypass_rls_sync`) which set it back to `'false'` explicitly.

There are 22 call sites across 6 files, and outside `app/rls.py` and
`app/db/session_context.py` — the seam itself — the real callers are few:

| Caller | Why it bypasses |
|---|---|
| `careers_service.py` | Public careers portal resolves an `Organization` from a URL slug. No org is in scope yet, because finding the org *is* the request. |
| `people/hr/onboarding.py` | Token-based onboarding. Its own comment: *"the token is the only identifier we have, and the row carries the org."* |
| `api/deps.py` | Documents the mechanism for the dependency layer. |
| `sot_relationships.py` | Names the GUCs in a registry entry. |

PR #260 additionally established, against production, that `dotmac_erp_app` is
**not** a superuser and does **not** hold `BYPASSRLS`, and that 87 tables carry
`FORCE ROW LEVEL SECURITY`.

So this is not a hole someone left open. It is a deliberate, narrow,
auto-reverting escape doing real work.

## The problem

The escape is expressed in the **policy predicate** rather than as a **role
privilege**, and it conflates two needs that deserve different answers.

**1. Bootstrap lookup.** The scope is inside the row being read. A slug or a
token arrives, and the organization it belongs to cannot be known until that one
row is fetched. This is legitimate, unavoidable, and narrow: one row, read-only,
and the very next thing the caller does is scope the session properly.

**2. Administrative cross-organization work.** Reports, repair scripts,
migrations, reconciliation across orgs. Broad by nature.

Today a single switch serves both. That has three consequences:

- **It is not a privilege boundary.** `SET LOCAL app.bypass_rls = 'true'` is
  unprivileged SQL. Any code path that reaches the database inside a
  transaction can turn it on, including one an attacker arrives at through
  injection. Transaction scoping limits the blast radius in time, not in
  authority.
- **Case 1's needs are met with case 2's power.** Resolving one organization by
  slug is granted the ability to read every organization's rows for the rest of
  the transaction.
- **Every policy pays for it.** The predicate is duplicated across every policy
  on every protected table, so the shape is now replicated 85+ times and would
  be replicated across all 309 as coverage grows.

## Decision

### 1. Administrative cross-org work becomes a role privilege

A dedicated role holds PostgreSQL's `BYPASSRLS` attribute. Reports, repair and
migrations connect as that role. It is grantable, revocable, visible in
`pg_roles`, and — critically — **unreachable from application SQL**, because
acquiring it requires a different connection rather than a `SET`.

### 2. Bootstrap lookup gets a narrow, purpose-built contract

Not a general read escape. A `SECURITY DEFINER` function per lookup that takes
the opaque identifier and returns **only the organization id** — not the row,
not a cursor, not a table:

```sql
resolve_organization_by_slug(slug text) RETURNS uuid
resolve_organization_by_onboarding_token(token text) RETURNS uuid
```

The caller then primes the session with `prime_tenant_context(...)` and every
subsequent read is ordinarily scoped. What the caller gains is exactly one
organization id, which is the least it can be given and still do its job.

### 3. Policies collapse

```sql
USING (organization_id = get_current_organization_id())
```

No `should_bypass_rls()`. The function and the `app.bypass_rls` GUC are retired
once no policy references them.

### 4. The application role must never hold `BYPASSRLS`

Stated explicitly because it is the obvious shortcut and it is strictly worse
than today. Granting the web role `BYPASSRLS` would convert a transaction-scoped
escape into a **permanent** one on every connection in the pool. `dotmac_erp_app`
does not hold it today (#260); it must not acquire it as a side effect of this
work.

## Why the order matters

Every table migrated under the current predicate inherits `should_bypass_rls()
OR ...`. Coverage today is 85 of 309 organization-scoped tables
(`docs/rls-coverage-baseline.json`). Fixing the predicate afterwards means
rewriting policies across all 309 a second time.

Deciding the shape first is the difference between one pass and two.

## Interaction with the script-scope ratchet

PR #260 records 29 batch scripts that open a session without setting any scope.
Under the current predicate they read whatever is unprotected. Under decision 3
they will read **nothing** on any protected table, silently, and exit 0.

That is not an argument against this decision — it is an argument for sequencing:
**burn down the 29 unscoped callers before, or alongside, the 224 unprotected
tables.** A protected table with an unscoped caller fails silently and succeeds
loudly, which is worse than either problem alone.

Decision 2 also gives those scripts the right tool: a script that genuinely needs
cross-org reach connects as the `BYPASSRLS` role and says so, rather than being
silently unscoped.

## Alternatives rejected

**Leave it as-is.** Defensible on the narrow security reading — `SET LOCAL` plus
context managers is not careless. But it leaves case 1 holding case 2's
authority, keeps an unprivileged switch in the predicate of every policy, and
makes the kernel-convergence question (`dotmac_kernel` has no bypass concept at
all) harder the longer coverage grows.

**Grant the app role `BYPASSRLS` and drop the GUC.** Simplest to implement and
strictly worse — see decision 4.

**Keep the GUC but restrict who may set it.** PostgreSQL can restrict `SET` of a
custom GUC only via `ALTER SYSTEM`-level controls that do not apply per-role in
the way this would need. The privilege model already exists as role attributes;
inventing a second one in the predicate is the thing to stop doing.

## Open questions this does not answer

- Whether `get_current_organization_id()`'s catch-all
  `EXCEPTION WHEN OTHERS THEN RETURN NULL` should remain. It converts a
  configuration error into an empty result set, which is the same silent-zero
  failure #260 documents. Related, but separable.
- Whether the eventual kernel convergence renames the GUC to
  `app.current_tenant`. That belongs to E8's identity mapping, not here.
