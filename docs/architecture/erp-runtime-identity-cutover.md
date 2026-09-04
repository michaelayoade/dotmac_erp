# ERP runtime identity cutover: `dotmac_erp_app` → `app_user`

**Status:** Change 1, step 2 — the manifest and its guard are built. Nothing
has been applied. No SQL in this change has been executed anywhere.

ERP production connects as the legacy login `dotmac_erp_app`. Every module
`GRANT` and every RLS policy in the composed lineages is addressed to
`app_user` by name, so `app.runtime_admission` — the deploy step that would
assert the runtime identity — is scoped to active modules and passes
vacuously today. It cannot stop being vacuous until the application actually
connects as `app_user`, and it cannot connect as `app_user` until `app_user`
can do everything `dotmac_erp_app` can.

That is this programme.

## The ruling: identity first, least privilege second

Identity migration and least-privilege reduction are separate changes, in that
order.

Doing both at once makes every missing permission ambiguous. After a combined
change, a request that returns `permission denied` could be a mirroring defect
or an intended reduction, and the only way to tell them apart is to put the old
login back — which is to say, to abandon the cutover mid-flight, during an
incident, on production. Separating them means every failure after Change 1 has
exactly one possible cause: something was not mirrored.

So Change 1:

- mirrors the source role's privileges onto the target;
- preserves everything the target already holds — **nothing is revoked**;
- grants the source role **nothing**, in particular none of the module access
  it lacks: it is the role being retired;
- uses no `GRANT ALL`, no role membership in either direction, no ownership
  transfer, no `BYPASSRLS`, and changes no module activation flag.

**The 1,716 relation privileges are the compatibility baseline, not the
permanent least-privilege target.** Reduction is Change 3, driven by its own
two-directional ratchet, once the cutover has been stable.

## The census

`docs/inventories/erp-privilege-census-2026-09-04.json`, captured read-only
from `erp.dotmac.io` at `2026-09-04T09:09:14Z`, database
`dotmac_erp@7650449984751865891`, PostgreSQL 16.4. It is frozen: re-taking it
is a separate, deliberate act.

It is a **delta**, not a dump: each recorded privilege is one the source role
holds and the target does not. So the manifest is *what must be granted*, and
behavioural equivalence is `what app_user already holds` ∪ `the manifest`.
Nothing in this change may be read as a complete picture of either role's ACL.

| Section | Rows | Objects |
| --- | --- | --- |
| schema USAGE | 42 | 37 observed + 5 derived |
| relations (legacy estate) | 1,712 | 428 (427 tables, 1 materialized view) |
| sequences | 3 | 1 |
| functions (by full signature) | 5 | 5, all `SECURITY DEFINER` |
| module-era grants | 4 | 1 |
| **total grant rows** | **1,766** | |
| exclusions | 12 | 5 preserved scopes (132 privileges) + 7 prohibitions |

### All 1,716 are direct grants

Zero arrive via `PUBLIC`, zero via ownership. The baseline is therefore a clean
set of deliberate grants — which is what makes a later finding of a
PUBLIC-derived privilege a **defect, not an equivalent**. `PUBLIC` reaches
every login in the cluster, including the next one someone creates; ownership
carries `DROP` and defeats RLS. "The role can do it" and "the role was granted
it" are different facts, and only the second one is the cutover's goal.

### The five `SECURITY DEFINER` functions are not bulk rows

| Schema | Signature | Owner |
| --- | --- | --- |
| `hr` | `enforce_employment_type_projection()` | `app_admin` |
| `public` | `claim_outbox_batch(p_worker text, p_batch integer, p_stale_seconds integer)` | `app_admin` |
| `public` | `claim_platform_outbox_batch(p_worker text, p_batch integer, p_stale_seconds integer)` | `app_admin` |
| `public` | `settle_outbox_event(p_id uuid, p_worker text, p_status text, p_available_at timestamptz, p_attempts integer, p_last_error text)` | `app_admin` |
| `public` | `settle_platform_outbox_event(p_id uuid, p_worker text, p_status text, p_available_at timestamptz, p_attempts integer, p_last_error text)` | `app_admin` |

