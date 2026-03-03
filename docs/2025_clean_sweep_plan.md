# Plan: 2025 Clean Sweep — Delete & Re-Import from ERPNext

## Context

DotMac's 2025 financial data accumulated multiple integrity issues from the ERPNext sync:
- 22,975 voided duplicate invoices (ERPNext copies of Splynx originals)
- 15,182 voided duplicate customer payments
- 1,156 purchase invoices with no GL posting (₦775M unrecognised in GL)
- ~3.2M in missing sync transfers/payments
- Account code mismatches (ERPNext names vs numbered chart)

Three prior migrations addressed the GL layer but left source documents orphaned:
1. `20260301_delete_voided_erpnext_invoices` — deleted 22,975 void invoices + GL
2. `20260301_remap_erpnext_accounts` — remapped 25 ERPNext accounts to numbered codes
3. `20260301_delete_orphan_void_journals` — deleted 15,227 orphan void journals

**Result: GL is now completely empty (0 journal entries, 0 posted ledger lines).** Source documents remain with orphaned `journal_entry_id` references. Rather than patching incrementally, a clean sweep re-imports everything from ERPNext — the authoritative, balanced 2025 dataset (including all Splynx records and bank reconciliation).

## Current DotMac State

| Table | Rows | Issue |
|-------|------|-------|
| `gl.journal_entry` | **0** | Empty from prior migrations |
| `gl.journal_entry_line` | **0** | Empty |
| `gl.posted_ledger_line` | **0** | Empty |
| `ar.invoice` | 23,914 | 23,540 have dangling `journal_entry_id` |
| `ar.customer_payment` | 34,603 | All have dangling `journal_entry_id` |
| `ap.supplier_invoice` | 1,251 | Most lack journal entries |
| `ap.supplier_payment` | 1,368 | Orphaned references |
| `expense.expense_claim` | 10,216 | Orphaned references |
| `banking.bank_statement_lines` | 41,630 | `matched_journal_line_id` dangling |
| `banking.bank_statement_line_matches` | 39,108 | Dangling references |
| `sync.sync_entity` (financial) | ~107K | Stale target references |

## ERPNext Source (Authoritative)

| Doctype | Count | Total Debit |
|---------|-------|-------------|
| Sales Invoice | 18,941 | ₦1,723M |
| Payment Entry | 27,972 | ₦2,583M |
| Journal Entry | 11,530 | ₦344M |
| Purchase Invoice | 1,160 | ₦788M |
| Expense Claim | 9,362 | ₦87M |
| **GL Entry (active)** | **167,139** | **₦5,526M** |
| Bank Transaction | 38,824 | ₦3,454M |

ERPNext GL imbalance: ₦211,981.40 credit excess (Purchase Invoice rounding) — imported as-is.

Connection: `docker exec erpnext_temp_maria mysql -uroot -proot erpnext_temp`

---

## Phase 1: Delete 2025 DotMac Data

**File:** `alembic/versions/20260302_clean_sweep_delete_2025.py`

Single idempotent Alembic migration. No FK constraints between source doc tables, so order is flexible. Delete child rows before parent rows within each module.

### Deletion Order

```
1. banking.bank_statement_line_matches  (39,108 rows — FK to bank_statement_lines)
2. banking.bank_statement_lines         (41,630 rows — FK to bank_statements)
3. banking.bank_statements              (129 rows)

4. ar.payment_allocation                (FK to customer_payment)
5. ar.invoice_line                      (FK to invoice)
6. ar.credit_note                       (if any 2025 rows)
7. ar.customer_payment                  (34,603 rows)
8. ar.invoice                           (23,914 rows)

9. ap.supplier_invoice_line             (FK to supplier_invoice)
10. ap.supplier_payment                 (1,368 rows)
11. ap.supplier_invoice                 (1,251 rows)

12. expense.expense_claim_line          (FK to expense_claim)
13. expense.expense_claim               (10,216 rows)

14. gl.account_balance                  (all rows — will rebuild)

15. sync.sync_entity WHERE source_doctype IN (
      'Sales Invoice', 'Payment Entry', 'Journal Entry',
      'Purchase Invoice', 'Expense Claim',
      'Bank Transaction', 'Bank Transaction Payments'
    )
```

