# Finance correction memorandum — Accounting gate D defects

**Status: DRAFT — the ar/INVOICE cohort proof is COMPLETE at the ledger level
(Appendix A). Bank-fee recurrence prevention is deployed at production revision
`sha-0dc07e4`. The exact-effect detector was run on 2026-08-23 and refused all
429 journal-keyed candidates: every header and unkeyed monetary shape agrees,
but every immutable ledger account set differs from its line-keyed canonical.
A purpose-built wrong-account schedule has now run twice on an isolated copy:
429 one-to-one linked reversals, SHA-256 plan digest
`dbeab5dafe0d27bafa834fde43c35ae9f36996ba5a332623784619cddcbd9148`.
It is rehearsal evidence, not production execution approval; nothing was
reversed. Still outstanding: approval and guarded execution of that exact plan,
the §1a audited-opening bridge, the composite reversal, the three micro repairs,
the per-document proof 6 reconciliation, the reporting/tax assessment, and
every named Finance approver and operator.**

**One operative instruction governs the ar/INVOICE population: Appendix A §A5.**
Earlier text in §3 and §4 is preserved as the historical hypothesis and is
superseded wherever the two disagree.

Decisions, approver and operator are Finance's and are not filled in.
Engineering produced the forensics and must not resolve any item here. Gate D
execution stays blocked on content completeness, not on CI.

Evidence taken read-only 2026-08-21/22/23 against `dotmac_erp_standby` (hot
standby, database writes physically impossible). Reproducible via
`scripts/accounting_backfill_survey.sql` plus the queries quoted below. No
production application or database row was written; the 2026-08-23 run used an
isolated host-local container and root-only temporary files, all destroyed.

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
| Bank-fee recurrence prevention | **DEPLOYED.** ERP PRs #337–#339 establish the one-owner, typed-source, at-most-once boundary, fail-closed effect verification and bulk-mutator kill switch. Production was observed healthy on `sha-0dc07e4` on 2026-08-23; this evidence run deployed nothing. None of those controls is a data repair. |
| **429 journal-keyed POSTED bank-fee account-effect mismatches (₦7,764.68 gross) on 39 statement-line identities** | ***CORRECTION DESIGNED AND REHEARSED; PRODUCTION EXECUTION PENDING* — Appendix B5.4.** The exact-duplicate detector still correctly refuses all 429. The separate wrong-account detector admits exactly the two 352/77 account substitutions, proves 858/858 journal-line parity, binds the 2025 target / March 2026 canonical / August 2026 reversal timing, and produced the same 429-row SHA-256 plan digest twice. No production accounting row changed; exact-plan approval, a guarded operator and execution proof remain pending. |
| POSTED bank-fee anomalies: 95 journals with dangling line ids; 875 of 1,743 crediting their line's bank GL account | ***not started* — Appendix B5.4.** |
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
| Generating defect behind 12,117 journals for 149 statement lines | **PROVEN, FIXED AND DEPLOYED — Appendix B8.** ERP PRs #337–#339 carry the two-invocation and database-race canaries and the exact-effect recovery checks; production was observed on their merge revision `0dc07e4`. |
| Permanent fix: one bank-fee owner, typed `source_document_id`, at-most-once create+post in one transaction | **DEPLOYED — ERP PRs #337–#339 / `sha-0dc07e4`.** Deployment prevents recurrence; it does not repair historical rows. |
| Non-dry-run generic backlog mutators removed or gated | **DEPLOYED — ERP PR #338.** Blast radius remains the full 14,263 / ₦76,495,739.50; the kill switch is not Finance authorization. |
| Pre-cleanup controls agreed (§B12) | **ENGINEERING CONTROLS DEPLOYED; accounting preconditions pending.** Current-state detectors, Finance dispositions/approval and a named operator remain mandatory before any cleanup. |

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
| Source | `dotmac_erp_standby` on `dotmac-db-primary` — hot standby, `pg_is_in_recovery() = t`, `transaction_read_only = on`. The exact detector exported only its required columns from this standby; no primary accounting row was queried or changed, and no standby setting changed. |
| Replay LSN at export start | `BF/EEB3F748` (2026-08-22 10:49:47Z) |
| Replay LSN at export end | `BF/EEB3F850` (2026-08-22 10:49:56Z) |
| Server version | PostgreSQL 16.4, source and target |
| Detector query hash (sha256) | `d5c834d8d66b0d17064654b31bd5d40dc33f0c41600c674e4cd1126402897eec` |
| Detector, as re-run on exact identity | `scripts/accounting_gate_g_detector.sql`, sha256 `f53f814273a8526f951aed999ac6553e9de8b379cda58db9c19dd27dd22b556b`, against `erp-forensic-verify-20260822`, LSN range `BF/EFB66430`..`BF/EFB705E8` (2026-08-22 11:30Z). Canary passed; produces H1A/Q dispositions directly, with no heuristic logic in the executable. |
| Journal-keyed POSTED candidates (Gate D) | The 2026-08-22 run of `scripts/accounting_bank_fee_duplicate_postings.sql`, sha256 `f97bd8e3c6a34579a9ff322fbe105781d1f0d118c449607fb107e074e5ad5e49`, against `erp-forensic-equiv-20260822`, LSN range `BF/EF546DA0`..`BF/EF54FBB8`, proves the second namespace, current liveness, target identity and ₦7,764.68 total. It did **not** prove target-to-canonical exact effect equivalence. |
| Exact-duplicate one-to-one schedule | **RUN AND REFUSED, twice, with the same result.** Commit `ed83989b5744b01d61ac9f0b54dc06ab7172a0af`, sha256 `2988835df245f1e1ed67faf4c6e855e95324de93ae2f6b003fe0a552f956586c`; repeatable-read snapshot `19017326:19018685:`, replay LSN `C0/6D6524A0`..`C0/6D790900`, PostgreSQL 16.4. All **429** targets failed exact immutable-effect equality. No D6 schedule or digest was emitted. |
| Exact-run isolated target | `erp-forensic-fee-exact-20260823` — source-image-identical PostgreSQL, memory-backed data directory, `--network none`, no published port and no application attached. Source subset counts were `13,955` fee journals, `1,838` batches, `3,676` ledger lines, `53` statements and `1,391` statement lines. Detector logs, copied columns and container were destroyed; `cleanup=complete`. |
| Purpose-built wrong-account schedule | **RUN AND PASSED twice.** `scripts/accounting_bank_fee_wrong_account_correction.sql`, sha256 `ae98776af6cc9496dd198d11cf340ce0b64d783626167ea283625d3b5d1d5432`; repeatable-read source snapshot `19051585:19051585:`, replay LSN at export start `C0/76C97728`, PostgreSQL 16.4. Both runs emitted 429 identical plan rows: internal schedule digest `a5e1776785856c579bd3ed6bc0d68308`; canonical plan-output sha256 `dbeab5dafe0d27bafa834fde43c35ae9f36996ba5a332623784619cddcbd9148`. |
| Wrong-account isolated target | `erp-fee-correction-rehearsal-20260823` — source-image-identical PostgreSQL, tmpfs data directory, `--network none`, no published port and no application attached. Source subset: 13,955 fee journals, 1,838 batches, 27,910 journal lines, 3,676 ledger lines, 24 fiscal periods, 53 statements and 1,391 statement lines. The copied columns excluded narrations and customer data. Container, copied rows, logs and staging directory were destroyed; `cleanup=verified`. |
| Target | `erp-forensic-gateg-20260822` — ephemeral, `--network none`, no published port, no application attached. Destroyed after verification (§B9). |
| Organization scope | Dotmac Technologies Ltd, with the same fail-closed canary as Appendix A — 12,225 unscoped against 12,224 scoped, `sensitivity proof PASSED`. |

