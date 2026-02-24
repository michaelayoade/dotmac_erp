"""
Diagnostic script: Find bank statement line matches linked to non-AP journal entries.

Problem: Bank statement lines have been matched to standalone journal entries
(not sourced from AP supplier payments). This script identifies the scope of
the issue and provides breakdowns for remediation planning.

Tables used:
  - banking.bank_statement_line_matches (match junction)
  - banking.bank_statement_lines (statement line details)
  - gl.journal_entry_line (join path to journal)
  - gl.journal_entry (source_module, source_document_type)
  - ap.supplier_invoice (overdue invoices)
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row


def get_database_url() -> str:
    """Resolve DATABASE_URL from .env or environment."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        # Read from .env file
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DATABASE_URL="):
                        url = line.split("=", 1)[1].strip()
                        break
    if not url:
        url = "postgresql://postgres:postgres@localhost:5437/dotmac_erp"

    # Strip SQLAlchemy dialect prefix: postgresql+psycopg:// -> postgresql://
    if "+psycopg" in url:
        url = url.replace("+psycopg", "")
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "")
    # Also handle postgres:// -> postgresql://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    return url


def fmt_amount(val: Decimal | None) -> str:
    """Format a decimal amount with commas."""
    if val is None:
        return "—"
    return f"{val:>16,.2f}"


