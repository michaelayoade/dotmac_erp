---
name: books-audit
description: "IFRS/ISA-grounded accounting audit: verify GL postings, expense accounts, banking matches, subledger reconciliation, payment integrity, revenue recognition, asset impairment, lease compliance, and book cleanliness"
arguments:
  - name: scope
    description: "'full' for all checks, or a specific check: 'expenses', 'banking', 'trial-balance', 'subledger', 'payments', 'postings', 'classifications', 'revenue', 'leases', 'assets', 'inventory', 'cutoff', 'disclosure'"
  - name: period
    description: "Optional fiscal period filter, e.g. '2026-01' or '2025-Q4' or 'all' (default: current month)"
---

# Accounting Books Audit

Act as a **qualified chartered accountant and internal auditor** (ICAN/ACCA/IFAC-aligned) reviewing the books of a DotMac ERP organization. Apply **ISA (International Standards on Auditing)** risk-based methodology and verify compliance with **IFRS/IAS** recognition, measurement, and disclosure requirements.

All queries use the `erp-db` MCP server (`execute_sql` tool). **Every query MUST be prefixed** with:
```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';
```
in the same `execute_sql` call.

---

## Auditing Framework

This audit follows **ISA 315 (Revised 2019)** — Understanding the Entity and Its Environment, and **ISA 330** — The Auditor's Responses to Assessed Risks.

### Audit Assertions Tested

Every check maps to one or more **financial statement assertions** (ISA 315.A190):

| Assertion | Code | What It Means |
|-----------|------|---------------|
| **Existence/Occurrence** | E/O | Recorded transactions actually happened; assets/liabilities exist |
| **Completeness** | C | All transactions that should be recorded are recorded |
| **Accuracy/Valuation** | A/V | Amounts are correctly recorded at proper values |
| **Classification** | CL | Transactions posted to correct accounts per IFRS taxonomy |
| **Cut-off** | CO | Transactions recorded in the correct period |
| **Rights & Obligations** | R/O | Entity has rights to assets and obligations for liabilities |
| **Presentation & Disclosure** | P/D | Amounts properly aggregated, described, and disclosed per IFRS |

### Materiality (ISA 320)

When interpreting findings, apply materiality:
- **Planning materiality**: ~1-2% of total revenue or total assets (whichever is larger)
- **Performance materiality**: 60-75% of planning materiality
- **Trivial threshold**: 5% of planning materiality (below this, aggregate only)

Calculate materiality from the data:

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT
    -- Revenue base (last 12 months)
    (SELECT COALESCE(SUM(credit_amount), 0) FROM gl.posted_ledger_line pll
     JOIN gl.account a ON a.account_id = pll.account_id
     WHERE a.account_code LIKE '4%' AND pll.posting_date >= CURRENT_DATE - INTERVAL '12 months'
    ) AS annual_revenue,
    -- Total assets
    (SELECT COALESCE(SUM(debit_amount - credit_amount), 0) FROM gl.posted_ledger_line pll
     JOIN gl.account a ON a.account_id = pll.account_id
     WHERE a.account_code LIKE '1%'
    ) AS total_assets,
    -- Suggested planning materiality (1.5% of larger base)
    ROUND(GREATEST(
        (SELECT COALESCE(SUM(credit_amount), 0) FROM gl.posted_ledger_line pll
         JOIN gl.account a ON a.account_id = pll.account_id
         WHERE a.account_code LIKE '4%' AND pll.posting_date >= CURRENT_DATE - INTERVAL '12 months'),
        (SELECT COALESCE(SUM(debit_amount - credit_amount), 0) FROM gl.posted_ledger_line pll
         JOIN gl.account a ON a.account_id = pll.account_id
         WHERE a.account_code LIKE '1%')
    ) * 0.015, 2) AS planning_materiality;
```

Report this at the top of every audit. Classify each finding as **material** or **immaterial** relative to this threshold.

---

## Check Categories

Run the requested scope, or all checks if `full`. Checks 1-7 are **data integrity** (mechanical errors). Checks 8-12 are **IFRS compliance** (accounting standards).

---

### 1. Expense Account Verification (`expenses`)

**Assertions tested**: Classification (CL), Accuracy (A/V)
**IFRS reference**: IAS 1.99 — expenses shall be classified by nature or function

**Purpose**: Detect expense claims where the GL journal posted to a different account than what the expense item or category specifies.

```sql
-- Compare expense item accounts vs actual GL journal line accounts
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT
    ec.claim_number,
    ec.claim_date,
    ec.status AS claim_status,
    eci.description AS item_description,
    eci.approved_amount,
    COALESCE(item_acct.account_code, cat_acct.account_code) AS expected_account_code,
    COALESCE(item_acct.account_name, cat_acct.account_name) AS expected_account_name,
    actual_acct.account_code AS actual_account_code,
    actual_acct.account_name AS actual_account_name,
    je.journal_number,
    je.status AS journal_status
FROM expense.expense_claim ec
JOIN expense.expense_claim_item eci ON eci.claim_id = ec.claim_id
LEFT JOIN gl.account item_acct ON item_acct.account_id = eci.expense_account_id
LEFT JOIN expense.expense_category ecat ON ecat.category_id = eci.category_id
LEFT JOIN gl.account cat_acct ON cat_acct.account_id = ecat.expense_account_id
LEFT JOIN gl.journal_entry je ON je.journal_entry_id = ec.journal_entry_id
LEFT JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
    AND jel.debit_amount > 0
    AND jel.description ILIKE '%' || LEFT(eci.description, 30) || '%'
LEFT JOIN gl.account actual_acct ON actual_acct.account_id = jel.account_id
WHERE ec.journal_entry_id IS NOT NULL
  AND je.status IN ('POSTED', 'APPROVED')
  AND COALESCE(item_acct.account_id, cat_acct.account_id) IS NOT NULL
  AND actual_acct.account_id IS NOT NULL
  AND actual_acct.account_id != COALESCE(item_acct.account_id, cat_acct.account_id)
