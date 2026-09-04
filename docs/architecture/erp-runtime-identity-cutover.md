# ERP runtime identity cutover: `dotmac_erp_app` → `app_user`

**Status:** Change 1, step 4 — both open manifest items are RULED and
applied. The five `SECURITY DEFINER` EXECUTE grants are denied with their
permitted executors recorded (Decision 1), the `mod_` plane heuristic is gone
and the plane is resolved from declarations (Decision 2), five control-plane
relations are denied with their absence proved at table and column level, and
no denied item renders SQL at all. The five schema-USAGE cases remain settled.
Nothing has been applied. No SQL in this change has been executed anywhere, and
no function, owner or role has been changed.

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
| schema USAGE | 37 | 37 observed (the 5 derived rows were settled and removed) |
| relations (tenant plane) | 1,696 | 424 (423 tables, 1 materialized view) |
| sequences | 3 | 1 |
| functions (by full signature) | 5 | 5, all `SECURITY DEFINER`, all `denied_by_architecture` |
| control plane | 20 | 5 relations, all `denied_by_architecture` — **not granted** |
| **total manifest rows** | **1,761** | 1,736 granted, 0 review-required, 25 denied |
| exclusions | 28 | 5 preserved scopes (132 privileges) + 5 settled schema cases + 5 denied relations + 5 denied functions + 8 prohibitions |

The census total is unchanged at 1,716 relation privileges. What moved on
2026-09-04 is **which side of the plane boundary sixteen of them are on** —
see "Decision 2" below.

## The two Change-1 blockers, ruled 2026-09-04

Michael ruled both open manifest items **blockers of Change 1, not optional
hardening**. Both are applied.

### Decision 1 — all five `SECURITY DEFINER` EXECUTE grants are DENIED

They were `review_required`; they are now `denied_by_architecture`. They stay
in the manifest as deliberate denials, because a denial that is merely absent
cannot be told apart from a denial nobody thought of. The generator emits **no
`GRANT EXECUTE` anywhere**.

| Function | Permitted executor | Why |
| --- | --- | --- |
| `hr.enforce_employment_type_projection()` | **none — no runtime principal** | trigger installation and operation only; PostgreSQL checks `EXECUTE` at `CREATE TRIGGER`, not at fire time, and a trigger function cannot be invoked as an ordinary function |
| `public.claim_outbox_batch(text, integer, integer)` | `outbox_dispatcher` | the cross-tenant drain IS the function; it requires `BYPASSRLS` by construction |
| `public.settle_outbox_event(…)` | `outbox_dispatcher` | same ledger, same absent tenant predicate |
| `public.claim_platform_outbox_batch(text, integer, integer)` | `platform_outbox_dispatcher` | operates on the control-plane ledger ADR-0023 forbids the tenant role |
| `public.settle_platform_outbox_event(…)` | `platform_outbox_dispatcher` | same |

Michael's reason, and it is the subtle half: *"Even though a trigger function
cannot be invoked as an ordinary function, granting it remains unnecessary and
would reverse a tested migration decision."* Both halves matter. The first says
the grant confers nothing; the second says that is not the reason to withhold
it. A grant that confers nothing and reverses a tested revoke is still a
reversal, and the next person reading the ACL cannot tell which of the two it
was.

#### The verifier tests EFFECTIVE privileges, and `PUBLIC` by name

`CREATE FUNCTION` grants `EXECUTE` to `PUBLIC` **by default**. That single fact
decides the shape of the check.

```
has_function_privilege('app_user', oid, 'EXECUTE')            must be false
has_function_privilege('public',   oid, 'EXECUTE')            must be false
has_function_privilege(<declared executor>, oid, 'EXECUTE')   must be true
```

* **Effective, not an ACL reading.** A function whose `proacl` names no
  `app_user` entry at all can still be executable by `app_user`, through the
  `PUBLIC` default. `has_function_privilege` — "can this role do it" — sees
  that; `aclexplode(proacl)` filtered to the role's own name does not. The
  snapshot records *how* each answer was obtained and the guard refuses one
  that is not effective (`NON-EFFECTIVE EXECUTE ANSWER`).
