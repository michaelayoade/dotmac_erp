# Finance correction memorandum — Accounting gate D defects

**Status: DRAFT — initial forensics complete for the three rounding defects and
the 1400 reversal overlap. Full composite-account tracing, cohort proof, Finance
decisions, reporting/tax assessment, approver, and operator remain
outstanding.**

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

> **SUPERSEDED BY APPENDIX A.** The detector has since run on an isolated
> restored database. 2,033 of the 2,039 pass every ledger-decidable proof; the
> remaining 6 are itemised in A2 and A6. Two findings in Appendix A change what
> this section and §4 assume: the cohort is **98.6% credit notes**, not
> invoices, and it carries **no VAT effect at all**.

The 2,039 are journals matching the stranded cohort's *producer*. They are not
yet proven stranded. **None may be called stranded, and no claim may be made
that their VAT effect is missing, until each passes the exact detector**:

> original → reversal → orphan → no later replacement

### The one-row delta (2,039 candidates vs 2,038 proven) is UNEXPLAINED

> **RESOLVED — see Appendix A6.** Kept as written because the reasoning below
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

> **THE OBJECTIVE BELOW IS WRONG FOR 2,010 OF THE 2,039 — see Appendix A3.**
> 98.6% of this population is CREDIT NOTES. Restoring a credit note reduces
> receivables and revenue; it does not restore missing income. The two
> directions are opposite economic acts and cannot share one approval. The
> per-invoice proofs below still stand and have been run (Appendix A1); it is
> the framing of the objective that must be corrected before any remediation
> path is designed.

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
total. Then determine whether management accounts, VAT/WHT/CIT filings, customer
balances or other issued reports relied on the incorrect ledger state.

Specific exposures this memorandum raises:

- **Trade Receivables understated by ₦1,619,218.75** since 2026-04-19 —
  affects customer balances, AR ageing and any balance sheet issued since.
- **VAT** — the ar/INVOICE candidates (₦39.2M gross) *may* contain VAT-bearing
  invoices whose output VAT was never posted. This cannot be asserted until the
  detector runs; what is required now is that VAT return periods covering
  2026-01 to 2026-07 be **checked**, not that a missing VAT effect be assumed.
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
| Recurrence fix landed (allocator + posting boundary + backlog tolerance) | *engineering, in progress — §2* |
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
| Disposition recorded for **every** remaining APPROVED journal | *not started* |
| Payroll journal (₦12,148,709.68) individually adjudicated | *pending* |
| Reporting and tax assessment for the backlog cohorts | *not yet performed* |

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

# Appendix A — Stranded-repost detector: results

**This appendix supersedes §3's "candidates, not a proven cohort" caveat and
§4's framing. Read it before acting on either.** §3 and §4 are left as written
so the change in understanding is visible; where they now say something this
evidence contradicts, this appendix governs.

## A0. Provenance

| item | value |
| --- | --- |
| Source | `dotmac_erp_standby` on `dotmac-db-primary` — hot standby, `pg_is_in_recovery() = t`, read-only. The production primary was **not** used, and no standby recovery setting was changed. |
| Replay LSN at export start | `BF/EBE4B5C0` (2026-08-22 09:02:58Z) |
| Replay LSN at export end | `BF/EBE8DA50` (2026-08-22 09:05:30Z) |
| Replay LSN at detector run | `BF/EC128148` (2026-08-22 09:14:02Z) |
| Server version | PostgreSQL 16.4 (source and target) |
| Migration revisions (source heads) | `20260815_academy_learning_sync`, `fi_0001_stored_files`, `20260815_academy_course_projection`, `20260816_platform_owned_webhook_ssrf_policy`, `20260818_dotmac_sub_customer_metrics` |
| Detector commit | `0fe3e49951e3faeaef87c36b5a0b6b85726585b4` |
| Detector query hash (sha256) | `d804178781d7a5657c735d12003cf713118ecb375c3ecb53c98b410dc007beef` |
| Target | `erp-forensic-20260822a`, ephemeral, `--network none`, no published port, no application attached. Destroyed after verification — see A7. |

**Snapshot integrity.** The three tables were exported as separate statements, so
the copy spans an LSN range rather than one snapshot. The candidate population,
its gross debit, and the row counts of all three tables were re-checked against
the standby after loading and matched exactly, and the cohort is 2026-03 to
2026-07 data that is not being written. The range is recorded rather than
claimed to be a point-in-time snapshot.

**What was copied.** Only the columns the proofs read, from four tables:
`gl.journal_entry` (14 columns), `gl.journal_entry_line` (4), `ar.invoice`
(4), `gl.account` (4). No description, no reference, no customer, no payload,
no credential. Nothing was written to disk on either host — each table was
streamed `COPY TO STDOUT` → `COPY FROM STDIN` directly between containers.

## A1. Counts at each proof stage