**Audit table:** `_migration_clean_sweep_2025_audit` with per-table row counts before deletion.

**Date filter:** `>= '2025-01-01' AND < '2026-02-01'` for all tables (covers Jan 2025 – Jan 2026 fiscal periods). Banking uses `transaction_date`, AR uses `invoice_date`/`payment_date`, etc.

**Note:** GL tables already empty, but include DELETE statements anyway for idempotency.

---

## Phase 2: Account Mapping & Creation

**File:** `scripts/clean_sweep/account_mapping.py`

Map all 93 ERPNext accounts (`- DT` suffix) to DotMac numbered accounts. Script reads ERPNext GL for distinct accounts, matches to existing DotMac accounts, creates ~30 new accounts that don't exist yet.

### Key Mappings (top 30 by GL line count)

| ERPNext Account | Lines | → Code | DotMac Name |
|---|---|---|---|
| Accounts Receivable - DT | 38,057 | 1400 | Trade Receivables |
| cash sales - DT | 20,226 | 4010 | Other Business Revenue |
| VAT - DT | 19,959 | 2110 | VAT Payable |
| Expense Payable - DT | 19,896 | 2000 | Trade Payables |
| Paystack OPEX - DT | 19,086 | 1211 | Paystack OPEX Account |
| Bank Fees and Charges - DT | 11,053 | 6030 | Bank Charges |
| Paystack - DT | 10,766 | 1211 | Paystack OPEX Account |
| Zenith 461 Bank - DT | 6,087 | 1205 | Zenith 461 Bank |
| Transportation Expense - DT | 5,855 | 6024 | Transportation (NEW) |
| Zenith 523 Bank - DT | 3,164 | 1204 | Zenith 523 Bank |
| Trade and Other Payables - DT | 1,654 | 2000 | Trade Payables |
| Fuel/Mileage Expenses - DT | 1,392 | 6025 | Fuel/Mileage (NEW) |
| Direct labour - COS - DT | 1,266 | 5010 | Direct Labour COS (NEW) |
| UBA Bank - DT | 1,264 | 1202 | UBA |
| Round off - DT | 1,248 | 6099 | Round Off (NEW) |

Plus ~63 more accounts with fewer lines (full mapping embedded in script as a Python dict).

**New accounts to create (~30):** Transportation, Fuel/Mileage, Direct Labour COS, Round Off, Equipment Rental, Vehicle Repairs, Site Logistics, Materials COS, Subcontractors COS, Shipping, Meals & Entertainment, Staff Loans, Staff Training, Pension Expense, Government Fees, Commissions, General Repairs, Computer Repairs, First Bank, Cash Garki, Undeposited Funds, etc.

Each new account inherits: `organization_id`, appropriate `category_id` from sibling accounts, `account_type='POSTING'`, `normal_balance` from root type (DEBIT for assets/expenses, CREDIT for liabilities/equity/revenue).

**Output:** `ACCOUNT_MAP: dict[str, UUID]` mapping ERPNext account name → DotMac account_id, saved as JSON for use by Phase 3.

---

## Phase 3: Import GL from ERPNext

**File:** `scripts/clean_sweep/import_gl.py`

This is the core import. Reads ERPNext `tabGL Entry`, groups by voucher, creates DotMac journal entries.

### ERPNext → DotMac GL Mapping

