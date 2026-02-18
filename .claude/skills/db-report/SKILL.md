---
name: db-report
description: Generate formatted analytical reports from the ERP database with tables, summaries, and optional chart code
arguments:
  - name: report
    description: "What report to generate (e.g. 'monthly revenue', 'AR aging', 'headcount by department', 'payroll cost trend', 'expense breakdown')"
---

# Database Analytics Report Generator

Generate a polished, formatted analytical report from the DotMac ERP database.
Use the `erp-db` MCP server (`execute_sql` tool) for all queries.

## How to Generate a Report

### Step 1: Understand the Request

Parse the user's report request. Common report types:

| Report Category | Key Tables | Typical Dimensions |
|----------------|------------|-------------------|
| Revenue & Collections | `ar.invoice`, `ar.customer_payment`, `ar.customer` | month, customer, status |
| AR Aging | `ar.invoice`, `ar.customer` | customer, aging bucket |
| AP & Payables | `ap.supplier_invoice`, `ap.supplier_payment`, `ap.supplier` | month, supplier, status |
| Payroll Cost | `payroll.salary_slip`, `payroll.payroll_entry`, `hr.employee` | month, department |
| Headcount | `hr.employee`, `hr.department`, `hr.designation` | department, status, date |
| Expenses | `expense.expense_claim`, `expense.expense_claim_item`, `expense.expense_category` | month, category, employee |
| Leave Utilization | `leave.leave_allocation`, `leave.leave_application`, `leave.leave_type` | department, leave type |
| Inventory | `inv.item`, `inv.inventory_transaction`, `inv.warehouse` | item, warehouse, month |
| Support | `support.ticket`, `ar.customer` | status, customer, month |
| GL Trial Balance | `gl.account`, `gl.account_balance`, `gl.journal_entry` | account type, period |

### Step 2: Query the Database

**ALWAYS prefix every data query with the RLS session variable:**
```sql
SET app.current_organization_id = '00000000-0000-0000-0000-000000000001';
-- your SELECT here (must be in the SAME execute_sql call)
```

**ALWAYS include the org filter in WHERE clauses too (belt-and-suspenders):**
```sql
WHERE organization_id = '00000000-0000-0000-0000-000000000001'
```

**For time-series reports**, use `DATE_TRUNC('month', date_column)` for grouping.

**For large tables**, use LIMIT and filter on indexed columns. Check approximate
row counts first if unsure:
```sql
SELECT reltuples::bigint FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'ar' AND c.relname = 'invoice';
```

**Status filters**: Only include business-relevant statuses. For revenue reports,
use `IN ('POSTED', 'PAID', 'OVERDUE')` — exclude DRAFT and VOID.

### Step 3: Format the Report

Structure every report with these sections:

```markdown
## [Report Title]
*Generated: [current date] | Period: [date range] | Currency: NGN*

### Summary
- **[Key metric 1]**: [value]
- **[Key metric 2]**: [value]
- **[Key metric 3]**: [value]

### Detail
[Formatted markdown table with results]

### Observations
1. [Insight from the data]
2. [Trend or anomaly noticed]
3. [Actionable recommendation if applicable]
```

### Formatting Rules

| Data Type | Format | Example |
|-----------|--------|---------|
| Currency (NGN) | Comma-separated, 2 decimals | `NGN 1,234,567.89` |
| Negative amounts | Parentheses + note | `NGN (45,000.00)` |
| Dates | DD MMM YYYY | `07 Feb 2026` |
| Percentages | 1 decimal | `85.3%` |
| Row counts | Comma-separated | `1,234` |
| NULL / zero | Em dash | `—` |
| Month labels | MMM YYYY | `Jan 2026` |

**Table alignment**: Left-align text, right-align numbers, center statuses.

### Step 4: Optional Chart Code

If the report has a time-series or comparison dimension, offer to generate a
Chart.js snippet the user can paste into a template:

```javascript
// Example: Monthly Revenue Chart
new Chart(ctx, {
  type: 'bar',
  data: {
    labels: ['Jan 2026', 'Feb 2026', ...],
    datasets: [{
      label: 'Revenue (NGN)',
      data: [1234567, 2345678, ...],
      backgroundColor: 'rgba(13, 148, 136, 0.5)',  // teal-500
      borderColor: 'rgb(13, 148, 136)',
      borderWidth: 1
    }]
  },
  options: {
    responsive: true,
    scales: {
      y: {
        beginAtZero: true,
        ticks: {
          callback: v => 'NGN ' + v.toLocaleString()
        }
      }
    }
  }
});
```

**Chart color palette** (from design system):
- Primary: `rgb(13, 148, 136)` (teal-500)
- Secondary: `rgb(217, 119, 6)` (amber-600)
- Success: `rgb(5, 150, 105)` (emerald-600)
- Danger: `rgb(225, 29, 72)` (rose-600)
- Info: `rgb(37, 99, 235)` (blue-600)
- Neutral: `rgb(100, 116, 139)` (slate-500)

## Pre-built Report Templates