def run_diagnostic() -> None:
    db_url = get_database_url()
    # Print host/db only (hide credentials)
    at_idx = db_url.find("@")
    display_url = db_url[at_idx + 1 :] if at_idx != -1 else db_url
    print(f"Connecting to: {display_url}")
    print("=" * 100)

    conn = psycopg.connect(db_url, row_factory=dict_row)
    cur = conn.cursor()

    # ──────────────────────────────────────────────────────────────────────
    # 1. Total bank statement line matches
    # ──────────────────────────────────────────────────────────────────────
    cur.execute("SELECT count(*) AS cnt FROM banking.bank_statement_line_matches")
    total_matches: int = cur.fetchone()["cnt"]
    print(f"\n1. TOTAL BANK STATEMENT LINE MATCHES: {total_matches:,}")

    # ──────────────────────────────────────────────────────────────────────
    # 2. Matches to AP-sourced journal entries (correct matches)
    # ──────────────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT count(*) AS cnt
        FROM banking.bank_statement_line_matches m
        JOIN gl.journal_entry_line jel ON jel.line_id = m.journal_line_id
        JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
        WHERE je.source_module = 'AP'
          AND je.source_document_type = 'SUPPLIER_PAYMENT'
    """)
    ap_matches: int = cur.fetchone()["cnt"]
    print(f"\n2. MATCHES TO AP-SOURCED JOURNAL ENTRIES (correct): {ap_matches:,}")
    print("   (source_module='AP', source_document_type='SUPPLIER_PAYMENT')")

    # ──────────────────────────────────────────────────────────────────────
    # 3. Matches to non-AP journal entries (problematic)
    # ──────────────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT count(*) AS cnt
        FROM banking.bank_statement_line_matches m
        JOIN gl.journal_entry_line jel ON jel.line_id = m.journal_line_id
        JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
        WHERE je.source_module IS DISTINCT FROM 'AP'
           OR je.source_document_type IS DISTINCT FROM 'SUPPLIER_PAYMENT'
    """)
    non_ap_matches: int = cur.fetchone()["cnt"]
    print(f"\n3. MATCHES TO NON-AP JOURNAL ENTRIES (problematic): {non_ap_matches:,}")

    # Also count those with NULL source_module specifically
    cur.execute("""
        SELECT count(*) AS cnt
        FROM banking.bank_statement_line_matches m
        JOIN gl.journal_entry_line jel ON jel.line_id = m.journal_line_id
        JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
        WHERE je.source_module IS NULL
    """)
    null_source_matches: int = cur.fetchone()["cnt"]
    print(
        f"   Of which source_module IS NULL (standalone JEs): {null_source_matches:,}"
    )

    # Count orphan matches (journal_line_id references no existing JEL)
    cur.execute("""
        SELECT count(*) AS cnt
        FROM banking.bank_statement_line_matches m
        LEFT JOIN gl.journal_entry_line jel ON jel.line_id = m.journal_line_id
        WHERE jel.line_id IS NULL
    """)
    orphan_matches: int = cur.fetchone()["cnt"]
    if orphan_matches > 0:
        print(
            f"   WARNING: {orphan_matches:,} matches reference non-existent journal lines!"
        )

    # ──────────────────────────────────────────────────────────────────────
    # 4. Sample of 20 non-AP matches with details
    # ──────────────────────────────────────────────────────────────────────
    print("\n4. SAMPLE OF NON-AP MATCHES (up to 20):")
    print("-" * 100)

    cur.execute("""
        SELECT
            bsl.description      AS line_description,
            bsl.amount           AS line_amount,
            bsl.transaction_type AS line_type,
            bsl.transaction_date AS line_date,
            bsl.reference        AS line_reference,
            je.journal_number,
            je.description       AS je_description,
            je.journal_type,
            je.source_module,
            je.source_document_type,
            je.status            AS je_status,
            je.total_debit       AS je_total_debit,
            m.match_type,
            m.matched_at
        FROM banking.bank_statement_line_matches m
        JOIN banking.bank_statement_lines bsl ON bsl.line_id = m.statement_line_id
        JOIN gl.journal_entry_line jel ON jel.line_id = m.journal_line_id
        JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
        WHERE je.source_module IS DISTINCT FROM 'AP'
           OR je.source_document_type IS DISTINCT FROM 'SUPPLIER_PAYMENT'
        ORDER BY bsl.transaction_date DESC, bsl.amount DESC
        LIMIT 20
    """)
    rows = cur.fetchall()

    if not rows:
        print("  (no non-AP matches found)")
    else:
        for i, r in enumerate(rows, 1):
            print(
                f"\n  [{i:>2}] Bank Line: {r['line_date']}  {r['line_type']}  "
                f"{fmt_amount(r['line_amount'])}  ref={r['line_reference'] or '—'}"
            )
            desc = (r["line_description"] or "—")[:80]
            print(f"       Description: {desc}")
            print(
                f"       Journal: {r['journal_number']}  type={r['journal_type']}  "
                f"status={r['je_status']}  debit={fmt_amount(r['je_total_debit'])}"
            )
            je_desc = (r["je_description"] or "—")[:80]
            print(f"       JE Desc: {je_desc}")
            print(
                f"       Source: module={r['source_module'] or 'NULL'}  "
                f"doc_type={r['source_document_type'] or 'NULL'}  "
                f"match_type={r['match_type'] or '—'}"
            )

    # ──────────────────────────────────────────────────────────────────────
    # 5. Breakdown by journal_type for non-AP matches
    # ──────────────────────────────────────────────────────────────────────
    print("\n\n5. NON-AP MATCHES BY JOURNAL TYPE:")
    print("-" * 60)

    cur.execute("""
        SELECT
            je.journal_type,
            count(*) AS cnt,
            sum(bsl.amount) AS total_amount
        FROM banking.bank_statement_line_matches m
        JOIN banking.bank_statement_lines bsl ON bsl.line_id = m.statement_line_id
        JOIN gl.journal_entry_line jel ON jel.line_id = m.journal_line_id
        JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
        WHERE je.source_module IS DISTINCT FROM 'AP'
           OR je.source_document_type IS DISTINCT FROM 'SUPPLIER_PAYMENT'
        GROUP BY je.journal_type
        ORDER BY cnt DESC
    """)
    rows = cur.fetchall()

    print(f"  {'Journal Type':<25} {'Count':>8} {'Total Amount':>18}")
    print(f"  {'─' * 25} {'─' * 8} {'─' * 18}")
    for r in rows:
        print(
            f"  {str(r['journal_type']):<25} {r['cnt']:>8,} {fmt_amount(r['total_amount'])}"
        )

    # ──────────────────────────────────────────────────────────────────────
    # 6. Breakdown by source_module for ALL matches
    # ──────────────────────────────────────────────────────────────────────
    print("\n\n6. ALL MATCHES BY SOURCE_MODULE:")
    print("-" * 60)

    cur.execute("""
        SELECT
            coalesce(je.source_module, '(NULL)') AS source_module,
            coalesce(je.source_document_type, '(NULL)') AS source_document_type,
            count(*) AS cnt,
            sum(bsl.amount) AS total_amount
        FROM banking.bank_statement_line_matches m
        JOIN banking.bank_statement_lines bsl ON bsl.line_id = m.statement_line_id
        LEFT JOIN gl.journal_entry_line jel ON jel.line_id = m.journal_line_id
        LEFT JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
        GROUP BY coalesce(je.source_module, '(NULL)'),
                 coalesce(je.source_document_type, '(NULL)')
        ORDER BY cnt DESC
    """)
    rows = cur.fetchall()

    print(
        f"  {'Source Module':<15} {'Source Doc Type':<25} {'Count':>8} {'Total Amount':>18}"
    )
    print(f"  {'─' * 15} {'─' * 25} {'─' * 8} {'─' * 18}")
    for r in rows:
        print(
            f"  {r['source_module']:<15} {r['source_document_type']:<25} "
            f"{r['cnt']:>8,} {fmt_amount(r['total_amount'])}"
        )

    # ──────────────────────────────────────────────────────────────────────
    # 7. Overdue supplier invoices (POSTED or PARTIALLY_PAID, due_date < today)
    # ──────────────────────────────────────────────────────────────────────
    today = date.today()
    print(f"\n\n7. OVERDUE SUPPLIER INVOICES (due_date < {today}):")
    print("-" * 60)

    cur.execute(
        """
        SELECT
            count(*) AS cnt,
            sum(si.total_amount) AS total_invoice_amount,
            sum(si.amount_paid) AS total_paid,
            sum(si.total_amount - si.amount_paid) AS total_balance_due
        FROM ap.supplier_invoice si
        WHERE si.status IN ('POSTED', 'PARTIALLY_PAID')
          AND si.due_date < %(today)s
    """,
        {"today": today},
    )
    r = cur.fetchone()
    print(f"  Total overdue invoices:  {r['cnt']:>8,}")
    print(f"  Total invoice amount:    {fmt_amount(r['total_invoice_amount'])}")
    print(f"  Total amount paid:       {fmt_amount(r['total_paid'])}")
    print(f"  Total balance due:       {fmt_amount(r['total_balance_due'])}")

    # Breakdown by age bucket
    cur.execute(
        """
        SELECT
            CASE
                WHEN (%(today)s - si.due_date) <= 30 THEN '1-30 days'
                WHEN (%(today)s - si.due_date) <= 60 THEN '31-60 days'
                WHEN (%(today)s - si.due_date) <= 90 THEN '61-90 days'
                ELSE '90+ days'
            END AS aging_bucket,
            count(*) AS cnt,
            sum(si.total_amount - si.amount_paid) AS balance_due
        FROM ap.supplier_invoice si
        WHERE si.status IN ('POSTED', 'PARTIALLY_PAID')
          AND si.due_date < %(today)s
        GROUP BY 1
        ORDER BY 1
    """,
        {"today": today},
    )
    rows = cur.fetchall()

    if rows:
        print(f"\n  {'Aging Bucket':<15} {'Count':>8} {'Balance Due':>18}")
        print(f"  {'─' * 15} {'─' * 8} {'─' * 18}")
        for r in rows:
            print(
                f"  {r['aging_bucket']:<15} {r['cnt']:>8,} {fmt_amount(r['balance_due'])}"
            )

    # ──────────────────────────────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"  Total matches:          {total_matches:>8,}")
    if total_matches > 0:
        print(
            f"  Correct (AP sourced):   {ap_matches:>8,}  "
            f"({ap_matches / total_matches * 100:.1f}%)"
        )
        print(
            f"  Problematic (non-AP):   {non_ap_matches:>8,}  "
            f"({non_ap_matches / total_matches * 100:.1f}%)"
        )
    else:
        print(f"  Correct (AP sourced):   {ap_matches:>8,}")
        print(f"  Problematic (non-AP):   {non_ap_matches:>8,}")
    if orphan_matches > 0:
        print(f"  Orphan (no JEL):        {orphan_matches:>8,}")
    print(f"  Standalone (NULL src):  {null_source_matches:>8,}")
    print("=" * 100)

    cur.close()
    conn.close()


if __name__ == "__main__":
    run_diagnostic()
