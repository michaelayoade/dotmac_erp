# Finance correction memorandum — Accounting gate D defects

**Status: DRAFT — the ar/INVOICE cohort proof is COMPLETE at the ledger level
(Appendix A). Still outstanding: the §1a audited-opening bridge, the composite
reversal, the three micro repairs, the per-document proof 6 reconciliation, the
reporting/tax assessment, and every Finance decision, approver and operator.**

**One operative instruction governs the ar/INVOICE population: Appendix A §A5.**
Earlier text in §3 and §4 is preserved as the historical hypothesis and is
superseded wherever the two disagree.

Decisions, approver and operator are Finance's and are not filled in.
Engineering produced the forensics and must not resolve any item here. Gate D
execution stays blocked on content completeness, not on CI.

Evidence taken read-only 2026-08-21/22 against `dotmac_erp_standby` (hot
standby, writes physically impossible). Reproducible via
`scripts/accounting_backfill_survey.sql` plus the queries quoted below. Nothing
was written to any production system.

Organization: **Dotmac Technologies Ltd**,
`00000000-0000-0000-0000-000000000001`, NGN.

---

## 1. Headline: the malformed reversal is not a metadata defect

The gate D defect list described "one journal flagged `is_reversal` with no
`reversed_journal_id`" — a labelling problem. **The evidence does not support
that reading.** It is an uncorrected double-reversal of **₦1,619,218.75** on
Trade Receivables and Retained Earnings, still in effect today.

It is materially larger than everything else on the gate D defect list, and it
was found only because the instruction was to adjudicate from source evidence
rather than infer from the `is_reversal` flag.

**Bounded, not open-ended.** The complete matrix below shows the duplication
occurs on accounts 1400 and 3100 only. The larger composite amounts on other
accounts — including ₦68,308,470.18 on 1420 — are reversals performed once, as
intended, and are **not** misstatements.

### What happened

**2026-03-12** — `REV-SYNC-OB-001` (`c0000001-0000-0000-0000-000000000001`, a
hand-assigned UUID, so operator-created) posts as an ADJUSTMENT with
`is_reversal = true` and no `reversed_journal_id`. Its description:

> "Reverse 16 ERPNext-synced opening balance journals (JE-2025-00017 thru 00080)
> that duplicate balances already in OB-000001. OB-000001 (2024 audited TB) is
> authoritative."

It credits account **1400 Trade Receivables by ₦2,841,816.25**. That figure is
**exactly** the sum of the 1400 debits in JE-2025-00026 through JE-2025-00035:

| original | Dr 1400 | | original | Dr 1400 |
| --- | ---: | --- | --- | ---: |
| JE-2025-00026 | 1,213,406.25 | | JE-2025-00031 | 59,125.00 |
| JE-2025-00027 | 311,750.00 | | JE-2025-00032 | 29,562.50 |
| JE-2025-00028 | 134,375.00 | | JE-2025-00033 | 48,375.00 |
| JE-2025-00029 | 722,937.50 | | JE-2025-00034 | 93,955.00 |
| JE-2025-00030 | 134,375.00 | | JE-2025-00035 | 93,955.00 |
| | | | **total** | **2,841,816.25** |

**2026-04-19** — thirty-eight days later, six journals `JE202604-43275` through
`JE202604-43280` reverse six of those same originals *individually*:

| reversing journal | original | Dr 1400 reversed |
| --- | --- | ---: |
| JE202604-43275 | JE-2025-00026 | 1,213,406.25 |
| JE202604-43276 | JE-2025-00028 | 134,375.00 |
| JE202604-43277 | JE-2025-00030 | 134,375.00 |
| JE202604-43278 | JE-2025-00031 | 59,125.00 |
| JE202604-43279 | JE-2025-00032 | 29,562.50 |
| JE202604-43280 | JE-2025-00033 | 48,375.00 |
| | **total** | **1,619,218.75** |

Those six originals now carry status `REVERSED`. `REV-SYNC-OB-001` is still
`POSTED` and has never itself been reversed.

### Effect — what is unconditional, and what is not

The six originals were `Dr 1400 / Cr 3100`. They have been reversed twice.

**Unconditional:** relative to a **single-reversal position**, the effect is
duplicated by ₦1,619,218.75 — 1400 credited twice, 3100 debited twice. No
compensating correction exists; a search for any journal after 2026-04-19
touching 1400 for ~1,619,218.75 returns zero rows.

**Conditional, and NOT yet established:** whether that duplication is a
*misstatement of the correct position*. That depends on whether
`REV-SYNC-OB-001` was itself economically correct — which the signed audited
evidence has not yet been obtained to confirm (§1a).

- If the composite was intended, the correct position is one reversal, and
  1400 is understated / 3100 misstated by ₦1,619,218.75.
- If the composite improperly removed genuine AR/AP opening balances, the
  required correction is **broader** than these six journals, and reversing them
  alone would be wrong.

An earlier draft asserted "Trade Receivables understated by ₦1,619,218.75"
unconditionally. **That was premature.** The duplicate effect is proven; the
misstatement is not, until the bridge in §1a resolves it.

### Complete account-by-account matrix

`REV-SYNC-OB-001`'s seven line descriptions are the authoritative mapping of
what it claims to reverse. Cross-referenced against **every** individual
reversal of any `JE-2025-000xx` original:

| acct | REV-SYNC-OB-001 line | originals it names | individual reversals of those originals | **proven duplication** |
| --- | ---: | --- | --- | ---: |
| 1211 | Cr 297,500.00 | JE-2025-00017 | none | **0.00** |
| 1220 | Cr 3,245.44 | JE-2025-00018 | none | **0.00** |
| 1400 | Cr 2,841,816.25 | JE-2025-00026…00035 (10) | 6 of the 10, Cr 1,619,218.75 | **1,619,218.75** |
| 1420 | Cr 68,308,470.18 | JE-2025-00080 | none | **0.00** |
| 2000 | Dr 3,040,000.00 | JE-2025-00075, 00077 | none | **0.00** |
| 2110 | Dr 14,536,448.13 | JE-2025-00079 | none | **0.00** |
| 3100 | Dr 53,874,583.74 | net RE of the 16 | 6, Dr 1,619,218.75 | **1,619,218.75** |

The reversal inventory is complete: **the only individual reversals of any
`JE-2025` original in the entire ledger are the six `JE202604-43275…43280`, and
they touch only 1400 and 3100.**

### The ₦68.3M WHT inference is DISPROVEN

An earlier draft of this memorandum said that if the double-reversal pattern
also touched account 1420, "the exposure is forty times what I found". **That
was wrong, and this matrix disproves it.**

₦68,308,470.18 is the *composite reversal amount* on 1420 — the size of the
opening balance being reversed once, as intended. It is not a misstatement.
**Only the overlap with a later duplicate reversal is a misstatement, and on
1420 that overlap is zero.**

The proven duplication across all seven accounts is bounded to **₦1,619,218.75**,
on 1400 and 3100 only.

### Expected versus current position

| acct | expected after exactly one intended reversal | current | proven variance |
| --- | ---: | ---: | ---: |
| 1400 | sync OB removed once | removed twice | **understated 1,619,218.75** |
| 3100 | net RE impact removed once | removed twice | **misstated 1,619,218.75** |
| 1211, 1220, 1420, 2000, 2110 | removed once | removed once | **nil** |

### Reconciliation to OB-000001 (claimed 2024 audited TB authority)