```
ERPNext tabGL Entry (flat)     →  DotMac (3-table structure)
─────────────────────────────     ─────────────────────────────
voucher_type + voucher_no      →  gl.journal_entry (1 per voucher)
  ├── posting_date             →    entry_date, posting_date
  ├── voucher_type             →    source_document_type (mapped)
  ├── voucher_no               →    source_document_id (from sync_entity)
  ├── fiscal_year              →    fiscal_period_id (resolved)
  └── remarks                  →    description

Each GL Entry row              →  gl.journal_entry_line (1 per row)
  ├── account                  →    account_id (from ACCOUNT_MAP)
  ├── debit / credit           →    debit_amount / credit_amount
  ├── party_type + party       →    (store in description or entity link)
  └── cost_center              →    cost_center (if DotMac tracks it)

Each GL Entry row              →  gl.posted_ledger_line (1 per row)
  ├── Same fields as JEL       →    + posting_year (for partitioning)
  ├── account_code             →    denormalized from account
  └── is_posted = true         →    immutable ledger copy
```

### Voucher Type Mapping

| ERPNext voucher_type | DotMac source_document_type | DotMac source_module |
|---|---|---|
| Sales Invoice | INVOICE | ar |
| Payment Entry (Receive) | CUSTOMER_PAYMENT | ar |
| Payment Entry (Pay, party=Supplier) | SUPPLIER_PAYMENT | ap |
| Payment Entry (Pay, party=Employee) | EXPENSE_REIMBURSEMENT | expense |
| Payment Entry (Internal Transfer) | INTERBANK_TRANSFER | banking |
| Journal Entry | null (FIN journal) | gl |
| Purchase Invoice | SUPPLIER_INVOICE | ap |
| Expense Claim | EXPENSE_CLAIM | expense |

### Import Logic

```python
for (voucher_type, voucher_no), gl_lines in grouped_gl_entries:
    # 1. Create journal_entry header
    je = JournalEntry(
        journal_entry_id=gen_uuid(),
        organization_id=ORG_ID,
        entry_date=gl_lines[0].posting_date,
        posting_date=gl_lines[0].posting_date,
        status='POSTED',
        source_document_type=map_voucher_type(voucher_type, gl_lines),
        source_document_id=None,  # linked in Phase 4
        total_debit=sum(line.debit for line in gl_lines),
        total_credit=sum(line.credit for line in gl_lines),
        fiscal_period_id=resolve_period(gl_lines[0].posting_date),
        description=gl_lines[0].remarks or f'{voucher_type}: {voucher_no}',
        journal_number=next_journal_number(),
        erpnext_voucher_type=voucher_type,  # temporary tracking
        erpnext_voucher_no=voucher_no,       # temporary tracking
    )

    # 2. Create journal_entry_line + posted_ledger_line for each GL row
    for i, gle in enumerate(gl_lines):
        account_id = ACCOUNT_MAP[gle.account]
        # ... create JEL and PLL with mapped account
```

### Key Details

- **Journal numbering:** Sequential `JE-2025-NNNNN` using `SyncNumberingService`
- **Fiscal periods:** Resolve via `PeriodGuardService.get_period_for_date()` — will auto-create if needed
- **Batch size:** Commit every 1000 vouchers for memory management
- **Idempotent:** Check if journal already exists (by erpnext_voucher_no) before inserting
- **GL imbalance:** The ₦211K PI rounding carries through as-is from ERPNext

### Expected Output

| Table | Rows Created |
|-------|-------------|
| `gl.journal_entry` | ~68,492 (one per unique voucher) |
| `gl.journal_entry_line` | ~167,139 |
| `gl.posted_ledger_line` | ~167,139 |

---

## Phase 4: Import Source Documents

**File:** `scripts/clean_sweep/import_source_docs.py`

Import source documents from ERPNext and link to the journal entries created in Phase 3.

### 4a. Sales Invoices (18,941)

```
tabSales Invoice → ar.invoice
  ├── name → erpnext_id
  ├── customer → customer_id (via sync_entity lookup)
  ├── posting_date → invoice_date
  ├── grand_total → total_amount
  ├── outstanding_amount → outstanding_amount
  ├── status → status (mapped: Paid→PAID, Unpaid→POSTED, etc.)
  ├── custom_splynx_invoice_id → splynx_id
  └── is_return → is_credit_note flag

tabSales Invoice Item → ar.invoice_line
  ├── item_code, description, qty, rate, amount
```

