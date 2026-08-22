# Finance correction memorandum — Accounting gate D defects

**Status: DRAFT. Evidence complete; decisions, approver and operator are
Finance's and are not filled in.** Engineering produced the forensics below and
must not resolve any item in it. Gate D execution stays blocked until this
memorandum is approved and the repairs are made and verified.

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

This is materially larger than everything else on the gate D defect list, and it
was found only because the instruction was to adjudicate from source evidence
rather than infer from the `is_reversal` flag.

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

### Classification against the adjudication policy

| Policy row | Applies? |
| --- | --- |
| Exact inverse of one original with no other reversal | **No** — it targets sixteen originals, not one |
| Valid independent journal, not an inverse | **No** — it *is* an inverse, and its effect is duplicated |
| **Duplicate or economically incorrect effect** | **Yes** |
| Evidence remains ambiguous | No — the amounts tie exactly |

**Indicated Finance action: process a proper correction through the normal
finance workflow.** Clearing the `is_reversal` flag would hide a ₦1.6M
misstatement behind a metadata tidy-up.

### Finance must still determine

1. Which of the two reversals was intended, and whether the ₦1,619,218.75
   correction is a current-period correction or a prior-period restatement
   (§5 below).
2. Whether the other ten originals in `JE-2025-00017 thru 00080` named by
   `REV-SYNC-OB-001` are correctly reversed once — this memorandum verified the
   1400 block only.
3. Whether the same double-reversal pattern affects the other accounts
   `REV-SYNC-OB-001` touches: 1211, 1220, 1420, 2000, 2110, 3100. Account 1420
   (Withholding Taxes) carries ₦68,308,470.18 in that journal and has **not**
   been checked.

**Item 3 is the largest open exposure in this memorandum and should be resolved
before anything else here is actioned.**

### A structural consequence for the migration

`dotmac-accounting` links a reversal to exactly one original, with a unique
constraint permitting one reversal per journal. **A 16-to-1 composite reversal
cannot be represented as a reversal in the module at all.** Whatever Finance
decides, the resulting journals must end as either one-to-one reversals or plain
adjustments. This is not a preference — it is a representability constraint on
the backfill.

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
value. Engineering's recommendation, for Finance to accept or vary: absorb the
residue in **line 2** (the largest line, and one of the two with the largest
round-up), giving `103,610.294117`.

Per instruction, **no balanced adjustment journal**: adding equal debit and
credit cannot remove an existing difference. No suspense or rounding account.
Header, lines, posted-ledger evidence, functional-currency amounts and dependent
projections update atomically, with before/after values, fingerprints, approver,
operator, rationale and work-item reference preserved in repair evidence.

### Root cause — and why data repair alone is insufficient

The AR invoice posting adapter **rounds each revenue line independently instead
of forcing the final line to absorb the allocation residue.** That is a code
defect, and it recurs.

Supporting evidence:

- **132 AR invoice journals** carry revenue lines with more than two decimal
  places — the same non-terminating split pattern. Three of them happen to
  round in a direction that breaks balance; the rest do not.
- **28 journals** exist for this customer alone, spanning 2026-01-14 to
  2026-07-02, of which these three tripped it.

Repairing the three rows without fixing the adapter leaves the next month's
invoice free to reproduce the defect. **Recommend a linked engineering
work-item: make the allocation force the residue onto the last line.** That fix
is separate from this memorandum and separate from gate D.

### Materiality

₦0.000003 across the three. Financially immaterial. The integrity defect is not:
every posted journal must balance independently, and the module refuses
unbalanced posting outright.

---

## 3. Population and financial exposure — the APPROVED backlog

14,263 journals are APPROVED but never posted, spanning 2026-01-15 to
2026-07-22. They carry **no ledger effect**. Total unposted debit: **₦76,495,739.50**.

The composition is not what the gate D plan assumed. It is dominated by bank
fees, not by the stranded AR cohort:

| producer | document type | journals | unposted debit | share of count |
| --- | --- | ---: | ---: | ---: |
| banking | BANK_FEE | **12,117** | ₦11,813,979.50 | 85.0% |
| ar | INVOICE | **2,039** | ₦39,211,120.76 | 14.3% |
| ar | CUSTOMER_PAYMENT | 98 | ₦12,564,606.69 | 0.7% |
| banking | BANK_RECONCILIATION | 6 | ₦726,322.87 | — |
| expense | EXPENSE_REIMBURSEMENT | 2 | ₦31,000.00 | — |
| payroll | PAYROLL_ENTRY | 1 | ₦12,148,709.68 | — |
| | **total** | **14,263** | **₦76,495,739.50** | |

Two observations:

- **`ar/INVOICE` = 2,039 is the stranded repost cohort** (previously stated as
  ~2,038). The cohort is exactly identifiable by producer, which makes it
  separable for the purpose-built remediation path.
- **The single `payroll/PAYROLL_ENTRY` journal carries ₦12,148,709.68** — one
  record holding 16% of the exposure. It warrants individual adjudication, not
  inclusion in a bulk classification pass.

### Prioritisation by value and risk

Following the required order — bank/cash, tax, AR/AP control, revenue, then
remaining expense and balance-sheet:

| order | cohort | journals | exposure | why |
| ---: | --- | ---: | ---: | --- |
| 1 | ar/CUSTOMER_PAYMENT | 98 | ₦12.56M | bank/cash and AR control |
| 2 | banking/BANK_FEE + BANK_RECONCILIATION | 12,123 | ₦12.54M | bank/cash; largest count |
| 3 | ar/INVOICE (stranded cohort) | 2,039 | ₦39.21M | AR control, revenue, VAT — largest exposure |
| 4 | payroll/PAYROLL_ENTRY | 1 | ₦12.15M | single high-value record |
| 5 | expense/EXPENSE_REIMBURSEMENT | 2 | ₦0.03M | residual |

---

## 4. Treatment of the stranded AR cohort (2,039)

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

Not yet performed: the per-invoice proof itself. This memorandum establishes the
cohort's identity and size; the evidence pass over all 2,039 is the next
engineering task and its output belongs in an appendix here.

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
- **VAT** — the stranded AR cohort (₦39.2M) contains VAT-bearing invoices whose
  output VAT was never posted. VAT return periods covering 2026-01 to 2026-07
  must be checked.
- **WHT** — `REV-SYNC-OB-001` moves ₦68,308,470.18 on account 1420 (Withholding
  Taxes) and ₦14,536,448.13 on 2110 (WHT Payable). Neither has been verified for
  the double-reversal pattern.

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