* **`PUBLIC` asked about by name.** Michael: *"If any function currently
  inherits default `PUBLIC EXECUTE`, treat that as a separate remediation
  required before cutover. `REVOKE … FROM app_user` alone does not neutralize
  an effective grant inherited through `PUBLIC`."* So a surviving default is
  reported as `PUBLIC EXECUTE INHERITED … REMEDIATION REQUIRED BEFORE CUTOVER`
  — a standing grant to remove, not a Change-1 grant to withhold. The census
  recorded `public_execute = False` for all five, so this is quiet on today's
  data; that is precisely why it is planted in a sensitivity proof. A check
  that is only correct on the data it was written against is not a check.
* **The executor asserted POSITIVELY.** A denial can pass for the wrong
  reason: the function became unreachable to everyone and the relay stopped
  draining. `PERMITTED EXECUTOR CANNOT EXECUTE` is the refusal that catches it.
  The `hr` fence names no executor, and says so explicitly rather than by
  leaving the field blank.

### Decision 2 — the `mod_` heuristic is removed; plane comes from declarations

> `ModuleManifest.tables` → tenant plane · `ModuleManifest.platform_tables` →
> control plane · host/assembly platform-table declaration → control plane ·
> **anything unclassified → refuse generation.**
>
> Never infer plane from the `mod_` prefix, the `public` schema, the presence
> or absence of `tenant_id`, RLS state, or current ACLs. *"Those are evidence
> to validate the declaration, not sources of ownership."*

`app/persistence_planes.py` owns this. `public.platform_outbox_events` is the
proof: its qualified name looks like legacy estate, its declared behaviour and
its migration are control plane. Reading the plane off a schema name got it —
and three others — exactly backwards:

| relation | migration | what the migration actually does |
| --- | --- | --- |
| `public.platform_outbox_events` | `20260824_outbox_relay` | no tenant column, no RLS, `REVOKE ALL PRIVILEGES … FROM app_user` plus column-level `REVOKE`s |
| `public.platform_idempotency_records` | `20260820_idempotency_ledger` | the same shape, beside the tenant ledger |
| `public.tenants` | `20260813_tenant_projection` | `REVOKE ALL … FROM PUBLIC` and `FROM app_user`; read through a narrow definer instead |
| `public.tenant_domains` | `20260813_tenant_projection` | the same block |
| `mod_files.platform_stored_files` | composed `dotmac-files` lineage | the module declares it in `platform_tables` |

Granting any of the four `public` ones would have **reversed a tested migration
decision** under cover of a compatibility sweep. Four privileges each: sixteen
rows moved out of the bulk file into the denied set, plus the four that were
already there.

**Where the declarations are read from.** Module planes come from
`app.runtime_admission.COMPOSED_MODULES`, which is the assembly's
manifest-derived declaration of every composed module's `tenant_tables` and
`platform_tables`, proven against `tests/integration/tenant_table_inventory.tsv`
by `tests/architecture/test_runtime_admission_is_read_only.py`. They are READ,
never restated: `test_no_module_table_name_is_a_literal_in_the_plane_module`
fails if any module table name appears as a literal in
`app/persistence_planes.py`, because a copied list is a second writer and a
second writer drifts.

The kernel's `ModuleManifest` objects themselves are **not** statically
readable in this assembly — `dotmac_kernel` is not an installed import in the
static toolchain and the composed module distributions are not vendored — so
`COMPOSED_MODULES` is the manifest-derived declaration that is readable, and
the test above is what makes reading it equivalent to reading the manifests.

For the host assembly there is no `ModuleManifest` at all: ERP's own estate is
built by ERP's alembic lineage. `HOST_CONTROL_PLANE_RELATIONS` is therefore the
**first** writer of that fact, not a copy of one — and every entry cites the
migration that is its evidence, which
`test_every_host_control_plane_declaration_cites_a_real_revoke` reads
statically and requires to contain the revoke it is cited for.

**Unknown fails closed.** A relation no declaration covers raises
`UnclassifiedRelation` and generation refuses. It never defaults to the tenant
plane — defaulting is exactly how the four `public` relations were swept up.

**A schema move is a finding, not a reclassification.** Relation-level
declarations are keyed by relation NAME with the schema recorded as data, so
relocating a relation reports `schema_moved` and leaves the plane alone. A
resolver that lost the classification at that moment would be the name-based
heuristic wearing a different hat.

**`mod_` survives for one question only** — "does the retiring role reach
module storage?", a namespace question, which is what
`MODULE_ERA_ALLOWLIST` and the legacy-role refusal ask. It is never consulted
for a plane.

## The target, stated once

> legacy compatibility privileges − architecturally forbidden access −
> unapproved `SECURITY DEFINER` execution + module-era privileges `app_user`
> already owns

