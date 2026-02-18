---
name: db-schema
description: Query the ERP database with full schema context, business rules, and cross-module relationship awareness
arguments:
  - name: question
    description: "What you want to know (e.g. 'top 10 customers by revenue', 'show employee headcount by department', 'which invoices are overdue')"
---

# Database Schema & Business Context

Use this when the user asks about data, database structure, querying,
reporting, analytics, or needs to understand entity relationships.

## Instructions

You have access to the DotMac ERP PostgreSQL database via the `erp-db` MCP
server. Use the `execute_sql` tool for SELECT queries only. The connection uses
a **read-only** user (`claude_readonly`) — INSERT/UPDATE/DELETE will be rejected
by PostgreSQL itself.

The MCP connection DSN must be provided via the `DOTMAC_ERP_DB_DSN` environment
variable (see `.mcp.json`). Do not hardcode DB passwords in repo-tracked files.

### RLS Session Setup — MANDATORY

Row-Level Security is enabled on 141 of 312 tables (all business data). Every
`execute_sql` call MUST prefix the query with:

```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';
-- then your SELECT ...
```

**Both statements must be in the SAME `execute_sql` call** — the `SET` does not
persist across calls. Without it, queries return **0 rows** silently.

Example:
```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';
SELECT c.customer_code, c.customer_name, COUNT(i.invoice_id) as invoices
FROM ar.invoice i
JOIN ar.customer c ON c.customer_id = i.customer_id
GROUP BY c.customer_id, c.customer_code, c.customer_name
ORDER BY invoices DESC LIMIT 10;
```

Tables WITHOUT RLS (schema metadata, enums) can be queried directly:
```sql
SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder)
FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
GROUP BY t.typname ORDER BY t.typname;
```

### information_schema Limitation

The `claude_readonly` user cannot see constraints via `information_schema` views
(they are privilege-filtered). To inspect foreign keys or constraints, use
`pg_constraint` instead:
```sql
SELECT conname, contype, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'ar.invoice'::regclass;
```

### Company Context

DotMac Technologies Ltd is a Nigerian ISP (Internet Service Provider) running
an IFRS-compliant ERP system. Currency: NGN (Nigerian Naira).

**Core business activities:**
- **Subscribers**: Internet service customers with recurring monthly subscriptions
- **Billing**: Monthly invoice generation, payment collection (Paystack, bank transfer), credit notes
- **Finance**: Full double-entry General Ledger, AR, AP, banking reconciliation, tax compliance
- **HR**: Employee lifecycle (hire-to-retire), payroll (Nigerian tax bands), leave, performance reviews
- **Inventory**: Network equipment (routers, ONTs, cables, splitters)
- **Operations**: Fleet management, field service dispatching, ticketing
- **Procurement**: Purchase requisitions, RFQs, vendor management, goods receipts

### Multi-Tenancy — CRITICAL

Every table has `organization_id` (UUID). **ALWAYS** include it in WHERE clauses.

Production org:
```
organization_id: 00000000-0000-0000-0000-000000000001
legal_name: Dotmac Technologies Ltd
trading_name: Dotmac
currency: NGN
```

### Schema Layout

The database uses PostgreSQL schemas (not a single `public` schema) to organize
domains. There are **39 schemas** with **312 tables**.