| acct | OB-000001 | REV-SYNC-OB-001 reverses | observation |
| --- | ---: | ---: | --- |
| 1211 | Dr 297,500.00 (+ Dr 40,615.65) | Cr 297,500.00 | ties exactly |
| 1220 | Dr 3,245.44 | Cr 3,245.44 | ties exactly |
| 1420 | Dr 68,308,470.02 | Cr 68,308,470.18 | **differs by ₦0.16** |
| 2110 | Cr 3,136,499.51 | Dr 14,536,448.13 | different figures; not a duplication |
| 3100 | net Dr 48,222,886.90 | Dr 53,874,583.74 | differs by 5,651,696.84 |
| **1400** | **no line** | Cr 2,841,816.25 | **OB-000001 does not replace it** |
| **2000** | **no line** | Dr 3,040,000.00 | **OB-000001 does not replace it** |

Three open reconciliation questions for Finance — **stated as questions, not as
findings**:

1. The sync opening balance for WHT Receivable and the audited figure differ by
   **₦0.16**. Reversing the sync version and retaining OB-000001's leaves the
   audited position, so this is not a duplication — but the two sources
   disagreed, and someone should say why.
2. **OB-000001 carries no 1400 or 2000 line.** The composite removed
   ₦2,841,816.25 of customer opening balances and ₦3,040,000 of supplier opening
   balances with no GL-level replacement in the journal claimed as authoritative.
   Whether those belong in AR/AP subledger opening documents instead is a
   Finance determination.
3. The 3100 figures differ by ₦5,651,696.84, which is consistent with
   OB-000001 and the sync set covering different populations — but it has not
   been traced.

### §1a. Finance evidence package REQUIRED — the source, not the description

Nothing above may be actioned on the strength of `REV-SYNC-OB-001`'s own
description. That description is the *assertion under test*. Obtain the actual
source package:

- signed **2024 audited trial balance**;
- **OB-000001 supporting schedule**;
- **customer-level AR opening ageing**;
- **supplier-level AP opening ageing**;
- **WHT receivable / payable opening schedules**;
- **retained-earnings roll-forward**.

Then build this bridge, end to end:

```
signed audited closing TB
  → OB-000001
    → ERPNext-sync opening journals
      → REV-SYNC-OB-001
        → later individual reversals (JE202604-43275…43280)
          → current GL and AR/AP subledgers
```

#### Question 2 is the priority

OB-000001 carries **no 1400 and no 2000 line**. If the AR and AP opening
schedules do contain the ₦2,841,816.25 and ₦3,040,000 balances, then **identify
the GL control-account entry that represents them**.

> **A subledger balance without its GL control counterpart is not a
> replacement.**

That is the question that decides whether the composite was economically correct
or improperly removed genuine opening balances.

#### The retained-earnings difference must reconcile, not plug

The ₦5,651,696.84 difference on 3100 must reconcile **through the same bridge**.
It must not become a balancing plug: a plug would conceal exactly the kind of
gap the bridge exists to find.

### Indicated treatment, CONDITIONAL on the signed evidence

**If the signed evidence confirms `REV-SYNC-OB-001` was intended:**

1. **Preserve it as a plain adjustment**, not a 16-to-1 reversal.
2. **Reverse `JE202604-43275…43280` individually.**
3. That debits 1400 and credits 3100 by **₦1,619,218.75**.
4. **Leave 1420 and 2110 untouched**, apart from documenting the ₦0.16 source
   difference on 1420.
5. **Preserve every historical journal.** Add correction chains; never delete or
   rewrite economic history.

**If the signed evidence shows the composite improperly removed genuine AR/AP
opening balances — STOP.** The required correction is broader than reversing
those six journals, and reversing them alone would entrench the error rather
than fix it.

### Accounting can represent this

An earlier draft claimed a 16-to-1 composite reversal "cannot be represented as
a reversal in the module at all" and framed that as a constraint on Finance's
options. **That framing was wrong.** The treatment above fits the module
directly: the composite lands as a plain **adjustment** journal, and each
correction is a clean **one-to-one reversal of the duplicate reversal journal**.
No representability problem arises, and no option is foreclosed.

### Finance must still determine

1. Whether OB-000001 genuinely supports the composite as intended — the
   reconciliation questions above.
2. Whether the ₦1,619,218.75 correction is a current-period correction or a
   prior-period restatement (§6).
3. The three reconciliation questions above.

---

## 2. The three unbalanced journals

`JE202604-40653`, `JE202604-40818`, `JE202604-42111` — each out of balance by
exactly **−0.000001** (credits exceed debits by one micro-unit).

All three are AR invoices to the same customer, **R.T. Communication Ltd**,
posted 2026-04-18 for invoices INV202602-147176, INV202603-145877 and
INV202604-148320. Identical structure and amounts — a recurring monthly bill.

### Which line is wrong

The debit and the VAT credits are exact. The revenue credits are not:

| line | account | amount | exact value |
| --- | --- | ---: | --- |
| 1 Dr | 1400 Trade Receivables | 135,375.000000 | exact |
| 2 Cr | 4000 Internet Revenue | 103,610.294118 | 1,761,375⁄17 |
| 3 Cr | 2120 VAT Payables | 7,875.000000 | exact |
| 4 Cr | 4000 Internet Revenue | 19,735.294118 | 335,500⁄17 |
| 5 Cr | 2120 VAT Payables | 1,500.000000 | exact |
| 6 Cr | 4000 Internet Revenue | 2,466.911765 | 83,875⁄34 |
| 7 Cr | 2120 VAT Payables | 187.500000 | exact |

The three revenue lines are non-terminating fractions (seventeenths). Their
**exact** sum is:

```
1,761,375/17 + 335,500/17 + 83,875/34  =  4,277,625/34  =  125,812.500000
```

Stored, each was rounded to six places and **each rounded up**:
`.294117647… → .294118` (twice) and `.911764705… → .911765`. The three round-ups
total 0.000001, so the stored revenue sums to **125,812.500001**.

**The defect is in the revenue allocation, not in VAT or the receivable.** The
credit side is overstated by exactly one micro-unit, and the value that should
be 125,812.500000 is stored as 125,812.500001.

### Indicated correction

Reduce **one** revenue line by 0.000001 so the allocation sums to its exact
value. Under the allocation policy below — residue to the **largest absolute
revenue line**, ties broken by stable line number — that is **line 2**, giving
`103,610.294117`.

The policy and the historical repair must agree. Do not document "line 2 absorbs
it" while implementing "the last line absorbs it".

Per instruction, **no balanced adjustment journal**: adding equal debit and
credit cannot remove an existing difference. No suspense or rounding account.
Header, lines, posted-ledger evidence, functional-currency amounts and dependent
projections update atomically, with before/after values, fingerprints, approver,
operator, rationale and work-item reference preserved in repair evidence.

### Root cause — TWO code defects, not one

An earlier draft named only the allocator. There are two, and the second is why
these journals were *accepted*:

**Defect 1a — the AR allocator rounds each revenue line independently.**
It never forces a line to absorb the allocation residue, so a split into
non-terminating fractions can leave the sum off by one or more micro-units.

**Defect 1b — the posting boundary permits an imbalance of exactly one
micro-unit.** `app/services/finance/gl/ledger_posting.py`:

```python
BALANCE_TOLERANCE = Decimal("0.000001")          # line 131
...
if abs(total_debit - total_credit) > LedgerPostingService.BALANCE_TOLERANCE:   # line 537
```