ORDER BY ec.claim_date DESC
LIMIT 50;
```

**Broader check** (aggregate comparison when line-level join misses):

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT
    ec.claim_number, ec.claim_date, ec.total_approved_amount, je.journal_number,
    string_agg(DISTINCT COALESCE(item_acct.account_code, cat_acct.account_code), ', ') AS expected_accounts,
    (SELECT string_agg(DISTINCT a2.account_code, ', ')
     FROM gl.journal_entry_line jel2 JOIN gl.account a2 ON a2.account_id = jel2.account_id
     WHERE jel2.journal_entry_id = je.journal_entry_id AND jel2.debit_amount > 0) AS actual_debit_accounts
FROM expense.expense_claim ec
JOIN expense.expense_claim_item eci ON eci.claim_id = ec.claim_id
LEFT JOIN gl.account item_acct ON item_acct.account_id = eci.expense_account_id
LEFT JOIN expense.expense_category ecat ON ecat.category_id = eci.category_id
LEFT JOIN gl.account cat_acct ON cat_acct.account_id = ecat.expense_account_id
JOIN gl.journal_entry je ON je.journal_entry_id = ec.journal_entry_id
WHERE je.status IN ('POSTED', 'APPROVED')
GROUP BY ec.claim_id, ec.claim_number, ec.claim_date, ec.total_approved_amount, je.journal_number, je.journal_entry_id
HAVING string_agg(DISTINCT COALESCE(item_acct.account_code, cat_acct.account_code), ', ')
    != (SELECT string_agg(DISTINCT a2.account_code, ', ')
        FROM gl.journal_entry_line jel2 JOIN gl.account a2 ON a2.account_id = jel2.account_id
        WHERE jel2.journal_entry_id = je.journal_entry_id AND jel2.debit_amount > 0)
ORDER BY ec.claim_date DESC LIMIT 50;
```

**Completeness check** — approved claims never posted (ISA 315 — completeness assertion):

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT claim_number, claim_date, status, total_approved_amount, employee_id
FROM expense.expense_claim
WHERE status IN ('APPROVED', 'PAID') AND journal_entry_id IS NULL
ORDER BY claim_date DESC LIMIT 30;
```

**Severity**: P0 if accounts mismatch (classification error — IAS 1 violation). P1 if approved claims not posted (completeness gap).

---

### 2. Banking Match Quality (`banking`)

**Assertions tested**: Existence (E/O), Completeness (C), Rights & Obligations (R/O)
**ISA reference**: ISA 505 — External Confirmations (bank confirmations), ISA 530 — Audit Sampling

**Purpose**: Detect bank statement matches that go to raw GL journals instead of traceable source documents.

```sql
-- Matches without a proper source document
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT
    bsl.transaction_date, bsl.description AS bank_description, bsl.amount, bsl.bank_reference,
    bslm.match_type, bslm.source_type, bslm.source_id,
    je.journal_number, je.description AS journal_description,
    je.source_module, je.source_document_type,
    CASE
        WHEN bslm.source_type IS NULL OR bslm.source_type = '' THEN 'NO SOURCE DOC — audit trail broken'
        WHEN bslm.source_id IS NULL THEN 'SOURCE TYPE BUT NO ID — incomplete trace'
        ELSE 'OK'
    END AS match_quality
FROM banking.bank_statement_line_matches bslm
JOIN banking.bank_statement_lines bsl ON bsl.line_id = bslm.statement_line_id
JOIN gl.journal_entry_line jel ON jel.line_id = bslm.journal_line_id
JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
WHERE (bslm.source_type IS NULL OR bslm.source_type = '' OR bslm.source_id IS NULL)
ORDER BY bsl.transaction_date DESC LIMIT 50;
```

**Orphaned source references** (ISA 500 — sufficient appropriate audit evidence):

```sql
-- AR payment matches where the payment record is missing
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT bsl.transaction_date, bsl.amount, bsl.description, bslm.source_type, bslm.source_id
FROM banking.bank_statement_line_matches bslm
JOIN banking.bank_statement_lines bsl ON bsl.line_id = bslm.statement_line_id
WHERE bslm.source_type = 'CUSTOMER_PAYMENT' AND bslm.source_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM ar.customer_payment cp WHERE cp.payment_id = bslm.source_id)
LIMIT 20;
```

```sql
-- AP payment matches where the payment record is missing
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT bsl.transaction_date, bsl.amount, bsl.description, bslm.source_type, bslm.source_id
FROM banking.bank_statement_line_matches bslm
JOIN banking.bank_statement_lines bsl ON bsl.line_id = bslm.statement_line_id
WHERE bslm.source_type = 'SUPPLIER_PAYMENT' AND bslm.source_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM ap.supplier_payment sp WHERE sp.payment_id = bslm.source_id)
LIMIT 20;
```

**Match quality distribution**:

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT bslm.source_type, bslm.match_type, COUNT(*) AS match_count,
    ROUND(SUM(ABS(bsl.amount))::numeric, 2) AS total_amount
FROM banking.bank_statement_line_matches bslm
JOIN banking.bank_statement_lines bsl ON bsl.line_id = bslm.statement_line_id
GROUP BY bslm.source_type, bslm.match_type ORDER BY match_count DESC;
```

**Unmatched statement lines** (potential unrecorded transactions — completeness risk):

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT bs.statement_date, ba.account_name AS bank_account,
    bs.total_lines, bs.matched_lines, bs.unmatched_lines,
    ROUND((bs.unmatched_lines::numeric / NULLIF(bs.total_lines, 0) * 100), 1) AS unmatched_pct