Link: `ar.invoice.journal_entry_id` ← journal created in Phase 3 for same voucher_no.

### 4b. Payment Entries (27,972)

Split by payment_type + party_type:
- **Receive (Customer)** → `ar.customer_payment` (16,746)
- **Pay (Supplier)** → `ap.supplier_payment` (~10,585)
- **Pay (Employee)** → `expense.expense_claim` reimbursement link (~9,000)
- **Internal Transfer** → `banking.interbank_transfer` if table exists, else GL-only (641)

### 4c. Purchase Invoices (1,160)

```
tabPurchase Invoice → ap.supplier_invoice
tabPurchase Invoice Item → ap.supplier_invoice_line
```

### 4d. Expense Claims (9,362)

```
tabExpense Claim → expense.expense_claim
tabExpense Claim Detail → expense.expense_claim_line
```

### 4e. Journal Entries (11,530)

Already imported as GL in Phase 3. No separate source document table needed — these are standalone journal entries.

### Source Document → Journal Linking

After both Phase 3 and 4 complete:
```sql
-- Link invoices to their journals
UPDATE ar.invoice inv
SET journal_entry_id = je.journal_entry_id
FROM gl.journal_entry je
WHERE je.erpnext_voucher_no = inv.erpnext_id
  AND je.erpnext_voucher_type = 'Sales Invoice';

-- Same for customer_payment, supplier_payment, etc.
```

### sync_entity Recreation

For each imported document, create a `sync.sync_entity` row:
```python
SyncEntity(
    organization_id=ORG_ID,
    source_system='erpnext',
    source_doctype='Sales Invoice',
    source_name=erpnext_name,      # e.g. 'ACC-SINV-2025-00002'
    target_table='ar.invoice',
    target_id=new_invoice_id,
    sync_status='SYNCED',
)
```

---

## Phase 5: Import Bank Transactions & Reconciliation

**File:** `scripts/clean_sweep/import_bank_transactions.py`

### 5a. Bank Statement Lines (38,824)

```
tabBank Transaction → banking.bank_statement_lines
  ├── name → erpnext_id (stored in raw_data JSON)
  ├── date → transaction_date
  ├── deposit/withdrawal → amount (positive/negative)
  ├── bank_account → bank_account_id (via bank_accounts lookup)
  ├── description → description
  ├── reference_number → reference
  ├── status → is_matched (Reconciled → true)
```

Group by bank_account + month to create `banking.bank_statements` (parent rows).

### 5b. Reconciliation Links

```
tabBank Transaction Payments → banking.bank_statement_line_matches
  ├── parent → bank_statement_line (via BTN name lookup)
  ├── payment_document + payment_entry → matched GL journal
```

Link `bank_statement_lines.matched_journal_line_id` to the journal_entry_line created in Phase 3, matched by ERPNext voucher_no.

### Cross-Year Reconciliation

Some 2025 bank transactions are reconciled against 2026 Payment Entries (e.g. `ACC-PAY-2026-01508`). These links will only work if the 2026 journal also exists. For now, set `is_matched=false` for cross-year orphans and log them for manual review.

---

## Phase 6: Rebuild Balances & Verify

### 6a. Rebuild Account Balances

```bash
docker exec dotmac_erp_app python scripts/rebuild_stale_balances.py
```

Uses existing `AccountBalanceService.rebuild_balances_for_period()`.

### 6b. Verification Queries