| PG Schema | Domain | Tables | Key Tables |
|-----------|--------|--------|------------|
| `ar` | Accounts Receivable | 19 | customer, invoice, invoice_line, customer_payment, quote, sales_order |
| `ap` | Accounts Payable | 12 | supplier, supplier_invoice, supplier_payment, purchase_order, goods_receipt |
| `gl` | General Ledger | 11 | account, journal_entry, journal_entry_line, fiscal_period, fiscal_year, budget |
| `banking` | Banking | 8 | bank_accounts, bank_statements, bank_reconciliations, payee |
| `hr` | Human Resources | 35 | employee, department, designation, disciplinary_case, employee_onboarding |
| `payroll` | Payroll | 15 | salary_structure, salary_slip, payroll_entry, employee_loan, tax_band |
| `leave` | Leave Mgmt | 5 | leave_application, leave_allocation, leave_type, holiday |
| `attendance` | Attendance | 4 | attendance, attendance_request, shift_type, shift_assignment |
| `perf` | Performance | 10 | appraisal_cycle, appraisal, kra, kpi, scorecard |
| `inv` | Inventory | 15 | item, warehouse, inventory_transaction, bill_of_materials, material_request |
| `fa` | Fixed Assets | 9 | asset, asset_category, depreciation_schedule, asset_disposal |
| `expense` | Expenses | 11 | expense_claim, expense_claim_item, expense_category, cash_advance, corporate_card |
| `tax` | Tax | 8 | tax_code, tax_period, tax_return, tax_transaction |
| `proc` | Procurement | 12 | purchase_requisition, request_for_quotation, bid_evaluation, procurement_contract |
| `fleet` | Fleet | 7 | vehicle, vehicle_assignment, fuel_log_entry, maintenance_record |
| `pm` | Projects | 10 | project (in core_org), task, milestone, time_entry, resource_allocation |
| `support` | Helpdesk | 6 | ticket, ticket_comment, ticket_attachment, support_team |
| `recruit` | Recruitment | 4 | job_opening, job_applicant, interview, job_offer |
| `automation` | Workflows | 8 | workflow_rule, document_template, recurring_template |
| `core_org` | Organization | 9 | organization, business_unit, cost_center, location, project |
| `core_config` | Config | 2 | numbering_sequence, system_configuration |
| `core_fx` | FX/Currency | 4 | currency, exchange_rate, exchange_rate_type |
| `cons` | Consolidation | 6 | legal_entity, consolidation_run, intercompany_balance |
| `fin_inst` | Financial Instruments | 5 | financial_instrument, hedge_relationship, instrument_valuation |
| `ipsas` | Public Sector (IPSAS) | 8 | fund, appropriation, commitment, allotment |
| `lease` | Lease Accounting | 5 | lease_contract, lease_asset, lease_liability |
| `payments` | Payment Gateway | 5 | payment_intent, remita_rrr, transfer_batch |
| `sync` | External Sync | 10 | integration_config, sync_entity, staging_employee |
| `rpt` | Reporting | 5 | report_definition, report_instance, financial_statement_line |
| `scheduling` | Shift Scheduling | 4 | shift_schedule, shift_pattern, shift_swap_request |
| `training` | Training | 3 | training_program, training_event, training_attendee |
| `audit` | Audit/Approval | 4 | approval_request, approval_workflow, audit_log |
| `platform` | Event Sourcing | 5 | event_outbox, saga_execution, idempotency_record |
| `public` | Core/Auth | 22 | people, roles, permissions, notification, sessions, api_keys |
| `settings` | Org Settings | 1 | org_bank_directory |
| `common` | Shared | 1 | attachment |
| `migration` | Data Migration | 2 | id_mapping, company_org_map |
| `exp` | Legacy Expense | 1 | expense_entry |

---

### Cross-Schema Relationship Map

Schemas are NOT isolated — they reference each other heavily via FKs.
This map shows the **business-critical** cross-schema joins (omitting
the ubiquitous `organization_id -> core_org.organization` FK on every table
and `created_by_id / updated_by_id -> public.people` audit columns).

```
                        ┌──────────────┐
                        │  core_org    │
                        │ organization │
                        │ cost_center  │
                        │ location     │
                        │ project      │
                        └──────┬───────┘
                               │ org_id on every table
            ┌──────────────────┼──────────────────────┐
            │                  │                       │
    ┌───────▼───────┐  ┌──────▼───────┐  ┌───────────▼──────────┐
    │   ar (AR)     │  │  gl (GL)     │  │   hr (People)        │
    │ customer ─────┼──┤ account      │  │ employee ────────────┤
    │ invoice ──────┼──┤ journal_entry│  │ department           │
    │ customer_     │  │ journal_line │  │ designation          │
    │   payment     │  │ fiscal_period│  └──────────┬───────────┘
    │ quote         │  │ fiscal_year  │             │ employee_id
    │ sales_order   │  │ budget       │     ┌───────┴────────────────┐
    └───┬───────────┘  └──────┬───────┘     │                        │
        │                     │        ┌────▼────────┐  ┌────────────▼───┐
        │ invoice.            │ j.e.   │  payroll    │  │  leave         │
        │ journal_entry_id    │ lines  │ salary_slip │  │ leave_app      │
        │ ────────────────────┘        │ payroll_    │  │ leave_alloc    │
        │                              │   entry     │  └────────────────┘
        │ customer_id                  └──────┬──────┘
        │ ┌──────────┐                        │ journal_entry_id
        ├─┤ support  │                        │ bank_account_id
        │ │ ticket   │                  ┌─────▼──────┐
        │ └──────────┘                  │  banking   │
        │                               │ bank_accts │
        │ customer_id                   │ statements │
        │ ┌──────────┐                  │ payee ─────┼── ar.customer / ap.supplier
        ├─┤ core_org │                  └────────────┘
        │ │ project  │                        │ gl_account_id -> gl.account
        │ └──────────┘
        │
    ┌───▼───────────┐    ┌─────────────┐
    │   ap (AP)     │    │  expense    │
    │ supplier      │    │ expense_    │
    │ supplier_     │    │   claim ────┼── hr.employee (employee_id)
    │   invoice ────┼────┤ expense_    │   gl.journal_entry (journal_entry_id)
    │ supplier_     │    │   claim_item│   ap.supplier_invoice (supplier_invoice_id)
    │   payment     │    └─────────────┘   core_org.project (project_id)
    └───┬───────────┘                      support.ticket (ticket_id)
        │
        │ supplier_id          ┌─────────────┐
        ├──────────────────────┤  fleet      │
        │ supplier_invoice_id  │ vehicle ────┼── hr.employee (assigned_employee_id)
        │                      │ maintenance │   hr.department (assigned_department_id)
        │                      │ fuel_log ───┼── expense.expense_claim (expense_claim_id)
        │                      └─────────────┘
        │
    ┌───▼───────────┐    ┌─────────────┐
    │   inv         │    │  tax        │
    │ item          │    │ tax_code ───┼── referenced by ar.invoice_line_tax,
    │ warehouse     │    │ tax_period  │   ap.supplier_invoice_line_tax,
    │ inv_txn ──────┼────┤ tax_return  │   ar.customer, ap.supplier_payment,
    │ material_req  │    └─────────────┘   banking.payee, ar/ap line items
    │   └── hr.employee (requested_by_id)
    │   └── core_org.project (project_id)
    │   └── support.ticket (ticket_id)
    └───────────────┘
        │ item_id
        │ warehouse_id
        │ lot_id
    ┌───▼───────────┐
    │ ar.invoice_   │
    │   line        │  (inventory fulfillment link)
    └───────────────┘
```