The comparison is `>`, not `>=`, so a difference of **exactly** 0.000001 passes.
At `NUMERIC(20,6)` one micro-unit is the smallest persisted amount, so this
tolerance admits the largest imbalance the storage can represent below a
hundredth of a kobo — and admits precisely the imbalance defect 1a produces.

The tolerance's own comment reads *"accounts for floating point"*. These are
exact `Decimal` values at a fixed scale; there is no floating-point error to
absorb. **The posting boundary should require exact equality at canonical
persisted precision.**

Defect 1a created the residue. Defect 1b let it into the ledger. Repairing the
rows fixes neither.

### Recommended allocation policy

1. Round each line normally.
2. Apply any balancing residue to the **largest absolute revenue line**.
3. Break ties by **stable line number**.

### Required engineering slice

Separate from this memorandum and separate from gate D:

- the **seventeenths regression canary** — this exact invoice shape;
- **deterministic residue allocation** per the policy above;
- **exact persisted-precision balance enforcement** at the posting boundary;
- a **sensitivity test proving 0.000001 is refused** — the current `>` passes it,
  so a test asserting refusal fails today and is the proof the fix landed;
- **review of the separate 0.01 approved-backlog tolerance** (below).

### The 0.01 backlog tolerance is a third, larger instance

`app/services/finance/gl/posting_backlog.py` — the service whose docstring is
*"Post APPROVED journal entries that never reached POSTED"*, i.e. the very path
that would post the 14,263 backlog:

```python
IMBALANCE_TOLERANCE = Decimal("0.01")   # line 44
def is_balanced(self) -> bool:
    return self.imbalance < IMBALANCE_TOLERANCE   # line 61
```

That is **10,000× the GL posting tolerance**, and its declaring comment says it
"mirrors the AR/AP payment tolerance". Payment dust is a *settlement* concept;
applying it to *journal balance* is a category error — a journal is either
balanced or it is not.

It is also **incompatible with `dotmac-accounting`'s exact-balance boundary**:
any backlog journal posted under this tolerance would be refused at backfill.
Review it in the same slice.

### Systemic reach

- **132 AR invoice journals** carry revenue lines with more than two decimal
  places — the same non-terminating split pattern. Three round in a direction
  that breaks balance; the rest do not.
- **28 journals** exist for this customer alone, 2026-01-14 to 2026-07-02.

Repairing three rows without the engineering slice leaves next month's invoice
free to reproduce the defect.

### Materiality

₦0.000003 across the three. Financially immaterial. The integrity defect is not:
every posted journal must balance independently, and the module refuses
unbalanced posting outright.

---

## 3. Population — the APPROVED backlog

14,263 journals are APPROVED but never posted, spanning 2026-01-15 to
2026-07-22. They carry **no ledger effect**.

**₦76,495,739.50 is gross unposted debit under review. It is not confirmed
exposure, and it is not a loss.** Some of it should be posted, some voided, some
quarantined; which is which is precisely what the classification determines. An
earlier draft called it "total exposure" — that was wrong.

| producer | document type | journals | gross unposted debit | share of count |
| --- | --- | ---: | ---: | ---: |
| banking | BANK_FEE | **12,117** | ₦11,813,979.50 | 85.0% |
| ar | INVOICE | **2,039** | ₦39,211,120.76 | 14.3% |
| ar | CUSTOMER_PAYMENT | 98 | ₦12,564,606.69 | 0.7% |
| banking | BANK_RECONCILIATION | 6 | ₦726,322.87 | — |
| expense | EXPENSE_REIMBURSEMENT | 2 | ₦31,000.00 | — |
| payroll | PAYROLL_ENTRY | 1 | ₦12,148,709.68 | — |
| | **total** | **14,263** | **₦76,495,739.50** | |

### `ar/INVOICE = 2,039` identifies CANDIDATES, not a proven cohort

> **HISTORICAL — SUPERSEDED BY APPENDIX A.** Kept because the caution below is
> what produced the answer. The detector has since run; Appendix A is the only
> operative text for this population.

The 2,039 are journals matching the stranded cohort's *producer*. They are not
yet proven stranded. **None may be called stranded, and no claim may be made
that their VAT effect is missing, until each passes the exact detector**:

> original → reversal → orphan → no later replacement

### The one-row delta (2,039 candidates vs 2,038 proven) is UNEXPLAINED

> **HISTORICAL — RESOLVED in Appendix A §A6.** Kept because the reasoning below
> about not assuming the two counts describe the same set is what produced the
> answer.

The prior exact investigation proved **2,038** cases. This memorandum counts
**2,039** candidates by producer. The single-row difference is not yet
explained, and there are two readings — one candidate fails the detector, or the
prior investigation's population differed. **It must be resolved by running the
detector, not by assuming the counts describe the same set.**

An attempt to run the detector against the standby was **cancelled by
PostgreSQL** (`canceling statement due to conflict with recovery`): the
correlated per-invoice subqueries are long-running, and replay must remove row
versions the query is reading.

**That is an operational finding in its own right: the per-invoice cohort proof
cannot be run on the standby.** It requires a restored database — which is
where §8 already places the rehearsal.

### Other cautions on this table

- **The single `payroll/PAYROLL_ENTRY` journal carries ₦12,148,709.68** — 16% of
  the gross figure in one record. It stays an **individual adjudication**, never
  part of a bulk pass.
- **The existing bank-fee posting mechanism selects by source, status and year.**
  That is a selection filter, not evidence. It is **not** grounds for treating
  all 12,117 fees as economically valid, and they are classified per §5 like
  everything else.

### Prioritisation by value and risk

Following the required order — bank/cash, tax, AR/AP control, revenue, then
remaining expense and balance-sheet:

| order | cohort | journals | gross debit | why |
| ---: | --- | ---: | ---: | --- |
| 1 | ar/CUSTOMER_PAYMENT | 98 | ₦12.56M | bank/cash and AR control |
| 2 | banking/BANK_FEE + BANK_RECONCILIATION | 12,123 | ₦12.54M | bank/cash; largest count |
| 3 | ar/INVOICE candidates | 2,039 | ₦39.21M | AR control, revenue, VAT — largest amount |
| 4 | payroll/PAYROLL_ENTRY | 1 | ₦12.15M | single high-value record |
| 5 | expense/EXPENSE_REIMBURSEMENT | 2 | ₦0.03M | residual |

## 4. Treatment of the AR/INVOICE candidate population (2,039)

> **HISTORICAL — SUPERSEDED BY APPENDIX A §A5, WHICH IS THE ONLY OPERATIVE
> INSTRUCTION FOR THIS POPULATION.** The objective stated below is wrong for
> 2,010 of the 2,039: they are CREDIT NOTES, and restoring a credit note reduces
> receivables and revenue rather than restoring income. The six per-document
> proofs listed below remain right in substance and are restated, corrected and
> extended in §A9. Do not work from this section.

Objective: restore each still-valid invoice's missing GL effect **exactly once**.

Per-invoice proof required before any orphan is posted:

1. The underlying invoice remains valid.
2. The original journal was reversed.
3. The posted reversal eliminated the original effect.
4. The APPROVED orphan exactly matches the expected replacement.
5. No later journal already restored the effect.
6. Customer balance, tax treatment, currency and accounting period remain
   correct.