Everything here is that arithmetic made executable. The artefacts it produces
stay **split, permanently**: bulk-safe grants in
`scripts/erp_identity_cutover_grants.sql`, every denial in
`scripts/erp_identity_cutover_denied.sql` — a file with no `BEGIN`, no
`COMMIT` and no statement at all. Collapsing them would put exceptional
authorization back inside mechanical compatibility, which is the one thing
this shape exists to prevent.

## Three dispositions, never a boolean

| Disposition | Meaning | Rendered as | Rows today |
| --- | --- | --- | --- |
| `grant` | mechanical; no judgement call | a `GRANT` in the grant file | 1,736 |
| `review_required` | a human must read it before it may be applied | *(nothing today)* | 0 |
| `denied_by_architecture` | never applied | a **comment** in the denial ledger | 25 |

A boolean can say "not routine". It cannot tell "grant this once someone has
read the body" apart from "never grant this at all", and those are opposite
instructions to whoever runs the SQL. `review_required` is empty today because
both open items were ruled on; the disposition stays in the vocabulary because
the next open question will need it, and an empty set is a fact rather than a
deletion.

**No denied item renders SQL.** A denied row stays in the manifest and in the
ledger, because a denial that is merely *absent* cannot be told apart from a
denial nobody thought of — but the ledger contains no executable statement, so
there is nothing to uncomment. That is a property of the bytes rather than of
how the file is read, and both renderers refuse the wrong rows
(`render_grant_sql` raises on a denied row, `render_denial_ledger` raises on a
grantable one) so the split is enforced rather than merely observed.

### All 1,716 are direct grants

Zero arrive via `PUBLIC`, zero via ownership. The baseline is therefore a clean
set of deliberate grants — which is what makes a later finding of a
PUBLIC-derived privilege a **defect, not an equivalent**. `PUBLIC` reaches
every login in the cluster, including the next one someone creates; ownership
carries `DROP` and defeats RLS. "The role can do it" and "the role was granted
it" are different facts, and only the second one is the cutover's goal.

### The five `SECURITY DEFINER` functions: bodies read, all five denied

*(The bodies below are what Decision 1 above was decided from. They are kept
in full because the ruling is only as good as the reading behind it.)*

A `SECURITY DEFINER` function executes as its **owner**. All five are owned by
`app_admin`, which is `BYPASSRLS`, so `EXECUTE` hands `app_user` whatever that
owner can do *through whatever the body does*. That is an escalation surface,
not a row in a sweep.

**The ruling.** Do not grant `EXECUTE` to `app_user` while a function executes
as `app_admin`. Each one may still be safe — but that has to be established
**from the body**, never inferred from the name.

**Established from the catalog for all five, and true of all five:**
`public_execute = False` (so none is reachable through PostgreSQL's default
`EXECUTE TO PUBLIC`), each carries a fixed `search_path` (`""` or
`pg_catalog`), and none matches a scan for dynamic `EXECUTE`, `format()`,
`quote_ident`, `SET ROLE`, `set_config` or `current_setting`. None of that
answers the question the bodies must answer, which is *why each needs
elevation at all*.

| Function | Outcome | Why |
| --- | --- | --- |
| `hr.enforce_employment_type_projection()` | **1 — unavailable to `app_user`, no replacement needed** | a trigger function; `EXECUTE` is unreachable privilege, and the grant contradicts an explicit revoke |
| `public.claim_outbox_batch(text, integer, integer)` | **1 — administrative** | drains **across every tenant**; requires `BYPASSRLS` by construction |
| `public.settle_outbox_event(...)` | **1 — administrative** | same ledger, same absent tenant predicate |
| `public.claim_platform_outbox_batch(text, integer, integer)` | **1 — administrative, and control-plane** | operates on the control-plane ledger ADR-0023 forbids the tenant role |
| `public.settle_platform_outbox_event(...)` | **1 — administrative, and control-plane** | same |

**All five are outcome 1. None is outcome 2, 3 or 4**, so no definer is
converted, no owner is moved and no function-owner role is created — those
would each be a separate authorized act, and none is needed.

#### `hr.enforce_employment_type_projection()` — trigger-only

The body checks that the row being written to the legacy compatibility
projection `hr.employment_type` exactly matches the authoritative row in
`mod_people.employment_types`, and raises `23514` if it does not. It reads one
fully-qualified relation, filters it by `NEW.employment_type_id` and
`NEW.organization_id`, returns `NEW`, and returns no data to the caller at
all — its only outputs are "proceed" and "raise".