**Corroborative evidence, not an atomic snapshot** — the copy spans an LSN
range. **Every disposition must be revalidated against current authoritative
state immediately before execution.**

## B1. THE HEADLINE

**Not one of the 12,224 is a candidate to post.** Every journal is either an
effect already in the ledger, or one the ledger cannot decide.

| disposition | journals | gross debit |
| --- | ---: | ---: |
| **V1** VOID candidate — identical effect already posted on the same document | 100 | ₦24,669,066.37 |
| **H1A** VOID candidate — same statement line, **same net effect by account, same currency, same period, posted journal currently effective** (§B5) | 12,117 | ₦11,813,979.50 |
| **H1B** QUARANTINE — same line but effect or effectiveness differs | **0** | — |
| **Q1** QUARANTINE — document posted but the effect differs | 1 | ₦75,250.00 |
| **Q2** QUARANTINE — header-unlinked reconciliations, partial linkage only (§B6) | 6 | ₦726,322.87 |
| **P** POST CANDIDATE | **0** | **₦0.00** |
| **total** | **12,224** | **₦37,284,618.74** |

Counted a second way, without the classification logic: 0 document-linked
journals have nothing posted against their document; 0 bank-fee statement lines
are unposted; 6 reconciliations are header-unlinked. The two methods agree.

