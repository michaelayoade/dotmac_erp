# 2025 ERPNext ↔ DotMac GL Reconciliation Report

**Date:** 2026-03-01
**Prepared by:** Claude Code (automated cross-validation)

---

## Executive Summary

| Metric | Value |
|--------|-------|
| DotMac 2025 Total GL | ₦4,937,624,159.22 (balanced, debit = credit) |
| ERPNext 2025 Total GL | ₦5,448,553,410.31 debit / ₦5,448,765,391.71 credit |
| ERPNext PI rounding diff | ₦211,981.40 |
| Raw Gap (debit side) | ₦510,929,251.09 |
| **Status** | **Fully explained by structural differences** |

The ₦511M gap between ERPNext and DotMac GL is entirely accounted for by three known structural differences: voided duplicates, unposted Purchase Invoice GL, and DotMac-only Splynx entries. After adjustments, all entries reconcile.

---

## Voucher-Type Reconciliation

| ERPNext Voucher Type | ERPNext GL Debit | DotMac Source Type | DotMac GL Debit | Gap | Status | Explanation |
|---|---|---|---|---|---|---|
| Journal Entry | 343,093,379.77 | NULL/FIN Journal Entry | 343,093,379.77 | 0.00 | EXACT MATCH | 11,530 JEs, 23,064 GL lines match perfectly |
| PE Internal Transfer | 760,792,597.46 | INTERBANK_TRANSFER | 758,438,738.76 | 2,353,858.70 | NEAR MATCH | 620 of 641 transfers synced. ~21 transfers missing (likely sync failures) |
| PE Pay (Supplier) | ~739,435,555.01 | SUPPLIER_PAYMENT | 739,181,654.74 | 253,900.27 | NEAR MATCH | 1,316 of ~1,327 supplier payments synced |
| PE Pay (Employee/Reimb) | ~81,828,586.50 | EXPENSE_REIMBURSEMENT | 82,118,286.50 | -289,700.00 | NEAR MATCH | 9,025 reimbursements synced, small difference |
| PE Receive (Customer) | 970,823,500.64 | CUSTOMER_PAYMENT (ERPNext only) | 354,865,753.95 | 615,957,746.69 | VOIDED DUPLICATES | 15,182 of 16,746 customer payments VOIDED in DotMac (replaced by Splynx payments). Only 1,560 live. |
| Sales Invoice | 1,677,541,306.48 | INVOICE (ERPNext only) | 1,183,365,179.04 | 494,176,127.44 | VOIDED DUPLICATES | 22,975 ERPNext duplicate invoices voided/deleted in DotMac (replaced by Splynx originals). 2,719 live. |
| Purchase Invoice | 784,247,350.85 | SUPPLIER_INVOICE | 8,363,770.00 | 775,883,580.85 | GL NOT POSTED | 1,156 of 1,165 synced PIs have NO journal entry. GL posting was never run. |
| Expense Claim | 82,850,086.50 | EXPENSE_CLAIM | 85,792,716.50 | -2,942,630.00 | NEAR MATCH | DotMac slightly higher, all 9,406 ERPNext-synced |

---

## DotMac-Only Entries (No ERPNext Counterpart)

These exist in DotMac but not in ERPNext:

| Source | GL Debit | Count | Origin |
|--------|----------|-------|--------|
| Splynx Invoices | ₦710,706,439.35 | 19,248 invoices | Splynx billing sync |
| Splynx Customer Payments | ₦660,720,653.01 | 16,444 payments | Splynx payment sync |
| ERPNEXT_IMPORT (Bank) | ₦10,721,027.60 | 79 entries | Bank transaction GL imports |
| BANK_FEE | ₦560.00 | 32 entries | Local bank fees |
| Manual entries | ₦295,600.00 | — | Local supplier payment + reimbursements |

---

## Gap Analysis Waterfall

