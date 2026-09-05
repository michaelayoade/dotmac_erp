# The migration executor proves its database, and which module goes next

**Dated characterization, 2026-09-05. Facts and one recommendation, not a mandate.**

Two questions, recorded together because the second depends on the first: a
module cannot be taken up the adoption progression if the thing that migrates it
cannot say which database it reached.

---

## 1. `app_admin` is now proven from the real migration executor

### The gap

`app_admin`'s contract was evaluated in two places that answered different
questions, and only one of them ran where migrations run.

| check | deploy preflight<br>`bootstrap_database_roles.py --verify-only` | real executor<br>`alembic/env.py` |
|---|---|---|
| WHO the connection is (`current_user`) | yes | yes |
| WHAT it owns (object ownership) | yes | yes |
| WHETHER runtime roles can become it (`SET ROLE` graph) | yes | **no** |
| WHERE it landed (`current_database()`) | yes | **no → now yes** |

The preflight is **one caller** — `scripts/deploy.sh` step 3a. Three other paths
reach the database without it:

| path | preflight? |
|---|---|
| `scripts/deploy.sh` | yes |
| `make migrate` (`Makefile:81`) | **no** |
| `make docker-migrate` (`Makefile:149`) | **no** |
| CI `Run Alembic migrations` (`ci.yml`) | **no** |

Role posture and object ownership are **both satisfiable by the wrong cluster**:
a staging database with its own correctly shaped `app_admin` owning its own
objects passes every check the executor made.

### The premise that kept it out was false

The reason recorded in #459 for pinning the check to the preflight:

> Both are wired into `--verify-only` (deploy.sh step 3a), which stops before any
> DDL and prints a remedy, rather than into alembic/env.py, **where a refusal
> mid-chain leaves a half-applied upgrade.**

That does not hold at this call site, and the call site is the whole argument.
`run_migrations_online` evaluates `verify_migration_connection` inside a
read-only `connection.begin()` block that closes **before** `context.configure`,
before `context.begin_transaction()` and before `context.run_migrations()`. The
code comment beside it already said so: *"Finish that read-only preflight before
Alembic takes transaction authority."*

Nothing has been applied when it raises — which has been true of the executor
and ownership checks living there since they were added. **There is no chain
there to be mid-way through.**

Per ADR-0018 an exemption states an enforceable premise or the region is
unmonitored rather than exempt. This premise was not merely unenforceable; it
was contradicted by the function it described. The region is closed.

### The premise is now measured, not argued

`tests/integration/test_migration_executor_database_identity.py::test_a_refused_upgrade_applies_nothing_at_all`
counts tables after a refused upgrade. If the ordering ever regresses, it fails
against a real database rather than against a reading of the source.
`tests/architecture/…::test_the_executor_check_runs_before_any_migration_is_applied`
pins the AST ordering as the cheap early warning.

### Availability → adoption

The knob existed and nothing set it. `MIGRATION_EXPECTED_DATABASE` is now bound
on CI's `Run Alembic migrations` step, so the real executor — as `app_admin`, on
a disposable database — **asserts** rather than prints `UNVERIFIED`. That is the
difference between shipping a capability and adopting one.

**Bound per RUN, not per job, and the first attempt got this wrong.** An
authorisation is a property of one run against one database. Binding it at job
level leaked into the pytest step, where `test_accounting_lineage_composition`
and `test_kernel_lineage_rehearsal` create isolated `erp_*_<uuid>` databases and
point the executor at them — and every one of those upgrades was refused,
naming both databases. The refusal was **correct**; the granularity was wrong.
Both fixtures now declare their own expectation, so the module-lineage
rehearsals assert their target too instead of running `UNVERIFIED`. The
regression turned into wider coverage, but the lesson is the one worth keeping:
a job is not a run.

### Still open