FROM banking.bank_statements bs
JOIN banking.bank_accounts ba ON ba.bank_account_id = bs.bank_account_id
WHERE bs.unmatched_lines > 0 ORDER BY bs.statement_date DESC LIMIT 20;
```

**Severity**: P0 if matches have no source document. P1 if orphaned source references. P2 for high unmatched percentage (>15%).

---

### 3. Trial Balance Verification (`trial-balance`)

**Assertions tested**: Accuracy (A/V), Completeness (C)
**IFRS reference**: IAS 1.28 — accounting equation (Assets = Liabilities + Equity)
**ISA reference**: ISA 520 — Analytical Procedures

```sql
-- Overall trial balance
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT
    SUM(debit_amount) AS total_debits, SUM(credit_amount) AS total_credits,
    SUM(debit_amount) - SUM(credit_amount) AS difference,
    CASE WHEN ABS(SUM(debit_amount) - SUM(credit_amount)) < 0.01 THEN 'BALANCED' ELSE 'OUT OF BALANCE' END AS status
FROM gl.posted_ledger_line;
```

**Per-period** (identify which period went out of balance):

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT fp.period_name, fp.start_date, fp.end_date, fp.status AS period_status,
    SUM(pll.debit_amount) AS total_debits, SUM(pll.credit_amount) AS total_credits,
    ROUND((SUM(pll.debit_amount) - SUM(pll.credit_amount))::numeric, 2) AS difference
FROM gl.posted_ledger_line pll
JOIN gl.fiscal_period fp ON fp.fiscal_period_id = pll.fiscal_period_id
GROUP BY fp.fiscal_period_id, fp.period_name, fp.start_date, fp.end_date, fp.status
HAVING ABS(SUM(pll.debit_amount) - SUM(pll.credit_amount)) > 0.01
ORDER BY fp.start_date DESC;
```

**Unbalanced individual journals**:

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT je.journal_number, je.entry_date, je.description, je.source_module, je.source_document_type,
    SUM(jel.debit_amount) AS total_debits, SUM(jel.credit_amount) AS total_credits,
    SUM(jel.debit_amount) - SUM(jel.credit_amount) AS imbalance
FROM gl.journal_entry je
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
WHERE je.status = 'POSTED'
GROUP BY je.journal_entry_id, je.journal_number, je.entry_date, je.description, je.source_module, je.source_document_type
HAVING ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) > 0.000001
ORDER BY ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) DESC LIMIT 30;
```

**Accounting equation check** (IAS 1.28):

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT
    (SELECT COALESCE(SUM(debit_amount - credit_amount), 0) FROM gl.posted_ledger_line pll
     JOIN gl.account a ON a.account_id = pll.account_id WHERE a.account_code LIKE '1%') AS total_assets,
    (SELECT COALESCE(SUM(credit_amount - debit_amount), 0) FROM gl.posted_ledger_line pll
     JOIN gl.account a ON a.account_id = pll.account_id WHERE a.account_code LIKE '2%') AS total_liabilities,
    (SELECT COALESCE(SUM(credit_amount - debit_amount), 0) FROM gl.posted_ledger_line pll
     JOIN gl.account a ON a.account_id = pll.account_id WHERE a.account_code LIKE '3%') AS total_equity,
    (SELECT COALESCE(SUM(credit_amount - debit_amount), 0) FROM gl.posted_ledger_line pll
     JOIN gl.account a ON a.account_id = pll.account_id WHERE a.account_code LIKE '4%') AS total_revenue,
    (SELECT COALESCE(SUM(debit_amount - credit_amount), 0) FROM gl.posted_ledger_line pll
     JOIN gl.account a ON a.account_id = pll.account_id
     WHERE a.account_code LIKE '5%' OR a.account_code LIKE '6%' OR a.account_code LIKE '7%') AS total_expenses;
```

Then verify: `Assets = Liabilities + Equity + (Revenue - Expenses)`. If not, report the imbalance.

**Severity**: P0 if trial balance or accounting equation out of balance. P0 if individual journals unbalanced.

---

### 4. Subledger Reconciliation (`subledger`)

**Assertions tested**: Completeness (C), Accuracy (A/V), Existence (E/O)
**ISA reference**: ISA 505 — subledger-to-GL reconciliation is a core substantive procedure

```sql
-- AR subledger vs GL control accounts
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT
    'AR Subledger' AS check_type,
    (SELECT COALESCE(SUM(
        CASE WHEN i.invoice_type = 'CREDIT_NOTE' THEN -(i.total_amount - COALESCE(i.amount_paid, 0))
             ELSE (i.total_amount - COALESCE(i.amount_paid, 0)) END
    ), 0) FROM ar.invoice i WHERE i.status NOT IN ('VOID', 'DRAFT')) AS subledger_balance,
    (SELECT COALESCE(SUM(pll.debit_amount - pll.credit_amount), 0)
     FROM gl.posted_ledger_line pll JOIN gl.account a ON a.account_id = pll.account_id
     WHERE a.subledger_type = 'AR') AS gl_balance;
```

```sql
-- AP subledger vs GL control accounts
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT
    'AP Subledger' AS check_type,
    (SELECT COALESCE(SUM(si.total_amount - COALESCE(si.amount_paid, 0)), 0)
     FROM ap.supplier_invoice si WHERE si.status NOT IN ('VOID', 'DRAFT')) AS subledger_balance,
    (SELECT COALESCE(SUM(pll.credit_amount - pll.debit_amount), 0)
     FROM gl.posted_ledger_line pll JOIN gl.account a ON a.account_id = pll.account_id
     WHERE a.subledger_type = 'AP') AS gl_balance;
```

**Per-customer AR detail** (spot individual discrepancies):

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT c.customer_name, c.customer_code,
    SUM(CASE WHEN i.invoice_type = 'CREDIT_NOTE' THEN -(i.total_amount - COALESCE(i.amount_paid, 0))
             ELSE (i.total_amount - COALESCE(i.amount_paid, 0)) END) AS outstanding_balance,
    COUNT(*) AS open_invoice_count