Two facts decide it, and the second is the sharper one.

It is a **trigger function** (`RETURNS trigger`, created by
`alembic/versions/20260828_people_et_activation.py` together with its
`BEFORE INSERT OR UPDATE` trigger on `hr.employment_type`). PostgreSQL checks
`EXECUTE` on a trigger function when the **trigger is created**, not when it
fires, and a trigger function cannot be invoked directly. So `app_user` needs
no `EXECUTE` for the fence to work, and granting it confers nothing callable.

And that same migration ends with:

```
REVOKE ALL ON FUNCTION hr.enforce_employment_type_projection() FROM PUBLIC
REVOKE ALL ON FUNCTION hr.enforce_employment_type_projection() FROM app_user
```

The revoke from `app_user` is explicit, deliberate and covered by
`tests/migrations/test_people_employment_type_activation_migration.py`.
Mirroring the legacy role's `EXECUTE` onto `app_user` would **reverse a
tested revocation** under cover of a compatibility sweep. The legacy role's
own grant is inert debt to be dropped at retirement, not behaviour to
preserve. `SECURITY DEFINER` itself stays: the migration states why — the
fence must compare against the authoritative table across tenants despite
that table's RLS, with a fixed `pg_catalog` search path and a fully-qualified
target excluding caller-controlled resolution.

#### The outbox pairs: the bodies span tenants, which settles it

Michael's rule decides these: *"If a body genuinely requires `BYPASSRLS`, it
is an administrative capability and should not be callable by the application
runtime."*

**`claim_outbox_batch` / `settle_outbox_event`** operate on
`public.outbox_events`. That table is created by
`alembic/versions/20260824_outbox_relay.py` with `tenant_id NOT NULL`,
`ENABLE` **and** `FORCE ROW LEVEL SECURITY`, and a policy
`USING (tenant_id = public.app_current_tenant_id())` with the matching
`WITH CHECK`.

The claim body has **no tenant predicate whatsoever**. It selects by
`status`/`available_at`/`leased_at` with `FOR UPDATE SKIP LOCKED` and updates
whatever it finds, `RETURNING *` — every tenant's rows, in one batch, by
design. The migration says so in as many words: it supplies "the two relay
planes, their claim/settle pairs and the privilege boundary that makes the
**cross-tenant drain** safe". Under invoker rights the drain would see only
the caller's tenant and would silently stop draining everyone else, so the
elevation is not incidental — it *is* the function. That makes it an
administrative capability by Michael's rule, and it must not be callable by
the application runtime.

It already has the right shape, and it is not `app_user`. The migration grants
`EXECUTE` on this pair to **`outbox_dispatcher`** — a role the migration
refuses to create itself, fails closed if absent, and pins to
`(rolbypassrls, rolsuper) = (False, False)`. A separate drain identity, with
`BYPASSRLS` reachable only through these two small bodies, is exactly the
containment outcome 3 asks for; it exists already and needs nothing from this
cutover.

**`claim_platform_outbox_batch` / `settle_platform_outbox_event`** operate on
`public.platform_outbox_events` — and yes, the body shows the control plane.
The relation is created in the same migration with **no `tenant_id` column at
all**, no RLS and no policy (there is nothing to scope), granted to
`platform_api` and `app_admin`, and then:

```
REVOKE ALL PRIVILEGES ON TABLE public.platform_outbox_events FROM app_user
REVOKE SELECT   (…13 columns…) ON TABLE public.platform_outbox_events FROM app_user
REVOKE INSERT   (…) …
REVOKE UPDATE   (…) …
REVOKE REFERENCES (…) …
```

That is ADR-0023's dual-plane rule applied at both table and column level,
naming `app_user` explicitly, with the migration's own comment giving the
reason this document repeats elsewhere: `has_any_column_privilege` sees a
column grant `has_table_privilege` cannot. `EXECUTE` on these two would hand
the tenant application role a `SECURITY DEFINER` path to precisely the rows
that revoke exists to keep from it. Their `EXECUTE` goes to
**`platform_outbox_dispatcher`**, under the same `(False, False)` contract.

Neither pair is obsolete (outcome 4): all four are live, granted to live
dispatcher roles, and pinned by `require_prerequisites` against the kernel
contract. What is obsolete is the *legacy role's* `EXECUTE`, which retirement
removes.