Where all six hold, restoration is approved **through a purpose-built, tested
remediation path**. Whether that path safely posts the existing orphan or
regenerates it is an engineering determination, made after reproducing the exact
three-journal state (original → reversal → orphan) in tests. Where the invoice
was cancelled, superseded or already represented elsewhere, **void the orphan
instead**.

**The prior standalone production script must not be retried.**

**Not yet performed: the per-invoice proof itself.** This memorandum establishes
the *candidate* population by producer; it does not establish that any of the
2,039 is stranded. The evidence pass over all 2,039 is the next engineering
task, it must run on a **restored database** (the standby cancels it — §3), and
its output belongs in an appendix here.

Until it runs, the 2,039 are candidates, the one-row delta against the prior
2,038 is unexplained, and no VAT claim may be made about them.

---

## 5. Classification policy for the remaining 12,224

> **APPLIED — see Appendix B.** The Gate G detector has run this policy against
> all 12,224. Result: **no post candidates at all** — 12,217 are already-posted
> effect and 7 are undecidable from the ledger. The policy below is unchanged
> and still governs; Appendix B is its evidence.

| condition | disposition |
| --- | --- |
| Valid source document, required ledger effect absent | Post through the owning service |
| Equivalent effect already posted | Void as duplicate/superseded |
| Source document cancelled or abandoned | Void |
| Approval incomplete or unsupported by source evidence | Return to workflow, or void after review |
| Conflicting or insufficient evidence | Quarantine with named owner and deadline |

**Approval status alone is not sufficient evidence to post. Age alone is not
sufficient evidence to void.**

The 12,117 bank-fee journals are the bulk of the count and the least value per
record (≈₦975 average). They are still classified per the policy above; volume
is a reason to automate the evidence gathering, not to skip it.

---

## 6. Reporting and tax assessment — REQUIRED, NOT YET PERFORMED

For every material cohort, reconcile **by month and account**, not by grand
total — and, for the ar/INVOICE population, **by document type as well**, since
credit notes and standard invoices move the same two accounts in opposite
directions (Appendix A §A4).

**Default treatment: correct in the ORIGINATING period.** These are 2026 errors
in open periods, so the default is correction in the period each posting belongs
to, not a single later net adjustment. Any monthly management accounts or tax
returns already issued for those periods then need an impact assessment. IAS 8
requires material prior-period errors to be corrected retrospectively unless
impracticable, and current-period errors to be corrected before the financial
statements are authorised.
<https://www.ifrs.org/issued-standards/list-of-standards/ias-8-basis-of-preparation-of-financial-statements/> Then determine whether management accounts, VAT/WHT/CIT filings, customer
balances or other issued reports relied on the incorrect ledger state.

Specific exposures this memorandum raises:

- **Trade Receivables potentially understated by ₦1,619,218.75** since
  2026-04-19 — **CONDITIONAL, per §1.** What is unconditional is that six
  originals are reversed twice, so the effect is duplicated relative to a
  single-reversal position. Whether that duplication is a *misstatement* depends
  on whether `REV-SYNC-OB-001` was itself economically correct, which only the
  §1a audited-opening bridge can establish. Until it does, this is an exposure to
  assess, not a confirmed understatement — and it must not be quoted as one. If
  the bridge confirms the composite was correct, it affects customer balances, AR
  ageing and any balance sheet issued since.
- **VAT** — the detector has run (Appendix A). The orphan journals themselves
  carry **no VAT lines at all**: the whole cohort touches only 1400 and 4000.

  **That proves only that the orphans contain no VAT. It does NOT establish that
  zero VAT is correct** — least of all for the 29 STANDARD documents, whose
  source invoices may well be VAT-bearing. The five quarantined orphans are the
  proof of the danger: their *originals* carry 2125 VAT lines that the orphans
  drop. Every standard document's tax treatment must be reconciled to its source
  document and its filed period before any of it posts. VAT return periods
  covering 2026-03 to 2026-07 must be checked, not assumed either way.
- **WHT** — `REV-SYNC-OB-001` moves ₦68,308,470.18 on 1420 and ₦14,536,448.13 on
  2110. The matrix in §1 shows **no duplicate reversal** on either account, so
  neither carries the duplication defect.

  **That is not a clean bill of health.** "No duplicate reversal" is a narrower
  statement than "WHT is correct". The WHT opening-balance reconciliation
  remains **open** and is part of the §1a bridge: the ₦0.16 difference between
  the sync opening balance and OB-000001 on 1420 is unexplained, and the WHT
  receivable/payable opening schedules have not been obtained.

These are 2026 errors in currently open fiscal periods (FY2026 periods 1–12 are
all OPEN), so they should normally be corrected before the related annual
financial statements are authorised. Where already-issued reporting is affected,
document whether restatement or current-period correction is appropriate. IAS 8
distinguishes error correction from changes in estimates and requires material
prior-period errors to be corrected retrospectively —
<https://www.ifrs.org/issued-standards/list-of-standards/ias-8-basis-of-preparation-of-financial-statements/>.

The FY2025 comparative position is affected by the opening-balance reversals and
is **soft-closed, not locked** — reopening is possible, which makes the
restatement-versus-current-period decision a live choice rather than a
constraint.

---

## 7. Sign-off — TO BE COMPLETED BY FINANCE

**Split deliberately.** The 14,263-journal backlog must **not** silently become
a gate D blocker. Gate D needs the three balance repairs, the composite-reversal
treatment and a clean survey; the backlog classification belongs to gate G,
where it already blocks legacy-writer retirement.

### 7a. Gate D sign-off — blocks the backfill

| Item | Value |
| --- | --- |
| Three balance repairs (`JE202604-40653/40818/42111`) | *pending Finance decision* — §2 |
| Composite-reversal treatment (`REV-SYNC-OB-001` + the six duplicates) | *pending Finance decision, conditional on §1a evidence* |
| §1a audited-opening bridge complete | *not started* |
| Recurrence fix landed (allocator + posting boundary + backlog tolerance) | **DONE — ERP PR #336 merged to `main` as `0e40d799` on 2026-08-22T08:06Z**, `version:patch`, all checks green. Not a data repair. |
| Reporting assessment **for these corrections only** | *not yet performed* — §6 |
| Clean survey re-run: zero unresolved balance and reversal defects | *pending repairs* |
| Named approver | *pending* |
| Named operator | *pending* |

### 7b. Gate G sign-off — blocks legacy-writer retirement, NOT gate D

| Item | Value |
| --- | --- |
| Population and gross unposted debit under review | §3 — 14,263 journals, ₦76,495,739.50 |
| AR/INVOICE candidate detector run on a restored database (2,039) | **RUN — Appendix A.** 2,033 of 2,039 pass every ledger-decidable proof. Proof 6 remains a Finance determination and is NOT discharged. |
| One-row delta (2,039 candidates vs 2,038 previously proven) explained | **EXPLAINED — Appendix A6.** `JE202607-20448` has no reversed original; it duplicates already-posted `JE202607-9550`. Indicated disposition VOID, pending Finance. |
| Approved classification policy for the 14,263 | §5 — *pending approval* |
| Disposition recorded for **every** remaining APPROVED journal | **LEDGER EVIDENCE COMPLETE — Appendix B.** 12,217 of 12,224 evidenced (void recommendations); 7 quarantined as undecidable. Finance approval, V2 bank-statement sampling and owners for the 7 outstanding. |
| Payroll journal (₦12,148,709.68) individually adjudicated | *pending* — Appendix B4 finds its payroll entry already carries a POSTED journal with an identical effect. Evidence for the adjudication, not a substitute for it. |
| Reporting and tax assessment for the backlog cohorts | *not yet performed* |
| Generating defect behind 12,117 journals for 111 fee events identified | ***not started* — Appendix B7. Gate G cannot close on a cleanup whose cause is unknown, because the population can regrow.** |
| Pre-existing duplication in POSTED bank fees (149 journals on 111 events) | ***not started* — Appendix B5. A ledger defect today, untouched by any Gate G disposition.** |

