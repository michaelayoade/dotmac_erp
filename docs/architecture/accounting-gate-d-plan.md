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

### D1 — clear the defects. ALL SURVEY DEFECTS ARE EXECUTION BLOCKERS

An earlier draft called only defect 1 a hard blocker. **The ruling makes all
four blockers**, and the reason is worth stating: a defect that the backfill
merely tolerates is a defect that survives into the module's permanent ledger,
where it is far more expensive to reason about than it is in ERP today.

None is a code change. All are ERP-side data or adjudication, and all are for
**Finance, through ordinary reviewed ERP processes** — not folded into an
adoption commit by whoever is running the migration.

| # | Defect | Disposition |
| --- | --- | --- |
| 1 | Three journals unbalanced by `-0.000001` (`JE202604-40818`, `JE202604-40653`, `JE202604-42111`, all 2026-04-18) | **Finance corrects in ERP.** The module enforces balanced posting and will refuse them. They fully explain the ledger's `-0.000003` trial-balance difference, so correcting them also makes the acceptance baseline exact. |
| 2 | One journal flagged `is_reversal` with no `reversed_journal_id` | **Finance adjudicates** — link it, unflag it, or explicitly quarantine it. A reversal with no original cannot be replayed through `reverse_journal`. |
| 3 | Six journals with no provenance at all | Recorded. Provenance rides in `description`/`reference`, not in identity, so this does not block — but it is resolved as part of the same review rather than rediscovered later. |
| 4 | Source-module casing inconsistent (`AR`/`ar`, …) | Normalise **on read**, in the extractor. Do not rewrite ERP data for a migration's convenience. |

**Do not hide defect 2 through importer exclusion.** Skipping the malformed
reversal in the loader would make the run green while leaving the ledger's
reversal structure quietly wrong, and would move an accounting decision from
Finance into a migration script. The only acceptable exclusion is one **Finance
explicitly quarantines**, recorded as such.

#### Forensics — see the Finance correction memorandum (PARTIALLY COMPLETE)

`docs/inventories/accounting-finance-correction-memorandum.md` carries the
read-only evidence. It is **not complete**, but one part of it now is.

**Done.** The ar/INVOICE candidate-population detector has run against an
isolated restored database (`scripts/accounting_stranded_repost_detector.sql`;
results in Appendix A). 2,033 of 2,039 candidates have a proven ledger chain,
the population is 98.6% CREDIT NOTES rather than invoices, the one-row delta is
explained as a duplicate of an already-posted journal, and the cohort splits
into **four separate dispositions** that must not be approved as one net batch
(Appendix A §A5). Appendix A is the only operative instruction for that
population.

**Also done, and not a data repair.** The recurrence fix for defect 1 — the
residue allocator, exact persisted-precision balance enforcement at all four
boundaries, and removal of the `Decimal("0.01")` backlog tolerance — merged as
**ERP PR #336 / `0e40d799`** on 2026-08-22. It stops new occurrences; it repairs
nothing historical.

**A fifth Gate D population was found during Gate G forensics.** A journal-keyed
backfill namespace produced 429 currently effective POSTED bank-fee journals
over 39 statement-line identities, with ₦7,764.68 gross debit. ERP PRs #337–#339
merge the single owner, database at-most-once boundary, exact live-effect
verification and bulk-mutator gate; production was observed healthy on their
merge revision `sha-0dc07e4` on 2026-08-23. They repair no historical data.

The first duplicate detector proved the second namespace, current liveness,
target identities and aggregate amount; it did not compare each target's full
immutable ledger effect with its line-keyed canonical. The revised
`scripts/accounting_bank_fee_duplicate_postings.sql` fails closed on unknown
keys, non-single canonicals and any account/direction/amount/currency/rate/date/
period/source/dimension mismatch, then emits a one-to-one schedule digest only
if every target passes. The 2026-08-23 run at commit `ed83989b`, sha256
`2988835df245f1e1ed67faf4c6e855e95324de93ae2f6b003fe0a552f956586c`,
**refused all 429 targets**: header and unkeyed monetary shapes match, but none
of the ledger account sets matches its canonical. No schedule or digest was
emitted by the exact-duplicate detector.

