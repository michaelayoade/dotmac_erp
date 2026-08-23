# Accounting adoption boundary

Status: **composed and disabled; authority remains entirely in ERP**.

Gates A and B are complete and gate C has landed. `dotmac-accounting==0.1.0a1`
is pinned exactly, its lineage is composed in `alembic.ini`, and
`mod_accounting` is created by `alembic upgrade heads` against a real database.
**That is storage, and storage alone.** `ACCOUNTING_COMPOSITION_ENABLED` is
false, nothing under `app/` imports the package, no ERP writer has been
repointed, no backfill has run, and no decision has moved.

ERP already applies this exact rule to `idempotency_ledger.v1`, whose tables
have existed since `20260820_idempotency_ledger` while its operations remain
uncut-over. Supplying a module's tables cuts nothing over.

## Ownership

| Concern | Current owner | Intended owner after an authorized cutover |
| --- | --- | --- |
| Chart of accounts and account categories | ERP `gl.account`, `gl.account_category` | `dotmac-accounting` |
| Fiscal calendar (years, periods) | ERP `gl.fiscal_year`, `gl.fiscal_period` | `dotmac-accounting` |
| Journal lifecycle and balanced posting | ERP `gl.journal_entry`, `gl.journal_entry_line` | `dotmac-accounting` |
| Immutable posted-ledger evidence | ERP `gl.posted_ledger_line` | `dotmac-accounting` |
| Linked reversals; period open/close/reopen/lock | ERP `gl.reversal`, `gl.period_guard`, `gl.period_close` | `dotmac-accounting` |
| Accounting dimensions | ERP fixed columns on GL lines | `dotmac-accounting` generic dimension registry |
| Derived balance cache and its refresh queue | ERP `gl.account_balance`, `gl.balance_refresh_queue` | **stays ERP** — rebuilt from the new source, never migrated into it |
| Budgets | ERP `gl.budget`, `gl.budget_line` | **stays ERP** — not in Accounting's boundary |
| Posting batches | ERP `gl.posting_batch` | **ends** — no module table and no surviving ERP writer; see below |
| Supplier liabilities, tax policy, banking, payment execution | Their existing ERP domains | Their own later slices (`dotmac-payables`, `dotmac-tax`, `dotmac-banking`) |

The ownership decision itself is Starter ADR-0041; this document is ERP's
as-built preparation against it, not a second opinion about the boundary.
Accounting must cut over **before** Payables activates accounting consequences.

## The map

`docs/inventories/accounting-gl-writers.tsv` — every site that mutates a `gl.*`
relation. `docs/inventories/accounting-gl-callers.tsv` — every site that depends
on a GL decision service. Both are exact two-directional ratchets enforced by
`tests/architecture/test_accounting_gl_boundary.py`: a new row is new authority
ERP will have to retire, and a vanished row must be removed from the ledger in
the same reviewed change.

At this revision:

Every row carries **two** columns, not one: a `disposition` (what the cutover
does to it) and a `final_state` (what must be true of it when gate G is
complete). A bucket with no stated end state is where work goes to be forgotten,
so `operator_tool` and `keep_local` are not terminal answers — each row says
where it ends.

| | rows | final states |
| --- | ---: | --- |
| **Writers** | 74 | 36 `writer_removed`, 20 `tool_repointed`, 12 `tool_archived`, 6 `retained_erp_writer` |
| **Callers** | 168 | 106 `caller_repointed`, 58 `retired_with_gl_owner`, 4 `retained_erp_caller` |

Each disposition/final-state pair carries an invariant checked against the code,
not against the label:

| Pair | Invariant |
| --- | --- |
| `keep_local` / `retained_erp_writer` | every relation the row writes is in `RETAINED_ERP_RELATIONS` |
| `retire_with_accounting_cutover` / `writer_removed` | the row writes something *not* retained |
| `operator_tool` / `tool_repointed` | the entry point is Python — a raw `.sql` one-off cannot call a module contract and must be archived instead |
| `operator_tool` / `tool_archived` | retired by moving the file to `scripts/archive/`, which the ratchet then forces out of the ledger |
| `keep_local` / `retained_erp_caller` | every decision it consumes is in `RETAINED_GL_DECISIONS` (only `gl.balances`) |
| `repoint_to_module` / `caller_repointed` | it consumes at least one decision that actually moves |
| `gl_internal` / `retired_with_gl_owner` | the path really is inside `app/services/finance/{gl,posting}/` |

That last column is what caught the one mis-filing in the first draft — see
"Posting batches" below.