**H1A is not V2.** An earlier version classified the 12,117 as `V2 VOID` on a
heuristic key; a second called them `H1 HOLD` on exact identity but had only
proved *association and cardinality*. §B5.2 now proves **economic equivalence**
as well, which is what a void candidate requires. §B10 records both supersessions.

`accounting_gate_g_detector.sql` produces these dispositions **directly** — the
heuristic bucket logic is removed from the executable, not merely annotated in
prose. Re-run 2026-08-22 against a fresh restore: canary passed, H1A = 12,117,
H1B = 0, identical to the table above.

## B2. THE RISK — and its blast radius is larger than this appendix

`gl.posting_backlog.post_approved_journals` decides whether to post an APPROVED
journal on two tests: **is it balanced, and does its period accept posting.**
That is the entire test.

**Its query does NOT exclude ar/INVOICE.** `find_approved_journals` selects every
APPROVED journal for the organization. So the blast radius is not this appendix's
12,224 — it is the **whole backlog of 14,263 journals, ₦76,495,739.50**, the
Appendix A cohort included.

| cohort | journals | balanced | period accepts | **it would post** | gross |
| --- | ---: | ---: | ---: | ---: | ---: |
| BANK_FEE | 12,117 | 12,117 | 12,117 | 12,117 | ₦11,813,979.50 |
| CUSTOMER_PAYMENT | 98 | 98 | 98 | 98 | ₦12,564,606.69 |
| BANK_RECONCILIATION | 6 | 6 | 6 | 6 | ₦726,322.87 |
| EXPENSE_REIMBURSEMENT | 2 | 2 | 2 | 2 | ₦31,000.00 |
| PAYROLL_ENTRY | 1 | 1 | 1 | 1 | ₦12,148,709.68 |
| **Appendix B subtotal** | **12,224** | **12,224** | **12,224** | **12,224** | **₦37,284,618.74** |
| ar/INVOICE (Appendix A) — also selected | 2,039 | — | — | also posted | ₦39,211,120.76 |
| **TOTAL EXPOSURE** | **14,263** | | | | **₦76,495,739.50** |

Every one of them passes both tests today. Balance and period tell you a journal
*can* be posted; they say nothing about whether it *should* be.

### The mutation paths, precisely

| path | default | scheduled? |
| --- | --- | --- |
| `app.tasks.gl_posting.post_approved_journal_backlog` | `dry_run=True` | **No** — not in `scheduler_config.py` |
| `app.tasks.gl_posting.post_stranded_source_journals` | `dry_run=True` | **No** |
| `scripts/post_stranded_bank_fees.py` | requires explicit flags | manual |

The exposure is therefore a **manual invocation with `dry_run=False`**, not an
automatic one. That is a materially smaller risk than a scheduled job and it is
stated that way deliberately — but it is one command, and it would report
success.

**Recommended control: remove or gate the non-dry-run path on these generic
backlog mutators until the dispositions here are executed.** A `dry_run=False`
argument is not a safeguard; it is a parameter.

*(Separately, `app.tasks.data_health.auto_post_approved_invoices` IS scheduled —
daily 08:30, `max_age_days=7`. It operates on `ar.invoice` documents in APPROVED
status, not on the journal backlog, so it is a different population. Noted so it
is not confused with the above, and not claimed as part of this exposure.)*

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

## B5. BANK_FEE (12,117) — resolved on EXACT statement-line identity

The heuristic key is abandoned. The bank-fee writers already record exact
identity, and it was there to be used:

```
correlation_id = "bank-fee-<bank_statement_line.line_id>"
idempotency key = organization + BANKING + line_id + "bank-fee"
```

`scripts/accounting_bank_fee_line_identity.sql` parses that UUID and joins it to
`banking.bank_statement_lines`. 100% of the population, no sampling — the
authoritative set is small enough that sampling would be a choice to know less.

### Identity health

| measure | APPROVED | POSTED |
| --- | ---: | ---: |
| Journals | 12,117 | 1,838 |
| `correlation_id` populated | 12,117 | 1,838 |
| Null correlation id | 0 | 0 |
| Malformed (does not parse to a UUID) | 0 | 0 |
| Distinct statement-line ids | **149** | 1,409 |

Across both statuses, 1,391 of 1,409 distinct line ids resolve to a statement
line. **18 do not** — carried by 95 POSTED journals (§B5.3).

### Validation of each APPROVED journal against ITS OWN statement line

| check | passes |
| --- | ---: |
| Organization matches (line → statement → bank account) | 12,117 / 12,117 |
| Entry date = line transaction date | 12,117 / 12,117 |
| Journal total = absolute line amount | 12,117 / 12,117 |
| Debit account is 6080 Finance Cost | 12,117 / 12,117 |
| Credit account = the line's bank GL account | **12,115 / 12,117** |