**No outcome-3 candidates.** The retained-definer checklist — fully qualified
relations, no dynamic SQL, no caller-controlled identifier interpolation, no
role change, honest tenant scope, no cross-tenant output, `FORCE` RLS
underneath, a cross-tenant sensitivity proof — is the checklist for keeping a
definer **callable by the runtime**. Nothing here is, so none of them is the
deciding question. For the record, the four relay bodies would pass the first
four checks (every relation is schema-qualified, both are static SQL with no
`format()`/`quote_ident`/`EXECUTE`, every parameter is a value never an
identifier, and there is no `SET ROLE`/`set_config`) and would fail the rest
by construction: the tenant pair's scope is *deliberately* not the caller's,
`claim_outbox_batch` `RETURNING *` is cross-tenant output by design, and
`platform_outbox_events` has no RLS to force because it has no tenant column.
That is the same conclusion arriving from the other direction.

### `mod_files.platform_stored_files` — denied, and the absence proved

Exactly one `mod_*` privilege is already held by the legacy role:
`mod_files.platform_stored_files`, all four of SELECT/INSERT/UPDATE/DELETE.

It is a **control-plane** relation. `app.runtime_admission` names it as the
`files` module's `platform_tables` entry in as many words, ADR-0023 requires a
module's platform tables to be `REVOKE`d from the tenant application role, and
`app_user` *is* the tenant application role.

**Ruling (2026-09-04): `denied_by_architecture`, reason "ADR-0023
control-plane relation".** The four rows stay in the manifest so the refusal
is visible, and render as commented `NOT GRANTED:` lines rather than
statements. Mirroring would move a control-plane privilege onto the identity
the architecture forbids it on, under cover of a compatibility sweep. The
legacy role's holding of it is debt to be revoked at retirement.

#### The negative verifier, and why it checks columns

A denial nobody checks is a comment. Absence is the one claim that passes for
free: a verifier that asked nothing produces an empty violation list, which is
byte-identical to a clean database. So `denial_violations` (refusal 8) proves
the absence, and can fail in three ways:

1. **all seven table privileges** must be answered — SELECT, INSERT, UPDATE,
   DELETE, TRUNCATE, REFERENCES, TRIGGER. Checking only the four the census
   recorded would leave a denial silent about three real privileges.
2. **the four column-grantable privileges** must be answered against the
   column catalog. `GRANT SELECT(storage_key) ON mod_files.platform_stored_files
   TO app_user` leaves `relacl` untouched and makes
   `has_table_privilege(..., 'SELECT')` answer **false** while the role reads
   the column. The grant lives in `pg_attribute.attacl` — the catalog behind
   `information_schema.column_privileges` — so a denial proved only against
   the relation ACL is not proved at all. The verifier reads
   `has_column_privilege(role, oid, attnum, priv)` for the effective answer
   and `aclexplode(attacl)` for the origin. (Note the asymmetry with `relacl`:
   a NULL `attacl` genuinely means "no column grants", because column
   privileges have no default, so no `acldefault` stand-in is needed here.)
3. **the probe must have happened.** The number of columns examined is carried
   into the snapshot, and `columns_probed == 0` is refused exactly as loudly
   as a missing observation — "nothing held" from a probe that looked at
   nothing is not evidence.

Denied rows are also removed from the ordinary privilege checks, which would
otherwise demand `app_user` **hold** the four privileges just refused. One
decision, one owner.

**Callers.** The legacy role's access to this relation has **no caller in the
ERP source**. `app/services/storage.py` consumes `dotmac-files` purely as an
object-storage provider — an S3/MinIO adapter with no session and no domain
decision — and `app.runtime_admission` already records the fact in a checked-in
comment: *"ERP consumes dotmac-files as an object-storage contract over its one
MinIO adapter; nothing under `app/` writes `mod_files`."* A repository-wide
search for `platform_stored_files`, `mod_files` and `StoredFile` finds only
governance artefacts — this manifest, `runtime_admission`, the bill of
materials, the lineage bindings and their tests. Nothing reads or writes the
relation. That is what allows the legacy grant to be revoked outright at
retirement rather than migrated.

### The five schema USAGE cases — settled, removed, origins kept