The 106 `repoint_to_module` callers, not the 36 writers, are the size of this
cutover. Where they are:

| | | | |
| --- | ---: | --- | ---: |
| `finance/ap` | 14 | `finance/banking` | 8 |
| `finance/ar` | 13 | `expense` | 8 |
| `scripts` | 11 | `fixed_assets` | 6 |
| `people` | 9 | `finance/lease` | 6 |
| `inventory` | 9 | `finance/tax` | 4 |
| `tasks` | 4 | `dotmac_sub` | 4 |
| `finance/cons` | 3 | `tools` | 2 |
| `finance/rpt`, `import_export`, `exp`, `automation`, `web` | 1 each | | |

Every domain that produces an accounting consequence is on that list. Sealing a
writer is one change; giving 106 call sites a different way to reach a posting
decision is the work, and it is why the caller ledger is kept separately from
the writer ledger rather than folded into it.

Scope, stated as an enforceable premise (ADR-0018): the scan covers `app`,
`scripts` and `tools` — the online, task/worker and operator entry-point
families. `alembic/` is excluded because a data migration defines or repairs
storage and has already run; it cannot be cut over. `scripts/archive/` is
excluded on the premise `check_session_context.py` already states — an archived
one-off is kept for provenance, never executed again. Migration-time DML against
`gl.*` is real and is inventoried here rather than in the ratchet: 24 files, 105
statements across `alembic/` and `scripts/archive/`, concentrated in the April
2026 VAT and opening-balance repair wave. None of it is an online writer.

### Posting batches, and why a relation has three possible outcomes

A GL relation is exactly one of three things, and the first draft of this slice
had only two — which is precisely how `gl.posting_batch` was mis-filed.

| Outcome | Meaning | Relations |
| --- | ---: | --- |
| **Migrates** | a module table takes it over | the seven in `RELATION_OWNERSHIP` with a counterpart |
| **Retained** | ERP goes on writing it | `gl.account_balance`, `gl.balance_refresh_queue`, `gl.budget`, `gl.budget_line` |
| **Ends with its writer** | no module table AND no surviving ERP writer | `gl.posting_batch` |

`gl.posting_batch` is ERP's batching envelope around a posting run, written by
`LedgerPostingService` and by nothing else. The module posts per journal with
`period_events` plus the immutable ledger as its evidence, so the batch record
has nowhere to migrate *to* — and once the poster is sealed, nothing is left to
write it. Under a "no counterpart therefore retained" rule,
`LedgerPostingService._retire_superseded_batch_key` was filed `keep_local`,
promising a surviving ERP writer inside a service that is being sealed. Calling
the relation "retained" promises a writer that will not exist; calling it
"migrating" promises a table that will not exist. It is neither.

`test_every_gl_relation_has_exactly_one_of_three_outcomes` asserts the partition
is total and disjoint, so a future relation cannot be added without a decision.

## Disabled composition

**Declared, and proven operationally inert.** Absence of the module is one claim
(`test_accounting_composition_disabled.py`); inertness of the scaffold is a
different one, and scaffolding can be entirely module-free and still change a
deployment. `tests/architecture/test_accounting_scaffold_is_inert.py` asserts,
against the booted application rather than by reading source, that: importing
`app.main` pulls in none of the three scaffold modules; no ORM model or
`__tablename__` joins `Base.metadata`; no route, Celery task, beat entry,
`APIRouter` or `Celery` object is registered at import; the scaffold reads
exactly one environment variable (`ACCOUNTING_COMPOSITION_ENABLED`); the
declaration's module body is imports, constants and definitions only; and
importing the scaffold with every outbound socket poisoned reaches no network or
database. Each of those checks carries a sensitivity proof — the boot probe and
the socket poison are both shown to fail when something really does import or
connect, because both pass by finding nothing, which is also what a broken probe
returns.

`app/accounting_adoption.py` declares the composition as data: distribution and
import names, the migration version location, the required database effects, the
ERP-relation → module-relation map, and the module-only relations the backfill
must synthesise. It imports nothing from the module.

`ACCOUNTING_COMPOSITION_ENABLED` (default `false`) is the single deploy-time
knob. `require_composition_ready()` refuses when the module is absent *or* when
the flag is off, and the two messages differ because the operator response
differs. Nothing degrades to "carry on against ERP's own tables" — a shadow run
that quietly compared ERP with ERP would report perfect agreement and mean
nothing.

`tests/architecture/test_accounting_composition.py` (renamed from
`..._disabled.py` at gate C) now asserts PRESENCE where gate A asserted absence.
The four absence assertions were replaced by their positive counterparts in the
same change that made them true, not deleted:

| gate A (absence) | gate C (presence) |
| --- | --- |
| not in `pyproject.toml` | pinned EXACTLY, from the `forgejo` source, and the INSTALLED version matches |
| not in `version_locations` | composed, and the entry RESOLVES to a directory holding `ac_0001_accounting.py` |
| `composition_state()` reports absent | reports the installed version, with `ready` still false |
| `require_composition_ready()` says "not installed" | says "not enabled" |

One gate-A assertion deliberately survives unchanged: **nothing under `app/`
imports the package.** Composition is a storage fact; a code-level import would
begin a runtime dependency, and that belongs to a later gate.

`TestOnceInstalled` is gone as a concept — its manifest checks now run
unconditionally, because the artifact is here. `EXPECTED_MODULE_TABLES`,
`REQUIRED_PREREQUISITES`, the module code, version, `core=False`, schema and
migration prefix are all checked against `dotmac_accounting`'s own manifest on
every run.

## Migrations

**What is proven, and what is not.** The module's manifest declares
`requires=(tenant_scope_catalog.v1, module_database_roles.v1,
idempotency_ledger.v1)`. ERP binds all three from its own lineage in
`app/migration_bindings.py`:

| Effect | ERP provider revision |
| --- | --- |
| `tenant_scope_catalog.v1` | `20260813_tenant_projection` |
| `module_database_roles.v1` | `20260814_database_roles` |
| `idempotency_ledger.v1` | `20260820_idempotency_ledger` (PR #328) |

Resolution yields exactly those three and nothing beginning `0001_`, which is the
point: ERP hosts `public.tenants` itself and can never run kernel `0001`
(`tests/integration/test_kernel_lineage_rehearsal.py` is the permanent negative
canary).

**Proven statically:** the three effect names resolve, through ERP's bindings,
onto three revisions ERP runs (`test_required_prerequisites_are_already_bound_to_erp_revisions`).

**Proven against PostgreSQL** at gate C, by
`tests/integration/test_accounting_lineage_composition.py`, on a disposable
database built the way a deploy builds one — ERP's own lineage to head first,
then `alembic upgrade heads` with the module lineages composed:

1. `ac_0001_accounting` applies and is stamped.
2. `require_prerequisites` — the same call `ac_0001` makes before its own DDL —
   passes against the live catalog for all three effects, with a sensitivity
   proof that drops a prerequisite table inside a rolled-back transaction and
   requires a refusal. This is the check gate A explicitly could not make.
3. Every declared `mod_accounting` table exists.
4. Every one of them has a non-nullable `tenant_id` and **FORCEd** row-level
   security. `FORCE` matters specifically: plain `ENABLE` is bypassed by the
   table owner, and migrations run as `app_admin`, which owns these tables.
5. `alembic upgrade heads` is repeatable — a second run is a no-op.
6. The kernel's composed migration gate reports nothing against either module
   lineage.

### ERP does not have one Alembic head, and two drafts got this wrong

An earlier draft of the gate C plan named "a single Alembic head" as an
acceptance criterion. It is not one, and asserting it would have been a defect.
Each composed module lineage is an independent ROOT with its own branch label —
that is the design, and it is why ERP's deploy path has always been
`alembic upgrade heads`, plural.

The correct criterion is: exactly one head per composed module branch at the
revision `COMPOSED_MODULE_LINEAGES` names, exactly one ERP head, and no
unintended heads. The last is the one with teeth — a second ERP root or an
orphaned revision makes `upgrade heads` apply a graph nobody drew.

**And the live run found a second, subtler thing.** At the original adoption
point, after `upgrade heads`, `alembic_version` held only TWO rows:

```
ac_0001_accounting
fi_0001_stored_files
```

The prerequisite provider, `20260820_idempotency_ledger`, is absent — correctly.
Both module lineages declare logical prerequisites that ERP's bindings resolve
onto ERP revisions, so each carries a `depends_on` edge onto that provider, and
Alembic treats a depended-upon revision as an ancestor of its dependent. It
stops being an effective head and its row is subsumed. Later ERP revisions are
not prerequisites of those module revisions, so the current ERP head remains a
separately stamped effective head.

So there are three numbers, not one, and they are all correct:

| | value | meaning |
| --- | ---: | --- |
| graph heads (`alembic heads`) | 3 | one ERP + one per module lineage |
| depended-upon | 3 ERP revisions | the bound prerequisite providers |
| stamped rows | derived | graph heads minus those subsumed by `depends_on`; currently 3 |

The test derives the expected stamped set from the script directory rather than
hard-coding 2, because the answer changes with the composition: a future module
needing no ERP prerequisite would leave ERP's head stamped. A separate test
distinguishes "subsumed" from "the provider never ran" — those look identical
in the version table — by requiring the provider revision to be depended upon
AND its tables to exist.

### The composed gate must see the whole composition

Scoping it to the module's own versions directory is wrong, and the gate says
so: each prerequisite is bound to an ERP revision, and a binding whose provider
is not in any selected location "names a revision this deployment never runs".
The first draft did exactly that and read the gate's correct rejection as a
defect in the module.

Given the whole composition, those violations disappear and what remains is
ERP's legacy history — ~380 assembly revisions with ids longer than 32
characters, and a root with no branch label. Those are not findings against this
change, so the assertion is scoped by the gate's own ATTRIBUTION: no violation
naming a module-owned revision. ERP's legacy ids are covered by a separate,
*enforceable* premise rather than an excuse — the gate warns that ids over 32
characters "fail at `alembic upgrade`", and for ERP that is untrue because a
revision named `extend_alembic_version` widened the column. The test checks that
width against the live catalog, so if anyone ever narrows it the warning becomes
true again and the build says so.

## Backfill

`app/services/finance/gl/accounting_backfill.py`, driven by
`scripts/backfill_accounting.py`. Read-only in every mode that runs today; the
loader refuses until the pin exists.

Masters are extracted row by row (categories, accounts, fiscal years, fiscal
periods, dimension definitions and values). Transactions are extracted as a
**work list** — one item per fiscal period with its counts and ERP's acceptance
digest — because a plan holding every posted line would be a copy of the ledger
with none of its guarantees. The loader walks the list one period at a time and
shadow-compares each period as it lands, so a divergence is attributable to a
period rather than to "the backfill".

Three shape changes the backfill performs rather than copies:

1. **Dimensions.** ERP carries four fixed dimension columns; the module carries a
   generic registry. The extraction synthesises four dimensions and reads their
   values from ERP's own masters (`core_org.business_unit`, `cost_center`,
   `project`, `reporting_segment`) — real codes and names. Reading them from
   distinct ids observed on ledger lines would silently drop every value not yet
   posted to.