## 8. Execution requirements

Rehearse every repair on a **restored database** first — never production, never
the standby.

Production execution requires:

- per-record verification;
- exact before/after trial balance;
- consistent subledger and control-account balances;
- all detectors returning zero, evidenced by re-running
  `scripts/accounting_backfill_survey.sql` and requiring **zero unresolved
  balance defects and zero unresolved reversal defects**.

The survey is the gate because it reads the database rather than the change log.

Gate D backfill execution remains blocked until every item in §7 is completed
and the re-run survey is clean.

---

# Appendix A — ar/INVOICE cohort: detector results

**This appendix is the ONLY operative instruction for the 2,039 ar/INVOICE
candidates.** §3 and §4 are retained as the historical hypothesis and are
superseded wherever they disagree with anything here.

## A0. Provenance — corroborative evidence, not an atomic snapshot

| item | value |
| --- | --- |
| Source | `dotmac_erp_standby` on `dotmac-db-primary` — hot standby, `pg_is_in_recovery() = t`, read-only. The production primary was **not** used, and no standby recovery setting was changed (`max_standby_streaming_delay` remains `30s`). |
| Replay LSN at export start | `BF/EDEA5778` (2026-08-22 10:25:04Z) |
| Replay LSN at export end | `BF/EDEA58F0` (2026-08-22 10:25:13Z) |
| Server version | PostgreSQL 16.4, source and target |
| Migration revisions (source heads) | `20260815_academy_learning_sync`, `fi_0001_stored_files`, `20260815_academy_course_projection`, `20260816_platform_owned_webhook_ssrf_policy`, `20260818_dotmac_sub_customer_metrics` |
| Detector commit | `35de9ced8add403038a3ad6085cf903d5d30b2e3` |
| Detector query hash (sha256) | `44c48998cf3d290bd4b28e06a1d5d35df93b29818d6e6afb74c67d6a6de4fa6e` |
| Target | `erp-forensic-20260822c` — ephemeral, `--network none`, no published port, no application attached. Destroyed after verification (**§A12**). |

**This copy spans an LSN RANGE and is NOT an atomic snapshot.** The four tables
were exported as separate statements. Row counts and the candidate population
were re-checked against the standby after loading and matched, and the cohort is
2026-03 to 2026-07 data that is not being written — but that makes the copy
**corroborative evidence of a state, not a point-in-time snapshot of it**.

**Consequence, and it is not a formality: every proposed disposition must be
revalidated against current authoritative state immediately before execution.**
Nothing in this appendix authorises acting on these journals as they were on
2026-08-22.

**What was copied.** Only the columns the proofs read, from four tables:
`gl.journal_entry` (14 columns), `gl.journal_entry_line` (4),
`ar.invoice` (8), `gl.account` (5). No description, no reference, no
customer, no payload, no credential. Nothing was written to disk on either host
— each table was streamed `COPY TO STDOUT` → `COPY FROM STDIN` directly
between containers.

## A1. Scope — one organization, and the predicate is tested

The detector is scoped to **Dotmac Technologies Ltd**,
`00000000-0000-0000-0000-000000000001`, which is the only organization in this
deployment today (206,075 of 206,075 journals). That is precisely why the
predicate is explicit: a query that is accidentally correct because there is one
tenant becomes silently wrong when there are two.

**Sensitivity proof (detector §0b) — and a defect that had to be fixed first.**

The first version of this proof was worthless, and the failure is worth stating
because it is the kind that looks like evidence. It inserted the canary *after*
`candidate` had already been materialized, then displayed two ad-hoc counts
beside it. `candidate` could not have seen the canary either way, so **removing
the organization predicate from `candidate` would have produced exactly the same
reassuring 2,040-vs-2,039 output.** It tested two queries written next to the
detector, not the detector.

Two things changed:

1. **The canary is inserted BEFORE `candidate` is built**, so the detector's own
   predicate is what decides whether it appears.
2. **The check is an assertion, not a display.** A `DO` block raises, and
   `ON_ERROR_STOP` aborts the run. A comment saying "MUST" and a `SELECT` are not
   an assertion — nothing reads them. A detector whose organization predicate has
   been removed or broken now cannot produce output at all.

Three conditions must all hold or the run aborts: the canary exists in the
unscoped source; the canary is **absent from `candidate`**; and the unscoped
count equals the detector population plus exactly one.

| measure | value |
| --- | ---: |
| Canary present in the unscoped source | 1 |
| Canary present in `candidate` | 0 |
| Unscoped count | 2,040 |
| Detector population | 2,039 |

Result: `NOTICE: sensitivity proof PASSED`.

**Negative control.** The same script with the organization predicate stripped
from `candidate` was run against the same restore. It aborted:

```
ERROR:  ORGANIZATION PREDICATE FAILED: the second-tenant canary reached
        `candidate` (1 rows). Every count in this run would be cross-tenant.
```

psql exited 3 and produced no results. The assertion is therefore proven to bite,
rather than merely proven to pass.

**None of this disturbs the 2,039 figure.** The predicate in the real query was
correct throughout; what was defective was the proof of it. The counts below were
identical before and after the fix.

All 2,039 candidate invoices are **NGN**; there is one transaction currency in
the cohort.

## A2. What the detector proves — and the words it does NOT use

The detector does **not** prove that an orphan is an "exact replacement" for its
original. It proves that the orphan has the **same functional-currency net
effect by account** as the original.

That signature says nothing about customer, cost centre, project or segment
dimensions, transaction currency, exchange rate, tax treatment, or line-level
structure. Two journals with different customers, different tax codes and a
different line breakdown can share one.

**This is measured, not theoretical (detector §12):**

| measure | value |
| --- | ---: |
| Journals carrying a net-effect signature | 205,285 |
| Distinct signatures among them | 13,566 |
| Signatures shared by 2 or more journals | 5,283 |
| Journals sharing their signature with another | 197,002 (96%) |
| Most journals on a single signature | 15,887 |
| Distinct signatures among the 2,038 candidate originals | 647 (max 270 on one) |

**An identical net effect is the norm in this ledger.** A signature match
between two *different* documents is therefore worth nothing as evidence. Proofs
3 and 4 are safe only because they compare journals on the **same document**.

## A3. Counts at each proof stage

| stage | count | gross debit |
| --- | ---: | ---: |
| Candidates by producer, organization-scoped | **2,039** | ₦39,211,120.76 |
| …with an original+reversal chain | 2,038 | — |
| …ambiguous (more than one chain) | 0 | — |
| Proof 1 — invoice present, not void or cancelled | 2,038 | — |
| Proof 2 — original was reversed | 2,038 | — |
| Proof 3 — reversal negates the original's net effect | 2,038 | — |
| Proof 4 — orphan has the same net effect as the original | **2,033** | — |
| Proof 5a — no POSTED equivalent on the document, **any date** | 2,038 | — |
| Proof 5b — no other POSTED journal on the document **at all** | 2,038 | — |
| **Ledger chain proven** | **2,033** | **₦38,247,308.26** |