`hr`, `mod_files`, `public`, `rpt` and `sync` carry 448 relation privileges
between them and appeared in no schema-USAGE row. A read-only follow-up query
on 2026-09-04 returned **`legacy=True, app_user=True` for all five**: the
delta interpretation is confirmed, `app_user` already reaches every one of
them, and the derived `GRANT` would have been a no-op. The five rows are
**removed**, and `BASELINE_TOTALS[schema_usage]` is lowered 42 → 37 in the same
commit — which is the reviewed, in-commit edit the two-directional ratchet
demands, not a count moving on its own.

The rows go; the **origins stay**, because they are not the same fact:

| Schema | Origin | Evidence |
| --- | --- | --- |
| `hr` | **direct** `app_user:USAGE` | `20260828_people_et_activation.py` (earlier `20260828_people_et_bootstrap.py`) |
| `rpt` | **direct** `app_user:USAGE` | `20260828_sales_analysis_refresh_definer.py` |
| `sync` | **direct** `app_user:USAGE` | `20260825_retire_dotmac_crm.py` |
| `mod_files` | **direct** `app_user:USAGE` | granted by the composed `dotmac-files` lineage |
| `public` | **`PUBLIC:USAGE`** — *not a direct grant* | PostgreSQL's own default on `public`; the ACL contains **no `app_user` entry at all** |

`public` is the distinction worth keeping. Its ACL is
`pg_database_owner:USAGE pg_database_owner:CREATE PUBLIC:USAGE
dotmac_erp_app:USAGE dotmac_erp_app:CREATE outbox_dispatcher:USAGE
platform_outbox_dispatcher:USAGE`. `app_user` reaches the schema only through
`PUBLIC`, which is precisely the origin `REJECTED_PRIVILEGE_ORIGINS` refuses
everywhere else in this manifest, because `PUBLIC` reaches every login in the
cluster including the next one someone creates. It makes the derived row a
no-op for the **identity cutover** and leaves an open question for **Change 3**.
Flattening it into "all five already have USAGE" would discard the only half
with a consequence.

`mod_files` is the second one worth keeping. Its ACL carries
`app_user:USAGE` **and** `platform_api:USAGE` — the module's tenant tables and
its platform tables share one schema. Schema `USAGE` cannot separate the two
planes and was never meant to, which is exactly why isolation there has to hold
at the **table** level. That is what the denial above does.

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

- `scripts/erp_identity_cutover_grants.sql` — 1,736 statements. The routine
  half: mechanical, reviewable in bulk. It contains no denied row at all.
- `scripts/erp_identity_cutover_denied.sql` — **25 rows and zero statements.**
  Every line is a comment; there is no `BEGIN`, no `COMMIT` and nothing to
  uncomment, so applying it with `psql` is a no-op by construction rather than
  by convention. Each entry names the privilege that is NOT granted, the
  reason, the declaration that decided the plane, and who MAY do it instead.

Splitting them is the control, and the split is **permanent**. A 1,700-line
file with escalation decisions buried in it gets skimmed, and that does not
stop being true once the decisions are made. Both renderers refuse the wrong
rows, so the split cannot quietly stop holding.

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

## The guard's nine refusals

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
   `MODULE_ERA_ALLOWLIST` entry (`relation:mod_files.platform_stored_files`,
   which may shrink, never grow);
8. **a DENIED relation being reachable after all** — at table *or* column
   level, or the verifier not having looked at all;
9. **a DENIED function being EXECUTABLE after all** — by `app_user`, by
   `PUBLIC`, or by *nobody* (the permitted executor lost it too) — or the
   answer having been read off an ACL rather than asked effectively.

Refusals 8 and 9 are the only two that assert an **absence**, which is why
each also refuses its own silence: an unprobed denial and a clean database
produce the same empty list.

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
an otherwise-clean 1,761-row snapshot built from the real committed manifest,
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
| 9 | `app_user` holds `SELECT` on the denied relation (table ACL) | `DENIED PRIVILEGE HELD` |
| 10 | `app_user` holds `SELECT(storage_key)` on it (column ACL only, table level left clean) | `DENIED COLUMN PRIVILEGE HELD` |
| 11 | the denial probe is removed, then reduced to `columns_probed == 0` | `UNPROBED DENIAL` / `UNPROBED COLUMN DENIAL` |
| 12 | `app_user` holds `EXECUTE` on a denied definer | `DENIED EXECUTE HELD` |
| 13 | `PUBLIC` holds `EXECUTE`, with the `app_user` answer left false | `PUBLIC EXECUTE INHERITED` (remediation owed) |
| 13b | the `app_user` answer marked non-effective | `NON-EFFECTIVE EXECUTE ANSWER` |
| 14 | `outbox_dispatcher` loses `EXECUTE` | `PERMITTED EXECUTOR CANNOT EXECUTE` |

