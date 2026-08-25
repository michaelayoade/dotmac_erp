# Accounting backfill survey — 2026-08-21

Measured facts about Dotmac Technologies' general ledger, taken before the
Accounting backfill was designed. Facts, not mandates: the plan that rests on
them is `docs/architecture/accounting-gate-d-plan.md`.

**Planning use superseded 2026-08-24.** ADR-0003 selects a governed-opening
clean installation and forbids historical journal replay. This survey remains
evidence about the legacy system; it is no longer an import scope or cutover
baseline.

`scripts/accounting_backfill_survey.sql` is the exact query that produced this.
Re-running it may support legacy forensics, but clean-instance acceptance uses
the approved opening pack and fresh-database rehearsal instead.

## How it was taken

Read-only against **`dotmac_erp_standby`** on `db-primary` — a hot standby
(`pg_is_in_recovery() = t`, ~23s replay lag), not the primary. A replica was
chosen deliberately: PostgreSQL refuses writes on a standby, which is a stronger
guarantee than "every statement is a SELECT", because it does not depend on the
file staying correct or on the operator reading it.

Nothing was written. No production application host was touched.

## Scope

One organization: **Dotmac Technologies Ltd**,
`00000000-0000-0000-0000-000000000001`. Functional and presentation currency
NGN.

| | count |
| --- | ---: |
| account categories | 19 |
| accounts | 332 |
| fiscal years | 12 |
| fiscal periods | 24 |
| journal entries | 206,071 |
| journal entry lines | 448,133 |
| **posted ledger lines** | **416,316** |
| posting batches | 203,878 |

Ledger spans **2025-01-01 → 2026-08-21** — about twenty months.

Trial balance: debit `9,614,004,099.566905`, credit `9,614,004,099.566908`.
The −0.000003 difference is fully explained by three journals (below).

`posting_batches` ≈ one per posted journal, confirming ERP's batch is a
per-posting envelope rather than a grouping of many journals. That is the
evidence behind `gl.posting_batch` being classified as
"ends with its writer" in the writer ledger.

## What is in scope for a backfill

Journals split cleanly by whether they carry posted ledger lines:

| ERP status | with posted lines | without |
| --- | ---: | ---: |
| POSTED | 187,982 | 0 |
| REVERSED | 2,197 | 0 |
| APPROVED | 0 | 14,263 |
| VOID | 0 | 1,253 |
| DRAFT | 0 | 375 |
| SUBMITTED | 0 | 1 |

**190,179 journals of 206,071 carry ledger effect.** The split is total — no
status mixes the two — which is what lets the backfill scope be stated as a
property of the data rather than as a policy: *backfill exactly the journals
that carry posted ledger lines.*

## Vocabulary coverage

### Journal kind — all four unmapped ERP values are ABSENT

ERP `JournalType` has nine values; module `JournalKind` has five.

| ERP `JournalType` | journals | module counterpart |
| --- | ---: | --- |
| STANDARD | 129,715 | STANDARD |
| ADJUSTMENT | 74,158 | ADJUSTMENT |
| REVERSAL | 2,197 | REVERSAL |
| OPENING | 1 | OPENING |
| CLOSING | 0 | CLOSING |
| RECURRING | **0** | *none* |
| INTERCOMPANY | **0** | *none* |
| REVALUATION | **0** | *none* |
| CONSOLIDATION | **0** | *none* |

**The backfill needs no module change.** But the ERP code paths that write three
of the four exist and are live:

```
app/services/finance/gl/fx_revaluation.py:850,885   JournalType.REVALUATION
app/services/finance/automation/recurring.py:916    JournalType.RECURRING
app/services/finance/cons/cons_posting_adapter.py   JournalType.CONSOLIDATION (×3)
```

So this is a **gate E/F constraint, not a gate D one**: before dual-write or
cutover, either those paths retire or `dotmac-accounting` gains the kinds. Until
then the mapping must refuse all four loudly rather than default them.

### Journal status — the gap carries no ledger effect

Module `JournalStatus` has four (DRAFT, POSTED, REVERSED, VOID); ERP has six.
The two extra — SUBMITTED and APPROVED — appear on **zero** journals with posted
lines (table above), so they never need a module representation.

### Account classification — fully covered

| IFRS category | account type | accounts |
| --- | --- | ---: |
| ASSETS | CONTROL | 47 |
| ASSETS | POSTING | 201 |
| LIABILITIES | POSTING | 18 |
| LIABILITIES | STATISTICAL | 1 |
| EQUITY | POSTING | 4 |
| REVENUE | POSTING | 3 |
| EXPENSES | POSTING | 58 |

Five IFRS categories and three account types, every one mapped. No
`OTHER_COMPREHENSIVE_INCOME` in use.

### Period status — LOCKED is never reached

| fiscal year | status | periods | reopened at least once |
| --- | --- | ---: | ---: |
| FY2025 | SOFT_CLOSED | 12 | 11 |
| FY2026 | OPEN | 12 | 0 |