Proof 5 was strengthened after the first run: 5a drops the original "after the
reversal date" bound so a *currently* posted equivalent is rejected whatever its
posting date, and 5b is stricter still — no other POSTED journal on the document
at all, whatever its effect. Both pass for all 2,038, so the strengthening did
not change the outcome; it changes what the outcome *means*.

Every candidate invoice is `PAID` (1,628), `POSTED` (406) or
`PARTIALLY_PAID` (5). Every original a reversal points at is `REVERSED`,
2,038 of 2,038.

## A4. Effect by account — split by type, because netting hides the decision

| type | account | journals | net debit effect |
| --- | --- | ---: | ---: |
| CREDIT_NOTE | 1400 Trade Receivables | 2,010 | (₦29,891,761.62) |
| CREDIT_NOTE | 4000 Internet Revenue | 2,010 | ₦29,891,761.62 |
| STANDARD | 1400 Trade Receivables | 23 | ₦8,355,546.64 |
| STANDARD | 4000 Internet Revenue | 23 | (₦8,355,546.64) |

The two types move the same two accounts in **opposite directions**. The
combined net — ₦21,536,214.98 off receivables and revenue — is arithmetic, not a
decision, and must not be presented as one.

No VAT account appears on any candidate. **That proves the orphan journals
contain no VAT. It does not establish that zero VAT is correct**, least of all
for the 29 STANDARD documents. See §6 and §A7.

### By posting period (all 2026, all open)

| period | type | journals | gross debit |
| --- | --- | ---: | ---: |
| 2026-03 | CREDIT_NOTE | 1,970 | ₦28,258,256.05 |
| 2026-04 | STANDARD | 3 | ₦1,300,000.00 |
| 2026-05 | CREDIT_NOTE | 29 | ₦1,465,019.82 |
| 2026-05 | STANDARD | 3 | ₦1,126,435.48 |
| 2026-06 | CREDIT_NOTE | 10 | ₦165,985.75 |
| 2026-06 | STANDARD | 8 | ₦2,834,500.00 |
| 2026-07 | CREDIT_NOTE | 1 | ₦2,500.00 |
| 2026-07 | STANDARD | 9 | ₦3,094,611.16 |

## A5. THE OPERATIVE INSTRUCTION — four cohorts, four separate decisions

**Do not authorise the 2,033 as one net batch.** Credit notes reduce
AR and revenue while standard invoices increase them; combining them hides two
opposite decisions behind one number.

| cohort | count | gross | disposition |
| --- | ---: | ---: | --- |
| Credit notes, ledger chain proven | 2,010 | ₦29,891,761.62 | **HOLD** pending proof that the credit notes remain valid, that they affect customer balances, and that no equivalent GL effect exists |
| Standard invoices, ledger chain proven | 23 | ₦8,355,546.64 | **SEPARATE APPROVAL, AND FOUR SEPARATE PERIOD ASSESSMENTS** — see below. Verify tax status, customer balance, currency and period per document |
| Incomplete standard-invoice orphans | 5 | ₦945,000.00 | **QUARANTINE — never post as written.** If valid, void and regenerate through the owning service with the complete VAT/revenue structure |
| `JE202607-20448` | 1 | ₦18,812.50 | **VOID**, subject to Finance approval after a final current-state duplicate verification |

### The 23 standard invoices are FOUR assessments, not one cohort

They span four originating periods, and each needs its own source-document,
VAT-return, customer-subledger and GL-control reconciliation. **Do not approve
them as a combined ₦8.36M cohort.**

| originating period | journals | gross debit |
| --- | ---: | ---: |
| 2026-04 | 3 | ₦1,300,000.00 |
| 2026-05 | 3 | ₦1,126,435.48 |
| 2026-06 | 8 | ₦2,834,500.00 |
| 2026-07 | 9 | ₦3,094,611.16 |
| **total** | **23** | **₦8,355,546.64** |

The credit-note cohort is concentrated differently — 1,970 of 2,010 in 2026-03 —
so the two cohorts do not even share a reporting-impact shape.

## A6. The one-row delta is explained

§3 recorded 2,039 candidates against a prior investigation's 2,038 and required
the difference be resolved by running the detector. It is.

**`JE202607-20448` (2026-03-05, ₦18,812.50, STANDARD) has no reversed
original.** Its invoice carries one other journal, `JE202607-9550`, which is
`POSTED`, is not a reversal, and posts the identical two lines — 1400 debit
₦18,812.50, 4000 credit ₦18,812.50 — on the identical date.

It is not a stranded repost. It is a **duplicate of an already-posted journal**,
and posting it would double-count ₦18,812.50.

Structural counts confirm the shape without the detector's signatures: 2,039
APPROVED orphans, 2,038 `REVERSED` originals, 2,038 `POSTED` reversals, and
exactly 1 `POSTED` non-reversal.

## A7. The five quarantined orphans

| orphan | invoice | type | status | orphan debit |
| --- | --- | --- | --- | ---: |
| JE202607-21559 | INV202606-154298 | STANDARD | POSTED | ₦350,000.00 |
| JE202607-21557 | INV202605-155374 | STANDARD | POSTED | ₦350,000.00 |
| JE202607-21563 | INV202606-154300 | STANDARD | POSTED | ₦105,000.00 |
| JE202607-21565 | INV202606-154299 | STANDARD | POSTED | ₦70,000.00 |
| JE202607-21561 | INV202606-154297 | STANDARD | POSTED | ₦70,000.00 |

Each original posts 1400, **2125 (two VAT lines)** and 4000 (two revenue lines).
Each orphan posts only 1400 and 4000, for the base amount. **Posting these as
they stand would understate output VAT and revenue.** They are the concrete
demonstration that "no VAT in the orphan" is not "no VAT due".

## A8. Possible untagged replacements — candidates, not answers

A replacement posted with no `source_document_id` cannot be linked to its
invoice by any ledger query. Searching by net effect instead returns eight
POSTED journals (detector §11), all `ar`/`INVOICE` with a null document id:

`JE-2025-00170`, `JE-2025-00171`, `JE-2025-00176`, `JE-2025-00189`,
`JE-2025-00190`, `JE-2025-13077`, `JE-2025-13078`, `JE-2025-37617` —
2025-01 to 2025-06, round ₦500,000 and ₦300,000 amounts.

**Read §A2 before treating any of these as a replacement.** They are 2025
postings against a 2026-03..07 cohort, and round-amount collisions are exactly
what a 96%-collision signature space produces. They are listed so the search is
on the record, not because any of them is believed to be a replacement.

## A9. Proof 6 — the per-document Finance reconciliation, restated

Not decidable from the ledger, not discharged by anything above. For **each**
document:

1. The source document and its approval remain valid.
2. The customer subledger already contains — or does not contain — the document's effect.
3. GL 1400 lacks that same effect.
4. No manual or differently tagged journal represents it (see §A8 for why the ledger cannot close this by itself).
5. Tax treatment matches the source document and the filed period.
6. Transaction and functional currency are correct.
7. The intended posting period is approved.

**Treatment after validation: correct in the originating period.** These are
2026 errors in open periods, so the default is correction in the period each
posting belongs to, not an August net adjustment. Monthly management accounts
and tax returns already issued for 2026-03 to 2026-07 need an impact assessment
(§6, IAS 8).