Proof 10 is the one that matters most on the relation side: it plants the
defect **only** at column level and asserts the table-level answers stay
false, so a guard missing the column half would pass it with zero violations
rather than failing loudly. Proof 13 is its counterpart on the function side —
it plants the grant on `PUBLIC` only, leaves the `app_user` answer false, and
requires the remediation to be named; a verifier reading the ACL for
`app_user` would see nothing at all. Proof 11 and the unprobed-EXECUTE proof
are the non-vacuity half — a negative verifier that never looks produces the
same empty list as a clean database, so not-probing is itself a refusal.

### The nine plane proofs (Decision 2)

The resolver's own coverage, each planted, in
`tests/architecture/test_privilege_manifest.py` (`test_plane_sensitivity_*`)
with the resolver's structural guards in
`tests/architecture/test_persistence_planes.py`:

| # | Planted | Required outcome |
| --- | --- | --- |
| 1 | a platform table in `public` | denied |
| 2 | a platform table in a `mod_*` schema | denied |
| 3 | a **tenant** table in a `mod_*` schema | **granted** — not falsely denied |
| 4 | a **tenant** table in `public`, declared at relation level, beside a control-plane one | follows its declaration: granted |
| 5 | a relation no declaration covers | `UnclassifiedRelation` — generation refuses |
| 6 | a table-level grant on a denied relation | `DENIED PRIVILEGE HELD` (proof 9 above) |
| 7 | a column-level grant with the table ACL clean | `DENIED COLUMN PRIVILEGE HELD` (proof 10 above) |
| 8 | a declared relation observed under a different schema | plane unchanged, `schema_moved` reported |
| 9 | the same relation declared tenant, then control | the disposition flips |

Numbers **3, 4 and 8** are the ones that carry the argument. Any rule that
denied things called `platform*` would satisfy 1, 2, 6, 7 and 9; only a
resolver actually reading a declaration can pass a `mod_` table that is
granted, a `public` table that is granted beside four that are denied, and a
relation whose plane survives its schema changing underneath it.

**Negative control.** The same clean snapshot every proof mutates produces
**zero** violations; `denial_violations` and `function_denial_violations` are
both silent on it; and on the *real* census the resolver returns 1,696 tenant
rows against 20 control rows rather than denying everything. Without those
assertions, every proof above could be passing because the detector flags
everything. Each proof additionally requires that one planted defect produces
exactly the expected violations and no cascade — specificity is half of
naming.

## The revised Change-1 boundary

Change 1 may proceed only when **all** of these hold. Each is checked, and the
check that checks it is named:

| Condition | Where it is enforced |
| --- | --- |
| the five functions are tracked denials | `DENIED_TOTALS[functions] == 5`; `test_decision_1_all_five_execute_grants_are_denied_with_an_executor` |
| both control-plane relation families are tracked denials | `DENIED_TOTALS[control_plane] == 20` over five relations; `test_decision_2_every_control_plane_relation_is_denied_by_declaration` |
| **no denied item renders SQL** | `render_grant_sql` raises on a denied row; the ledger has zero executable lines; `test_no_denied_item_renders_sql_because_the_ledger_has_none` |
| the plane resolver uses declarations | `app/persistence_planes.py`; `tests/architecture/test_persistence_planes.py`; the nine plane proofs |
| unknown plane classification **fails closed** | `UnclassifiedRelation` propagates out of `manifest_from_census`; plane proof 5 |
| effective and column-level denial checks pass | `denial_violations` + `function_denial_violations`; proofs 9–14 |
| totals and ratchets updated mechanically | `BASELINE_TOTALS` and `DENIED_TOTALS`, both two-directional; `test_the_denial_ratchet_is_two_directional` |
| the safe grant set remains idempotent | `GRANT` is an ACL assertion, not an append — see "The generated SQL, and its idempotency"; one privilege, one object, per statement |

### Two constraints that bound the scope, and were not crossed

- **`PUBLIC:USAGE` on schema `public` is unchanged.** Whether to revoke it is a
  **Change 3** least-privilege decision, because revoking it affects every
  database role in the cluster rather than only ERP's runtime. It is recorded
  in `SETTLED_SCHEMA_USAGE["public"]` as a settled no-op for the identity
  cutover and an open question for Change 3, and nothing here touches it. The
  generated SQL contains no `REVOKE` at all.