FROM ar.invoice i JOIN ar.customer c ON c.customer_id = i.customer_id
WHERE i.status NOT IN ('VOID', 'DRAFT', 'PAID')
GROUP BY c.customer_id, c.customer_name, c.customer_code
HAVING ABS(SUM(CASE WHEN i.invoice_type = 'CREDIT_NOTE' THEN -(i.total_amount - COALESCE(i.amount_paid, 0))
                     ELSE (i.total_amount - COALESCE(i.amount_paid, 0)) END)) > 0.01
ORDER BY outstanding_balance DESC LIMIT 30;
```

**Bank subledger** (GL bank accounts vs bank statement balances):

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT
    ba.account_name AS bank_account,
    ba.current_balance AS bank_system_balance,
    (SELECT COALESCE(SUM(pll.debit_amount - pll.credit_amount), 0)
     FROM gl.posted_ledger_line pll WHERE pll.account_id = ba.gl_account_id) AS gl_balance,
    ba.current_balance - (SELECT COALESCE(SUM(pll.debit_amount - pll.credit_amount), 0)
     FROM gl.posted_ledger_line pll WHERE pll.account_id = ba.gl_account_id) AS difference
FROM banking.bank_accounts ba
WHERE ba.is_active = true;
```

**Severity**: P0 if subledger/GL difference > materiality. P1 if any difference. INFO if balanced.

---

### 5. Payment Integrity (`payments`)

**Assertions tested**: Accuracy (A/V), Existence (E/O), Completeness (C)
**ISA reference**: ISA 500 — sufficient appropriate evidence for payment transactions

```sql
-- Invoices where amount_paid doesn't match sum of allocations
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT i.invoice_number, i.invoice_date, i.total_amount,
    i.amount_paid AS recorded_amount_paid,
    COALESCE(alloc.total_allocated, 0) AS actual_allocated,
    i.amount_paid - COALESCE(alloc.total_allocated, 0) AS discrepancy, i.status
FROM ar.invoice i
LEFT JOIN (SELECT invoice_id, SUM(allocated_amount) AS total_allocated
    FROM ar.payment_allocation GROUP BY invoice_id) alloc ON alloc.invoice_id = i.invoice_id
WHERE i.status NOT IN ('VOID', 'DRAFT')
  AND ABS(COALESCE(i.amount_paid, 0) - COALESCE(alloc.total_allocated, 0)) > 0.01
ORDER BY ABS(COALESCE(i.amount_paid, 0) - COALESCE(alloc.total_allocated, 0)) DESC LIMIT 30;
```

```sql
-- Invoices marked PAID but still have outstanding balance
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT i.invoice_number, i.invoice_date, i.total_amount, i.amount_paid,
    i.total_amount - COALESCE(i.amount_paid, 0) AS outstanding, i.status
FROM ar.invoice i WHERE i.status = 'PAID'
  AND ABS(i.total_amount - COALESCE(i.amount_paid, 0)) > 0.01
ORDER BY i.invoice_date DESC LIMIT 20;
```

```sql
-- Unallocated payments (money received but not applied)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT cp.payment_number, cp.payment_date, cp.amount, cp.status, cp.payment_method
FROM ar.customer_payment cp
WHERE cp.status NOT IN ('VOID', 'REVERSED', 'BOUNCED') AND cp.amount > 0
  AND NOT EXISTS (SELECT 1 FROM ar.payment_allocation pa WHERE pa.payment_id = cp.payment_id)
ORDER BY cp.payment_date DESC LIMIT 30;
```

```sql
-- Overpaid invoices
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT i.invoice_number, i.invoice_date, i.total_amount, i.amount_paid,
    i.amount_paid - i.total_amount AS overpayment, i.status
FROM ar.invoice i WHERE COALESCE(i.amount_paid, 0) > i.total_amount + 0.01 AND i.status NOT IN ('VOID')
ORDER BY (i.amount_paid - i.total_amount) DESC LIMIT 20;
```

**Severity**: P0 for overpaid invoices or PAID with outstanding balance. P1 for allocation mismatches. P2 for unallocated payments.

---

### 6. GL Posting Integrity (`postings`)

**Assertions tested**: Existence (E/O), Completeness (C), Occurrence
**ISA reference**: ISA 240 — fraud risk indicators (duplicate postings, ghost journals)

```sql
-- Journals posted but no ledger lines (ghost postings)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT je.journal_number, je.entry_date, je.description, je.source_module, je.status
FROM gl.journal_entry je WHERE je.status = 'POSTED'
  AND NOT EXISTS (SELECT 1 FROM gl.posted_ledger_line pll WHERE pll.journal_entry_id = je.journal_entry_id)
ORDER BY je.entry_date DESC LIMIT 20;
```

```sql
-- Duplicate source document postings (ISA 240 fraud risk)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT je.source_module, je.source_document_type, je.source_document_id,
    COUNT(*) AS journal_count,
    string_agg(je.journal_number, ', ') AS journal_numbers,
    string_agg(je.status, ', ') AS statuses
FROM gl.journal_entry je
WHERE je.source_document_id IS NOT NULL AND je.status NOT IN ('VOID', 'REVERSED') AND je.journal_type != 'REVERSAL'
GROUP BY je.source_module, je.source_document_type, je.source_document_id
HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC LIMIT 20;
```

```sql
-- Postings to closed periods (period-lock violation)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT je.journal_number, je.entry_date, je.posting_date, je.description,
    fp.period_name, fp.status AS period_status
FROM gl.journal_entry je JOIN gl.fiscal_period fp ON fp.fiscal_period_id = je.fiscal_period_id
WHERE je.status = 'POSTED' AND fp.status IN ('SOFT_CLOSED', 'HARD_CLOSED') AND je.posted_at > fp.closed_at
ORDER BY je.posted_at DESC LIMIT 20;
```

```sql
-- Reversal chains (a reversed reversal — indicates confusion)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT orig.journal_number AS original, orig.status AS orig_status,
    rev.journal_number AS reversal, rev.status AS rev_status,
    rev2.journal_number AS double_reversal, rev2.status AS rev2_status
FROM gl.journal_entry orig
JOIN gl.journal_entry rev ON rev.journal_entry_id = orig.reversal_journal_id
LEFT JOIN gl.journal_entry rev2 ON rev2.journal_entry_id = rev.reversal_journal_id
WHERE rev2.journal_entry_id IS NOT NULL LIMIT 10;
```