The separate `scripts/accounting_bank_fee_wrong_account_correction.sql` now
implements the purpose-built correction schedule without weakening that
refusal. On 2026-08-23 it ran twice against one isolated repeatable-read standby
copy and admitted exactly the 352 Paystack and 77 Zenith substitutions. It
proved 858/858 journal-line-to-ledger parity, bound the 2025 target / March 2026
canonical / August 2026 reversal timing, simulated 429 balanced linked
reversals with every target/account all-time net zero, and emitted the same
429-row SHA-256 plan digest twice:
`dbeab5dafe0d27bafa834fde43c35ae9f36996ba5a332623784619cddcbd9148`.
No production accounting row changed. Exact-plan Finance approval, a guarded
operator and execution proof remain Gate D work.

**Still outstanding:** exact-plan approval and execution of the 429
wrong-account corrections, the
§1a audited-opening bridge, the composite-reversal treatment, Finance's approval
of the three micro repairs, the per-document proof 6 reconciliation for the
ar/INVOICE cohort, the reporting and tax assessment, approver and operator.

What it establishes:

- **Defect 2 is not a labelling problem.** `REV-SYNC-OB-001` and six later
  journals reverse the same six originals twice. Relative to a single-reversal
  position the effect is duplicated by **₦1,619,218.75** on accounts 1400 and
  3100, with no compensating correction.

  **Whether that duplication is a misstatement is conditional** on whether the
  composite reversal was itself economically correct — which requires the signed
  2024 audited trial balance and the AR/AP/WHT opening schedules, none of which
  has been obtained. If the composite improperly removed genuine AR/AP opening
  balances, the required correction is broader than reversing those six.

- **The duplication is bounded to 1400 and 3100.** A complete account-by-account
  matrix found no duplicate reversal on 1211, 1220, 1420, 2000 or 2110. The
  ₦68,308,470.18 on 1420 is a reversal performed once, as intended — not a
  misstatement. An earlier draft inferred otherwise from the composite amount;
  that inference is disproven. It is **not** a clean bill of health for WHT: the
  opening-balance reconciliation stays open.

- **Defect 1 has TWO code causes.** The AR allocator rounds each revenue line
  independently, and `LedgerPostingService` admits an imbalance of *exactly* one
  micro-unit because it rejects only on `abs(debit - credit) > Decimal("0.000001")`.
  A third, larger instance sits in `posting_backlog.py` at `Decimal("0.01")`.
  Repairing the rows fixes none of them.

  The allocation policy is: round normally, apply the residue to the **largest
  absolute revenue line**, break ties by stable line number. For these journals
  that is line 2.

Decisions, approver and operator are Finance's. Gate D stays blocked on content
completeness, not on CI.

#### Exit criterion

Re-run `scripts/accounting_backfill_survey.sql` after the repairs and require
**zero unresolved balance defects and zero unresolved reversal defects**. Also
re-run `scripts/accounting_bank_fee_duplicate_postings.sql`: before execution
its count/gross/digest must equal the Finance-approved schedule; after the
one-to-one corrections it must report zero live journal-keyed reversal targets.
The database detectors are the gate, not an assurance that repairs were
attempted: they read current effects rather than the change log.

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

#### SourceIdentity — RULED 2026-08-21

The module's journal uniqueness is
`(tenant, source_owner, source_document_kind, source_document_id, source_version)`.
The backfill sets:

```
owner         = "erp.gl"
document_kind = "journal_entry"
document_id   = <ERP journal_entry UUID>
version       = "1"
fingerprint   = <canonical digest of the journal and its ordered lines>
```

**Why the GL journal and not the source document it came from.** If a
backfilled journal claimed the source document's identity, then after cutover a
*new* journal legitimately arising from that same document could never be
created — the unique constraint would refuse it. That is exactly backwards: the
point of the cutover is that those documents go on producing accounting
consequences.

The survey supports this independently. `source_document_id` is present on only
101,465 of 206,071 journals, and the module names that do exist are
case-inconsistent (`AR`/`ar`, `EXPENSE`/`expense`). Keying on them would bake a
data-quality defect into permanent identity.

The source document still travels, in `description`/`reference`, where it is
provenance rather than identity.

**Why `version = "1"` and NOT a run identifier.** An earlier draft of this plan
proposed `erp-backfill.v1`, with a corrected re-run using `.v2` as "a
deliberate, reviewed act producing new rows". **That was wrong, and the ruling
corrects it.**