| stage | count | gross debit |
| --- | ---: | ---: |
| Candidates by producer (§3) | **2,039** | ₦39,211,120.76 |
| …with an original+reversal chain | 2,038 | — |
| …ambiguous (more than one chain) | 0 | — |
| Proof 1 — invoice still valid | 2,038 | — |
| Proof 2 — original was reversed | 2,038 | — |
| Proof 3 — reversal exactly eliminated the original | 2,038 | — |
| Proof 4 — orphan is the original's replacement | **2,033** | — |
| Proof 5 — nothing later already restored it | 2,038 | — |
| **Passing every ledger-decidable proof** | **2,033** | **₦38,247,308.26** |

Every candidate invoice is `PAID` (1,628), `POSTED` (406) or
`PARTIALLY_PAID` (5); none is void or cancelled, so proof 1 excludes nobody.
Every original a reversal points at is in status `REVERSED` — 2,038 of 2,038.

## A2. Disposition of all 2,039 candidates

| disposition | candidates | gross debit |
| --- | ---: | ---: |
| A — proven stranded | 2,033 | ₦38,247,308.26 |
| D — orphan is not the original's replacement | 5 | ₦945,000.00 |
| E — no original+reversal chain | 1 | ₦18,812.50 |
| **total** | **2,039** | **₦39,211,120.76** |

## A3. THE POPULATION IS CREDIT NOTES, NOT INVOICES

| document type | disposition | candidates | gross debit |
| --- | --- | ---: | ---: |
| CREDIT_NOTE | proven stranded | **2,010** | ₦29,891,761.62 |
| STANDARD | proven stranded | 23 | ₦8,355,546.64 |
| STANDARD | not proven (D) | 5 | ₦945,000.00 |
| STANDARD | no chain (E) | 1 | ₦18,812.50 |

**98.6% of this cohort is credit notes.** Posting direction confirms it
independently: all 2,010 credit-note candidates credit 1400 and debit 4000; all
29 standard-invoice candidates do the reverse. Type and direction agree on every
single row.

§4 says the objective is to "restore each still-valid **invoice's** missing GL
effect". For 2,010 of 2,039 that is the wrong economic act: restoring a credit
note **reduces** receivables and revenue. §4's framing must be corrected before
any remediation path is designed, and the two directions cannot share one
approval.

## A4. THERE IS NO MISSING VAT EFFECT IN THIS COHORT

§3 required that no claim of a missing VAT effect be made until tested. It is now
tested, and the answer is negative.

Every account touched by any of the 2,039 candidates — unfiltered, so an account
touched equally on both sides would still appear:

| account | journals | total debit | total credit |
| --- | ---: | ---: | ---: |
| 1400 Trade Receivables | 2,039 | ₦9,319,359.14 | ₦29,891,761.62 |
| 4000 Internet Revenue | 2,039 | ₦29,891,761.62 | ₦9,319,359.14 |

No VAT account (2125) appears on any candidate. **The VAT concern is closed for
this cohort.**

It is precisely the five proof-4 failures that make this legible: their
*originals* carry 2125 VAT lines and a second 4000 revenue line, while their
orphans carry only 1400 and 4000. That is why they fail proof 4 — the orphan
would post an incomplete replacement, dropping VAT.

## A5. Net effect if the proven cohort were posted

| account | net debit effect |
| --- | ---: |
| 1400 Trade Receivables | (₦21,536,214.98) |
| 4000 Internet Revenue | ₦21,536,214.98 |

The headline "₦39.21M" in §3 is the sum of absolute header debits across both
directions. **The net effect of posting the proven cohort is a ₦21,536,214.98
reduction in receivables and an equal reduction in revenue** — not ₦38.2M of
restored income. Section §6's reporting and tax assessment must use the net
figure and the direction, not the gross.

### By posting period

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

2026-03 carries 1,970 of 2,033 — the cohort is overwhelmingly one month.

## A6. The one-row delta is EXPLAINED

§3 recorded 2,039 candidates against a prior investigation's 2,038 and required
the difference be resolved by running the detector. It is:

**`JE202607-20448` (2026-03-05, ₦18,812.50) has no reversed original.** Its
invoice carries one other journal, `JE202607-9550`, which is `POSTED`, is not
a reversal, and posts the identical two lines — 1400 debit ₦18,812.50, 4000
credit ₦18,812.50 — on the identical date.

It is not a stranded repost. It is an **exact duplicate of an already-posted
journal**, and posting it would double-count ₦18,812.50.

Structural counts confirm the shape without using the detector's signatures at
all: 2,039 APPROVED orphans, 2,038 `REVERSED` originals, 2,038 `POSTED`
reversals, and exactly 1 `POSTED` non-reversal.

**Indicated disposition: VOID, not post.** That is a Finance determination, not
an engineering one.