**No `HARD_CLOSED` anywhere**, so the ERP→module `LOCKED` mapping has zero
instances and `lock_period` is never exercised by this backfill. The replay is
open → post → soft-close.

Eleven of twelve FY2025 periods were reopened at least once. ERP keeps a
`reopen_count`; the module keeps an event stream. That history cannot be
reconstructed — only the current status can be replayed, which is the accepted
loss already recorded in `app/services/finance/gl/accounting_backfill.py`.

Ten of the twelve fiscal years hold no periods at all.

## Four defects that block the backfill

Small, and all tractable. **All four are gate D execution blockers** (ruled
2026-08-21): a defect the backfill merely tolerates survives into the module's
permanent ledger, where it is far more expensive to reason about than it is in
ERP today. Finance corrects and adjudicates them through ordinary reviewed ERP
process, and this survey is re-run afterwards to prove zero unresolved balance
and reversal defects.

1. **Three unbalanced journals.** Each off by exactly `-0.000001` — one
   micro-unit at the `NUMERIC(20,6)` scale limit. All POSTED, all dated
   2026-04-18, all `135375.000000` debit vs `135375.000001` credit:
   `JE202604-40818`, `JE202604-40653`, `JE202604-42111`. They fully account for
   the ledger's `-0.000003` trial-balance difference. The module enforces
   balanced posting, so Finance corrects these in ERP first.
2. **One journal flagged `is_reversal` with no `reversed_journal_id`.** The
   other 2,197 reversal pairs are symmetric: no dangling targets, no journal
   reversed twice. Finance adjudicates: link, unflag, or explicitly quarantine.
   **It must not be hidden by excluding it in the importer** — that would make
   the run green while leaving the ledger's reversal structure wrong, and would
   move an accounting decision into a migration script.
3. **Six journals with no provenance at all** — no `source_module`,
   `source_document_type` or `source_document_id`.
4. **Source module casing is inconsistent.** `AR`/`ar`, `EXPENSE`/`expense`,
   `GL`/`gl`, `AP`/`ap`, `BANKING`/`banking` all appear as distinct values for
   the same logical module.

## Simplifications

- **Single currency.** Zero foreign-currency journals; one distinct currency
  across 206,071 journals. The FX and original-amount dimension of the backfill
  collapses — but the loader must REFUSE a non-functional currency rather than
  assume its absence, because absence today is not a constraint tomorrow.
- **Dimensions are effectively unused.**

  | dimension | master rows | used on lines | used but unknown |
  | --- | ---: | ---: | ---: |
  | business_unit | 0 | 0 | 0 |
  | cost_center | 0 | 0 | 0 |
  | project | 1,589 | 11 | 0 |
  | segment | 0 | 0 | 0 |

- **Referential health is perfect.** Zero orphan posted lines, zero lines
  without an account, zero categories with a missing parent, zero duplicate or
  blank journal numbers, zero posted journals with no lines.

## Size distribution

Posted lines per period. The FY2026 P4 spike is the April 2026 VAT and
opening-balance repair wave.

| year | period | status | posted lines | journals |
| --- | ---: | --- | ---: | ---: |
| FY2025 | 1–12 | SOFT_CLOSED | 14,782 – 22,715 | 6,539 – 10,319 |
| FY2026 | 1 | OPEN | 26,737 | 12,034 |
| FY2026 | 2 | OPEN | 15,736 | 7,795 |
| FY2026 | 3 | OPEN | 18,651 | 9,019 |
| FY2026 | **4** | OPEN | **93,236** | **44,518** |
| FY2026 | 5 | OPEN | 11,525 | 5,005 |
| FY2026 | 6 | OPEN | 9,633 | 3,952 |
| FY2026 | 7 | OPEN | 12,862 | 5,372 |
| FY2026 | 8 | OPEN | 880 | 434 |
| FY2026 | 9–12 | OPEN | 0 | 0 |

## The APPROVED backlog — now a tracked Finance remediation track

**14,263 journals sit APPROVED-but-not-posted, spanning 2026-01-15 to
2026-07-22.** They carry no ledger effect, so they are outside gate D's backfill
scope — but they are not left unexamined.

Ruled 2026-08-21: a **separate Finance remediation track starts now**, in
parallel with gate D. It is **not one homogeneous backlog** — at least **2,038
belong to the already-known stranded repost cohort**. The track classifies by
producer and by business-document state *before* anything is posted, voided or
quarantined; deciding disposition ahead of classification would be guessing at
scale.

`app/services/finance/gl/stranded_fee_posting.py` and `app/tasks/gl_posting.py`
are the first ERP surfaces to examine for the producer classification, alongside
the `source_module` / `source_document_type` breakdown above.

**This creates a gate G blocking condition**: final legacy-writer retirement
requires an explicit disposition for every remaining APPROVED journal, because
retiring ERP's GL writers while any remain undisposed would strand live workflow
state inside a retired system. Recorded in
`docs/architecture/accounting-adoption-boundary.md`.