Putting the run identifier in `source_version` makes the run part of the
identity, so a second run under `.v2` is a DIFFERENT identity for the same ERP
journal. Accounting's uniqueness boundary would not refuse it — it would create
a second journal, and posting it would **duplicate ledger effect**. The very
mechanism meant to make a correction reviewable would instead route around the
guard that exists to prevent double-posting.

These are immutable legacy journals. Their identity is the ERP journal, once, so
the version is `"1"` and stays `"1"`. The correct behaviour for the same
identity carrying changed content is to **fail closed** — the fingerprint
differs, the module refuses, and a human looks at why.

A correction after data has landed is therefore not a re-identified re-import.
It is what the ledger already provides for: a **linked reversal or correcting
journal**, which leaves both the original and the correction visible and
auditable, exactly as any other post-hoc accounting change would.

The backfill run identifier belongs in **import and rehearsal evidence** — the
run's own record of what it did — not in the identity of the thing it imported.

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

## The APPROVED backlog — a separate Finance remediation track

**14,263 journals sit APPROVED-but-not-posted**, spanning 2026-01-15 to
2026-07-22. They carry no ledger effect, so they are outside gate D's backfill
scope and gate D proceeds without waiting for them.

That is not the same as ignoring them. A **separate Finance remediation track
starts now, in parallel.**

### It is not one homogeneous backlog

At least **2,038 belong to the already-known stranded repost cohort** — a
distinct population with a distinct cause. Treating all 14,263 as "a backlog to
post" would mass-post journals whose business documents may no longer justify
them.

So the track classifies before it acts:

1. **By producer.** Which code path created each journal. ERP's stranded-fee and
   GL posting surfaces (`app/services/finance/gl/stranded_fee_posting.py`,
   `app/tasks/gl_posting.py`) are the first places to look, alongside the
   `source_module` / `source_document_type` breakdown the survey already
   produced.
2. **By business-document state.** Whether the underlying invoice, payment or
   claim still supports posting, has been superseded, or has been voided
   downstream.

**Only then** is each classified journal disposed of: post, void, or quarantine.
Deciding the disposition before the classification would be guessing at scale.

### The retirement gate this creates

Gate D may proceed while the classification runs, but the constraint it creates
lands later and must not be lost:

> **Final legacy-writer retirement requires an explicit disposition for every
> remaining APPROVED journal.**

Retiring ERP's GL writers while APPROVED journals remain undisposed would strand
live workflow state inside a retired system — work that someone approved, that
was never posted, and that no longer has a system able to post it. That is
recorded against gate G in `accounting-adoption-boundary.md`, not left to
memory.

## Open decisions

| Decision | State |
| --- | --- |
| SourceIdentity keyed on the GL journal, `version = "1"` | **RULED 2026-08-21.** See above — the run identifier belongs in import/rehearsal evidence, not identity. |
| Gate D stops before dual write | **Confirmed.** Dual write is gate E. |
| All four data defects are execution blockers | **RULED 2026-08-21.** Finance corrects and adjudicates through ordinary reviewed ERP process; the survey re-run is the gate. |
| The 429 journal-keyed POSTED bank-fee effects | **Correction designed and rehearsed; execution pending.** The exact-duplicate comparison still refuses all 429 because their ledger accounts differ. The separate wrong-account plan admits only the measured `352` (`Paystack OPEX - DT`→`1211`) and `77` (`Zenith USD - DT`→`1207`) substitutions, proves 858/858 reversal-source parity and explicitly binds the different posting periods. Both isolated runs produced SHA-256 plan digest `dbeab5dafe0d27bafa834fde43c35ae9f36996ba5a332623784619cddcbd9148`. No production row changed; exact-plan approval, guarded execution and post-write proof remain required. |
| The 14,263 APPROVED-but-unposted journals | **Separate Finance remediation track, started now.** Classify by producer and business-document state before disposing. Gate D proceeds in parallel; gate G blocks on complete disposition. |
| RECURRING / REVALUATION / CONSOLIDATION writers | Must retire, or the module must gain the kinds, before gate E. Not gate D's problem; recorded so it is not discovered at gate E. |