The two exceptions are `JE202603-0227` and `JE202603-0230` (₦25.00 and ₦10.00,
2026-01-15). Their line's bank account is Paystack OPEX; the journals credit the
legacy `Paystack OPEX - DT` account code. A chart-of-accounts duplication, not a
wrong bank — but they should not be dispositioned with the rest.

### The exact answer

| measure | value |
| --- | ---: |
| Distinct statement lines the 12,117 are about | **149** |
| True fee value of those 149 lines, once | **₦151,829.22** |
| Gross if every APPROVED journal were posted | ₦11,813,979.50 |
| **Exact inflation factor** | **77.8×** |
| Of those 149 lines: never posted | **0** |
| posted exactly once | **149** |
| posted more than once | **0** |

**Every one of the 149 lines carries exactly one POSTED BANK_FEE journal.**
Stated that way deliberately: cardinality is not equivalence, and §B5.2 is what
licenses any stronger claim.

### B5.2 Economic equivalence — H1A / H1B

Exact line identity proves **association and cardinality**. It does not prove the
APPROVED journal and the POSTED journal on that line have the same effect. Each
APPROVED journal was therefore compared against the POSTED journal on **its own**
line:

| test | passes |
| --- | ---: |
| A POSTED journal exists on the line | 12,117 / 12,117 |
| Same functional-currency net effect by account | **12,117 / 12,117** |
| Same transaction currency | 12,117 / 12,117 |
| Same fiscal period | 12,117 / 12,117 |
| The POSTED journal is not itself a reversal | 12,117 / 12,117 |
| The POSTED journal has not since been reversed | 12,117 / 12,117 |
| The POSTED journal has rows in `gl.posted_ledger_line` | 12,117 / 12,117 |

**H1A = 12,117. H1B = 0.** Every APPROVED bank-fee journal duplicates an effect
that is on its line, identical in every dimension tested, and currently
effective. That is what makes them void candidates rather than holds.

The two legacy Paystack rows (`JE202603-0227`, `JE202603-0230`) also satisfy
every test, but their journals credit the legacy `Paystack OPEX - DT` code where
the line's bank account is Paystack OPEX. **Dispose of them separately** — the
detector holds them out in its own section for that purpose.

### B5.3 What exact identity DISPROVED

Two claims in the heuristic version do not survive:

1. **"111 real fee events."** There are **149** statement lines. The heuristic
   key merged distinct lines: of 111 buckets, 36 covered 2 lines and 1 covered 3.
   Buckets were never events.
2. **"149 POSTED journals on 111 buckets, so ~38 duplicate postings."** On exact
   identity the 149 lines carry **exactly 149 POSTED journals — zero excess.**
   The apparent duplication was entirely an artefact of bucket merging.

### B5.4 The journal-keyed POSTED effects — cause proven, exact-duplicate equivalence refused

The earlier calculation was `(posted row count − 1) × source fee`. That proves
excess **rows**, not excess **effect**: a second posted row is not a duplicate if
it is a reversal, was itself reversed, never reached the ledger, or posts a
different account, direction, currency, period or dimension. The 2026-08-22 run
tested current effectiveness and traced the namespace cause, but did not compare
each target's complete immutable effect with its canonical. Calling all 429
"proven duplicate effects" was therefore one proof too early.

**The cause is a second idempotency namespace.** POSTED bank-fee journals split
cleanly in two:

| namespace | postings | statement lines | in the ledger | reversed |
| --- | ---: | ---: | ---: | ---: |
| `<org>:BANKING:<line_id>:bank-fee:v1` | 1,409 | 1,409 | 1,409 | 0 |
| `backfill-stranded-bank-fees-<journal_number>` | **429** | 39 | 429 | 0 |

The ledger's at-most-once boundary is keyed on the **statement line**. The second
key is keyed on the **journal**, so it bypasses that boundary completely: every
stranded APPROVED journal for a line posted under its own key and succeeded.

Per line the pattern is exact — **1,370 lines have 1 canonical posting and 0
bypassed; 39 lines have 1 canonical and exactly 11 bypassed.** 39 × 11 = 429.

That namespace is `DEFAULT_IDEMPOTENCY_PREFIX = "backfill-stranded"` in
`app/services/finance/gl/stranded_fee_posting.py`, reached through
`app.tasks.gl_posting.post_stranded_source_journals` and
`scripts/post_stranded_bank_fees.py`. **The remediation path for stranded fees is
what created the duplicate postings.** The standing instruction not to retry the
prior standalone script is now evidenced rather than precautionary.