**Key cross-schema joins you'll need most often:**

| From | FK Column | To | Business Meaning |
|------|-----------|-----|-----------------|
| `ar.invoice` | `customer_id` | `ar.customer` | Who was billed |
| `ar.invoice` | `journal_entry_id` | `gl.journal_entry` | GL posting for revenue recognition |
| `ar.invoice_line` | `warehouse_id` | `inv.warehouse` | Where stock was shipped from |
| `ar.invoice_line` | `inventory_transaction_id` | `inv.inventory_transaction` | Stock movement linked to sale |
| `ar.invoice_line_tax` | `tax_code_id` | `tax.tax_code` | Tax rate applied to line |
| `ap.supplier_invoice_line` | `created_asset_id` | `fa.asset` | AP invoice created a fixed asset |
| `banking.bank_accounts` | `gl_account_id` | `gl.account` | Bank account's GL mapping |
| `banking.payee` | `customer_id` / `supplier_id` | `ar.customer` / `ap.supplier` | Payee is a customer or supplier |
| `expense.expense_claim` | `employee_id` | `hr.employee` | Who filed the expense |
| `expense.expense_claim` | `journal_entry_id` | `gl.journal_entry` | GL posting for expense |
| `expense.expense_claim` | `supplier_invoice_id` | `ap.supplier_invoice` | Expense converted to AP invoice |
| `expense.expense_claim` | `project_id` | `core_org.project` | Project the expense is charged to |
| `payroll.salary_slip` | `employee_id` | `hr.employee` | Whose payslip |
| `payroll.salary_slip` | `journal_entry_id` | `gl.journal_entry` | GL posting for payroll |
| `payroll.payroll_entry` | `bank_account_id` | `banking.bank_accounts` | Which bank account pays salaries |
| `payroll.payroll_entry` | `journal_entry_id` | `gl.journal_entry` | GL posting for payroll batch |
| `leave.leave_application` | `employee_id` | `hr.employee` | Who took leave |
| `leave.leave_application` | `salary_slip_id` | `payroll.salary_slip` | Leave deduction on payslip |
| `perf.appraisal` | `employee_id` / `manager_id` | `hr.employee` | Employee and their reviewer |
| `support.ticket` | `customer_id` | `ar.customer` | Customer who raised ticket |
| `support.ticket` | `assigned_to_id` | `hr.employee` | Engineer assigned to resolve |
| `fleet.vehicle` | `assigned_employee_id` | `hr.employee` | Who drives the vehicle |
| `fleet.maintenance_record` | `supplier_id` | `ap.supplier` | Mechanic/vendor |
| `fleet.maintenance_record` | `invoice_id` | `ap.supplier_invoice` | AP invoice for repair |
| `inv.material_request` | `requested_by_id` | `hr.employee` | Who requested materials |
| `inv.material_request` | `ticket_id` | `support.ticket` | Ticket that triggered request |
| `hr.employee` | `person_id` | `public.people` | User login account |
| `hr.employee` | `cost_center_id` | `core_org.cost_center` | Cost center for payroll allocation |
| `hr.department` | `cost_center_id` | `core_org.cost_center` | Department's cost center |
| `core_org.project` | `customer_id` | `ar.customer` | Customer the project is for |
| `recruit.job_offer` | `converted_to_employee_id` | `hr.employee` | Hired candidate becomes employee |

---

### Primary Key Naming Convention

PKs are NOT `id` — each model has domain-specific PK names. Key examples:

| Table | PK Column | Notes |
|-------|-----------|-------|
| `ar.customer` | `customer_id` | UUID |
| `ar.invoice` | `invoice_id` | UUID |
| `ar.customer_payment` | `payment_id` | UUID |
| `ap.supplier` | `supplier_id` | UUID |
| `ap.supplier_invoice` | `invoice_id` | UUID |
| `ap.supplier_payment` | `payment_id` | UUID |
| `gl.account` | `account_id` | UUID |
| `gl.journal_entry` | `journal_entry_id` | UUID |
| `gl.fiscal_period` | `fiscal_period_id` | UUID |
| `hr.employee` | `employee_id` | UUID |
| `hr.department` | `department_id` | UUID |
| `inv.item` | `item_id` | UUID |
| `inv.warehouse` | `warehouse_id` | UUID |
| `expense.expense_claim` | `claim_id` | UUID |
| `payroll.salary_slip` | `slip_id` | UUID |
| `payroll.payroll_entry` | `entry_id` | UUID |
| `core_org.organization` | `organization_id` | UUID |
| `public.people` | `id` | UUID (exception — uses bare `id`!) |
| `public.notification` | `notification_id` | UUID |
| `support.ticket` | `ticket_id` | UUID |
| `banking.bank_accounts` | `bank_account_id` | UUID |
| `gl.posted_ledger_line` | `ledger_line_id, posting_year` | Composite PK! |

---

### Status Semantics (What Values Mean in Business Terms)

Understanding what status values mean is critical for correct queries.

Do **not** assume enum values from memory or from this document: verify values
in [SCHEMA_REF.md](SCHEMA_REF.md) (auto-generated) or query Postgres catalogs.

Example (list values for an enum by name):
```sql
SELECT t.typname AS enum_name, e.enumlabel AS value, e.enumsortorder AS ord
FROM pg_type t
JOIN pg_enum e ON e.enumtypid = t.oid
WHERE t.typname = 'invoice_status'
ORDER BY e.enumsortorder;
```

---

### Data Quality & Table Usage Notes

Avoid stale assumptions about table sizes or “unused” modules. If you need to
optimize or avoid large scans, consult `SCHEMA_REF.md` row-count hints (generated
from `pg_class.reltuples`) and use `EXPLAIN` when available.

**Reference/config tables (small but important):**
- `core_org.organization` — 1 row (single-tenant in production)
- `core_config.numbering_sequence` — sequence counters for CUST-, EMP-, INV-, etc.
- `tax.tax_code` — 1+ tax codes (VAT rates)
- `leave.leave_type` — 3 leave types configured
- `attendance.shift_type` — 4 shift types

---

### Business Query Cookbook

**Copy and adapt these queries. They use correct joins, indexes, and org filters.**

#### Finance — Revenue & Collections

```sql
-- 1. Top 20 customers by total invoiced amount
SELECT c.customer_code, c.customer_name,
       COUNT(i.invoice_id) as invoice_count,
       SUM(i.total_amount) as total_invoiced,
       SUM(i.amount_paid) as total_paid,
       SUM(i.total_amount - i.amount_paid) as outstanding
FROM ar.invoice i
JOIN ar.customer c ON c.customer_id = i.customer_id
WHERE i.organization_id = '00000000-0000-0000-0000-000000000001'
  AND i.status IN ('POSTED', 'PAID', 'OVERDUE')
GROUP BY c.customer_id, c.customer_code, c.customer_name
ORDER BY total_invoiced DESC
LIMIT 20;

-- 2. Monthly revenue trend (last 12 months)
SELECT DATE_TRUNC('month', i.invoice_date) as month,
       COUNT(*) as invoice_count,
       SUM(i.total_amount) as revenue
FROM ar.invoice i
WHERE i.organization_id = '00000000-0000-0000-0000-000000000001'
  AND i.status IN ('POSTED', 'PAID', 'OVERDUE')
  AND i.invoice_date >= CURRENT_DATE - INTERVAL '12 months'
GROUP BY DATE_TRUNC('month', i.invoice_date)
ORDER BY month;

-- 3. AR aging (current, 30, 60, 90+ days overdue)
SELECT
  c.customer_name,
  SUM(CASE WHEN i.due_date >= CURRENT_DATE THEN i.total_amount - i.amount_paid ELSE 0 END) as current_bal,
  SUM(CASE WHEN i.due_date < CURRENT_DATE AND i.due_date >= CURRENT_DATE - 30
      THEN i.total_amount - i.amount_paid ELSE 0 END) as days_1_30,
  SUM(CASE WHEN i.due_date < CURRENT_DATE - 30 AND i.due_date >= CURRENT_DATE - 60
      THEN i.total_amount - i.amount_paid ELSE 0 END) as days_31_60,
  SUM(CASE WHEN i.due_date < CURRENT_DATE - 60 AND i.due_date >= CURRENT_DATE - 90
      THEN i.total_amount - i.amount_paid ELSE 0 END) as days_61_90,
  SUM(CASE WHEN i.due_date < CURRENT_DATE - 90
      THEN i.total_amount - i.amount_paid ELSE 0 END) as days_90_plus
FROM ar.invoice i
JOIN ar.customer c ON c.customer_id = i.customer_id
WHERE i.organization_id = '00000000-0000-0000-0000-000000000001'
  AND i.status IN ('POSTED', 'OVERDUE')
  AND i.total_amount > i.amount_paid
GROUP BY c.customer_id, c.customer_name
ORDER BY days_90_plus DESC
LIMIT 25;

-- 4. Daily collections (payments received last 30 days)
SELECT DATE(cp.payment_date) as day,
       COUNT(*) as payment_count,
       SUM(cp.amount) as total_collected
FROM ar.customer_payment cp
WHERE cp.organization_id = '00000000-0000-0000-0000-000000000001'
  AND cp.status = 'CLEARED'
  AND cp.payment_date >= CURRENT_DATE - 30
GROUP BY DATE(cp.payment_date)
ORDER BY day;

-- 5. Collection rate (% of invoiced amount that's been paid)
SELECT
  DATE_TRUNC('month', i.invoice_date) as month,
  SUM(i.total_amount) as invoiced,
  SUM(i.amount_paid) as collected,
  ROUND(SUM(i.amount_paid) * 100.0 / NULLIF(SUM(i.total_amount), 0), 1) as collection_pct
FROM ar.invoice i
WHERE i.organization_id = '00000000-0000-0000-0000-000000000001'
  AND i.status IN ('POSTED', 'PAID', 'OVERDUE')
  AND i.invoice_date >= CURRENT_DATE - INTERVAL '12 months'
GROUP BY DATE_TRUNC('month', i.invoice_date)
ORDER BY month;
```