**Severity**: P0 for duplicate postings or ghost journals. P1 for closed-period violations. P2 for reversal chains.

---

### 7. Account Classification Audit (`classifications`)

**Assertions tested**: Classification (CL), Presentation (P/D)
**IFRS reference**: IAS 1.99-105 — classification of expenses by nature/function; IAS 1.54-80A — line item presentation

```sql
-- Expense source documents posting to non-expense accounts
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT je.journal_number, je.entry_date, je.source_module, je.source_document_type, je.description,
    a.account_code, a.account_name, ac.category_name, jel.debit_amount, jel.credit_amount
FROM gl.journal_entry je
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
JOIN gl.account a ON a.account_id = jel.account_id
JOIN gl.account_category ac ON ac.category_id = a.category_id
WHERE je.source_module = 'EXPENSE' AND je.status = 'POSTED' AND jel.debit_amount > 0
  AND a.account_code NOT LIKE '5%' AND a.account_code NOT LIKE '6%' AND a.account_code NOT LIKE '7%'
ORDER BY je.entry_date DESC LIMIT 30;
```

```sql
-- Revenue debits on revenue accounts (should be credits, except for reversals/returns)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT je.journal_number, je.entry_date, je.description, a.account_code, a.account_name, jel.debit_amount
FROM gl.journal_entry je
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
JOIN gl.account a ON a.account_id = jel.account_id
WHERE je.source_module = 'AR' AND je.status = 'POSTED' AND a.account_code LIKE '4%'
  AND jel.debit_amount > 0 AND je.journal_type != 'REVERSAL'
ORDER BY je.entry_date DESC LIMIT 20;
```

```sql
-- Postings to CONTROL (non-posting) accounts — violates chart of accounts design
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT je.journal_number, je.entry_date, je.description,
    a.account_code, a.account_name, a.account_type, jel.debit_amount, jel.credit_amount
FROM gl.journal_entry je
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
JOIN gl.account a ON a.account_id = jel.account_id
WHERE je.status = 'POSTED' AND a.account_type = 'CONTROL'
ORDER BY je.entry_date DESC LIMIT 20;
```

```sql
-- Postings to inactive/disallowed accounts
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT a.account_code, a.account_name, a.is_active, a.is_posting_allowed,
    COUNT(*) AS posting_count, MAX(pll.posting_date) AS last_posting_date,
    SUM(pll.debit_amount + pll.credit_amount) AS total_posted
FROM gl.posted_ledger_line pll JOIN gl.account a ON a.account_id = pll.account_id
WHERE (a.is_active = false OR a.is_posting_allowed = false)
GROUP BY a.account_id, a.account_code, a.account_name, a.is_active, a.is_posting_allowed
ORDER BY MAX(pll.posting_date) DESC LIMIT 20;
```

**Severity**: P0 for posting to control accounts. P1 for wrong account class. P2 for inactive account postings.

---

### 8. Revenue Recognition — IFRS 15 (`revenue`)

**Assertions tested**: Occurrence (E/O), Accuracy (A/V), Cut-off (CO)
**IFRS reference**: IFRS 15.31-45 — satisfaction of performance obligations; IFRS 15.46-72 — determining transaction price

```sql
-- Performance obligations satisfied but no revenue recognized (completeness gap)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT po.obligation_id, po.description, po.status, po.standalone_selling_price,
    po.total_satisfied_amount, po.satisfaction_percentage,
    c.contract_number
FROM ar.performance_obligation po
JOIN ar.contract c ON c.contract_id = po.contract_id
WHERE po.status = 'SATISFIED' AND po.total_satisfied_amount = 0;
```

```sql
-- Revenue recognized exceeding transaction price (over-recognition)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT po.obligation_id, po.description, po.standalone_selling_price,
    po.total_satisfied_amount, c.contract_number,
    po.total_satisfied_amount - po.standalone_selling_price AS over_recognition
FROM ar.performance_obligation po
JOIN ar.contract c ON c.contract_id = po.contract_id
WHERE po.total_satisfied_amount > po.standalone_selling_price + 0.01
  AND po.status != 'CANCELLED';
```

```sql
-- Contracts with no performance obligations defined (IFRS 15 step 2 incomplete)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT c.contract_id, c.contract_number, c.contract_name, c.status, c.total_value
FROM ar.contract c
WHERE c.status NOT IN ('CANCELLED', 'DRAFT')
  AND NOT EXISTS (SELECT 1 FROM ar.performance_obligation po WHERE po.contract_id = c.contract_id);
```

```sql
-- Revenue recognized in wrong period vs performance obligation satisfaction date (cut-off)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT rre.event_id, rre.recognition_date, rre.amount_recognized,
    po.description AS obligation,
    fp.period_name, fp.start_date, fp.end_date
FROM ar.revenue_recognition_event rre
JOIN ar.performance_obligation po ON po.obligation_id = rre.obligation_id
LEFT JOIN gl.fiscal_period fp ON rre.recognition_date BETWEEN fp.start_date AND fp.end_date
WHERE rre.recognition_date NOT BETWEEN fp.start_date AND fp.end_date
LIMIT 20;
```

**Severity**: P0 for over-recognition (overstated revenue). P1 for satisfied obligations with no recognition. P2 for contracts without obligations.

---

### 9. Lease Accounting — IFRS 16 (`leases`)

**Assertions tested**: Completeness (C), Accuracy (A/V), Presentation (P/D)
**IFRS reference**: IFRS 16.22-46 — lessee measurement; IFRS 16.47-48 — subsequent measurement