**Proven journal-keyed current-effect population:**

| measure | value |
| --- | ---: |
| Affected statement lines | 39 |
| Journal-keyed postings, all currently effective | **429** |
| **Gross debit of the journal-keyed current effects** | **₦7,764.68** |
| …of which on lines that resolve to a statement line | ₦6,160.00 |
| …on 7 lines whose id does not resolve | ₦1,604.68 |

The earlier ₦6,160.00 was the resolvable subset quoted as the whole. All 429 are
individually identifiable by their complete journal-keyed idempotency key and
each has its own posting batch. That proves identity and cardinality; it does
not prove a duplicate economic effect or authorize a correcting reversal.

The revised detector now builds the complete immutable-ledger signature for
each target and its one line-keyed canonical: account and direction, functional
and transaction amounts, currency and rate, dates and fiscal period, source
metadata and every accounting dimension. It fails closed on an unknown complete
key, anything other than one live canonical per affected line, an incomplete
schedule or any signature mismatch. It emits the 429 target ids only inside the
Finance boundary and binds the set with a count, gross amount and stable schedule
digest only after every comparison passes.

**The 2026-08-23 run refused before D6.** Both identical runs found 429 target
pairs and 429 immutable-effect mismatches, so there is no schedule digest for
Finance to sign:

| exact comparison | matches |
| --- | ---: |
| Header dates, period, currency/rate and totals | 429 / 429 |
| Ledger row count | 429 / 429 |
| Unkeyed functional/original amount shapes | 429 / 429 |
| Unkeyed dimension and source-metadata shapes | 429 / 429 |
| Ledger account ids and codes | **0 / 429** |
| Complete immutable ledger effect | **0 / 429** |

The account-set split is systematic:

| pairs | canonical account codes | journal-keyed account codes |
| ---: | --- | --- |
| 352 | `1211`, `6080` | `6080`, `Paystack OPEX - DT` |
| 77 | `1207`, `6080` | `6080`, `Zenith USD - DT` |

The two sides have the same ₦7,764.68 gross, but equal gross and line identity
do not make different accounts the same accounting effect. These 429 rows are
therefore not an exact-duplicate reversal schedule. That refusal remains true
and is not weakened by the purpose-built correction below.

#### B5.4a Purpose-built wrong-account correction — guarded execution pending

Michael authorized construction and rehearsal of the separate wrong-account
correction on 2026-08-23, then approved the exact reproduced plan digest and
named the production application and database hosts. That authorization does
not bypass the remaining code/deploy/current-state guards. The
`scripts/accounting_bank_fee_wrong_account_correction.sql` keeps the duplicate
detector's refusal intact and asks a narrower question: does each target differ
from its canonical by exactly one of the two measured account substitutions,
with the same debit/credit direction, money, currency/rate, dimensions, source
metadata and intended entry date?

It also proves the operational reversal source. `ReversalService` reverses
`gl.journal_entry_line`, not a reconstructed ledger aggregate, so the schedule
requires every target journal line to reproduce its immutable ledger row by
`journal_line_id`, account, functional/original amount, currency/rate and every
dimension. The run proved **858 journal lines = 858 ledger lines, zero
mismatches, across all 429 targets**.

The timing is not equivalent and is deliberately bound rather than hidden:

| effect | ledger date/period | treatment |
| --- | --- | --- |
| 429 wrong-account targets | 2025-01-20 through 2025-09-12, in their header periods | The linked reversals name these exact journals. |
| 39 retained line-keyed canonicals | 2026-03-13 / March 2026 (`7bc1edbb-270c-4096-b9e4-67cc72dd44a4`) | Remain posted and are never reversal targets. |
| proposed linked reversals | 2026-08-23 / open August 2026 (`13659716-9fe2-42f8-8aca-32cd67da22b6`) | Current-period corrections; they do not rewrite immutable 2025 or March 2026 ledger rows. |

The two exact admitted mappings and simulated correcting effect are:

| targets | wrong bank account credited by target | canonical bank account credited | correcting debit | correcting credit |
| ---: | --- | --- | ---: | ---: |
| 352 | `Paystack OPEX - DT` | `1211` | ₦6,160.00 to `Paystack OPEX - DT` | included in ₦7,764.68 to `6080` |
| 77 | `Zenith USD - DT` | `1207` | ₦1,604.68 to `Zenith USD - DT` | included in ₦7,764.68 to `6080` |