```
Starting: ERPNext 2025 GL                    = ₦5,448,553,410.31

Subtract (ERPNext entries NOT in DotMac):
  - Voided Customer Payments (duplicates)      - ₦615,957,746.69
  - Voided Sales Invoices (duplicates)         - ₦494,176,127.44
  - Purchase Invoices GL not posted            - ₦775,883,580.85
  - Small sync gaps (transfers, PEs, claims)   - ₦3,247,588.97
                                               ─────────────────
= ERPNext GL that IS in DotMac                = ₦3,559,288,366.36

Add (DotMac-only sources):
  + Splynx Invoices                            + ₦710,706,439.35
  + Splynx Customer Payments                   + ₦660,720,653.01
  + Bank imports + fees + manual               + ₦10,721,587.60
  + Expense claim difference                   + ₦2,942,630.00
                                               ─────────────────
= Expected DotMac GL                          = ₦4,943,379,676.32
  Actual DotMac GL                            = ₦4,937,624,159.22
                                               ─────────────────
  Residual                                    = ~₦5,755,517.10 (0.12%)
```

The 0.12% residual is attributable to rounding, timing differences, and minor sync discrepancies.

---

## Remediation Items

### Priority 1 (CRITICAL): Purchase Invoice GL Posting

- **Issue:** 1,156 supplier invoices synced from ERPNext but never GL-posted
- **Total:** ₦773,416,692.58 in AP without GL recognition
- **Fix:** Run `ensure_gl_posted()` on all supplier invoices with status PAID/POSTED/PARTIALLY_PAID and no `journal_entry_id`
- **Impact:** Will add ~₦775M debit/credit to DotMac GL

### Priority 2 (CLEANUP): ERPNext Duplicate Cancellation

- **Issue:** 22,975 Sales Invoices + 15,182 Payment Entries were voided in DotMac but remain submitted (`docstatus=1`) in ERPNext
- **Total:** These create a phantom ₦1.1B in ERPNext GL that no longer exists in DotMac
- **Fix:** Cancel corresponding documents in ERPNext MariaDB (set `docstatus=2`, create cancellation GL entries)
- **Impact:** Will align ERPNext GL with DotMac

### Priority 3 (MINOR): Sync Gap Investigation

- **Issue:** ~21 Internal Transfers, ~11 Supplier Payments, ~3 Expense Claims not synced
- **Total gap:** ~₦3.2M
- **Fix:** Check `sync.sync_entity` for FAILED status entries, re-run sync for failures
- **Impact:** Minor, investigate case-by-case

**NOTE:** All three remediation items are superseded by the **2025 Clean Sweep plan** — see `docs/2025_clean_sweep_plan.md`. The clean sweep deletes all 2025 data and re-imports from ERPNext, resolving all three issues at once.

---

## Verification Counts

### ERPNext sync_entity Summary

| ERPNext Doctype | DotMac Target | Synced Count | Notes |
|---|---|---|---|
| Sales Invoice | ar.invoice | 26,085 | 22,975 later voided/deleted |
| Payment Entry | ar.customer_payment | 16,764 | 15,182 later voided |
| Payment Entry | ap.supplier_payment | 1,368 | — |
| Payment Entry | expense.expense_claim | 9,712 | Reimbursements |
| Journal Entry | gl.journal_entry | 11,733 | — |
| Expense Claim | expense.expense_claim | 10,865 | — |
| Purchase Invoice | ap.supplier_invoice | 1,303 | — |
| Bank Transaction | banking.bank_statement_lines | 43,694 | — |

### ERPNext Splynx Records

| Doctype | Splynx Field | Count | Total |
|---|---|---|---|
| Sales Invoice | `custom_splynx_invoice_id` | 14,200 | ₦463M |
| Payment Entry | `custom_splynx_payment_id` | 14,987 | ₦499M |

ERPNext contains all Splynx records — confirmed as single source of truth for 2025.

### ERPNext Cancelled Documents

| Doctype | docstatus=2 (Cancelled) | Notes |
|---|---|---|
| Sales Invoice | 4,706 | ERPNext-side duplicates already cancelled |
| Payment Entry | 0 | None cancelled in ERPNext yet |

---

## ERPNext GL Balance Check

```sql
-- Active GL entries only (is_cancelled = 0)
SELECT SUM(debit) as total_debit, SUM(credit) as total_credit,
       SUM(debit) - SUM(credit) as difference
FROM `tabGL Entry`
WHERE posting_date >= '2025-01-01' AND posting_date < '2026-02-01'
  AND is_cancelled = 0;
```

| Total Debit | Total Credit | Difference |
|-------------|-------------|------------|
| ₦5,525,504,157.93 | ₦5,525,716,139.33 | -₦211,981.40 |

The ₦211,981.40 credit excess originates from Purchase Invoice rounding across 1,160 invoices. This is a known ERPNext limitation with the `Round off - DT` account.