## A10. Reconciliation against the July 2026 investigation

[[erp-prod-stranded-reposts-2026-07]] recorded, over its 2,038: a **net
understatement of ₦20,591,214.98** and a **gross balanced line volume of
₦39,348,870.76**. Both are now reproduced exactly.

**Net — reconciled.** ₦21,536,214.98 (net over the 2,033 chain-proven) minus
₦945,000.00 (the five quarantined orphans, which debit 1400 where the rest
credit it) = **₦20,591,214.98**. It also equals `sum(ar.invoice.total_amount)`
over the 2,038 chained invoices, which is negative — itself confirming the
population is credit notes.

**Gross — RESOLVED, and it was never a discrepancy.** The July figure is the sum
of **debit lines on the ORIGINAL (reversed) journals** across the 2,038 chains:

| formula | value |
| --- | ---: |
| Original journals' debit lines, 2,038 chains | **₦39,348,870.76** ← the July figure, exactly |
| Reversal journals' debit lines, 2,038 chains | ₦39,348,870.76 |
| Orphan journals' debit lines, 2,038 chains | ₦39,192,308.26 |
| Orphan header debit, 2,039 candidates | ₦39,211,120.76 ← this appendix's figure |

The originals include the VAT and second revenue lines that the orphans drop, so
they are larger. The two figures measure **different journals**, and the earlier
₦137,750.00 "gap" was a category error in comparing them — **not** a
reconciliation, and it is withdrawn as such. Both figures stand, each correct
for what it measures. **When a single gross figure is quoted for this cohort,
quote ₦39,211,120.76 with its definition attached**; the July gross is superseded
for that purpose because it measures the reversed originals, not the orphans.

**A contradiction in the earlier record, settled.** The July entry described the
orphans as `STANDARD` in its body while calling them credit notes in its
summary. From the data: **2,010 `CREDIT_NOTE` and 29 `STANDARD`**, with
posting direction agreeing with document type on every row. The summary was
right; the body's reading is what appears to have carried into §4.

**The population difference, from the other side.** July's 2,038 required a
reversed original, which correctly excluded `JE202607-20448`. §3's 2,039 came
from counting by producer, which does not. The two counts never described the
same set — exactly as §3 warned.

## A11. Limits, stated plainly

1. **Proof 6 (§A9) is NOT discharged.** "Ledger chain proven" means the ledger
   chain is proven, nothing more. No candidate is approved for posting here.
2. **The signature is a net-effect test, not an identity test** (§A2), and
   identical effects are the norm in this ledger.
3. **The copy is corroborative, not atomic** (§A0). Revalidate against current
   authoritative state immediately before executing any disposition.
4. **No data was repaired.** No candidate was posted, voided or altered. The
   detector runs in a transaction ending in `ROLLBACK`, against a copy.
5. **Two defects were found in the detector while running it**, both recorded in
   the script rather than quietly fixed.
   - An early version required the original to be `POSTED` and returned ZERO
     chains for all 2,039. Reversed originals move to `REVERSED`, so that zero
     was a query defect, not a finding.
   - The organization sensitivity proof was initially ordered so that it could
     not fail (§A1). It is now fail-closed and has a negative control.

   Every number here comes from the corrected version, and each was re-derived
   by a second query using no signatures.

## A12. Cleanup record

| step | evidence |
| --- | --- |
| Container `erp-forensic-20260822c` removed | `docker rm -f`; `docker ps -a --filter name=erp-forensic` returns 0 rows |
| Volume `erp-forensic-20260822c-data` removed | `docker volume rm`; `docker volume ls --filter name=erp-forensic` returns 0 rows |
| Earlier instances `erp-forensic-20260822a` and `-20260822b`, and their volumes | removed the same way, earlier the same day |
| SQL and output removed from `dotmac-db-primary` | all `/tmp` detector, diagnostic and output files — `No such file or directory` |
| No dump file ever written | tables were streamed container-to-container; no export file existed on either host at any point |
| Standby unchanged | `pg_is_in_recovery() = t`, `max_standby_streaming_delay = 30s` (untouched — and the reason the earlier correlated query was cancelled), replay advancing normally |
| Production primary | never connected to |

---

# Appendix B — Gate G: the remaining 12,224 APPROVED journals

**This appendix is the operative text for the 12,224 APPROVED journals outside
the ar/INVOICE cohort.** It supersedes any earlier assumption that they are
work waiting to be posted. §5's classification policy is what it applies; §5
itself is unchanged and still governs.

Produced by `scripts/accounting_gate_g_detector.sql` — read-only, one
transaction, temp tables, ending in `ROLLBACK`, run against an isolated restored
database.

## B0. Provenance

| item | value |
| --- | --- |
| Source | `dotmac_erp_standby` on `dotmac-db-primary` — hot standby, `pg_is_in_recovery() = t`, read-only. Production primary never connected to; no standby setting changed. |
| Replay LSN at export start | `BF/EEB3F748` (2026-08-22 10:49:47Z) |
| Replay LSN at export end | `BF/EEB3F850` (2026-08-22 10:49:56Z) |
| Server version | PostgreSQL 16.4, source and target |
| Detector query hash (sha256) | `d5c834d8d66b0d17064654b31bd5d40dc33f0c41600c674e4cd1126402897eec` |
| Target | `erp-forensic-gateg-20260822` — ephemeral, `--network none`, no published port, no application attached. Destroyed after verification (§B9). |
| Organization scope | Dotmac Technologies Ltd, with the same fail-closed canary as Appendix A — 12,225 unscoped against 12,224 scoped, `sensitivity proof PASSED`. |

**Corroborative evidence, not an atomic snapshot** — the copy spans an LSN
range. **Every disposition must be revalidated against current authoritative
state immediately before execution.**

## B1. THE HEADLINE

**Not one of the 12,224 is a candidate to post.** Every journal is either an
effect that is already in the ledger, or one that cannot be decided from the
ledger at all.

| disposition | journals | gross debit |
| --- | ---: | ---: |
| **V1** VOID — identical effect already posted on the same document | 100 | ₦24,669,066.37 |
| **V2** VOID — fee event already posted (heuristic key, §B5) | 12,117 | ₦11,813,979.50 |
| **Q1** QUARANTINE — document posted but the effect differs | 1 | ₦75,250.00 |
| **Q2** QUARANTINE — no linkage of any kind; not decidable | 6 | ₦726,322.87 |
| **P** POST CANDIDATE | **0** | **₦0.00** |
| **total** | **12,224** | **₦37,284,618.74** |

Counted a second way, without the classification logic: 0 document-linked
journals have nothing posted against their document; 0 bank fees have an
unposted event; 6 reconciliations are unlinkable. The two methods agree.

## B2. THE RISK THIS QUANTIFIES

`gl.posting_backlog.post_approved_journals` decides whether to post an APPROVED
journal on two tests: **is it balanced, and does its period accept posting.**
That is the entire test.