All 352 Paystack targets still resolve to their statement-line row. All 77
Zenith targets carry one of the already-measured dangling line identities; the
schedule requires that exact resolution state instead of pretending the rows
resolve. Each still has exactly one live canonical under the same immutable
correlation identity and exact economic shape. A third mapping, changed
resolution state, changed timing, line mismatch, missing canonical, changed
count or changed ₦7,764.68 gross refuses the plan.

The isolated, networkless rehearsal ran the schedule twice against one
repeatable-read standby snapshot. Both runs emitted the same 429 rows:

| binding | value |
| --- | --- |
| Internal schedule digest | `a5e1776785856c579bd3ed6bc0d68308` |
| Canonical 429-row plan sha256 | `dbeab5dafe0d27bafa834fde43c35ae9f36996ba5a332623784619cddcbd9148` |
| Reversal simulation | 429 balanced linked reversals; all 858 target/account all-time nets become zero; all 39 canonicals remain live |
| Production writes | **zero** |

This establishes an exact correction plan, not its execution. The guarded
operator added after that approval consumes only the private W3a file with the
two approved digests, requires the database name and server address, validates
the exact 429/39/₦7,764.68 and 352/77 bindings, locks and revalidates every
target/canonical/effect/source identity, and delegates each write to the normal
one-to-one linked-reversal service. It defaults to a non-writing dry run and
commits only after all 429 posted reversals and postconditions succeed. Before
any production write, that operator must be merged and deployed and the current
production state must reproduce the same private plan. A bulk reversal or an
aggregate journal is still forbidden.

**Zero APPROVED journals sit on the 39 affected lines**, so the Gate G backlog
and this Gate D defect are disjoint populations and can be dispositioned
independently.

### B5.5 The 1,743-vs-1,398 question, answered — and the earlier count withdrawn

The earlier appendix reported "1,398 posting batches across 1,391 lines" and
asked why 1,743 POSTED rows corresponded to fewer batches. **That count was
wrong.** It matched batches with `idempotency_key LIKE '%' || line_id || '%'`,
which finds line-keyed batches only and silently misses the journal-keyed ones.

The true figures: **1,838 POSTED bank-fee journals, 1,838 distinct posting
batches** — 1,409 line-keyed plus 429 journal-keyed. There is no shortfall. There
were two key namespaces, and the query could only see one of them.

### B5.6 POSTED-side anomalies still open

- **95 POSTED journals carry a statement-line id that resolves to nothing** (18
  distinct ids).
- Of 1,743 POSTED journals whose line resolves, only **875** credit the line's
  bank GL account and **1,670** match its date. The APPROVED population is clean
  on both.

Neither is addressed by any disposition here.

## B6. Q2 — the six reconciliation journals are HEADER-unlinked, not unlinked

An earlier version of this appendix called them "no linkage of any kind". That
was wrong, and the way it was wrong matters: the first check joined
`bank_statement_line_matches` on the JOURNAL id rather than the journal LINE id,
and returned zeros that looked like a finding.

Re-run on the correct key, with a sanity check proving the join works (41,496
matches in the table, all 41,496 resolving to a journal line):

| journal | entry date | gross debit | statement-line matches | reconciliation lines | posting batches |
| --- | --- | ---: | ---: | ---: | ---: |
| JE202607-24872 | 2026-07-22 | ₦464,631.89 | **1** | 0 | 0 |
| JE202606-10330 | 2026-06-18 | ₦200,346.98 | 0 | 0 | 0 |
| JE202607-0044 | 2026-06-02 | ₦18,812.50 | 0 | 0 | 0 |
| JE202607-0065 | 2026-06-02 | ₦18,812.50 | **1** | 0 | 0 |
| JE202607-24999 | 2026-07-06 | ₦18,719.00 | **1** | 0 | 0 |
| JE202607-24972 | 2026-03-27 | ₦5,000.00 | **1** | 0 | 0 |

**Four of the six have a bank-statement-line match.** That is a concrete lead,
not a dead end, and each should be followed to its statement line before any
disposition.

What is genuinely absent, checked and confirmed:

- No `source_document_id`, `reference` or `correlation_id` on any of the six.
- `gl.journal_entry_line.reconciliation_id` is **globally unused** — 0 populated
  of 448,141 lines — so it is not a linkage path for anything.
- No `bank_reconciliation_lines` rows and no posting batches for any of them.
- `banking.bank_reconciliations` holds 108 records, **all dated 2025-01-31 to
  2025-12-31 and none COMPLETED**. No reconciliation record covers these
  2026-03..07 journals.

Their postings are all bank-to-bank or bank-to-deposit transfers: 1210 Paystack
Collections against 1211 Paystack OPEX, 2300 Customer Deposits, or 1207 Zenith
USD against 1222 Cash Deposit (USD). Quarantined with a named owner, per §5.