A `SECURITY DEFINER` function executes as its **owner**. Every one of these is
owned by `app_admin`, which is `BYPASSRLS`. Granting `EXECUTE` therefore hands
`app_user` whatever that owner can do, through whatever the body does — it is
an escalation surface, not a row in a sweep.

They get their own manifest section, are marked `review_required`, and are
rendered into a **separate SQL file**. Each needs individual review — body,
owner, and `search_path` — before Change 1 is applied.

One thing to check during that review: a function whose `proacl` is NULL has
`EXECUTE` granted to `PUBLIC` by PostgreSQL default. If that is the state
today, `app_user` may already be able to call these, via a path the verifier
correctly refuses to accept as equivalent to a grant.

### The module-era grant, and why it is not mechanical

Exactly one `mod_*` privilege is already held by the legacy role:
`mod_files.platform_stored_files`, all four of SELECT/INSERT/UPDATE/DELETE.

That is a **control-plane** relation. ADR-0023 requires platform tables to be
`REVOKE`d from the tenant application role, and `app_user` *is* the tenant
application role — `app.runtime_admission` says so in as many words when it
names a module's `platform_tables` and then deliberately does not demand them.
So mirroring this privilege is a dual-plane isolation decision, not a mirror.

It is carried in its own manifest section, marked `review_required`, and
rendered into the review-required SQL. It is also the one frozen entry in
`MODULE_ERA_ALLOWLIST`, which the guard permits the legacy role to hold and
which may **shrink, never grow**.

### The reverse gap: 132 privileges, preserved

| Scope | Privileges the target holds and the source lacks |
| --- | --- |
| `mod_tax` | 46 |
| `mod_accounting` | 42 |
| `mod_people` | 20 |
| `mod_imports` | 12 |
| `mod_numbering` | 12 |

These are **preserved, never revoked**. This change adds the legacy estate to
the target; it does not subtract the target's own module access.

`mod_people` is the sharp one: 6 tables × 4 privileges = 24, of which the
runtime holds only 4 — so the dual grant was **table-specific to
`employment_types`**, not schema-wide.

The census recorded this gap as a **per-schema count only**. Object-level
detail was never captured, so the exclusion is a count-level ratchet in both
directions and cannot be verified object by object from this artefact. Saying
otherwise would be claiming evidence nobody has.

## OID-independent identity, and why it survives a restore

Change 2 applies this manifest to a **restored** database. A restore recreates
every object with a new OID, so a manifest keyed by OID would describe a
database that no longer exists.

Every row is therefore keyed by catalog identity:

- `schema:<nspname>`
- `relation:<nspname>.<relname>` — with `relkind` carried as **data**, not key,
  so a table replaced by a view of the same name is reported as a KIND CHANGE
  rather than silently matching
- `sequence:<nspname>.<relname>`
- `function:<nspname>.<proname>(<argument types>)` — the argument **type** list,
  never the bare name

Names, `relkind` and argument type names all survive `pg_dump`/`pg_restore`
unchanged; OIDs do not.

### Why the function key is the type list

The census records the signature an operator sees, with parameter names
(`claim_outbox_batch(p_worker text, ...)`). The identity PostgreSQL uses to
tell overloads apart is the argument type list alone
(`claim_outbox_batch(text, integer, integer)`).
`app.privilege_manifest.function_name_and_identity_arguments` is the
deterministic reduction between the two, and it **raises rather than guesses**:
`timestamp with time zone` begins with a word that looks exactly like a
parameter name, so a naive "the first token is the name" rule mangles it. An
unrecognised type fails the generator instead of quietly keying a grant to the
wrong function body.

## The generated SQL, and its idempotency

Two files, both generated, both committed, both regenerable and byte-compared
— by `make privilege-manifest-check` for a human, and, as the enforcing gate,
by `tests/architecture/test_privilege_manifest.py`, which regenerates them in
memory and compares bytes:

- `scripts/erp_identity_cutover_grants.sql` — 1,752 statements. The routine
  half: mechanical, reviewable in bulk.
- `scripts/erp_identity_cutover_review_required.sql` — 14 statements. The five
  `SECURITY DEFINER` EXECUTEs, the control-plane module-era relation, and the
  five derived schema USAGE grants. **Do not apply until each row has been
  individually signed off.**