```sql
-- Active leases without ROU asset record (completeness)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT lc.contract_id, lc.contract_number, lc.description, lc.lease_type,
    lc.commencement_date, lc.total_lease_payments
FROM lease.lease_contract lc
WHERE lc.status = 'ACTIVE'
  AND lc.lease_type NOT IN ('SHORT_TERM', 'LOW_VALUE')
  AND NOT EXISTS (SELECT 1 FROM lease.lease_asset la WHERE la.lease_contract_id = lc.contract_id);
```

```sql
-- Active leases without liability record (completeness)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT lc.contract_id, lc.contract_number, lc.description,
    lc.commencement_date, lc.total_lease_payments
FROM lease.lease_contract lc
WHERE lc.status = 'ACTIVE'
  AND lc.lease_type NOT IN ('SHORT_TERM', 'LOW_VALUE')
  AND NOT EXISTS (SELECT 1 FROM lease.lease_liability ll WHERE ll.lease_contract_id = lc.contract_id);
```

```sql
-- ROU asset carrying amount vs accumulated depreciation sanity check
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT la.asset_id, lc.contract_number,
    la.initial_cost, la.accumulated_depreciation,
    la.initial_cost - la.accumulated_depreciation AS carrying_amount,
    CASE WHEN la.accumulated_depreciation > la.initial_cost THEN 'OVER-DEPRECIATED'
         WHEN la.accumulated_depreciation < 0 THEN 'NEGATIVE DEPRECIATION'
         ELSE 'OK' END AS status
FROM lease.lease_asset la
JOIN lease.lease_contract lc ON lc.contract_id = la.lease_contract_id
WHERE la.accumulated_depreciation > la.initial_cost OR la.accumulated_depreciation < 0;
```

```sql
-- Lease liability current vs non-current split (IFRS 16.47(b) / IAS 1.60)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT ll.liability_id, lc.contract_number,
    ll.current_portion, ll.non_current_portion,
    ll.current_portion + ll.non_current_portion AS total_liability,
    ll.carrying_amount,
    ABS((ll.current_portion + ll.non_current_portion) - ll.carrying_amount) AS split_error
FROM lease.lease_liability ll
JOIN lease.lease_contract lc ON lc.contract_id = ll.lease_contract_id
WHERE ABS((ll.current_portion + ll.non_current_portion) - ll.carrying_amount) > 0.01;
```

**Severity**: P0 for missing ROU assets/liabilities on active leases (IFRS 16 non-compliance). P1 for over-depreciation or split errors.

---

### 10. Fixed Assets & Impairment — IAS 16, IAS 36 (`assets`)

**Assertions tested**: Existence (E/O), Valuation (A/V), Completeness (C)
**IFRS reference**: IAS 16.43 — depreciation of each significant part; IAS 36.9 — impairment indicators

```sql
-- Assets with carrying amount below zero (over-depreciated)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT a.asset_id, a.asset_code, a.asset_name, a.status,
    a.acquisition_cost, a.accumulated_depreciation,
    a.acquisition_cost - a.accumulated_depreciation AS carrying_amount
FROM fixed_assets.asset a
WHERE a.status = 'ACTIVE'
  AND a.accumulated_depreciation > a.acquisition_cost;
```

```sql
-- Fully depreciated assets still marked ACTIVE (should be FULLY_DEPRECIATED)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT a.asset_id, a.asset_code, a.asset_name,
    a.acquisition_cost, a.accumulated_depreciation, a.remaining_useful_life_months, a.status
FROM fixed_assets.asset a
WHERE a.status = 'ACTIVE'
  AND a.remaining_useful_life_months <= 0
  AND a.accumulated_depreciation >= a.acquisition_cost - 0.01;
```

```sql
-- Assets with impairment indicators but no impairment test recorded
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT a.asset_id, a.asset_code, a.asset_name,
    a.acquisition_cost - a.accumulated_depreciation AS carrying_amount, a.status
FROM fixed_assets.asset a
WHERE a.status = 'ACTIVE'
  AND a.acquisition_cost - a.accumulated_depreciation > 0
  AND NOT EXISTS (
      SELECT 1 FROM fixed_assets.asset_impairment ai
      WHERE ai.asset_id = a.asset_id
        AND ai.test_date >= CURRENT_DATE - INTERVAL '12 months'
  )
  AND a.acquisition_cost > 1000000;  -- Material assets only
```

```sql
-- Disposed assets still carrying a net book value (disposal gain/loss not posted)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT a.asset_id, a.asset_code, a.asset_name,
    a.acquisition_cost - a.accumulated_depreciation AS nbv_at_disposal,
    ad.disposal_date, ad.disposal_proceeds, ad.gain_loss_amount
FROM fixed_assets.asset a
JOIN fixed_assets.asset_disposal ad ON ad.asset_id = a.asset_id
WHERE a.status = 'DISPOSED'
  AND ad.journal_entry_id IS NULL;
```

**Severity**: P0 for over-depreciated or disposed assets without GL entries. P1 for status mismatches. P2 for missing impairment tests on material assets (IAS 36.9 indicator check).

---

### 11. Inventory Valuation — IAS 2 (`inventory`)

**Assertions tested**: Valuation (A/V), Existence (E/O)
**IFRS reference**: IAS 2.9 — lower of cost and NRV; IAS 2.28 — write-down to NRV

```sql
-- Inventory items where carrying value exceeds NRV (IAS 2 write-down required)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT iv.item_id, iv.item_name, iv.quantity_on_hand,
    iv.unit_cost, iv.total_cost,
    iv.estimated_selling_price, iv.estimated_costs_to_complete, iv.estimated_selling_costs,
    GREATEST(0, iv.estimated_selling_price - COALESCE(iv.estimated_costs_to_complete, 0) - COALESCE(iv.estimated_selling_costs, 0)) AS nrv,
    iv.total_cost - GREATEST(0, iv.estimated_selling_price - COALESCE(iv.estimated_costs_to_complete, 0) - COALESCE(iv.estimated_selling_costs, 0)) AS required_writedown
FROM inventory.inventory_valuation iv
WHERE iv.total_cost > GREATEST(0, iv.estimated_selling_price - COALESCE(iv.estimated_costs_to_complete, 0) - COALESCE(iv.estimated_selling_costs, 0))
  AND iv.quantity_on_hand > 0
ORDER BY (iv.total_cost - GREATEST(0, iv.estimated_selling_price - COALESCE(iv.estimated_costs_to_complete, 0) - COALESCE(iv.estimated_selling_costs, 0))) DESC
LIMIT 20;
```