```sql
-- 1. DotMac GL balanced (debit = credit)
SELECT SUM(debit_amount) as debits, SUM(credit_amount) as credits,
       SUM(debit_amount) - SUM(credit_amount) as diff
FROM gl.posted_ledger_line
WHERE organization_id = '00000000-0000-0000-0000-000000000001';
-- Expected: diff ≈ -211,981.40 (inherited ERPNext PI rounding)

-- 2. DotMac GL total matches ERPNext
-- Expected: debits ≈ ₦5,525,504,157.93

-- 3. Journal count matches voucher count
SELECT COUNT(DISTINCT journal_entry_id) FROM gl.journal_entry
WHERE organization_id = '00000000-0000-0000-0000-000000000001';
-- Expected: ~68,492

-- 4. All source docs linked to journals
SELECT COUNT(*) FROM ar.invoice
WHERE journal_entry_id IS NULL AND status NOT IN ('DRAFT', 'VOID')
  AND organization_id = '00000000-0000-0000-0000-000000000001';
-- Expected: 0

-- 5. Bank statement lines matched
SELECT is_matched, COUNT(*) FROM banking.bank_statement_lines bsl
JOIN banking.bank_statements bs ON bs.statement_id = bsl.statement_id
GROUP BY is_matched;
-- Expected: majority true (Reconciled in ERPNext)

-- 6. Account balance rebuild complete
SELECT COUNT(*) FROM gl.account_balance
WHERE is_stale = true
  AND organization_id = '00000000-0000-0000-0000-000000000001';
-- Expected: 0
```

---

## Files to Create

| # | File | Purpose |
|---|------|---------|
| 1 | `alembic/versions/20260302_clean_sweep_delete_2025.py` | Delete all 2025 data (Alembic migration) |
| 2 | `scripts/clean_sweep/__init__.py` | Package init |
| 3 | `scripts/clean_sweep/config.py` | Shared constants (ORG_ID, DB connections, account mapping dict) |
| 4 | `scripts/clean_sweep/account_mapping.py` | Phase 2: Map & create accounts |
| 5 | `scripts/clean_sweep/import_gl.py` | Phase 3: Import GL from ERPNext |
| 6 | `scripts/clean_sweep/import_source_docs.py` | Phase 4: Import source documents |
| 7 | `scripts/clean_sweep/import_bank_transactions.py` | Phase 5: Import bank transactions |
| 8 | `scripts/clean_sweep/verify.py` | Phase 6: Run verification queries |

### Execution Order

```bash
# 1. Run migration to delete 2025 data
alembic upgrade head

# 2. Map accounts (creates missing accounts in DotMac)
docker exec dotmac_erp_app python -m scripts.clean_sweep.account_mapping

# 3. Import GL (journal entries + posted ledger lines)
docker exec dotmac_erp_app python -m scripts.clean_sweep.import_gl

# 4. Import source documents + link to journals
docker exec dotmac_erp_app python -m scripts.clean_sweep.import_source_docs

# 5. Import bank transactions + reconciliation
docker exec dotmac_erp_app python -m scripts.clean_sweep.import_bank_transactions

# 6. Rebuild balances + verify
docker exec dotmac_erp_app python scripts/rebuild_stale_balances.py
docker exec dotmac_erp_app python -m scripts.clean_sweep.verify
```

### Connectivity

Both databases accessible from the DotMac app container:
- **PostgreSQL:** `localhost:5432` (internal) via SQLAlchemy `SessionLocal`
- **MariaDB:** `172.18.0.9:3306` (Docker network `dotmac_default`) via `pymysql`

The app container needs `pymysql` installed (add to requirements or install at runtime).

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| ERPNext GL ₦211K imbalance | Import as-is; document in audit table; fix in ERPNext later |
| Cross-year bank reconciliation | Log orphaned links; set is_matched=false for manual review |
| Missing customer/supplier IDs | Customers/suppliers already synced (4,823 + 900 in sync_entity); verify before import |
| Script fails mid-import | Each phase is idempotent (check-before-insert); can re-run safely |
| MariaDB connectivity from app container | Both on `dotmac_default` network; verify with ping first |

## Out of Scope

- 2026 data (separate import when ready)
- ERPNext-side cleanup (cancelling docstatus=1 duplicates)
- Deactivating remaining 182 ERPNext-style accounts with zero GL activity