### Revenue Summary
```sql
SELECT
  DATE_TRUNC('month', i.invoice_date) AS month,
  COUNT(*) AS invoices,
  SUM(i.total_amount) AS invoiced,
  SUM(i.amount_paid) AS collected,
  SUM(i.total_amount - i.amount_paid) AS outstanding,
  ROUND(SUM(i.amount_paid) * 100.0 / NULLIF(SUM(i.total_amount), 0), 1) AS collection_pct
FROM ar.invoice i
WHERE i.organization_id = '00000000-0000-0000-0000-000000000001'
  AND i.status IN ('POSTED', 'PAID', 'OVERDUE', 'PARTIALLY_PAID')
  AND i.invoice_date >= CURRENT_DATE - INTERVAL '12 months'
GROUP BY DATE_TRUNC('month', i.invoice_date)
ORDER BY month;
```

### AR Aging Report
```sql
SELECT
  c.customer_code, c.customer_name,
  SUM(CASE WHEN i.due_date >= CURRENT_DATE THEN i.total_amount - i.amount_paid ELSE 0 END) AS current_bal,
  SUM(CASE WHEN i.due_date < CURRENT_DATE AND i.due_date >= CURRENT_DATE - 30
      THEN i.total_amount - i.amount_paid ELSE 0 END) AS "1-30",
  SUM(CASE WHEN i.due_date < CURRENT_DATE - 30 AND i.due_date >= CURRENT_DATE - 60
      THEN i.total_amount - i.amount_paid ELSE 0 END) AS "31-60",
  SUM(CASE WHEN i.due_date < CURRENT_DATE - 60 AND i.due_date >= CURRENT_DATE - 90
      THEN i.total_amount - i.amount_paid ELSE 0 END) AS "61-90",
  SUM(CASE WHEN i.due_date < CURRENT_DATE - 90
      THEN i.total_amount - i.amount_paid ELSE 0 END) AS "90+",
  SUM(i.total_amount - i.amount_paid) AS total_outstanding
FROM ar.invoice i
JOIN ar.customer c ON c.customer_id = i.customer_id
WHERE i.organization_id = '00000000-0000-0000-0000-000000000001'
  AND i.status IN ('POSTED', 'OVERDUE', 'PARTIALLY_PAID')
  AND i.total_amount > i.amount_paid
GROUP BY c.customer_id, c.customer_code, c.customer_name
HAVING SUM(i.total_amount - i.amount_paid) > 0
ORDER BY total_outstanding DESC
LIMIT 30;
```

### Headcount Report
```sql
SELECT
  d.department_name,
  COUNT(*) FILTER (WHERE e.status = 'ACTIVE') AS active,
  COUNT(*) FILTER (WHERE e.status = 'ON_LEAVE') AS on_leave,
  COUNT(*) FILTER (WHERE e.status = 'SUSPENDED') AS suspended,
  COUNT(*) FILTER (WHERE e.status IN ('RESIGNED', 'TERMINATED', 'RETIRED')) AS separated,
  COUNT(*) AS total
FROM hr.employee e
JOIN hr.department d ON d.department_id = e.department_id
WHERE e.organization_id = '00000000-0000-0000-0000-000000000001'
GROUP BY d.department_name
ORDER BY active DESC;
```

### Payroll Cost Trend
```sql
SELECT
  DATE_TRUNC('month', ss.start_date) AS month,
  COUNT(DISTINCT ss.employee_id) AS employees,
  SUM(ss.gross_pay) AS gross_pay,
  SUM(ss.total_deduction) AS deductions,
  SUM(ss.net_pay) AS net_pay,
  ROUND(SUM(ss.gross_pay) / NULLIF(COUNT(DISTINCT ss.employee_id), 0), 2) AS avg_gross
FROM payroll.salary_slip ss
WHERE ss.organization_id = '00000000-0000-0000-0000-000000000001'
  AND ss.status IN ('SUBMITTED', 'POSTED', 'PAID')
GROUP BY DATE_TRUNC('month', ss.start_date)
ORDER BY month DESC
LIMIT 12;
```

### Expense Category Breakdown
```sql
SELECT
  ec.category_name,
  COUNT(DISTINCT ecl.claim_id) AS claims,
  COUNT(eci.item_id) AS line_items,
  SUM(eci.claimed_amount) AS total_spent,
  ROUND(SUM(eci.claimed_amount) * 100.0 / NULLIF(SUM(SUM(eci.claimed_amount)) OVER (), 0), 1) AS pct_of_total
FROM expense.expense_claim_item eci
JOIN expense.expense_category ec ON ec.category_id = eci.category_id
JOIN expense.expense_claim ecl ON ecl.claim_id = eci.claim_id
WHERE eci.organization_id = '00000000-0000-0000-0000-000000000001'
  AND ecl.status IN ('APPROVED', 'PAID')
  AND ecl.claim_date >= CURRENT_DATE - INTERVAL '6 months'
GROUP BY ec.category_name
ORDER BY total_spent DESC;
```

## Reference

- **Schema details**: See `db-schema` skill for full table/column reference
- **Cross-schema FKs**: See SCHEMA_REF.md for join paths
- **Status enums**: Always verify enum values in database rather than assuming
- **Multi-tenancy**: ALWAYS filter by `organization_id`
- **Read-only**: Connection uses `claude_readonly` — writes will be rejected
- **LIMIT**: Always use LIMIT (default 25, max 100) on detail queries

## Safety Rules

- NEVER run INSERT, UPDATE, DELETE, DROP, TRUNCATE, or ALTER
- Don't expose PII (mask emails, phone numbers) in report output
- Always note the currency (NGN) when presenting financial figures
- Prefix schema name on all tables: `ar.invoice` not just `invoice`
- For tables >100K rows, filter on indexed columns