```sql
-- Inventory GL balance vs inventory subledger (valuation reconciliation)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT
    (SELECT COALESCE(SUM(pll.debit_amount - pll.credit_amount), 0)
     FROM gl.posted_ledger_line pll JOIN gl.account a ON a.account_id = pll.account_id
     WHERE a.subledger_type = 'INVENTORY') AS gl_inventory_balance,
    (SELECT COALESCE(SUM(iv.total_cost), 0)
     FROM inventory.inventory_valuation iv WHERE iv.quantity_on_hand > 0) AS subledger_inventory_value;
```

**Severity**: P0 for inventory above NRV without write-down (IAS 2 violation). P1 for GL/subledger mismatch.

---

### 12. Period Cut-Off Testing (`cutoff`)

**Assertions tested**: Cut-off (CO), Occurrence (E/O)
**ISA reference**: ISA 330.A56 — cut-off procedures for revenue and expenses
**IFRS reference**: IAS 1.27-28 — accrual basis; Conceptual Framework 4.50 — matching principle

```sql
-- Revenue invoices dated in one period but posted in a different period (cut-off error)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT i.invoice_number, i.invoice_date, je.posting_date,
    fp_inv.period_name AS invoice_period, fp_post.period_name AS posting_period,
    i.total_amount
FROM ar.invoice i
JOIN gl.journal_entry je ON je.source_document_id = i.invoice_id AND je.source_module = 'AR'
LEFT JOIN gl.fiscal_period fp_inv ON i.invoice_date BETWEEN fp_inv.start_date AND fp_inv.end_date
LEFT JOIN gl.fiscal_period fp_post ON je.posting_date BETWEEN fp_post.start_date AND fp_post.end_date
WHERE fp_inv.fiscal_period_id != fp_post.fiscal_period_id
  AND je.status = 'POSTED'
ORDER BY i.invoice_date DESC LIMIT 30;
```

```sql
-- Expenses recognized in wrong period (expense dated before period start or after period end)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT ec.claim_number, ec.claim_date, je.posting_date,
    fp_claim.period_name AS claim_period, fp_post.period_name AS posting_period,
    ec.total_approved_amount
FROM expense.expense_claim ec
JOIN gl.journal_entry je ON je.journal_entry_id = ec.journal_entry_id
LEFT JOIN gl.fiscal_period fp_claim ON ec.claim_date BETWEEN fp_claim.start_date AND fp_claim.end_date
LEFT JOIN gl.fiscal_period fp_post ON je.posting_date BETWEEN fp_post.start_date AND fp_post.end_date
WHERE fp_claim.fiscal_period_id != fp_post.fiscal_period_id
  AND je.status = 'POSTED'
ORDER BY ec.claim_date DESC LIMIT 30;
```

```sql
-- Last-day/first-day transactions (high-risk for cut-off manipulation — ISA 240)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT je.journal_number, je.entry_date, je.posting_date, je.description,
    je.source_module, je.source_document_type,
    SUM(jel.debit_amount) AS total_amount,
    fp.period_name, fp.end_date
FROM gl.journal_entry je
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
JOIN gl.fiscal_period fp ON fp.fiscal_period_id = je.fiscal_period_id
WHERE je.status = 'POSTED'
  AND (je.entry_date = fp.end_date OR je.entry_date = fp.end_date - 1
       OR je.entry_date = fp.start_date OR je.entry_date = fp.start_date + 1)
GROUP BY je.journal_entry_id, je.journal_number, je.entry_date, je.posting_date,
    je.description, je.source_module, je.source_document_type, fp.period_name, fp.end_date
HAVING SUM(jel.debit_amount) > 100000  -- Material transactions only
ORDER BY je.entry_date DESC LIMIT 30;
```

**Severity**: P1 for cross-period cut-off errors (material amounts). P2 for last-day transactions (requires review, not necessarily errors). INFO for immaterial cut-off differences.

---

### 13. IFRS Disclosure Completeness (`disclosure`)

**Assertions tested**: Presentation & Disclosure (P/D)
**IFRS reference**: IAS 1.112-138 — notes to financial statements

```sql
-- Disclosure checklist items not completed
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT dc.checklist_id, dc.standard_reference, dc.disclosure_requirement,
    dc.is_mandatory, dc.status, dc.applicability
FROM rpt.disclosure_checklist dc
WHERE dc.status NOT IN ('COMPLETED', 'NOT_APPLICABLE', 'REVIEWED')
  AND dc.is_mandatory = true
ORDER BY dc.standard_reference;
```

```sql
-- Related party transactions requiring disclosure (IAS 24)
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';

SELECT je.journal_number, je.entry_date, je.description,
    SUM(jel.debit_amount) AS amount, je.source_module
FROM gl.journal_entry je
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
WHERE je.status = 'POSTED'
  AND (je.description ILIKE '%related party%' OR je.description ILIKE '%director%'
       OR je.description ILIKE '%shareholder%' OR je.description ILIKE '%key management%')
GROUP BY je.journal_entry_id, je.journal_number, je.entry_date, je.description, je.source_module
ORDER BY SUM(jel.debit_amount) DESC LIMIT 20;
```

**Severity**: P1 for incomplete mandatory disclosures. P2 for related party transactions needing review.

---

## Output Format

Present results as an ISA-structured audit report:

```markdown
## Accounting Books Audit Report — {date}

### Materiality Assessment (ISA 320)
- **Planning materiality**: ₦X (1.5% of [revenue/total assets])
- **Performance materiality**: ₦X (70% of planning)
- **Trivial threshold**: ₦X (5% of planning)

### Executive Summary
- **P0 (Critical — ISA 705 Modified Opinion Risk)**: N findings
- **P1 (High — ISA 260 Communication Required)**: N findings
- **P2 (Medium — Management Letter Point)**: N findings
- **INFO (Observations)**: N items

### Assertion Coverage Matrix

| Assertion | Checks Run | Findings | Status |
|-----------|-----------|----------|--------|
| Existence/Occurrence | postings, payments, banking | N | Pass/Fail |
| Completeness | expenses, subledger, revenue | N | Pass/Fail |
| Accuracy/Valuation | trial-balance, payments, inventory, assets | N | Pass/Fail |
| Classification | classifications, expenses | N | Pass/Fail |
| Cut-off | cutoff | N | Pass/Fail |
| Rights & Obligations | banking, subledger | N | Pass/Fail |
| Presentation & Disclosure | classifications, disclosure, leases | N | Pass/Fail |

### P0 — Critical Findings
For each finding:
- **Assertion**: Which assertion is violated
- **IFRS/IAS Reference**: Specific paragraph
- **Issue**: What is wrong
- **Impact**: Financial impact (material/immaterial vs materiality threshold)
- **Evidence**: Key records
- **Recommended Fix**: Corrective action using DotMac ERP capabilities

### P1 — High Priority (ISA 260 — Communication with Governance)
(same structure)

### P2 — Medium Priority (Management Letter Points)
(same structure)

### INFO — Observations
(metrics, patterns, ratios)

### Recommended Corrective Actions
Numbered, prioritized, with IFRS/ISA reference for each.

### Verdict: CLEAN / NEEDS_CORRECTION / CRITICAL / QUALIFIED_OPINION_RISK
```

**Verdict logic**:
- **QUALIFIED_OPINION_RISK**: P0 findings that are material (> planning materiality) — would trigger ISA 705 modified opinion
- **CRITICAL**: P0 findings below materiality but still errors requiring correction
- **NEEDS_CORRECTION**: P1 findings only
- **CLEAN**: Only P2/INFO items

---

## IFRS/ISA Reference Guide

When reporting findings, always cite the relevant standard:

| Area | Standard | Key Paragraphs |
|------|----------|----------------|
| Financial statement presentation | IAS 1 | 15 (fair presentation), 27-28 (accrual basis), 54-80A (line items), 99-105 (expense classification) |
| Accounting equation | IAS 1 | 28 (A = L + E) |
| Revenue recognition | IFRS 15 | 31-34 (when to recognize), 46-72 (transaction price), 73-90 (allocation) |
| Lease accounting | IFRS 16 | 22-28 (initial recognition), 29-35 (initial measurement), 36-46 (subsequent measurement) |
| Fixed assets | IAS 16 | 30-42 (cost model vs revaluation), 43-62 (depreciation), 67-72 (derecognition) |
| Impairment | IAS 36 | 9-14 (indicators), 18-57 (measuring recoverable amount), 59-64 (recognizing impairment) |
| Inventory | IAS 2 | 9 (lower of cost and NRV), 28-33 (NRV write-down), 34-35 (reversal of write-down) |
| Deferred tax | IAS 12 | 15-18 (deferred tax liabilities), 24-31 (deferred tax assets), 47-52 (measurement) |
| Foreign currency | IAS 21 | 21-37 (reporting in functional currency), 38-49 (translation to presentation currency) |
| Related parties | IAS 24 | 18 (disclosure requirements), 9 (definition of related party) |
| Events after reporting | IAS 10 | 8-11 (adjusting events), 14 (dividends declared), 17-18 (going concern) |
| Provisions | IAS 37 | 14 (recognition criteria), 36-52 (measurement — best estimate) |
| Cash flow | IAS 7 | 10-12 (operating activities), 16-17 (investing), 21-22 (financing) |
| Audit materiality | ISA 320 | 9-11 (determining materiality), A1-A13 (benchmarks) |
| Fraud indicators | ISA 240 | 26-27 (fraud risk factors), A1-A6 (examples) |
| Substantive procedures | ISA 330 | 18-22 (tests of details), A56 (cut-off procedures) |
| Analytical procedures | ISA 520 | 5-7 (substantive analytical procedures) |
| External confirmations | ISA 505 | 7-16 (bank confirmations, receivable confirmations) |

---

## Correction Guidance

When recommending fixes, use DotMac ERP capabilities:

| Error Type | Fix Method | IFRS Basis |
|-----------|-----------|------------|
| Wrong GL account on posted journal | `ReversalService.create_reversal()` → create correct journal | IAS 8.42 (correction of errors) |
| Expense posted to wrong account | Reverse → fix expense item/category account → re-approve | IAS 8.42 |
| Duplicate GL posting | Reverse the duplicate via `ReversalService` | IAS 8.42 |
| Bank match to wrong entity | `BankReconciliationService.unmatch_statement_line()` → re-match | ISA 505 |
| Invoice status mismatch | Run `reconcile_invoice_statuses()` from `data_health.py` | IAS 1.15 |
| Payment allocation gap | Run `reconcile_payment_allocations()` from `data_health.py` | IAS 1.15 |
| Posting to closed period | `FiscalPeriodService.reopen_period()` (SOFT_CLOSED only) → fix → re-close | IAS 10 |
| Subledger/GL gap | Create adjustment journal entries | IAS 1.15 |
| Revenue over-recognition | Reverse recognition event → recalculate IFRS 15 steps | IFRS 15.31-45 |
| Missing ROU asset/liability | Create initial recognition entries via lease posting adapter | IFRS 16.22-28 |
| Inventory NRV write-down | Post write-down journal to cost of sales | IAS 2.34 |
| Material prior period error | Retrospective restatement (IAS 8.42-48) via adjustment journals | IAS 8.42 |

**IMPORTANT**: Never suggest deleting GL records. All corrections must be additive (reversals + new entries) to maintain audit trail integrity per ISA 230 (Audit Documentation).