#### Finance — General Ledger

```sql
-- 6. Trial balance (all accounts with balances)
SELECT a.account_code, a.account_name, a.account_type,
       COALESCE(ab.closing_debit, 0) as debit,
       COALESCE(ab.closing_credit, 0) as credit,
       COALESCE(ab.closing_debit, 0) - COALESCE(ab.closing_credit, 0) as net
FROM gl.account a
LEFT JOIN gl.account_balance ab ON ab.account_id = a.account_id
  AND ab.balance_type = 'ACTUAL'
WHERE a.organization_id = '00000000-0000-0000-0000-000000000001'
  AND a.account_type = 'POSTING'
ORDER BY a.account_code
LIMIT 50;

-- 7. Journal entries for a specific account (account ledger)
-- Replace the account_code with the one you need
SELECT je.entry_date, je.journal_number, je.description,
       jel.debit_amount, jel.credit_amount
FROM gl.journal_entry je
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
JOIN gl.account a ON a.account_id = jel.account_id
WHERE je.organization_id = '00000000-0000-0000-0000-000000000001'
  AND je.status = 'POSTED'
  AND a.account_code = '4000'  -- replace with target account
ORDER BY je.entry_date DESC
LIMIT 25;

-- 8. Fiscal period status overview
SELECT fy.year_name, fp.period_name, fp.start_date, fp.end_date, fp.status
FROM gl.fiscal_period fp
JOIN gl.fiscal_year fy ON fy.fiscal_year_id = fp.fiscal_year_id
WHERE fp.organization_id = '00000000-0000-0000-0000-000000000001'
ORDER BY fp.start_date DESC
LIMIT 24;
```

#### HR & People

```sql
-- 9. Employee headcount by department
SELECT d.department_name,
       COUNT(*) FILTER (WHERE e.status = 'ACTIVE') as active,
       COUNT(*) FILTER (WHERE e.status = 'ON_LEAVE') as on_leave,
       COUNT(*) FILTER (WHERE e.status = 'SUSPENDED') as suspended,
       COUNT(*) as total
FROM hr.employee e
JOIN hr.department d ON d.department_id = e.department_id
WHERE e.organization_id = '00000000-0000-0000-0000-000000000001'
GROUP BY d.department_name
ORDER BY total DESC;

-- 10. Employees who haven't taken leave this year
SELECT e.employee_code, e.first_name, e.last_name, d.department_name
FROM hr.employee e
JOIN hr.department d ON d.department_id = e.department_id
LEFT JOIN leave.leave_application la ON la.employee_id = e.employee_id
  AND la.status = 'APPROVED'
  AND EXTRACT(YEAR FROM la.from_date) = EXTRACT(YEAR FROM CURRENT_DATE)
WHERE e.organization_id = '00000000-0000-0000-0000-000000000001'
  AND e.status = 'ACTIVE'
  AND la.application_id IS NULL
ORDER BY e.employee_code;

-- 11. Leave balance summary by department
SELECT d.department_name, lt.leave_type_name,
       SUM(la.total_leaves_allocated) as allocated,
       SUM(la.leaves_used) as used,
       SUM(la.total_leaves_allocated - la.leaves_used) as remaining
FROM leave.leave_allocation la
JOIN hr.employee e ON e.employee_id = la.employee_id
JOIN hr.department d ON d.department_id = e.department_id
JOIN leave.leave_type lt ON lt.leave_type_id = la.leave_type_id
WHERE la.organization_id = '00000000-0000-0000-0000-000000000001'
  AND e.status = 'ACTIVE'
GROUP BY d.department_name, lt.leave_type_name
ORDER BY d.department_name, lt.leave_type_name;
```

#### Payroll