Two of them post ₦18,812.50 — the same amount as `JE202607-20448` in Appendix
A6. Whether that is coincidence or a shared cause is **not established** and
should not be assumed either way.

## B7. What this appendix does NOT establish

1. **It approves nothing.** V1 and H1 are ledger-evidenced recommendations under
   §5. Voiding is a Finance decision.
2. **It says nothing about business validity** — whether a source document should
   have produced an effect at all is outside the ledger.
3. **The 429 journal-keyed POSTED bank-fee effects (₦7,764.68) are not fixed
   here.** They are already in the posted ledger and belong to Gate D (§B5.4).
   Exact target-to-canonical verification ran and refused all 429 because their
   account sets differ. A separate timing-aware wrong-account schedule now
   names and rehearses all 429 linked-reversal targets, but no production
   reversal has been approved or written.
4. **The POSTED-side anomalies are not resolved** — 95 journals with dangling
   line ids, and only 875 of 1,743 crediting their line's bank GL account.
5. **Cross-document effect matching was never used as evidence.** 96% of journals
   in this ledger share a net-effect signature (Appendix A2).

## B8. THE GENERATING DEFECT — proven, fixed and deployed

All three bank-fee writers —
`banking/auto_reconciliation_parts/special.py:92`,
`banking/programmatic_parts/special_strategies.py:67`,
`banking/reconciliation_engine_parts/handlers.py:327` —
follow the same sequence, and it explains the population exactly:

1. They compute `correlation_id = f"bank-fee-{line.line_id}"`, so **the exact
   statement-line identity is in hand**.
2. They build a `JournalInput` with `source_document_type="BANK_FEE"` and
   **leave `source_document_id` unset** — the identity is spent on a string
   instead of the typed field that would have made it a key.
3. They call `create_and_approve_journal` — **a new journal is created and
   approved unconditionally, before anything checks whether the effect exists.**
4. Only then do they post, with the line-based idempotency key.
5. When a batch already exists for that key the ledger returns
   `PostingResult(success=True, …, idempotent_replay=True)`.
6. **No bank-fee writer reads `idempotent_replay`.** Verified: the flag is set at
   `gl/ledger_posting.py:213` and the only consumer anywhere under `app/` is
   `gl/stranded_fee_posting.py`. The writers see `success=True` and move on.
7. The journal created in step 3 is never posted and never cleaned up. It stays
   **APPROVED** — which is precisely the population in this appendix.

The order is the defect: **create-then-check instead of check-then-create.** Each
re-run of a reconciliation pass over the same statement line mints another
APPROVED journal, which is why 149 lines carry 12,117 of them and one line
carries 85.

That diagnosis is now proved by the row-level, two-invocation and database-race
canaries merged in ERP PRs #337–#339. They prove that a second invocation creates
no journal, two transactions racing after both prechecks produce exactly one
effect, a collision re-reads the winner, and a wrong live effect is an attention
state rather than replay success. Unrelated integrity failures still propagate.

### The permanent fix

One bank-fee posting owner now serves all three reconciliation adapters;
`bank_statement_line.line_id` is carried as **typed source identity**
(`source_document_id`), not merely as a formatted string; and at-most-once
creation and posting are enforced together in one transaction. ERP PR #337
introduced the owner and database boundary, #338 gated the dangerous bulk paths,
and #339 made every success/recovery path verify the exact live effect. All are
merged to `main` and production was observed healthy on their merge revision
`sha-0dc07e4` on 2026-08-23. This forensic run performed no deployment.

## B9. Consequence for Gate G

Gate G requires an owned disposition for every remaining APPROVED journal. This
appendix supplies evidence for **12,217 of 12,224** (V1 + H1) and identifies the
**7** the ledger cannot decide (Q1 + Q2 — though four of the six Q2 rows now have
a concrete statement-line lead).

It does not close Gate G. Outstanding:

- Finance approval of the V1 and H1 recommendations, and final document-state
  verification for V1's 100 rows.
- Owners and deadlines for the seven quarantined journals; the four Q2 rows with
  a statement-line match should be followed to that line first.
- Continued production verification of the deployed single-owner, at-most-once
  and bulk-mutator controls at `sha-0dc07e4`.
- Exact-plan Finance approval, a named guarded operator and production
  execution proof for the **429 journal-keyed wrong-account POSTED effects
  (₦7,764.68)**, plus the remaining POSTED-side anomalies (§B5.4, §B5.6).

## B10. What this appendix corrected about itself

