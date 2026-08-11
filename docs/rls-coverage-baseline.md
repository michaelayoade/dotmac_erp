# RLS coverage baseline

`rls-coverage-baseline.json` records the organization-scoped tables that were
**not** fully protected by row-level security when the ratchet was turned on,
measured against the live catalog on 2026-08-10.

```
tables                    414
carrying organization_id  309
  fully protected          85   (27.5% of scoped)
  unprotected             158   (RLS not enabled)
  unforced                 66   (RLS enabled, not FORCED)
inherited                   6   (no scope column; policy joins to a scoped parent)
global                     99
```

224 entries: the 158 unprotected plus the 66 unforced.

## What the file is for

It is a **ratchet, not a target**. `scripts/architecture/rls_coverage_audit.py
--enforce --baseline docs/rls-coverage-baseline.json` fails only on a gap that
is *not* in this file. So:

- a **new** organization-scoped table without RLS fails immediately;
- the 224 already here do not fail, because failing 224 times on day one would
  get the gate switched off rather than the tables fixed;
- a table that gets fixed must be **removed** from this file, and the audit
  prints which entries are now protected so it cannot silently return.

The list may only shrink. Adding an entry means admitting a regression, and
should be a conscious act with a reason in the pull request.

## Why the count is not the whole story

ERP isolates in two independent layers (`app/db/session_context.py`): a
SQLAlchemy ORM listener and PostgreSQL RLS. Every table in this file is still
filtered by the ORM layer, so this is not 224 open doors. What it measures is
where the *second* layer — the one that holds when something reaches the
database by another path, such as a raw query, a task that primed only one
layer, or an export — is absent.

`unforced` deserves separate attention. Those 66 tables have RLS enabled and
policies attached, so they look protected from every angle except the one that
matters: without `FORCE`, the table owner bypasses the policy entirely, and the
owner is the role running migrations and repair scripts.

## Regenerating

The baseline can only be produced from a live migrated database:

```
poetry run python scripts/architecture/rls_coverage_audit.py --json \
    > docs/rls-coverage-baseline.json
```

Do this only to *remove* fixed entries. Regenerating wholesale after adding
unprotected tables would launder a regression into the baseline, which is
exactly what the ratchet exists to prevent.