**The executor does not run `role_escalation_violations`.** The preflight does.
A runtime role that can `SET ROLE app_admin` is invisible on the three paths that
skip the preflight. Deliberately not closed here: a dirty cluster-wide role graph
would block an unrelated migration, which is a different risk trade from "am I
migrating the right database" and belongs to whoever owns the deploy contract.
Recorded as an unmonitored region, not an exemption. **Decision needed.**

---

## 2. Module recommendation: `imports`

### The evidence

| module | tenant tables | platform tables | legacy writer to retire? | position |
|---|---|---|---|---|
| `files` | 1 | 1 | **none** — "nothing under `app/` writes `mod_files`" | composed |
| `imports` | 3 | 0 | **yes** — `CustomerImporter`, still the compared verdict | **rehearsed (shadow live)** |
| `numbering` | 4 | 0 | not repointed — "nothing reads or writes these tables yet" | migrated |
| `people` | 6 | 0 | yes | composed |
| `accounting` | 12 | 0 | yes — ERP remains live posting authority | composed, gated at C |
| `tax` | 16 | 0 | yes | composed |

### Why `imports` and not the smaller ones

**`files` is smaller and would prove nothing.** Its note records that nothing
under `app/` writes `mod_files`, so step 8 — *old writer retired* — is vacuous.
Adopting it would move availability, not authority, which is the exact pattern
this stream exists to break.

**`numbering` is not low-risk despite being small.** It allocates document
numbers; gapless invoice numbering is a tax-compliance property, so a wrong
allocation is externally visible and hard to unwind. Its note also records that
no series is configured and no legacy allocator is repointed — it is further from
adoption than `imports`, not closer.

**`accounting` and `tax` are the highest-consequence modules in the assembly**
(12 and 16 tables, live posting/tax authority). They are the wrong place to
learn the progression.

**`imports` has the lowest blast radius of any module with a real writer to
retire.** A failed import is retryable and corrupts no authoritative ledger,
unlike a mis-posted journal or a mis-allocated invoice number. It already runs a
shadow with a compared verdict, and it has a checked-in boundary document.

### How far it goes without production, and exactly where it stops

`docs/architecture/imports-adoption-boundary.md` states five retirement gates:

| # | gate | state |
|---|---|---|
| 1 | released a2 module + exact ERP lock pin | **complete** |
| 2 | fresh + predecessor-upgrade PostgreSQL proof of `mod_imports` | **complete** |
| 3 | ≥2 concurrent dry-run claims proving disjoint ownership | **open — CI-provable, no production needed** |
| 4 | real customer files, row-for-row clean shadow comparisons | **BLOCKED — needs production data** |
| 5 | delete the legacy decode/map/run loop, caller baseline reduced | depends on 4 |

Against the eight-step progression:

```
published → installed → composed → migrated → rehearsed → authoritative
                                              ^^^^^^^^^   ^^^^^^^^^^^^^
                                              reachable   STOPS HERE
                                              in CI
→ production-proven → old writer retired
  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  requires a named production target
```

**Gate 3 is the next real non-production step** and is the recommended next
slice: two concurrent dry-run claims against an ephemeral PostgreSQL database in
CI, proving partitions are disjoint and token-gated. It needs no production
access and no credential.

**It stops hard at gate 4.** "Real customer files" is production data. Under the
standing hold that is not reachable, and no amount of CI work substitutes for it
— a shadow comparison over synthetic rows proves the comparator runs, not that
the two implementations agree on the data that exists.

Recording it this way so gate 4 is not quietly satisfied with fixtures and
reported as adoption.

---

## Summary

| item | state |
|---|---|
| executor asserts WHERE it landed | **closed**, on all four migration paths |
| the premise that kept it out | **false**, and now measured against a real database |
| CI binds the expectation | **adopted**, not merely available |
| executor runs the role-escalation check | open — decision needed |
| `imports` gates 1–2 | complete |
| `imports` gate 3 | open, CI-reachable — recommended next slice |
| `imports` gates 4–5 | blocked on a named production target |
