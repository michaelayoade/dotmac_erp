# Accounting gate D — backfill and shadow

Status: **planned, not started.** Gate C is merged (`a7ead0b0`): the
`mod_accounting` lineage is composed and its tables exist, switched off. This
document is the plan for filling them and proving the fill is faithful.

Scope is inherited from `accounting-adoption-boundary.md` and is not widened
here: **backfill masters, then periods oldest first, shadow-comparing each as it
lands. Dual write is gate E.** Nothing in gate D repoints an ERP writer or moves
a decision.

Every number below comes from
`docs/inventories/accounting-backfill-survey-2026-08-21.md`, measured read-only
against the ERP standby. Re-run `scripts/accounting_backfill_survey.sql` before
cutover; the ledger gains rows daily and a stale survey is a guess.

## What the survey settled

The measurement was worth taking before writing code. Four things it decided:

1. **No `dotmac-accounting` release is needed.** ERP's four journal types with
   no module counterpart — RECURRING, INTERCOMPANY, REVALUATION, CONSOLIDATION —
   have zero rows. Had any been non-zero, gate D would have been blocked behind
   a Starter release.
2. **The scope rule is a property of the data, not a policy.** Journals split
   cleanly: POSTED and REVERSED carry posted ledger lines, DRAFT / SUBMITTED /
   APPROVED / VOID carry none. So gate D backfills **exactly the journals that
   carry posted ledger lines** — 190,179 of 206,071 — which is precisely the set
   the shadow comparator already covers. Scope and acceptance align by
   construction rather than by agreement.
3. **The module's missing journal statuses do not matter.** SUBMITTED and
   APPROVED never carry ledger effect.
4. **Four small data defects block the run**, and they are the first work.

## Ordered work

### D0 — survey. DONE

`docs/inventories/accounting-backfill-survey-2026-08-21.md`.

### D1 — clear the four defects

None is a code change; all are data or a decision, and all are ERP-side.

| # | Defect | Disposition |
| --- | --- | --- |
| 1 | Three journals unbalanced by `-0.000001` (`JE202604-40818`, `JE202604-40653`, `JE202604-42111`, all 2026-04-18) | Correct in ERP. The module enforces balanced posting and will refuse them. They fully explain the ledger's `-0.000003` trial-balance difference, so correcting them also makes the acceptance baseline exact. |
| 2 | One journal flagged `is_reversal` with no `reversed_journal_id` | Identify and either link or unflag. A reversal with no original cannot be replayed through `reverse_journal`. |
| 3 | Six journals with no provenance at all | No action required for the backfill — provenance rides in `description`/`reference`, not in identity. Recorded so it is not rediscovered. |
| 4 | Source-module casing inconsistent (`AR`/`ar`, …) | Normalise **on read**, in the extractor. Do not rewrite ERP data for a migration's convenience. |

Defect 1 is the only hard blocker. Until it is fixed the backfill cannot
complete, and it should be fixed as ordinary ERP finance work with its own
review — not folded into an adoption commit.

### D2 — implement the two seams

`load_masters()` and `build_module_digest()` are `NotImplementedError` stubs
left deliberately at gate A. They land here, with unit tests, still gated off.

Three mapping tables join the two that already exist
(`IFRS_CATEGORY_TO_ACCOUNT_CLASS`, `ACCOUNT_TYPE_TO_KIND`), and each is held to
the same rules: exhaustive over the ERP enum, injective, non-overlapping, with a
sensitivity proof.

| mapping | note |
| --- | --- |
| ERP `JournalType` → module `JournalKind` | Five map identically. The four absent values are **refused loudly**, never defaulted — their ERP writers still exist, so a future journal could carry one. |
| ERP `PeriodStatus` → module `PeriodStatus` | Identity for FUTURE / OPEN / SOFT_CLOSED / REOPENED; ERP `HARD_CLOSED` → module `LOCKED`. Zero instances today; the mapping exists so the zero is checked rather than assumed. |
| ERP `JournalStatus` → in-scope or not | Only POSTED and REVERSED are in scope. The rule is derived from posted-line presence, and asserted against the status split so the two cannot silently diverge. |

#### SourceIdentity — key on the GL journal, not the source document

The module's journal uniqueness is
`(tenant, source_owner, source_document_kind, source_document_id, source_version)`.
The backfill sets:

```
owner         = "erp.gl"
document_kind = "journal_entry"
document_id   = <ERP journal_entry_id>
version       = "erp-backfill.v1"
fingerprint   = <hash of the journal content>
```

