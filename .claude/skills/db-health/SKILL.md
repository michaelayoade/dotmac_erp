---
name: db-health
description: "Run data integrity checks: orphaned records, numbering gaps, duplicates, schema drift, and the built-in health check"
arguments:
  - name: mode
    description: "'quick' for built-in health check only, 'full' for all checks, or a specific check name"
---

# Database Health Check

Run data integrity diagnostics against the DotMac ERP database.

## Modes

### Quick Mode (`quick`)

Run the existing `run_data_health_check()` task from `app/tasks/data_health.py`:

```bash
docker exec dotmac_erp_app python -c "
from app.tasks.data_health import run_data_health_check
import json
result = run_data_health_check()
print(json.dumps(result, indent=2, default=str))
"
```

Format the 8 checks as a table:

| Check | Value | Status |
|-------|-------|--------|
| Unbalanced journals | N | OK/P0 |
| False-paid invoices | N | OK/P0 |
| Stuck outbox events | N | OK/P1 |
| Dead outbox events | N | OK/P1 |
| Stale journal drafts | N | OK/P2 |
| Account balance rows | N | INFO |
| Notification total | N | INFO |
| Approved invoices stuck | N | OK/P1 |

P0 = any non-zero value, P1 = above threshold, P2 = informational concern.

### Full Mode (`full`)

Run all quick checks PLUS the following via the `erp-db` MCP (`execute_sql`):

#### 1. Orphaned Records (`orphans`)

```sql
-- Invoices referencing deleted/missing customers
SELECT i.invoice_id, i.invoice_number, i.customer_id
FROM ar.invoice i
LEFT JOIN ar.customer c ON c.customer_id = i.customer_id
WHERE c.customer_id IS NULL
LIMIT 20;

-- Salary slips referencing deleted/missing employees
SELECT ss.salary_slip_id, ss.slip_number, ss.employee_id
FROM people.salary_slip ss
LEFT JOIN people.employee e ON e.employee_id = ss.employee_id
WHERE e.employee_id IS NULL
LIMIT 20;

-- Journal lines referencing missing accounts
SELECT jel.line_id, jel.journal_entry_id, jel.account_id
FROM gl.journal_entry_line jel
LEFT JOIN gl.account a ON a.account_id = jel.account_id
WHERE a.account_id IS NULL
LIMIT 20;

-- Customer payments referencing missing invoices
SELECT cp.payment_id, cp.payment_number, cp.invoice_id
FROM ar.customer_payment cp
LEFT JOIN ar.invoice i ON i.invoice_id = cp.invoice_id
WHERE cp.invoice_id IS NOT NULL AND i.invoice_id IS NULL
LIMIT 20;
```

Report orphan counts per relationship. P0 if any found.

#### 2. Numbering Gaps (`numbering`)

```sql
-- Compare numbering_sequence current_number vs actual max entity numbers
SELECT
    ns.sequence_type,
    ns.prefix,
    ns.current_number AS sequence_says,
    sub.actual_max,
    CASE
        WHEN sub.actual_max > ns.current_number THEN 'DRIFT: actual > sequence'
        WHEN ns.current_number - sub.actual_max > 100 THEN 'GAP: >100 unused numbers'
        ELSE 'OK'
    END AS status
FROM platform.numbering_sequence ns
LEFT JOIN LATERAL (
    SELECT max(
        CASE ns.sequence_type
            WHEN 'INVOICE' THEN (SELECT max(cast(regexp_replace(invoice_number, '[^0-9]', '', 'g') AS int)) FROM ar.invoice WHERE organization_id = ns.organization_id)
            WHEN 'CUSTOMER' THEN (SELECT max(cast(regexp_replace(customer_code, '[^0-9]', '', 'g') AS int)) FROM ar.customer WHERE organization_id = ns.organization_id)
            WHEN 'EMPLOYEE' THEN (SELECT max(cast(regexp_replace(employee_code, '[^0-9]', '', 'g') AS int)) FROM people.employee WHERE organization_id = ns.organization_id)
            ELSE NULL
        END
    ) AS actual_max
) sub ON true
WHERE ns.sequence_type IN ('INVOICE', 'CUSTOMER', 'EMPLOYEE', 'RECEIPT', 'JOURNAL_ENTRY');
```

P1 if DRIFT found (sequence counter behind actual data — risk of duplicate numbers).

#### 3. Duplicate Detection (`duplicates`)

```sql
-- Duplicate customer names within same org
SELECT organization_id, customer_name, count(*) AS cnt
FROM ar.customer
GROUP BY organization_id, customer_name
HAVING count(*) > 1
ORDER BY cnt DESC
LIMIT 20;

-- Duplicate invoice numbers within same org
SELECT organization_id, invoice_number, count(*) AS cnt
FROM ar.invoice
GROUP BY organization_id, invoice_number
HAVING count(*) > 1
ORDER BY cnt DESC
LIMIT 10;

-- Duplicate employee codes within same org
SELECT organization_id, employee_code, count(*) AS cnt
FROM people.employee
GROUP BY organization_id, employee_code
HAVING count(*) > 1
ORDER BY cnt DESC
LIMIT 10;
```

P0 if duplicate invoice numbers found (accounting integrity). P2 for duplicate names.

#### 4. Schema Drift (`schema`)

```bash
docker exec dotmac_erp_app alembic check 2>&1
```

If output contains "New upgrade operations detected", report P1 with the drift details.
If "No new upgrade operations", report OK.

## Output Format

Present as a prioritized report matching `/run-audit` format:

```
## Database Health Report — {date}

### Summary
- **P0 (Critical)**: N issues
- **P1 (High)**: N issues
- **P2 (Medium)**: N issues
- **INFO**: N items

### P0 — Critical
(list issues with details and remediation steps)

### P1 — High
(list issues)

### P2 — Medium
(list issues)

### INFO
(informational metrics)

### Verdict: HEALTHY / NEEDS_ATTENTION / CRITICAL
```

**Verdict logic**:
- CRITICAL: Any P0 issues
- NEEDS_ATTENTION: Any P1 issues
- HEALTHY: Only P2/INFO items