```sql
-- 12. Monthly payroll cost trend
SELECT DATE_TRUNC('month', ss.start_date) as month,
       COUNT(DISTINCT ss.employee_id) as employees_paid,
       SUM(ss.gross_pay) as total_gross,
       SUM(ss.total_deduction) as total_deductions,
       SUM(ss.net_pay) as total_net
FROM payroll.salary_slip ss
WHERE ss.organization_id = '00000000-0000-0000-0000-000000000001'
  AND ss.status IN ('SUBMITTED', 'POSTED', 'PAID')
GROUP BY DATE_TRUNC('month', ss.start_date)
ORDER BY month DESC
LIMIT 12;

-- 13. Payroll breakdown by department (latest month)
SELECT d.department_name,
       COUNT(DISTINCT ss.employee_id) as headcount,
       SUM(ss.gross_pay) as gross,
       SUM(ss.net_pay) as net
FROM payroll.salary_slip ss
JOIN hr.employee e ON e.employee_id = ss.employee_id
JOIN hr.department d ON d.department_id = e.department_id
WHERE ss.organization_id = '00000000-0000-0000-0000-000000000001'
  AND ss.status IN ('SUBMITTED', 'POSTED', 'PAID')
  AND DATE_TRUNC('month', ss.start_date) = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
GROUP BY d.department_name
ORDER BY gross DESC;
```

#### Expenses

```sql
-- 14. Expense claims pipeline (status breakdown)
SELECT status,
       COUNT(*) as claim_count,
       SUM(total_claimed_amount) as total_claimed
FROM expense.expense_claim
WHERE organization_id = '00000000-0000-0000-0000-000000000001'
GROUP BY status
ORDER BY total_claimed DESC;

-- 15. Top expense categories (last 6 months)
SELECT ec.category_name,
       COUNT(eci.item_id) as line_items,
       SUM(eci.claimed_amount) as total_spent
FROM expense.expense_claim_item eci
JOIN expense.expense_category ec ON ec.category_id = eci.category_id
JOIN expense.expense_claim ecl ON ecl.claim_id = eci.claim_id
WHERE eci.organization_id = '00000000-0000-0000-0000-000000000001'
  AND ecl.status IN ('APPROVED', 'PAID')
  AND ecl.claim_date >= CURRENT_DATE - INTERVAL '6 months'
GROUP BY ec.category_name
ORDER BY total_spent DESC
LIMIT 15;
```

#### Operations

```sql
-- 16. Support ticket volume by status
SELECT status,
       COUNT(*) as ticket_count,
       ROUND(AVG(EXTRACT(EPOCH FROM (resolution_date - created_at::date)) * 24), 1) as avg_resolution_hours
FROM support.ticket
WHERE organization_id = '00000000-0000-0000-0000-000000000001'
GROUP BY status
ORDER BY ticket_count DESC;

-- 17. Tickets per customer (top 10 most active)
SELECT c.customer_code, c.customer_name,
       COUNT(t.ticket_id) as ticket_count,
       COUNT(*) FILTER (WHERE t.status = 'OPEN') as open_tickets
FROM support.ticket t
JOIN ar.customer c ON c.customer_id = t.customer_id
WHERE t.organization_id = '00000000-0000-0000-0000-000000000001'
GROUP BY c.customer_id, c.customer_code, c.customer_name
ORDER BY ticket_count DESC
LIMIT 10;
```

#### Inventory

```sql
-- 18. Current stock levels by item
-- Transaction types that ADD stock: RECEIPT, RETURN, ASSEMBLY, COUNT_ADJUSTMENT(+)
-- Transaction types that REMOVE stock: ISSUE, SALE, TRANSFER, DISASSEMBLY, SCRAP
-- ADJUSTMENT and COUNT_ADJUSTMENT can be +/-; use the signed quantity
SELECT i.item_code, i.item_name, w.warehouse_name,
       SUM(CASE
           WHEN it.transaction_type IN ('RECEIPT', 'RETURN', 'ASSEMBLY') THEN it.quantity
           WHEN it.transaction_type IN ('ISSUE', 'SALE', 'TRANSFER', 'DISASSEMBLY', 'SCRAP') THEN -it.quantity
           ELSE it.quantity  -- ADJUSTMENT, COUNT_ADJUSTMENT: use signed value
       END) as balance
FROM inv.inventory_transaction it
JOIN inv.item i ON i.item_id = it.item_id
JOIN inv.warehouse w ON w.warehouse_id = it.warehouse_id
WHERE it.organization_id = '00000000-0000-0000-0000-000000000001'
GROUP BY i.item_code, i.item_name, w.warehouse_name
HAVING SUM(CASE
           WHEN it.transaction_type IN ('RECEIPT', 'RETURN', 'ASSEMBLY') THEN it.quantity
           WHEN it.transaction_type IN ('ISSUE', 'SALE', 'TRANSFER', 'DISASSEMBLY', 'SCRAP') THEN -it.quantity
           ELSE it.quantity
       END) > 0
ORDER BY i.item_code, w.warehouse_name;
```

#### Data Integrity Checks

