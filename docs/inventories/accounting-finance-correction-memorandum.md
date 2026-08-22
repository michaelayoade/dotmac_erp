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

### Effect

The six originals were `Dr 1400 / Cr 3100`. Reversing them twice credits 1400
twice and debits 3100 twice:

- **Trade Receivables (1400) understated by ₦1,619,218.75**
- **Retained Earnings (3100) correspondingly misstated by ₦1,619,218.75**

**No compensating correction exists.** A search for any journal after 2026-04-19
touching account 1400 for ~1,619,218.75 returns zero rows.

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

### Indicated treatment, if the audited opening balance confirms the composite was intended

1. **Preserve `REV-SYNC-OB-001` as a plain adjustment**, not a 16-to-1 reversal.
2. **Reverse each later duplicate reversal individually** — the six
   `JE202604-43275…43280`.
3. For the six proven cases that produces the required correction: **debit Trade
   Receivables and credit Retained Earnings by ₦1,619,218.75**.
4. Apply the same treatment to other accounts **only where the matrix proves
   duplication** — which, on this evidence, is nowhere else.
5. **Preserve every historical journal.** Add correction chains; never delete or
   rewrite economic history.

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

The 2,039 are journals matching the stranded cohort's *producer*. They are not
yet proven stranded. **None may be called stranded, and no claim may be made
that their VAT effect is missing, until each passes the exact detector**:

> original → reversal → orphan → no later replacement

### The one-row delta (2,039 candidates vs 2,038 proven) is UNEXPLAINED

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

## 4. Treatment of the stranded AR cohort (2,039 candidates)

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
- **WHT** — `REV-SYNC-OB-001` moves ₦68,308,470.18 on account 1420 and
  ₦14,536,448.13 on 2110. **The matrix in §1 shows no duplicate reversal on
  either account, so neither is a misstatement.** The only WHT item outstanding
  is the ₦0.16 difference between the sync opening balance and OB-000001 on
  1420.

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

| Item | Value |
| --- | --- |
| Population and financial exposure | §3 above — 14,263 journals, ₦76,495,739.50 |
| Root cause and source evidence | §1, §2 above |
| Treatment: three unbalanced journals | *pending Finance decision* |
| Treatment: malformed reversal | *pending Finance decision* |
| Treatment: six journals with no provenance | *pending Finance decision* |
| Treatment: source-module casing | Normalise on read; no ERP data rewrite |
| Approved classification policy for the 14,263 | §5 above — *pending approval* |
| Reporting and tax assessment | §6 — *not yet performed* |
| Named approver | *pending* |
| Named operator | *pending* |
| Before/after reconciliation requirements | §8 below |

---

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