2. **Period status.** ERP keeps a status column; the module keeps an event
   stream. The extraction states the current status and the loader replays each
   period forward to it. What is lost — who closed a period in 2024 — is already
   lost in ERP, apart from the `soft_closed_at`/`hard_closed_at` stamps this
   carries across.
3. **Account classification, and where it lives.** ERP puts the IFRS class on the
   *category* and control/posting/statistical on the *account*; the module splits
   them the same way but names them `account_class` and `kind`. Both mappings are
   single tables with an explicit refusal for anything unmapped, and the unit
   tests check each table against the ERP enum it maps, so a new enum member is a
   build failure rather than a mid-run one.

## Shadow comparison

`app/services/finance/gl/accounting_shadow.py`. Both sides produce the same
normalised `LedgerFact` values and `digest_facts` folds either stream into a
`PeriodLedgerDigest`, so the comparison logic is a pure function over exact
`Decimal`s — unit-tested today, unchanged on the day of the cutover. The ERP
producer is real; the module producer refuses until composition is enabled.

The **posted ledger** is what is compared, not journals (intent) and not
balances (a derived cache). Three levels, because they fail differently:

| Level | A mismatch means |
| --- | --- |
| Control totals (line count, total debit, total credit) | a whole document missing or duplicated |
| Per account (debit, credit, line count by account code) | a **mapping** error — the right money in the wrong account, invisible to a control-total check |
| Ordered line digest | the same position reached by different entries — same balances, different books |

Money is exact `Decimal` at six decimal places, the scale both stores carry.
Scale normalisation is not rounding: a value that does not fit is refused, because
rounding it would manufacture a match between two different facts. Digests carry
a version so a serialisation change can never be read as a ledger change, and
comparing two different scopes raises rather than reporting a divergence — a
caller bug must not send someone hunting for a posting error that does not exist.

## Writer-retirement canaries

The two ledgers above are the canaries. They are ratchets in both directions, so:

- adding a GL writer or a GL caller fails the build until it is recorded with a
  disposition, which makes new GL authority a reviewed decision rather than a
  side effect;
- retiring one fails the build until its row is removed, which makes retirement
  visible as progress and stops a "cleanup" from quietly deleting the evidence
  that a writer ever existed.

