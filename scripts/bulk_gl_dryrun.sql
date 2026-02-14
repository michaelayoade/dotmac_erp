\set org_id '''00000000-0000-0000-0000-000000000001'''
\set tax_code '''4b180259-b0b0-41fb-955b-0e089df66b42'''

-- Build invoice staging (dry run)
DROP TABLE IF EXISTS _inv_dry;
CREATE TEMP TABLE _inv_dry AS
SELECT
    i.invoice_id,
    i.invoice_number,
    i.invoice_date,
    i.total_amount,
    i.invoice_type,
    fp.fiscal_period_id,
    c.legal_name AS customer_name
FROM ar.invoice i
JOIN gl.fiscal_period fp ON fp.organization_id = i.organization_id
    AND i.invoice_date BETWEEN fp.start_date AND fp.end_date
JOIN ar.customer c ON c.customer_id = i.customer_id
WHERE i.journal_entry_id IS NULL
  AND i.total_amount != 0
  AND i.organization_id = :org_id::uuid;

\echo '--- Invoice staging ---'
SELECT COUNT(*) AS invoices_staged FROM _inv_dry;
SELECT invoice_type, COUNT(*) AS cnt FROM _inv_dry GROUP BY invoice_type;

-- Build line staging (dry run)
DROP TABLE IF EXISTS _line_dry;
CREATE TEMP TABLE _line_dry AS
SELECT
    il.line_id,
    il.invoice_id,
    il.line_number,
    il.line_amount,
    COALESCE(il.tax_amount, 0) AS tax_amount,
    il.revenue_account_id,
    CASE WHEN il.tax_code_id = :tax_code::uuid AND COALESCE(il.tax_amount, 0) != 0
         THEN true ELSE false END AS has_separate_tax,
    il.line_amount + CASE WHEN il.tax_code_id = :tax_code::uuid AND COALESCE(il.tax_amount, 0) != 0
        THEN 0 ELSE COALESCE(il.tax_amount, 0) END AS raw_revenue
FROM ar.invoice_line il
JOIN _inv_dry s ON s.invoice_id = il.invoice_id;

\echo '--- Line staging ---'
SELECT
    COUNT(*) AS total_lines,
    COUNT(*) FILTER (WHERE raw_revenue = 0) AS zero_lines,
    COUNT(*) FILTER (WHERE has_separate_tax) AS tax_lines,
    COUNT(*) FILTER (WHERE raw_revenue < 0) AS negative_lines
FROM _line_dry;

-- Simulate the "last nonzero line adjustment"
-- Mark non-zero, find last per invoice, compute adjusted
ALTER TABLE _line_dry ADD COLUMN is_zero boolean DEFAULT false;
UPDATE _line_dry SET is_zero = true WHERE raw_revenue = 0;

ALTER TABLE _line_dry ADD COLUMN is_last_nonzero boolean DEFAULT false;
WITH last_lines AS (
    SELECT DISTINCT ON (invoice_id) line_id
    FROM _line_dry
    WHERE NOT is_zero
    ORDER BY invoice_id, line_number DESC
)
UPDATE _line_dry l
SET is_last_nonzero = true
FROM last_lines ll
WHERE l.line_id = ll.line_id;

ALTER TABLE _line_dry ADD COLUMN adjusted_revenue numeric;
WITH inv_sums AS (
    SELECT
        l.invoice_id,
        COALESCE(SUM(CASE WHEN NOT l.is_zero AND NOT l.is_last_nonzero
                          THEN l.raw_revenue ELSE 0 END), 0) AS other_revenue,
        COALESCE(SUM(CASE WHEN l.has_separate_tax THEN l.tax_amount ELSE 0 END), 0) AS total_sep_tax
    FROM _line_dry l
    GROUP BY l.invoice_id
)
UPDATE _line_dry l
SET adjusted_revenue = CASE
    WHEN l.is_zero THEN 0
    WHEN l.is_last_nonzero THEN d.total_amount - inv.other_revenue - inv.total_sep_tax
    ELSE l.raw_revenue
END
FROM _inv_dry d, inv_sums inv
WHERE l.invoice_id = d.invoice_id
  AND l.invoice_id = inv.invoice_id;

-- Validate: all journals should balance
\echo '--- Balance check ---'
WITH sums AS (
    SELECT
        d.invoice_id,
        d.total_amount,
        COALESCE(SUM(CASE WHEN NOT l.is_zero THEN l.adjusted_revenue ELSE 0 END), 0) +
        COALESCE(SUM(CASE WHEN l.has_separate_tax THEN l.tax_amount ELSE 0 END), 0) AS computed_total
    FROM _inv_dry d
    LEFT JOIN _line_dry l ON l.invoice_id = d.invoice_id
    GROUP BY d.invoice_id, d.total_amount
)
SELECT
    COUNT(*) AS total_invoices,
    COUNT(*) FILTER (WHERE ABS(total_amount - computed_total) <= 0.01) AS balanced,
    COUNT(*) FILTER (WHERE ABS(total_amount - computed_total) > 0.01) AS unbalanced
FROM sums;

-- Show a few examples of unbalanced (if any)
WITH sums AS (
    SELECT
        d.invoice_id,
        d.invoice_number,
        d.total_amount,
        COALESCE(SUM(CASE WHEN NOT l.is_zero THEN l.adjusted_revenue ELSE 0 END), 0) +
        COALESCE(SUM(CASE WHEN l.has_separate_tax THEN l.tax_amount ELSE 0 END), 0) AS computed_total
    FROM _inv_dry d
    LEFT JOIN _line_dry l ON l.invoice_id = d.invoice_id
    GROUP BY d.invoice_id, d.invoice_number, d.total_amount
)
SELECT invoice_id, invoice_number, total_amount, computed_total,
       total_amount - computed_total AS delta
FROM sums
WHERE ABS(total_amount - computed_total) > 0.01
LIMIT 10;

-- Check: any invoices with ALL zero lines (would be unbalanced)?
\echo '--- Invoices with all-zero lines ---'
SELECT COUNT(*) AS invoices_with_all_zero_lines
FROM _inv_dry d
WHERE NOT EXISTS (
    SELECT 1 FROM _line_dry l
    WHERE l.invoice_id = d.invoice_id AND NOT l.is_zero
);

DROP TABLE IF EXISTS _inv_dry;
DROP TABLE IF EXISTS _line_dry;
