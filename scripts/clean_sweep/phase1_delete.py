"""
Phase 1: Delete all 2025 financial data from DotMac.

Performs raw SQL DELETEs in child-first order, creating an audit table
with per-table row counts before deletion.

GL tables are already empty (from prior migrations) but included for
idempotency. Source documents, banking, and financial sync_entity rows
are deleted. Non-financial data (customers, suppliers, bank accounts,
GL accounts, fiscal periods, numbering sequences) is preserved.

Usage:
    docker exec dotmac_erp_app python -m scripts.clean_sweep.phase1_delete
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from scripts.clean_sweep.config import DATE_END, DATE_START, ORG_ID, setup_logging

logger = setup_logging("phase1_delete")

# Ordered list of (table_name, WHERE clause fragment, description)
# Child tables must come before parents within each module group.
# NOTE: journal_entry is referenced by AR, AP, and Expense tables via
# journal_entry_id FK, so it must be deleted AFTER all source documents.
DELETE_STEPS: list[tuple[str, str, str]] = [
    # ── GL children (no inbound FKs from source docs) ─────────────
    (
        "gl.posted_ledger_line",
        "organization_id = :org_id",
        "Posted ledger lines",
    ),
    (
        "gl.journal_entry_line",
        """journal_entry_id IN (
            SELECT journal_entry_id FROM gl.journal_entry
            WHERE organization_id = :org_id
        )""",
        "Journal entry lines",
    ),
    (
        "gl.posting_batch",
        "organization_id = :org_id",
        "Posting batches",
    ),
    # ── AR ─────────────────────────────────────────────────────────
    (
        "ar.payment_allocation",
        """payment_id IN (
            SELECT payment_id FROM ar.customer_payment
            WHERE organization_id = :org_id
        )""",
        "AR payment allocations",
    ),
    (
        "ar.invoice_line_tax",
        """line_id IN (
            SELECT il.line_id FROM ar.invoice_line il
            JOIN ar.invoice i ON i.invoice_id = il.invoice_id
            WHERE i.organization_id = :org_id
        )""",
        "AR invoice line taxes",
    ),
    (
        "ar.invoice_line",
        """invoice_id IN (
            SELECT invoice_id FROM ar.invoice
            WHERE organization_id = :org_id
        )""",
        "AR invoice lines",
    ),
    (
        "ar.external_sync",
        "organization_id = :org_id",
        "AR external sync",
    ),
    (
        "ar.customer_payment",
        "organization_id = :org_id",
        "AR customer payments",
    ),
    (
        "ar.invoice",
        "organization_id = :org_id",
        "AR invoices",
    ),
    # ── AP ─────────────────────────────────────────────────────────
    (
        "ap.payment_allocation",
        """payment_id IN (
            SELECT payment_id FROM ap.supplier_payment
            WHERE organization_id = :org_id
        )""",
        "AP payment allocations",
    ),
    (
        "ap.supplier_invoice_line_tax",
        """line_id IN (
            SELECT sil.line_id FROM ap.supplier_invoice_line sil
            JOIN ap.supplier_invoice si ON si.invoice_id = sil.invoice_id
            WHERE si.organization_id = :org_id
        )""",
        "AP invoice line taxes",
    ),
    (
        "ap.supplier_invoice_line",
        """invoice_id IN (
            SELECT invoice_id FROM ap.supplier_invoice
            WHERE organization_id = :org_id
        )""",
        "AP invoice lines",
    ),
    (
        "ap.supplier_payment",
        "organization_id = :org_id",
        "AP supplier payments",
    ),
    (
        "ap.supplier_invoice",
        "organization_id = :org_id",
        "AP supplier invoices",
    ),
    # ── Expense ────────────────────────────────────────────────────
    (
        "expense.expense_claim_action",
        """claim_id IN (
            SELECT claim_id FROM expense.expense_claim
            WHERE organization_id = :org_id
        )""",
        "Expense claim actions",
    ),
    (
        "expense.expense_period_usage",
        "organization_id = :org_id",
        "Expense period usage",
    ),
    (
        "expense.expense_claim_item",
        """claim_id IN (
            SELECT claim_id FROM expense.expense_claim
            WHERE organization_id = :org_id
        )""",
        "Expense claim items",
    ),
    (
        "expense.expense_claim",
        "organization_id = :org_id",
        "Expense claims",
    ),
    # ── GL parents (after NULL-out step below) ──────────────────
    (
        "gl.journal_entry",
        "organization_id = :org_id",
        "Journal entries",
    ),
    (
        "gl.account_balance",
        "organization_id = :org_id",
        "Account balances (will rebuild in Phase 6)",
    ),
    (
        "gl.balance_refresh_queue",
        "organization_id = :org_id",
        "Balance refresh queue",
    ),
    # ── Banking ────────────────────────────────────────────────────
    (
        "banking.bank_statement_line_matches",
        """statement_line_id IN (
            SELECT bsl.line_id FROM banking.bank_statement_lines bsl
            JOIN banking.bank_statements bs ON bs.statement_id = bsl.statement_id
            WHERE bs.organization_id = :org_id
        )""",
        "Bank statement line matches",
    ),
    (
        "banking.bank_statement_lines",
        """statement_id IN (
            SELECT statement_id FROM banking.bank_statements
            WHERE organization_id = :org_id
        )""",
        "Bank statement lines",
    ),
    (
        "banking.bank_statements",
        "organization_id = :org_id",
        "Bank statements",
    ),
    # ── Sync (financial doctypes only) ─────────────────────────────
    (
        "sync.sync_entity",
        """organization_id = :org_id AND source_doctype IN (
            'Sales Invoice', 'Payment Entry', 'Journal Entry',
            'Purchase Invoice', 'Expense Claim',
            'Bank Transaction', 'Bank Transaction Payments'
        )""",
        "Sync entities (financial doctypes)",
    ),
]


def _create_audit_table(db: Session) -> None:
    """Create the audit table to record pre-deletion row counts."""
    db.execute(
        text("""
        CREATE TABLE IF NOT EXISTS _clean_sweep_2025_audit (
            table_name TEXT NOT NULL,
            description TEXT,
            row_count_before INTEGER NOT NULL DEFAULT 0,
            rows_deleted INTEGER NOT NULL DEFAULT 0,
            deleted_at TIMESTAMPTZ DEFAULT NOW(),
            phase TEXT DEFAULT 'phase1_delete'
        )
    """)
    )
    db.commit()


def _count_rows(db: Session, table: str, where: str) -> int:
    """Count rows matching the WHERE clause."""
    sql = f"SELECT COUNT(*) FROM {table} WHERE {where}"  # noqa: S608
    result = db.execute(text(sql), {"org_id": str(ORG_ID)})
    return result.scalar() or 0


def _delete_rows(db: Session, table: str, where: str) -> int:
    """Delete rows matching the WHERE clause, return count deleted."""
    sql = f"DELETE FROM {table} WHERE {where}"  # noqa: S608
    result = db.execute(text(sql), {"org_id": str(ORG_ID)})
    return result.rowcount or 0


NULLIFY_STEPS: list[tuple[str, str, str]] = [
    (
        "payroll.salary_slip",
        "organization_id = :org_id AND journal_entry_id IS NOT NULL",
        "Salary slip journal refs",
    ),
    (
        "payroll.payroll_entry",
        "organization_id = :org_id AND journal_entry_id IS NOT NULL",
        "Payroll entry journal refs",
    ),
    (
        "expense.cash_advance",
        "organization_id = :org_id AND journal_entry_id IS NOT NULL",
        "Cash advance journal refs",
    ),
    (
        "exp.expense_entry",
        """journal_entry_id IN (
            SELECT journal_entry_id FROM gl.journal_entry
            WHERE organization_id = :org_id
        )""",
        "Expense entry journal refs",
    ),
]


def _nullify_journal_refs(db: Session) -> None:
    """NULL out journal_entry_id on preserved tables before deleting journals."""
    logger.info("  Clearing journal_entry_id references on preserved tables...")
    for table, where, desc in NULLIFY_STEPS:
        sql = f"UPDATE {table} SET journal_entry_id = NULL WHERE {where}"  # noqa: S608
        result = db.execute(text(sql), {"org_id": str(ORG_ID)})
        count = result.rowcount or 0
        if count > 0:
            logger.info("    %-40s %d rows nullified", desc, count)
    db.commit()


def main() -> None:
    from app.db import SessionLocal

    logger.info("=" * 60)
    logger.info("Phase 1: Delete 2025 financial data from DotMac")
    logger.info("  Org: %s", ORG_ID)
    logger.info("  Date range: %s to %s (exclusive)", DATE_START, DATE_END)
    logger.info("=" * 60)

    with SessionLocal() as db:
        _create_audit_table(db)

        # NULL out journal_entry_id FKs in tables we're NOT deleting
        # (payroll, cash_advance, expense_entry) so journal_entry can
        # be deleted without FK violations.
        _nullify_journal_refs(db)

        total_deleted = 0

        for table, where_clause, description in DELETE_STEPS:
            count_before = _count_rows(db, table, where_clause)

            if count_before == 0:
                logger.info("  %-45s %8d rows (skip)", description, 0)
                continue

            rows_deleted = _delete_rows(db, table, where_clause)

            # Record in audit table
            db.execute(
                text("""
                    INSERT INTO _clean_sweep_2025_audit
                        (table_name, description, row_count_before, rows_deleted, deleted_at, phase)
                    VALUES (:table, :desc, :before, :deleted, :now, 'phase1_delete')
                """),
                {
                    "table": table,
                    "desc": description,
                    "before": count_before,
                    "deleted": rows_deleted,
                    "now": datetime.now(tz=UTC),
                },
            )
            db.commit()

            total_deleted += rows_deleted
            logger.info(
                "  %-45s %8d → %8d deleted",
                description,
                count_before,
                rows_deleted,
            )

        logger.info("-" * 60)
        logger.info("Phase 1 complete. Total rows deleted: %d", total_deleted)
        logger.info("Audit table: _clean_sweep_2025_audit")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Phase 1 failed")
        sys.exit(1)