The detector carries sensitivity proofs for every evidence shape it claims to
find (constructor, `session.add`/`add_all`, attribute assignment, `setattr`,
core DML, raw SQL) and negative proofs that it does not fire on a pure read or on
a same-named class imported from elsewhere. Two further checks assert that the
GL service and model modules it keys on still exist — a renamed module would
otherwise make the caller ledger read "no callers", which is exactly the failure
a ratchet exists to prevent.

## Ordered gates

Each gate is a separate authorized change. None is implied by the one before it.

- **Gate A — readiness. COMPLETE** (PR #329, merged `c656bb90`). Map frozen,
  composition declared and disabled, prerequisites proven bound, backfill
  extraction and shadow comparison built and tested. No pin, no cutover.
- **Gate B — the tag. COMPLETE.** `dotmac-accounting` is released from Starter
  as the annotated tag `dotmac-accounting-v0.1.0a1`, peeling to Starter commit
  `20d24703`.

  Gate A's `test_the_distribution_is_not_pinned` was never an oracle for this —
  it was an ABSENCE guard, and treating it as a tag oracle would have made "we
  forgot to pin" and "there is nothing to pin" indistinguishable. Gate B is
  settled by the tag, and gate C replaced that assertion with its positive
  counterpart rather than deleting it quietly.
- **Gate C — composition. COMPLETE (this change).** Kernel repinned `a83` →
  `a85` (demanded by Accounting's own floor, `dotmac-kernel >= 0.1.0a85`),
  `dotmac-accounting==0.1.0a1` pinned exactly, the lineage composed in
  `version_locations`, the three prerequisite bindings re-derived, and
  `ac_0001_accounting` applied through ERP's real Alembic environment against a
  production-shaped non-production predecessor database. `dotmac-files` is NOT
  repinned. `ACCOUNTING_COMPOSITION_ENABLED` stays false. The module-side digest
  reader and master loader deliberately do NOT land here — they belong with the
  backfill that uses them.
- **Gate D — backfill and shadow.** Backfill masters, then periods oldest first,
  comparing each as it lands. Acceptance is `ShadowComparison.matches` at all
  three levels for every period, with the unbalanced-period list empty.

  **Planned in detail: `docs/architecture/accounting-gate-d-plan.md`**, on
  measured evidence in
  `docs/inventories/accounting-backfill-survey-2026-08-21.md`. The survey
  settled the scope rule — backfill exactly the journals that carry posted
  ledger lines, 190,179 of 206,071 — and cleared the one thing that could have
  blocked the gate behind a Starter release: ERP's four journal types with no
  module counterpart have zero rows. Four small data defects are the first work,
  and with one organization the rehearsal must manufacture a second tenant,
  because a mis-scoped write would otherwise have no observable symptom.

  Gate G forensics added a fifth Gate D population: 429 currently effective
  journal-keyed bank-fee postings over 39 statement-line identities. The
  namespace and ₦7,764.68 gross population are measured. The 2026-08-23 exact
  detector refused all 429 because their ledger account sets differ from the one
  line-keyed canonical per identity; it emitted no schedule or digest. They are
  a Gate D wrong-account quarantine requiring a Finance-approved correction
  design and before/after proof. Aggregate count or amount is not authorization.

- **Gate E — dual write and parity.** Both sides post; every posting is compared.
  Divergence is a stop, not a warning.
- **Gate F — cutover.** One writer at a time, in the order the writer ledger
  gives, lowering the ledger row by row. Each row's `final_state` says what
  "done" means for it: `writer_removed` deletes the path, `tool_repointed` gives
  the tool the module contract, `tool_archived` moves the file to
  `scripts/archive/`, `retained_erp_writer` stays. The 32 operator tools are
  settled before the online writers are sealed, because an operator script that
  still posts into `gl.*` after the module owns posting is a second writer with
  a human trigger.
- **Gate G — retirement.** Every row has reached its `final_state`: the writer
  ledger holds only the 6 `retained_erp_writer` rows, the caller ledger only the
  4 `retained_erp_caller` rows, and the ratchets are retired with the authority
  they were tracking. Reaching that state is checkable — it is what the two
  ledgers reduce to, not a judgement call.

  **Additional blocking condition: every remaining APPROVED-but-unposted journal
  must carry an explicit disposition.** At the 2026-08-21 survey there were
  14,263, of which at least 2,038 belong to the known stranded repost cohort.
  Retiring ERP's GL writers while any remain undisposed would strand live
  workflow state inside a retired system — work someone approved, that was never
  posted, and that no longer has a system able to post it. The remediation track
  is described in `accounting-gate-d-plan.md`; it runs in parallel with gate D
  and blocks here.

Production deployment and any authority move are separate authorizations, and
neither is granted by this document.