The first version of Appendix B relied on a heuristic key — reference + date +
amount + bank account — because the fees carry no `source_document_id`. Three of
its claims did not survive exact statement-line identity, and they are recorded
rather than quietly replaced:

| claim | status |
| --- | --- |
| "111 real fee events" | **WRONG.** 111 were buckets; there are **149** statement lines. 36 buckets merged 2 lines, 1 merged 3. |
| "79× inflation" | **PROVISIONAL, now exact: 77.8×** (₦11,813,979.50 against ₦151,829.22). |
| "149 POSTED journals across 111 buckets → ~38 duplicate postings" | **DISPROVEN.** The 149 lines carry exactly 149 POSTED journals, zero excess. The real excess is elsewhere: 352 journals, ₦6,160.00, on 32 other lines. |
| "V2 VOID" | **Superseded twice.** First downgraded to `H1 HOLD` on exact identity; now **`H1A VOID candidate`** once economic equivalence was proved (§B5.2). |
| "effect already posted correctly and once" | **Overstated when written** — cardinality is not equivalence. Now earned: §B5.2 tests effect, currency, period, reversal state and ledger presence. |
| "352 excess rows, ₦6,160.00 misstatement" | **Corrected to 429 journal-keyed current effects with ₦7,764.68 gross debit**; the earlier figure was the resolvable subset quoted as the whole. The exact detector then **refused all 429** because the journal-keyed and canonical account sets differ (§B5.4). |
| "1,398 posting batches across 1,391 lines" | **WRONG.** The query matched line-keyed batches only. True: 1,838 journals, 1,838 batches, in two namespaces (§B5.5). |
| "no linkage of any kind" for the six reconciliations | **WRONG.** They are header-unlinked; **four have a statement-line match.** The original check joined on the journal id instead of the journal LINE id and returned a zero that read as a finding. |

The pattern in two of those five is the same one this memorandum has now hit
three times: **a query that cannot distinguish its answer from its own defect.**
The sanity check added in §B6 — proving the corrected join finds 41,496 matches
overall — exists so a zero there can be trusted next time.

## B12. Controls required BEFORE any cleanup

Not recommendations — preconditions. In order:

1. **Keep the deployed controls verified before cleanup.** ERP PRs #337–#339,
   observed in production at `sha-0dc07e4`, gate every generic non-dry-run
   backlog mutator and install the exact statement-line boundary.
   `post_approved_journal_backlog`,
   `post_stranded_source_journals` and `scripts/post_stranded_bank_fees.py` must
   refuse a live run by default in the deployed revision. A merge is not proof
   that production has the control.
2. **Revalidate every disposition against current authoritative state**
   immediately before executing it. This appendix rests on a copy spanning an
   LSN range (§B0).
3. **VOID approved duplicates — never delete them.** The APPROVED rows are
   evidence of the defect and of the decision taken about them.
4. **Do not use the exact-duplicate path for the 429.** That detector still
   refuses every target on account mismatch. Use only the separate §B5.4a
   wrong-account plan: re-run immediately before execution, require the exact
   429-row SHA-256 digest, the 2025/March/August timing proof, 858/858 journal
   parity and Finance approval of that exact plan. Execute one normal linked
   reversal per target in one transaction with postconditions. No bulk or
   aggregate journal, and nothing reversed on the strength of a count or gross.
5. **Keep the seven undecided journals quarantined with named Finance owners**
   and deadlines: Q1 `JE202607-9427`, and the six Q2 reconciliations — four of
   which have a statement-line match to follow first (§B6).

## B11. Cleanup record

| step | evidence |
| --- | --- |
| Containers `erp-forensic-gateg-20260822` and `erp-forensic-lineid-20260822` removed | `docker rm -f`; `docker ps -a --filter name=erp-forensic` returns 0 rows |
| Volumes `erp-forensic-gateg-20260822-data` and `erp-forensic-lineid-20260822-data` removed | `docker volume rm`; `docker volume ls --filter name=erp-forensic` returns 0 rows |
| Detector, discovery and verification SQL removed from `dotmac-db-primary` and from the standby container | all `No such file or directory` |
| Wrong-account rehearsal `erp-fee-correction-rehearsal-20260823`, copied subset and `/tmp/codex-erp-fee-correction-20260823` removed | explicit container, source-container file and staging-path checks returned `cleanup=verified` |
| No full database dump ever written | Earlier tables streamed container-to-container. The wrong-account rehearsal wrote only the enumerated, column-limited non-PII CSV subset into temporary container/host paths; cleanup checks proved every file absent afterward. |
| Standby unchanged | `pg_is_in_recovery() = t`, `max_standby_streaming_delay = 30s`, replay advancing |
| Production primary | never connected to |