**Why not the source document it came from.** If a backfilled journal claimed
the source document's identity, then after cutover a *new* journal legitimately
arising from that same document could never be created — the unique constraint
would refuse it. That is exactly backwards: the whole point of the cutover is
that those documents go on producing accounting consequences.

The survey supports this independently. `source_document_id` is present on only
101,465 of 206,071 journals, and the module names that do exist are
case-inconsistent — keying on them would bake a data-quality defect into
permanent identity.

The source document still travels, in `description`/`reference`, where it is
provenance rather than identity.

`version` carries the run. A corrected re-backfill is `erp-backfill.v2` — a
deliberate, reviewed act producing new rows, not a silent no-op against the old
ones.

#### Period close evidence — real, not a placeholder

`soft_close_period` requires `PeriodCloseEvidence` with at least one passing
check. For a period ERP closed months ago there is no evidence to carry across.
Rather than invent a placeholder, synthesise a true one:

```
code               = "ERP_BACKFILL_PARITY"
passed             = True
evidence_reference = <period scope label>
fingerprint        = <ordered line digest from the shadow comparison>
```

The period is closed **because its ledger provably matched ERP's**. A period
cannot be closed before its comparison passes, which makes the ordering
constraint carry the meaning rather than a comment.

#### Replay order

1. Masters: categories (parents before children), accounts, fiscal years,
   fiscal periods, dimension definitions, dimension values.
2. Periods oldest first. For each: `open_period` → create and post its journals
   → shadow-compare → `soft_close_period` if ERP's status is closed.
3. Reversals through `reverse_journal`, never as two independent journals, so
   `reverses_journal_id` / `reversal_journal_id` link correctly. An original may
   sit in an earlier period than its reversal, so ordering is by posting date
   across the whole run, not within a period.

Every module write takes an idempotency key, so the run is resumable and a
retried batch is a no-op rather than a duplicate.

### D3 — rehearse and prove

On a **restored copy**, never production and never the standby. The flag
`ACCOUNTING_COMPOSITION_ENABLED=true` is set **there only**; `main`'s default
stays false.

Acceptance:

- every one of the 24 periods returns `ShadowComparison.matches` at all three
  levels — control totals, per account, ordered line digest;
- the unbalanced-period list is empty;
- the module's trial balance equals ERP's exactly
  (`9,614,004,099.566905` / `…908` at survey time, and exactly equal once
  defect 1 is corrected);
- the run is resumable: interrupting and restarting produces the same result.

#### One organization makes tenant isolation invisible — so manufacture a second

Dotmac Technologies is the only organization. A backfill that wrote rows under
the wrong `tenant_id` would therefore produce **no observable symptom**: there is
nothing to leak into, and every query would still return the expected rows.

Gate C proved the tables are shaped correctly — `tenant_id NOT NULL`, RLS
enabled and FORCEd. It did not prove the backfill *writes* correctly scoped, and
production cannot prove it either.

So the rehearsal database must carry a **synthetic second tenant**, seeded
before the run with its own accounts and journals, and asserted byte-identical
afterwards. That check is worthless in production and is the only place the
property can be established at all.

## Sizing

190,179 journals × two idempotent service calls (create, then post) ≈ **380,000
calls**, producing 416,316 posted lines. Throughput is the engineering concern,
not correctness: the largest single period is FY2026 P4 at 93,236 lines.

The dimension backfill is near-trivial — 1,589 project values and eleven
line-dimension rows — and the master backfill is 19 categories and 332 accounts.
Effectively all the work is journals.

## What gate D deliberately does NOT do

- Repoint any ERP caller or writer. The 106 `repoint_to_module` rows in
  `docs/inventories/accounting-gl-callers.tsv` are untouched.
- Dual-write. Both systems posting is gate E.
- Enable `ACCOUNTING_COMPOSITION_ENABLED` anywhere but a disposable rehearsal
  database.
- Touch production, or run against the standby beyond the read-only survey.
- Retire `gl.posting_batch`, or any relation in the writer ledger.

## Open decisions

| Decision | State |
| --- | --- |
| SourceIdentity keyed on the GL journal | Recommended above; needs Michael's ruling before data lands, because it is permanent provenance. |
| Gate D stops before dual write | Stated in `accounting-adoption-boundary.md`; restated here for the avoidance of doubt. |
| The 14,263 APPROVED-but-unposted journals | Operational question raised by the survey. Not a gate D blocker — they carry no ledger effect — but unresolved. |
| RECURRING / REVALUATION / CONSOLIDATION writers | Must retire, or the module must gain the kinds, before gate E. Not gate D's problem; recorded so it is not discovered at gate E. |