```sql
-- 19. Unbalanced journal entries (debits != credits)
SELECT je.journal_number, je.entry_date, je.description,
       SUM(jel.debit_amount) as total_debit,
       SUM(jel.credit_amount) as total_credit,
       SUM(jel.debit_amount) - SUM(jel.credit_amount) as imbalance
FROM gl.journal_entry je
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
WHERE je.organization_id = '00000000-0000-0000-0000-000000000001'
  AND je.status = 'POSTED'
GROUP BY je.journal_entry_id, je.journal_number, je.entry_date, je.description
HAVING ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) > 0.01
LIMIT 25;

-- 20. Invoices where amount_paid exceeds total (overpayments)
SELECT i.invoice_number, i.invoice_date, i.total_amount, i.amount_paid,
       i.amount_paid - i.total_amount as overpayment,
       c.customer_name
FROM ar.invoice i
JOIN ar.customer c ON c.customer_id = i.customer_id
WHERE i.organization_id = '00000000-0000-0000-0000-000000000001'
  AND i.amount_paid > i.total_amount
ORDER BY overpayment DESC
LIMIT 25;
```

---

### Output Formatting Conventions

When presenting query results to the user:
- **Currency**: Format as `NGN 1,234,567.89` (comma thousands, 2 decimals)
- **Negative amounts**: Show in parentheses: `NGN (1,234.56)` and note it's negative
- **Dates**: Format as `DD MMM YYYY` (e.g., `07 Feb 2026`)
- **Percentages**: One decimal: `85.3%`
- **Large row counts**: Use commas: `134,258 rows`
- **NULL values**: Show as `—` (em dash), never "None" or "null"
- **Always state the currency**: When showing amounts, always prefix with `NGN` since that's the functional currency

---

### Entity Lifecycle Flows

Understanding how entities move through states helps answer questions about stuck
items, pipeline health, and process compliance.

#### Invoice Lifecycle (AR)
```
DRAFT → SUBMITTED → APPROVED → POSTED → PARTIALLY_PAID → PAID
                                  ↓              ↓
                              OVERDUE ──────→ PAID
                                  ↓
                              DISPUTED
         ↓ (any pre-POSTED)
        VOID
```
- **Tables**: `ar.invoice` + `ar.invoice_line` + `ar.invoice_line_tax`
- **GL link**: `invoice.journal_entry_id` -> `gl.journal_entry` (created on POSTED)
- **Payment link**: `ar.payment_allocation` (M:N between `ar.customer_payment` and `ar.invoice`)
- **Key query**: "Invoices stuck in DRAFT" = `WHERE status = 'DRAFT' AND created_at < NOW() - INTERVAL '7 days'`

#### AP Invoice Lifecycle
```
DRAFT → SUBMITTED → PENDING_APPROVAL → APPROVED → POSTED → PARTIALLY_PAID → PAID
                                                      ↓
                                                   ON_HOLD
         ↓ (any pre-POSTED)         ↓
        VOID                    DISPUTED
```
- **Tables**: `ap.supplier_invoice` + `ap.supplier_invoice_line` + `ap.supplier_invoice_line_tax`
- **GL link**: `supplier_invoice.journal_entry_id` -> `gl.journal_entry`
- **Payment link**: `ap.supplier_payment` (status: DRAFT→PENDING→APPROVED→SENT→CLEARED)

#### Quote-to-Cash Flow
```
ar.quote (DRAFT→SENT→VIEWED→ACCEPTED→CONVERTED)
    ↓ CONVERTED
ar.sales_order (DRAFT→SUBMITTED→APPROVED→CONFIRMED→IN_PROGRESS→SHIPPED→COMPLETED)
    ↓ invoiced
ar.invoice (DRAFT→...→PAID)
    ↓ payment
ar.customer_payment (PENDING→APPROVED→CLEARED)
```
- **Conversion FKs**: `sales_order.quote_id` -> `ar.quote`; invoice can link back via line items
- **Quote expiry**: EXPIRED status when past `valid_until` date

#### Procure-to-Pay Flow
```
proc.purchase_requisition (DRAFT→SUBMITTED→BUDGET_VERIFIED→APPROVED→CONVERTED)
    ↓ CONVERTED
proc.request_for_quotation (DRAFT→PUBLISHED→CLOSED→EVALUATED→AWARDED)
    ↓ AWARDED
ap.purchase_order (DRAFT→PENDING_APPROVAL→APPROVED→PARTIALLY_RECEIVED→RECEIVED→CLOSED)
    ↓ goods received
ap.goods_receipt (RECEIVED→INSPECTING→ACCEPTED / REJECTED / PARTIAL)
    ↓ invoice matched
ap.supplier_invoice (DRAFT→...→PAID)
    ↓ payment
ap.supplier_payment (DRAFT→PENDING→APPROVED→SENT→CLEARED)
```

#### Expense Claim Flow
```
expense.expense_claim (DRAFT→SUBMITTED→PENDING_APPROVAL→APPROVED→PAID)
                                          ↓                 ↓
                                       REJECTED         Creates ap.supplier_invoice
                                          ↓             (expense_claim.supplier_invoice_id)
                                       CANCELLED
```
- **Employee link**: `expense_claim.employee_id` -> `hr.employee`
- **GL link**: `expense_claim.journal_entry_id` -> `gl.journal_entry`
- **Project link**: `expense_claim.project_id` -> `core_org.project`
- **Line items**: `expense.expense_claim_item` with `category_id` -> `expense.expense_category`