Splitting them is the control. A 1,700-line file with six escalation decisions
buried in it gets skimmed.

**Idempotent on re-run.** `GRANT` in PostgreSQL is an assertion about an ACL,
not an append: granting a privilege the role already holds leaves the ACL
byte-identical and returns success. There is nothing to make conditional, no
`IF NOT EXISTS` to add, and no ordering that changes the outcome. Every
statement names exactly **one** privilege on exactly **one** object, so a
partial re-run converges to the same state as a full one. Both files wrap in a
single transaction.

Neither file contains `REVOKE`, `ALTER`, `CREATE`, `DROP`, `SET ROLE`,
`WITH ADMIN`, `WITH GRANT OPTION`, `OWNER TO`, or `GRANT ALL`, and the legacy
role never appears as a grantee. `tests/architecture/test_privilege_manifest.py`
reads both files back and asserts every one of those.

Note on the materialized view: PostgreSQL has no `GRANT ... ON MATERIALIZED
VIEW`, so `rpt.sales_analysis_mv` is granted `ON TABLE` — the correct spelling
— while the manifest keeps `relkind = 'm'` so a kind change is still
detectable. Its INSERT/UPDATE/DELETE entries are in the ACL and are mirrored
faithfully, even though DML on a matview is not executable.

## The verifier: why every check takes an OID

`scripts/verify_identity_cutover_privileges.py` is read-only and never issues
DDL. Its structure is dictated by two PostgreSQL facts.

**1. Name-based privilege checks need schema USAGE to resolve.**
`has_table_privilege('app_user', 'hr.employees', 'SELECT')` first *resolves*
the name, and name resolution requires USAGE on `hr`. Missing schema USAGE is
precisely what this programme exists to find, so the name form returns `false`
for two completely different reasons and cannot tell them apart. **A live probe
hit exactly this on 2026-09-04.**

Resolution and authorization are therefore split. Resolution is a catalog
SELECT against `pg_namespace` / `pg_class` / `pg_proc`, which needs no USAGE on
anything. Authorization is `has_schema_privilege(role, oid, …)`,
`has_table_privilege(role, oid, …)`, `has_sequence_privilege(role, oid, …)` and
`has_function_privilege(role, oid, …)` — every one taking the OID the first
step produced. Names appear in every report line, because an OID means nothing
to a reviewer; no check is ever made against one.

**2. A comma-joined privilege string is an ANY test.**
`has_table_privilege(role, oid, 'SELECT,INSERT,UPDATE')` returns true if the
role holds *any* of the three. It is never an ALL assertion, and reading it as
one certifies a role that can read but not write. Every privilege is its own
call: SELECT, INSERT, UPDATE, DELETE separately; sequence SELECT, UPDATE and
USAGE separately; EXECUTE on its own.

**Effective privilege is not grant origin.** `has_*_privilege` answers the
union of every path — direct grant, ownership, `PUBLIC`, role membership,
`ALTER DEFAULT PRIVILEGES`. Those are not equivalent outcomes, so the verifier
decomposes each object's ACL with `aclexplode` (substituting `acldefault` for a
NULL ACL, which is *not* "no privileges") and classifies the origin as
`direct` / `ownership` / `public` / `inherited` / `default`. The target must
reach its state by deliberate **direct** grants; anything else is refused.

## The guard's seven refusals

`app.privilege_manifest.cutover_violations` is a pure function over one
manifest and one snapshot. It refuses:

1. **an object disappearing** without `BASELINE_TOTALS` being lowered;
2. **a new privilege appearing** on the target, on any object the manifest
   covers and outside every preserved scope;
3. **an expected privilege being absent** — or reached by the wrong origin;
4. **an object changing kind**;
5. **a function overload confused with another** — the manifest names one
   signature and the catalog resolved a different one, or the name matched
   more than one candidate;
6. **ownership, `BYPASSRLS`/`SUPERUSER`, or role membership** being granted, in
   either direction;
7. **a module privilege added to the legacy role** — outside the one frozen
   `MODULE_ERA_ALLOWLIST` entry.

Plus the two-directional baseline ratchet, which is refusal 1 seen offline: a
manifest whose section counts moved — up **or down** — without
`BASELINE_TOTALS` being edited in the same commit. Lowering the baseline is
legitimate; that is exactly what Change 3 does. Lowering it *silently* is not.