### The five proof-4 failures, identified

| orphan | invoice | invoice status | orphan debit | why it fails |
| --- | --- | --- | ---: | --- |
| JE202607-21559 | INV202606-154298 | POSTED | ₦350,000.00 | orphan omits the original's VAT and second revenue line |
| JE202607-21557 | INV202605-155374 | POSTED | ₦350,000.00 | same |
| JE202607-21563 | INV202606-154300 | POSTED | ₦105,000.00 | same |
| JE202607-21565 | INV202606-154299 | POSTED | ₦70,000.00 | same |
| JE202607-21561 | INV202606-154297 | POSTED | ₦70,000.00 | same |

Each original posts 1400, 2125 (two VAT lines) and 4000 (two revenue lines); each
orphan posts only 1400 and 4000 for the base amount. **Posting these five as they
stand would understate output VAT and revenue.** They must not be included in any
bulk pass.

## A7. Limits on this result, stated plainly

1. **Proof 6 of §4 is not discharged.** Customer balance, tax treatment,
   currency and period correctness are not decidable from the ledger. "Proven
   stranded" here means *the ledger chain is proven*, nothing more. No candidate
   is approved for posting by this appendix.
2. **Proof 5 is necessary, not sufficient.** A replacement posted as a manual
   journal carrying no `source_document_id` cannot be linked to its invoice by
   any ledger query.
3. **No data was repaired.** No Gate D defect is corrected, and no candidate was
   posted, voided or altered. The detector runs in a transaction that ends in
   `ROLLBACK`, against a copy.
4. **A defect was found in the detector itself while running it**, and is
   recorded in the script rather than quietly fixed: an earlier version required
   the original to be `POSTED` and returned ZERO chains for all 2,039
   candidates. Reversed originals move to `REVERSED`, so that zero was a query
   defect, not a finding. Every number above comes from the corrected version,
   and each was re-derived by a second query that uses no signatures.
5. **Cleanup.** `erp-forensic-20260822a` and its volume
   `erp-forensic-20260822a-data` were destroyed after these results were
   verified; the detector and diagnostic SQL were removed from both hosts. No
   copy of the data survives. See the cleanup record below.

## A9. Reconciliation against the July 2026 investigation

The prior investigation (2026-07-20, `erp.dotmac.io`) recorded a **net
understatement of ₦20,591,214.98** across its 2,038. This appendix computes
**₦21,536,214.98** across the 2,033 proven. They reconcile exactly:

```
21,536,214.98  net over the 2,033 proven (credit to 1400)
  -945,000.00  the five proof-4 failures, which DEBIT 1400
= 20,591,214.98  net over all 2,038 chained candidates
```

₦945,000.00 is precisely the gross of the five itemised in A6. Two independent
investigations, four months apart, over the same ledger, agree to the kobo. That
is strong corroboration of both.

**What does NOT reconcile, and is left open:** the July figure for "gross
balanced line volume" was ₦39,348,870.76 against this appendix's ₦39,211,120.76
gross header debit — a difference of ₦137,750.00 over a smaller population. The
two are different measures (line volume vs header total), so the gap is not
necessarily an error, but it is **not explained here and should not be treated as
reconciled.**

**A contradiction in the earlier record, now settled.** The July entry described
the orphans as `STANDARD` in its body while calling them credit notes in its
summary. This appendix settles it from the data: **2,010 `CREDIT_NOTE` and 29
`STANDARD`**, with posting direction agreeing with document type on every row.
The summary was right and the body was wrong, and it is the body's reading that
appears to have carried into §4's framing of this memorandum.

**The one-row delta from the other direction.** July's population was 2,038
because its detector required a reversed original — which correctly excluded
`JE202607-20448`. §3's 2,039 came from counting by producer, which does not. The
two counts never described the same set, exactly as §3 warned they might not.

## A8. Cleanup record

| step | evidence |
| --- | --- |
| Container `erp-forensic-20260822a` removed | `docker rm -f` — `docker ps -a --filter name=erp-forensic-20260822a` returns 0 rows |
| Volume `erp-forensic-20260822a-data` removed | `docker volume rm` — `docker volume ls --filter name=erp-forensic-20260822a` returns 0 rows |
| Detector, diagnostic and output files removed from `dotmac-db-primary` | `/tmp/detector.sql`, `/tmp/detector-out.txt`, `/tmp/diag.sql`, `/tmp/verify.sql`, `/tmp/types.sql` — all `No such file or directory` |
| No dump file ever written | The tables were streamed container-to-container (`COPY TO STDOUT` → `COPY FROM STDIN`); no export file existed on either host at any point |
| Standby unchanged | `pg_is_in_recovery() = t`, `max_standby_streaming_delay = 30s` (untouched — and the reason the earlier correlated query was cancelled), replay advancing normally |
| Production primary | never connected to |