#### Payroll Cycle
```
payroll.payroll_entry (DRAFT→SLIPS_CREATED→SUBMITTED→APPROVED→POSTED)
    ↓ SLIPS_CREATED (generates one per employee)
payroll.salary_slip (DRAFT→SUBMITTED→APPROVED→POSTED→PAID)
    ↓ POSTED
gl.journal_entry (payroll expense + liability entries)
    ↓ PAID (bank transfer)
banking.bank_accounts (disbursement from payroll_entry.bank_account_id)
```
- **Payroll entry** is the batch; salary slips are individual
- **Tax**: Nigerian PAYE bands in `payroll.tax_band`

#### Employee Lifecycle
```
recruit.job_applicant (NEW→SCREENING→SHORTLISTED→INTERVIEW_SCHEDULED→
                       INTERVIEW_COMPLETED→SELECTED→OFFER_EXTENDED)
    ↓ OFFER_EXTENDED
recruit.job_offer (DRAFT→PENDING_APPROVAL→APPROVED→EXTENDED→ACCEPTED)
    ↓ ACCEPTED (converted_to_employee_id FK)
hr.employee (DRAFT→ACTIVE→ON_LEAVE / SUSPENDED→RESIGNED / TERMINATED / RETIRED)
```
- **Onboarding**: `hr.employee_onboarding` tracks first-day tasks
- **Separation**: RESIGNED / TERMINATED / RETIRED are terminal states

#### Journal Entry & GL Posting
```
gl.journal_entry (DRAFT→SUBMITTED→APPROVED→POSTED)
    ↓ POSTED (lines affect account_balance)
gl.journal_entry_line (debit_amount + credit_amount per line)
    ↓ SUM must balance (debit = credit)
gl.account_balance (running balance per account per period)
                                      ↓ REVERSED
                         gl.journal_entry (new reversal entry)
```
- **Period gating**: Can only post to `gl.fiscal_period` with status OPEN or REOPENED
- **Period close**: OPEN → SOFT_CLOSED → HARD_CLOSED (no more postings)

#### Leave Management
```
leave.leave_allocation (allocated per employee per leave_type per year)
    ↓
leave.leave_application (DRAFT→SUBMITTED→APPROVED / REJECTED)
    ↓ APPROVED
attendance.attendance (marked as ON_LEAVE, links via leave_application_id)
    ↓ deducted from
leave.leave_allocation.leaves_used (incremented)
```

#### Support Ticket
```
support.ticket (OPEN→REPLIED→ON_HOLD→RESOLVED→CLOSED)
```
- **Customer link**: `ticket.customer_id` -> `ar.customer`
- **Assignment**: `ticket.assigned_to_id` -> `hr.employee`
- **Material link**: `inv.material_request.ticket_id` (if parts needed)

#### Discipline Case
```
hr.disciplinary_case (DRAFT→QUERY_ISSUED→RESPONSE_RECEIVED→UNDER_INVESTIGATION→
                      HEARING_SCHEDULED→HEARING_COMPLETED→DECISION_MADE→CLOSED)
                                                              ↓
                                                         APPEAL_FILED→APPEAL_DECIDED→CLOSED
```

---

### Business Rules

1. **Invoices flow**: DRAFT -> POSTED -> PAID (or OVERDUE -> PAID)
2. **All amounts**: Numeric(15,2) in NGN unless multi-currency enabled
3. **Fiscal years**: Calendar year (Jan-Dec), 12 monthly periods
4. **Customer numbers**: CUST-NNNNN (sequential, currently up to ~5241)
5. **Employee numbers**: EMP-NNNNN (sequential)
6. **Double-entry**: Every journal has balanced debit/credit lines
7. **Subscription billing**: Separate module in `app/starter/subscription_billing/`
8. **Payment allocation**: One payment can cover multiple invoices (`ar.payment_allocation` is the M:N link)
9. **Posting**: Only `POSTED` journals affect GL balances. Draft/Submitted journals have no financial impact.
10. **Expense -> AP flow**: Approved expense claims can create an `ap.supplier_invoice` (the `supplier_invoice_id` FK)

### Detailed Column Reference

See [SCHEMA_REF.md](SCHEMA_REF.md) for auto-generated complete column-level
details for all 312 tables, including data types, nullability, and foreign keys.

### Safety Rules

- **NEVER** run INSERT, UPDATE, DELETE, DROP, TRUNCATE, or ALTER
- **Always** use LIMIT (default 25, max 100)
- For large result sets, run `SELECT COUNT(*)` first to gauge size
- Don't expose PII (mask email addresses, phone numbers) in outputs
- When querying amounts, always note the currency (usually NGN)
- Prefix schema name on all tables: `ar.invoice` not just `invoice`
- For tables >100K rows, always filter on indexed columns (see table above)
- Avoid `SELECT *` on large tables — list only the columns you need