| cohort | journals | balanced | period accepts | **it would post** | gross |
| --- | ---: | ---: | ---: | ---: | ---: |
| BANK_FEE | 12,117 | 12,117 | 12,117 | 12,117 | ₦11,813,979.50 |
| CUSTOMER_PAYMENT | 98 | 98 | 98 | 98 | ₦12,564,606.69 |
| BANK_RECONCILIATION | 6 | 6 | 6 | 6 | ₦726,322.87 |
| EXPENSE_REIMBURSEMENT | 2 | 2 | 2 | 2 | ₦31,000.00 |
| PAYROLL_ENTRY | 1 | 1 | 1 | 1 | ₦12,148,709.68 |
| **total** | **12,224** | **12,224** | **12,224** | **12,224** | **₦37,284,618.74** |

**Every one of the 12,224 passes both tests today.** Run against this
organization, that service would post ₦37,284,618.74 of duplicate and
undecidable effect, and report success. Balance and period tell you a journal
*can* be posted; they say nothing about whether it *should* be.

**No bulk posting path may be pointed at this population.** That is the finding.

## B3. Why the cohorts cannot share one decision

They differ in the only thing that matters here — whether the journal can be
tied to a source document at all.

| cohort | journals | source_document_id | evidence strength |
| --- | ---: | --- | --- |
| CUSTOMER_PAYMENT | 98 | present | **strong** — real document identity |
| EXPENSE_REIMBURSEMENT | 2 | present | **strong** |
| PAYROLL_ENTRY | 1 | present | **strong** |
| BANK_FEE | 12,117 | **absent** | **heuristic** — see §B5 |
| BANK_RECONCILIATION | 6 | **absent**, and no reference or correlation id either | **none** — undecidable |

## B4. The document-linked cohorts (101) — strong evidence

| cohort | journals | document already posted | posted effect identical | effect differs |
| --- | ---: | ---: | ---: | ---: |
| CUSTOMER_PAYMENT | 98 | 98 | 97 | 1 |
| EXPENSE_REIMBURSEMENT | 2 | 2 | 2 | 0 |
| PAYROLL_ENTRY | 1 | 1 | 1 | 0 |

**All 101 documents already carry a POSTED journal.** 100 of them carry one with
an identical net effect by account. Confirmed by a second, independent test
comparing header totals rather than signatures: same 97 / 2 / 1 split.

The `PAYROLL_ENTRY` journal (₦12,148,709.68) was flagged in §3 for individual
adjudication. Its payroll entry already has a POSTED journal with an identical
effect. It remains an individual adjudication; this is evidence for it, not a
substitute.

### Q1 — the one that differs

`JE202607-9427` (CUSTOMER_PAYMENT, 2026-07-13, ₦75,250.00). Its payment already
has a POSTED journal, but that journal's effect is not identical. **Quarantine
with a named owner.** It is neither safely voidable as a duplicate nor safely
postable.

## B5. BANK_FEE (12,117) — 111 real events, inflated 79×

The fees carry no `source_document_id`, so they can only be grouped by a
**heuristic key**: reference + entry date + amount + bank account.

| measure | value |
| --- | ---: |
| APPROVED bank-fee journals | 12,117 |
| Distinct fee events they describe | **111** |
| Value of those 111 events | **₦149,917.97** |
| Gross if every journal were posted | **₦11,813,979.50** |
| Inflation factor | **78.8×** |
| Events with more than one APPROVED journal | 109 |
| Most APPROVED journals on a single event | **252** |

**Every one of the 12,117 has a POSTED journal on its event** — same reference,
same date, same amount, same bank account. All ₦11,813,979.50 of it.

Posting them would book ₦11,813,979.50 to **6080 Finance Cost** against ₦149,917.97
of actual bank charges, and credit the bank accounts by the same — 1204 Zenith 523
by ₦7,250,116.66, 1205 Zenith 461 by ₦3,267,613.00, 1202 UBA by ₦1,296,213.24.

### Corroboration, and its limit

All **70 of 70** distinct fee references resolve to a `banking.bank_statement_lines`
row, so the grouping is anchored to real bank events rather than to a string
alone.

**But the key is still heuristic, and V2 is weaker than V1.** `reference` alone
is not a document key: 12,117 journals carry only 70 distinct references, up to
504 on one, a mean of 173. That is why the key adds date, amount and bank
account. Before executing V2, Finance should confirm the disposition against the
bank statement itself for at least a sample, and for every event whose value is
material.

### A separate finding: the POSTED side is not clean either

Those 111 events already carry **149** POSTED journals. Roughly 38 duplicate
postings exist in the ledger *today*, independent of anything in this backlog.
That is a Gate D-adjacent defect and is **not** addressed by any disposition
here.

## B6. Q2 — the six undecidable reconciliations

| journal | entry date | gross debit |
| --- | --- | ---: |
| JE202607-24872 | 2026-07-22 | ₦464,631.89 |
| JE202606-10330 | 2026-06-18 | ₦200,346.98 |
| JE202607-0044 | 2026-06-02 | ₦18,812.50 |
| JE202607-0065 | 2026-06-02 | ₦18,812.50 |
| JE202607-24999 | 2026-07-06 | ₦18,719.00 |
| JE202607-24972 | 2026-03-27 | ₦5,000.00 |

All six carry **no `source_document_id`, no `reference` and no `correlation_id`**.
They cannot be tied to a bank reconciliation, a statement or anything else by any
ledger query. **No ledger evidence can classify them.** They require the
reconciliation records themselves, and until someone produces those they are
quarantined with a named owner and a deadline, per §5.

Two of them post ₦18,812.50 — the same amount as `JE202607-20448` in Appendix A6.
Whether that is coincidence or a shared cause is **not established here** and
should not be assumed either way.

## B7. What this appendix does NOT establish

1. **It does not approve a single void.** V1 and V2 are ledger-evidenced
   *recommendations* under §5. Voiding is a Finance decision, and for V2 it rests
   on a heuristic key that should be sampled against bank statements first.
2. **It says nothing about business validity.** Whether a source document should
   have produced an effect at all is outside the ledger.
3. **It does not explain WHY 12,117 journals exist for 111 events.** The
   generating defect is not identified here. Until it is, the same population can
   regrow — and Gate G cannot close on a cleanup whose cause is unknown.
4. **The 149 posted journals on 111 events are untouched** by any disposition
   here.
5. **Cross-document effect matching was never used as evidence.** In this ledger
   96% of journals share a net-effect signature with another (Appendix A2), so
   signatures here only ever compare journals already tied to the same document
   or the same fee event.

## B8. Consequence for Gate G

Gate G requires an owned disposition for every remaining APPROVED journal. This
appendix supplies ledger evidence for **12,217 of 12,224** (V1 + V2) and
identifies the **7** that ledger evidence cannot decide (Q1 + Q2).

It does not close Gate G. Outstanding: Finance's approval of the void
recommendations, the sampling of V2 against bank statements, owners and deadlines
for the seven quarantined journals, and — the item most likely to reopen this —
identification of the defect that produced 12,117 journals for 111 events.

## B9. Cleanup record

| step | evidence |
| --- | --- |
| Container `erp-forensic-gateg-20260822` removed | `docker rm -f`; `docker ps -a --filter name=erp-forensic` returns 0 rows |
| Volume `erp-forensic-gateg-20260822-data` removed | `docker volume rm`; `docker volume ls --filter name=erp-forensic` returns 0 rows |
| Detector, discovery and verification SQL removed from `dotmac-db-primary` and from the standby container | all `No such file or directory` |
| No dump file ever written | tables streamed container-to-container; no export file existed on either host |
| Standby unchanged | `pg_is_in_recovery() = t`, `max_standby_streaming_delay = 30s`, replay advancing |
| Production primary | never connected to |