- **The legacy role's existing `mod_files.platform_stored_files` grant is not
  revoked.** Change 1 is **grant-only**. That grant has no caller anywhere in
  the ERP source, so it is revoked during the explicit old-role retirement
  step, not here. `MODULE_ERA_ALLOWLIST` keeps it frozen — it may shrink,
  never grow — and the guard would refuse any *new* module privilege on the
  legacy role.

## Closed on 2026-09-04

- **The five derived schema USAGE rows.** Settled as no-ops, removed, origins
  recorded — see above. `BASELINE_TOTALS` lowered 42 → 37 in the same commit.
- **The five `SECURITY DEFINER` bodies.** Read and classified. All five are
  outcome 1: keep unavailable to `app_user`, no replacement needed — and now
  DENIED, with the permitted executor of each recorded (Decision 1).
- **The `mod_` plane heuristic.** Removed. Plane is resolved from declarations
  (Decision 2), and an unclassified relation refuses generation.
- **Five control-plane relations** — `mod_files.platform_stored_files` plus the
  four in `public` the heuristic was missing — are `denied_by_architecture`,
  with the absence proved at table and column level and no caller anywhere in
  the ERP source.
- **`public.platform_outbox_events`, and the three relations beside it.** The
  finding recorded on 2026-09-04 was ruled a Change-1 blocker and acted on the
  same day.

## What is not closed

- **The scope of Decision 2 exceeded the letter of the ruling, and that needs
  Michael's sign-off.** The ruling named `public.platform_outbox_events` and
  `mod_files.platform_stored_files` — "both control-plane relations". Read
  honestly, the host declaration classifies **four** relations in `public`, not
  one: `20260820_idempotency_ledger` revokes
  `public.platform_idempotency_records` from `app_user` at table and column
  level, and `20260813_tenant_projection` revokes `public.tenants` and
  `public.tenant_domains` from both `PUBLIC` and `app_user`. Granting any of
  them reverses a tested migration decision for exactly the reason the ruling
  gives, and there is no third disposition for "declared control plane, granted
  anyway" — that shape would be the second writer the ruling forbids. So all
  five are denied and the extra twelve privileges are reported here rather than
  applied quietly. Evidence that the extra three are safe to deny: no ERP
  runtime code reads `public.tenants` or `public.tenant_domains` — the tenant
  catalogue is reached through the narrow `SECURITY DEFINER`
  `tenant_catalog.organization_ids` (`app/tenant_catalog.py`), which returns
  identifiers and nothing else — and `platform_idempotency_records` is the
  control-plane half of a ledger whose tenant half `app_user` keeps.
- **The reverse gap is a count, not an inventory.** 132 privileges across five
  schemas, with no object-level detail. The guard ratchets the count; it cannot
  tell you *which* privilege changed.
- **The denials are proved against a live catalog, and that has not been run.**
  No SQL from this programme has been executed anywhere; `denial_violations`
  and `function_denial_violations` are exercised today only against the
  synthetic clean snapshot and their planted defects. In particular the
  `PUBLIC EXECUTE` remediation check has never met a real catalog — the census
  recorded `public_execute = False` for all five, so if a default has survived
  anywhere the first thing that will say so is the verifier's first real run.

## Files

| Path | What it is |
| --- | --- |
| `docs/inventories/erp-privilege-census-2026-09-04.json` | the frozen production census (input, provenance) |
| `docs/inventories/erp-identity-cutover-manifest-2026-09-04.json` | the generated manifest |
| `app/privilege_manifest.py` | the pure contract: generator, renderer, refusals |
| `scripts/generate_privilege_manifest.py` | offline generator / `--check` |
| `app/persistence_planes.py` | the plane DECLARATIONS and the resolver that refuses when none covers a relation |
| `scripts/erp_identity_cutover_grants.sql` | generated, routine half — 1,736 `GRANT`s, no denied row |
| `scripts/erp_identity_cutover_denied.sql` | generated DENIAL LEDGER — 25 rows, **zero statements** |
| `scripts/verify_identity_cutover_privileges.py` | read-only OID-based verifier |
| `tests/architecture/test_privilege_manifest.py` | the guard, its sensitivity proofs and the nine plane proofs |
| `tests/architecture/test_persistence_planes.py` | the resolver's own guard: derived-not-copied, cited evidence, fail-closed |