The exclusions ratchet the same way: a preserved scope whose count falls means
something revoked module access, and this change revokes nothing.

### Why refusal 2 is not vacuous

A verifier that only asks about privileges it already expects can never find an
extra one. So the verifier enumerates, from `pg_class.relacl`, every privilege
the target holds on a manifest-covered relation, and feeds the ones the
manifest does not list to the guard. Similarly, refusal 7 enumerates module
relations from the catalog rather than from the manifest — a refusal scoped to
what the manifest already knows about could never see a *new* module grant.

## Sensitivity proofs (ADR-0018)

A check that fires on nothing and a check that fires on everything both
"pass". `tests/architecture/test_privilege_manifest.py` plants each defect into
an otherwise-clean 1,766-row snapshot built from the real committed manifest,
and asserts the violation **names** it:

| # | Planted defect | Refusal named |
| --- | --- | --- |
| 1 | an object marked non-existent | `OBJECT VANISHED` |
| 2 | `TRUNCATE` appears on a manifest relation | `PRIVILEGE ADDED` |
| 3 | a sequence `SELECT` no longer held | `PRIVILEGE ABSENT` |
| 3b | a privilege reached via `PUBLIC` | `PRIVILEGE ORIGIN` |
| 4 | `relkind` `r` → `v` under a stable name | `KIND CHANGE` |
| 5 | `claim_outbox_batch(text, integer, integer)` resolving to `claim_outbox_batch(text)` | `OVERLOAD AMBIGUITY` + `OVERLOAD CONFUSION` |
| 6 | `app_user` made a member of `dotmac_erp_app` | `ROLE MEMBERSHIP` |
| 6b | `BYPASSRLS` set, and an owner moved to `postgres` | `ROLE ATTRIBUTE` + `OWNERSHIP CHANGE` |
| 7 | `dotmac_erp_app` given `SELECT` on `mod_people.employees` | `LEGACY MODULE PRIVILEGE` |
| 8 | the manifest quietly loses (and separately gains) rows | `BASELINE FELL` / `BASELINE GREW` |
| 8b | a preserved `mod_tax` privilege disappears | `EXCLUSION FELL` |

**Negative control.** The same clean snapshot the eleven proofs mutate produces
**zero** violations. Without that assertion, every proof above could be passing
because the detector flags everything. Each proof additionally requires that
one planted defect produces exactly the expected violations and no cascade —
specificity is half of naming.

## What is not closed

- **The five derived schema USAGE rows.** `hr`, `mod_files`, `public`, `rpt`
  and `sync` carry 448 relation privileges between them and appear in no
  schema-USAGE row. Under the delta reading, `app_user` already has USAGE there
  and applying the row is a no-op; under a whole-ACL reading, the source role's
  own USAGE was never observed and those 448 privileges are inert without it.
  The census does not say which. One read-only follow-up query settles it, and
  until then the rows are marked `review_required` rather than swept in.
- **The reverse gap is a count, not an inventory.** 132 privileges across five
  schemas, with no object-level detail. The guard ratchets the count; it cannot
  tell you *which* privilege changed.
- **The `SECURITY DEFINER` reviews themselves.** Five bodies to read. This
  change isolates them and states why; it does not sign them off.
- **`mod_files.platform_stored_files`.** Mirroring a control-plane relation to
  the tenant application role is in tension with ADR-0023. This change surfaces
  the decision; it does not make it.

## Files

| Path | What it is |
| --- | --- |
| `docs/inventories/erp-privilege-census-2026-09-04.json` | the frozen production census (input, provenance) |
| `docs/inventories/erp-identity-cutover-manifest-2026-09-04.json` | the generated manifest |
| `app/privilege_manifest.py` | the pure contract: generator, renderer, refusals |
| `scripts/generate_privilege_manifest.py` | offline generator / `--check` |
| `scripts/erp_identity_cutover_grants.sql` | generated, routine half |
| `scripts/erp_identity_cutover_review_required.sql` | generated, review-required half |
| `scripts/verify_identity_cutover_privileges.py` | read-only OID-based verifier |
| `tests/architecture/test_privilege_manifest.py` | the guard and its sensitivity proofs |
