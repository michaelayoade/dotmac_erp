#!/usr/bin/env python3
"""
Tax Transaction Backfill & Report Activation Script.

Populates the tax.tax_transaction table from existing AR/AP invoice line tax
records so that tax reports (VAT Register, Liability Summary, WHT Report)
display data.

Steps:
  1. Fix tax code GL account links (VAT-7.5, WHT-2%, SD-1%)
  2. Fix jurisdiction GL account links (NG-FED)
  3. Generate monthly tax periods (2018-01 through 2026-02)
  4. Backfill tax transactions:
     4a. OUTPUT from ar.invoice_line_tax  (~106K rows)
     4b. INPUT  from ap.supplier_invoice_line_tax (~374 rows)
     4c. Orphan AR invoice lines with tax_amount > 0 but no line_tax record

Usage:
    python scripts/backfill_tax_transactions.py --dry-run          # Report only
    python scripts/backfill_tax_transactions.py --execute           # Run all steps
    python scripts/backfill_tax_transactions.py --execute --step 3  # Run specific step
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

# ── Bootstrap ────────────────────────────────────────────────────────────
sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class StepResult:
    """Result of a single step."""

    step: int
    name: str
    dry_run: bool
    details: dict[str, object] = field(default_factory=dict)

    def log_summary(self) -> None:
        logger.info("=" * 60)
        mode = "DRY RUN" if self.dry_run else "EXECUTED"
        logger.info("Step %d: %s [%s]", self.step, self.name, mode)
        for k, v in self.details.items():
            logger.info("  %-35s %s", k, v)
        logger.info("=" * 60)


# ── Helpers ──────────────────────────────────────────────────────────────


def get_org_id(db: Session) -> UUID:
    """Get the single organization ID."""
    row = db.execute(
        text("SELECT organization_id FROM core_org.organization LIMIT 1")
    ).fetchone()
    if not row:
        raise RuntimeError("No organization found")
    return UUID(str(row[0]))


def get_account_id(db: Session, org_id: UUID, account_code: str) -> UUID | None:
    """Look up a GL account UUID by its numeric code."""
    row = db.execute(
        text("""
        SELECT account_id FROM gl.account
        WHERE organization_id = :org_id AND account_code = :code
        LIMIT 1
    """),
        {"org_id": org_id, "code": account_code},
    ).fetchone()
    return row[0] if row else None


def get_jurisdiction_id(db: Session, org_id: UUID, code: str) -> UUID | None:
    """Look up a tax jurisdiction UUID by code."""
    row = db.execute(
        text("""
        SELECT jurisdiction_id FROM tax.tax_jurisdiction
        WHERE organization_id = :org_id AND jurisdiction_code = :code
        LIMIT 1
    """),
        {"org_id": org_id, "code": code},
    ).fetchone()
    return row[0] if row else None


# ── Step 1: Fix Tax Code GL Account Links ────────────────────────────────


def step1_fix_tax_code_accounts(
    db: Session, org_id: UUID, *, dry_run: bool
) -> StepResult:
    """Update tax code GL account pointers and tax_return_box."""
    result = StepResult(step=1, name="Fix tax code GL account links", dry_run=dry_run)

    # Look up target accounts
    acct_1420 = get_account_id(db, org_id, "1420")  # WHT Receivable
    acct_1440 = get_account_id(db, org_id, "1440")  # Input VAT
    acct_2100 = get_account_id(db, org_id, "2100")  # Income Tax
    acct_6100 = get_account_id(db, org_id, "6100")  # VAT Paid (expense)

    if not all([acct_1420, acct_1440, acct_2100, acct_6100]):
        missing = []
        if not acct_1420:
            missing.append("1420")
        if not acct_1440:
            missing.append("1440")
        if not acct_2100:
            missing.append("2100")
        if not acct_6100:
            missing.append("6100")
        logger.warning("Missing GL accounts: %s", ", ".join(missing))
        result.details["warning"] = f"Missing GL accounts: {', '.join(missing)}"

    # Get existing tax codes
    codes = db.execute(
        text("""
        SELECT tax_code_id, tax_code, tax_name,
               tax_collected_account_id, tax_paid_account_id, tax_expense_account_id,
               tax_return_box
        FROM tax.tax_code
        WHERE organization_id = :org_id
        ORDER BY tax_code
    """),
        {"org_id": org_id},
    ).fetchall()

    updates: list[dict[str, object]] = []

    for row in codes:
        code_id, code, name = row[0], row[1], row[2]
        current_collected, current_paid, current_expense = row[3], row[4], row[5]
        current_box = row[6]

        logger.info("  Tax code: %s (%s)", code, name)
        logger.info(
            "    collected=%s  paid=%s  expense=%s  box=%s",
            current_collected,
            current_paid,
            current_expense,
            current_box,
        )

        if "VAT" in code.upper():
            updates.append(
                {
                    "code_id": code_id,
                    "code": code,
                    "tax_paid_account_id": acct_1440,
                    "tax_expense_account_id": acct_6100,
                    "tax_return_box": "VAT",
                }
            )
        elif "WHT" in code.upper():
            updates.append(
                {
                    "code_id": code_id,
                    "code": code,
                    "tax_paid_account_id": acct_1420,
                    "tax_expense_account_id": None,  # leave NULL for WHT
                    "tax_return_box": "WHT",
                }
            )
        elif "SD" in code.upper() or "STAMP" in code.upper():
            updates.append(
                {
                    "code_id": code_id,
                    "code": code,
                    "tax_collected_account_id": acct_2100,
                    "tax_return_box": "SD",
                }
            )

    for upd in updates:
        code_id = upd.pop("code_id")
        code = upd.pop("code")

        # Build SET clause dynamically from non-None update fields
        set_parts = []
        params: dict[str, object] = {"code_id": code_id}
        for k, v in upd.items():
            if (
                k == "tax_expense_account_id"
                and v is None
                and "WHT" in str(code).upper()
            ):
                # Explicitly set to NULL for WHT
                set_parts.append(f"{k} = NULL")
            elif v is not None:
                set_parts.append(f"{k} = :{k}")
                params[k] = v
            # Skip None values for fields we don't want to change

        if not set_parts:
            continue

        sql = f"UPDATE tax.tax_code SET {', '.join(set_parts)} WHERE tax_code_id = :code_id"
        logger.info(
            "  %s: %s → %s", "WOULD UPDATE" if dry_run else "UPDATING", code, upd
        )

        if not dry_run:
            db.execute(text(sql), params)

    result.details["tax_codes_found"] = len(codes)
    result.details["updates_applied"] = len(updates)
    result.log_summary()
    return result


# ── Step 2: Fix Jurisdiction GL Account Links ────────────────────────────


def step2_fix_jurisdiction_accounts(
    db: Session, org_id: UUID, *, dry_run: bool
) -> StepResult:
    """Update jurisdiction GL account pointers to numeric accounts."""
    result = StepResult(
        step=2, name="Fix jurisdiction GL account links", dry_run=dry_run
    )

    acct_2100 = get_account_id(db, org_id, "2100")  # Income Tax
    acct_6100 = get_account_id(db, org_id, "6100")  # VAT Paid / tax expense

    # Get existing jurisdictions
    jurisdictions = db.execute(
        text("""
        SELECT jurisdiction_id, jurisdiction_code, jurisdiction_name,
               current_tax_payable_account_id, current_tax_expense_account_id
        FROM tax.tax_jurisdiction
        WHERE organization_id = :org_id
    """),
        {"org_id": org_id},
    ).fetchall()

    updated = 0
    for row in jurisdictions:
        jur_id, jur_code, jur_name = row[0], row[1], row[2]
        current_payable, current_expense = row[3], row[4]

        logger.info("  Jurisdiction: %s (%s)", jur_code, jur_name)
        logger.info("    payable=%s  expense=%s", current_payable, current_expense)

        # Check if payable/expense accounts are pointing to inactive text-coded accounts
        needs_update = False
        params: dict[str, object] = {"jur_id": jur_id}
        set_parts = []

        if acct_2100 and current_payable != acct_2100:
            set_parts.append("current_tax_payable_account_id = :payable")
            params["payable"] = acct_2100
            needs_update = True

        if acct_6100 and current_expense != acct_6100:
            set_parts.append("current_tax_expense_account_id = :expense")
            params["expense"] = acct_6100
            needs_update = True

        if needs_update:
            sql = f"UPDATE tax.tax_jurisdiction SET {', '.join(set_parts)} WHERE jurisdiction_id = :jur_id"
            logger.info("  %s: %s", "WOULD UPDATE" if dry_run else "UPDATING", jur_code)
            if not dry_run:
                db.execute(text(sql), params)
            updated += 1

    result.details["jurisdictions_found"] = len(jurisdictions)
    result.details["updated"] = updated
    result.log_summary()
    return result


# ── Step 3: Generate Monthly Tax Periods ─────────────────────────────────


def step3_generate_tax_periods(
    db: Session, org_id: UUID, *, dry_run: bool
) -> StepResult:
    """Create tax.tax_period rows for each month from 2018-01 to 2026-02."""
    import calendar
    from datetime import date

    result = StepResult(step=3, name="Generate monthly tax periods", dry_run=dry_run)

    # Get jurisdiction
    jur_row = db.execute(
        text("""
        SELECT jurisdiction_id FROM tax.tax_jurisdiction
        WHERE organization_id = :org_id
        ORDER BY jurisdiction_code
        LIMIT 1
    """),
        {"org_id": org_id},
    ).fetchone()

    if not jur_row:
        result.details["error"] = "No jurisdiction found"
        result.log_summary()
        return result

    jurisdiction_id = jur_row[0]

    # Get all fiscal periods for date→fiscal_period_id mapping
    fiscal_periods = db.execute(
        text("""
        SELECT fiscal_period_id, start_date, end_date
        FROM gl.fiscal_period
        WHERE organization_id = :org_id
        ORDER BY start_date
    """),
        {"org_id": org_id},
    ).fetchall()

    fp_map: dict[str, UUID] = {}
    for fp in fiscal_periods:
        fp_id, fp_start, fp_end = fp[0], fp[1], fp[2]
        # Map months covered by this fiscal period
        current = fp_start.replace(day=1)
        while current <= fp_end:
            key = current.strftime("%Y-%m")
            fp_map[key] = fp_id
            # Advance to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

    # Check existing tax periods to avoid duplicates
    existing = db.execute(
        text("""
        SELECT period_name FROM tax.tax_period
        WHERE organization_id = :org_id AND jurisdiction_id = :jur_id
    """),
        {"org_id": org_id, "jur_id": jurisdiction_id},
    ).fetchall()
    existing_names = {r[0] for r in existing}

    # Generate months from 2018-01 to 2026-02
    created = 0
    skipped = 0
    start_year, start_month = 2018, 1
    end_year, end_month = 2026, 2

    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        period_name = f"{year}-{month:02d}"

        if period_name in existing_names:
            skipped += 1
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            continue

        _, last_day = calendar.monthrange(year, month)
        start_date = date(year, month, 1)
        end_date = date(year, month, last_day)

        # Due date: 21st of following month (FIRS deadline)
        if month == 12:
            due_date = date(year + 1, 1, 21)
        else:
            due_date = date(year, month + 1, 21)

        fp_id = fp_map.get(period_name)

        if not dry_run:
            db.execute(
                text("""
                INSERT INTO tax.tax_period (
                    organization_id, jurisdiction_id, fiscal_period_id,
                    period_name, frequency, start_date, end_date, due_date,
                    status, is_extension_filed
                ) VALUES (
                    :org_id, :jur_id, :fp_id,
                    :name, 'MONTHLY', :start, :end, :due, 'OPEN', false
                )
            """),
                {
                    "org_id": org_id,
                    "jur_id": jurisdiction_id,
                    "fp_id": fp_id,
                    "name": period_name,
                    "start": start_date,
                    "end": end_date,
                    "due": due_date,
                },
            )

        created += 1
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    result.details["periods_created"] = created
    result.details["periods_skipped (already exist)"] = skipped
    result.details["fiscal_period_coverage"] = f"{len(fp_map)} months mapped"
    result.log_summary()
    return result


# ── Step 4a: OUTPUT Tax Transactions (AR Sales VAT) ─────────────────────


def step4a_backfill_output_tax(
    db: Session, org_id: UUID, *, dry_run: bool
) -> StepResult:
    """Backfill OUTPUT tax transactions from ar.invoice_line_tax."""
    result = StepResult(
        step=4, name="Backfill OUTPUT tax (AR invoice_line_tax)", dry_run=dry_run
    )

    # Count source rows
    count_row = db.execute(
        text("""
        SELECT COUNT(*)
        FROM ar.invoice_line_tax ilt
        JOIN tax.tax_code tc ON tc.tax_code_id = ilt.tax_code_id
        JOIN ar.invoice_line il ON il.line_id = ilt.line_id
        JOIN ar.invoice inv ON inv.invoice_id = il.invoice_id
        WHERE inv.organization_id = :org_id
          AND inv.status NOT IN ('DRAFT', 'VOID')
          AND tc.tax_type = 'VAT'
    """),
        {"org_id": org_id},
    ).fetchone()
    source_count = count_row[0] if count_row else 0

    # Count existing rows to replace
    existing_row = db.execute(
        text("""
        SELECT COUNT(*)
        FROM tax.tax_transaction
        WHERE organization_id = :org_id
          AND transaction_type = 'OUTPUT'
          AND source_document_type = 'AR_INVOICE'
    """),
        {"org_id": org_id},
    ).fetchone()
    existing_count = existing_row[0] if existing_row else 0

    result.details["source_rows (invoice_line_tax)"] = source_count
    result.details["existing_output_transactions"] = existing_count

    if dry_run:
        result.details["would_delete"] = existing_count
        result.details["would_insert"] = source_count
        result.log_summary()
        return result

    if existing_count > 0:
        deleted = db.execute(
            text("""
        DELETE FROM tax.tax_transaction
        WHERE organization_id = :org_id
          AND transaction_type = 'OUTPUT'
          AND source_document_type = 'AR_INVOICE'
    """),
            {"org_id": org_id},
        )
        result.details["deleted"] = deleted.rowcount

    # Bulk INSERT ... SELECT
    inserted = db.execute(
        text("""
        INSERT INTO tax.tax_transaction (
            organization_id, fiscal_period_id, tax_code_id, jurisdiction_id,
            transaction_type, transaction_date,
            source_document_type, source_document_id, source_document_line_id,
            source_document_reference,
            counterparty_type, counterparty_id, counterparty_name, counterparty_tax_id,
            currency_code, base_amount, tax_rate, tax_amount,
            exchange_rate, functional_base_amount, functional_tax_amount,
            recoverable_amount, non_recoverable_amount,
            tax_return_box, is_included_in_return
        )
        SELECT
            inv.organization_id,
            fp.fiscal_period_id,
            ilt.tax_code_id,
            tc.jurisdiction_id,
            'OUTPUT',
            inv.invoice_date,
            'AR_INVOICE',
            inv.invoice_id,
            ilt.line_id,
            inv.invoice_number,
            'CUSTOMER',
            inv.customer_id,
            c.legal_name,
            c.tax_identification_number,
            inv.currency_code,
            ilt.base_amount,
            ilt.tax_rate,
            ilt.tax_amount,
            COALESCE(inv.exchange_rate, 1),
            ilt.base_amount * COALESCE(inv.exchange_rate, 1),
            ilt.tax_amount * COALESCE(inv.exchange_rate, 1),
            0,
            0,
            tc.tax_return_box,
            false
        FROM ar.invoice_line_tax ilt
        JOIN ar.invoice_line il ON il.line_id = ilt.line_id
        JOIN ar.invoice inv ON inv.invoice_id = il.invoice_id
        JOIN ar.customer c ON c.customer_id = inv.customer_id
        JOIN LATERAL (
            SELECT fiscal_period_id
            FROM gl.fiscal_period fp
            WHERE fp.organization_id = inv.organization_id
              AND inv.invoice_date BETWEEN fp.start_date AND fp.end_date
            ORDER BY fp.start_date DESC, fp.fiscal_period_id
            LIMIT 1
        ) fp ON true
        JOIN tax.tax_code tc ON tc.tax_code_id = ilt.tax_code_id
        WHERE inv.organization_id = :org_id
          AND inv.status NOT IN ('DRAFT', 'VOID')
          AND tc.tax_type = 'VAT'
    """),
        {"org_id": org_id},
    )

    result.details["rows_inserted"] = inserted.rowcount  # type: ignore[attr-defined]
    result.log_summary()
    return result


# ── Step 4b: INPUT Tax Transactions (AP Purchase VAT) ───────────────────


def step4b_backfill_input_tax(
    db: Session, org_id: UUID, *, dry_run: bool
) -> StepResult:
    """Backfill INPUT tax transactions from ap.supplier_invoice_line_tax."""
    result = StepResult(
        step=4,
        name="Backfill INPUT tax (AP supplier_invoice_line_tax)",
        dry_run=dry_run,
    )

    # Count source rows
    count_row = db.execute(
        text("""
        SELECT COUNT(*)
        FROM ap.supplier_invoice_line_tax silt
        JOIN tax.tax_code tc ON tc.tax_code_id = silt.tax_code_id
        JOIN ap.supplier_invoice_line sil ON sil.line_id = silt.line_id
        JOIN ap.supplier_invoice si ON si.invoice_id = sil.invoice_id
        WHERE si.organization_id = :org_id
          AND si.status NOT IN ('DRAFT', 'VOID')
          AND tc.tax_type = 'VAT'
    """),
        {"org_id": org_id},
    ).fetchone()
    source_count = count_row[0] if count_row else 0

    # Count existing rows to replace
    existing_row = db.execute(
        text("""
        SELECT COUNT(*)
        FROM tax.tax_transaction
        WHERE organization_id = :org_id
          AND transaction_type = 'INPUT'
          AND source_document_type = 'AP_INVOICE'
    """),
        {"org_id": org_id},
    ).fetchone()
    existing_count = existing_row[0] if existing_row else 0

    result.details["source_rows (supplier_invoice_line_tax)"] = source_count
    result.details["existing_input_transactions"] = existing_count

    if dry_run:
        result.details["would_delete"] = existing_count
        result.details["would_insert"] = source_count
        result.log_summary()
        return result

    if existing_count > 0:
        deleted = db.execute(
            text("""
        DELETE FROM tax.tax_transaction
        WHERE organization_id = :org_id
          AND transaction_type = 'INPUT'
          AND source_document_type = 'AP_INVOICE'
    """),
            {"org_id": org_id},
        )
        result.details["deleted"] = deleted.rowcount

    # Bulk INSERT ... SELECT
    inserted = db.execute(
        text("""
        INSERT INTO tax.tax_transaction (
            organization_id, fiscal_period_id, tax_code_id, jurisdiction_id,
            transaction_type, transaction_date,
            source_document_type, source_document_id, source_document_line_id,
            source_document_reference,
            counterparty_type, counterparty_id, counterparty_name, counterparty_tax_id,
            currency_code, base_amount, tax_rate, tax_amount,
            exchange_rate, functional_base_amount, functional_tax_amount,
            recoverable_amount, non_recoverable_amount,
            tax_return_box, is_included_in_return
        )
        SELECT
            si.organization_id,
            fp.fiscal_period_id,
            silt.tax_code_id,
            tc.jurisdiction_id,
            'INPUT',
            si.invoice_date,
            'AP_INVOICE',
            si.invoice_id,
            silt.line_id,
            si.invoice_number,
            'SUPPLIER',
            si.supplier_id,
            s.legal_name,
            s.tax_identification_number,
            si.currency_code,
            silt.base_amount,
            silt.tax_rate,
            silt.tax_amount,
            COALESCE(si.exchange_rate, 1),
            silt.base_amount * COALESCE(si.exchange_rate, 1),
            silt.tax_amount * COALESCE(si.exchange_rate, 1),
            silt.tax_amount * COALESCE(tc.recovery_rate, 1),
            silt.tax_amount * (1 - COALESCE(tc.recovery_rate, 1)),
            tc.tax_return_box,
            false
        FROM ap.supplier_invoice_line_tax silt
        JOIN ap.supplier_invoice_line sil ON sil.line_id = silt.line_id
        JOIN ap.supplier_invoice si ON si.invoice_id = sil.invoice_id
        JOIN ap.supplier s ON s.supplier_id = si.supplier_id
        JOIN LATERAL (
            SELECT fiscal_period_id
            FROM gl.fiscal_period fp
            WHERE fp.organization_id = si.organization_id
              AND si.invoice_date BETWEEN fp.start_date AND fp.end_date
            ORDER BY fp.start_date DESC, fp.fiscal_period_id
            LIMIT 1
        ) fp ON true
        JOIN tax.tax_code tc ON tc.tax_code_id = silt.tax_code_id
        WHERE si.organization_id = :org_id
          AND si.status NOT IN ('DRAFT', 'VOID')
          AND tc.tax_type = 'VAT'
    """),
        {"org_id": org_id},
    )

    result.details["rows_inserted"] = inserted.rowcount  # type: ignore[attr-defined]
    result.log_summary()
    return result


# ── Step 4c: Orphan AR Invoice Lines ─────────────────────────────────────


def step4c_backfill_orphan_lines(
    db: Session, org_id: UUID, *, dry_run: bool
) -> StepResult:
    """Backfill tax transactions for AR invoice lines with tax_amount > 0 but no line_tax record."""
    result = StepResult(
        step=4, name="Backfill orphan AR lines (tax but no line_tax)", dry_run=dry_run
    )

    # Count orphan lines
    count_row = db.execute(
        text("""
        SELECT COUNT(*)
        FROM ar.invoice_line il
        LEFT JOIN ar.invoice_line_tax ilt ON ilt.line_id = il.line_id
        JOIN ar.invoice inv ON inv.invoice_id = il.invoice_id
        WHERE ilt.line_tax_id IS NULL
          AND il.tax_amount > 0
          AND inv.organization_id = :org_id
          AND inv.status NOT IN ('DRAFT', 'VOID')
    """),
        {"org_id": org_id},
    ).fetchone()
    orphan_count = count_row[0] if count_row else 0

    # Count existing orphan transactions to replace
    existing_row = db.execute(
        text("""
        SELECT COUNT(*)
        FROM tax.tax_transaction
        WHERE organization_id = :org_id
          AND source_document_type = 'AR_INVOICE_ORPHAN'
    """),
        {"org_id": org_id},
    ).fetchone()
    existing_count = existing_row[0] if existing_row else 0

    result.details["orphan_lines"] = orphan_count
    result.details["existing_orphan_transactions"] = existing_count

    if dry_run:
        result.details["would_delete"] = existing_count
        result.details["would_insert"] = orphan_count
        result.log_summary()
        return result

    if existing_count > 0:
        deleted = db.execute(
            text("""
        DELETE FROM tax.tax_transaction
        WHERE organization_id = :org_id
          AND source_document_type = 'AR_INVOICE_ORPHAN'
    """),
            {"org_id": org_id},
        )
        result.details["deleted"] = deleted.rowcount

    # Get the default VAT tax code (VAT-7.5%)
    vat_code_row = db.execute(
        text("""
        SELECT tax_code_id, jurisdiction_id, tax_return_box
        FROM tax.tax_code
        WHERE organization_id = :org_id
          AND tax_code ILIKE '%VAT%'
          AND is_active = true
        ORDER BY tax_code
        LIMIT 1
    """),
        {"org_id": org_id},
    ).fetchone()

    if not vat_code_row:
        result.details["error"] = "No active VAT tax code found"
        result.log_summary()
        return result

    vat_code_id = vat_code_row[0]
    vat_jurisdiction_id = vat_code_row[1]
    vat_return_box = vat_code_row[2]

    # Bulk insert for orphan lines; derive effective tax rate from line values.
    inserted = db.execute(
        text("""
        INSERT INTO tax.tax_transaction (
            organization_id, fiscal_period_id, tax_code_id, jurisdiction_id,
            transaction_type, transaction_date,
            source_document_type, source_document_id, source_document_line_id,
            source_document_reference,
            counterparty_type, counterparty_id, counterparty_name, counterparty_tax_id,
            currency_code, base_amount, tax_rate, tax_amount,
            exchange_rate, functional_base_amount, functional_tax_amount,
            recoverable_amount, non_recoverable_amount,
            tax_return_box, is_included_in_return
        )
        SELECT
            inv.organization_id,
            fp.fiscal_period_id,
            :vat_code_id,
            :vat_jur_id,
            'OUTPUT',
            inv.invoice_date,
            'AR_INVOICE_ORPHAN',
            inv.invoice_id,
            il.line_id,
            inv.invoice_number,
            'CUSTOMER',
            inv.customer_id,
            c.legal_name,
            c.tax_identification_number,
            inv.currency_code,
            il.line_amount,
            CASE
                WHEN COALESCE(il.line_amount, 0) = 0 THEN 0
                ELSE il.tax_amount / il.line_amount
            END,
            il.tax_amount,
            COALESCE(inv.exchange_rate, 1),
            il.line_amount * COALESCE(inv.exchange_rate, 1),
            il.tax_amount * COALESCE(inv.exchange_rate, 1),
            0,
            0,
            :vat_box,
            false
        FROM ar.invoice_line il
        LEFT JOIN ar.invoice_line_tax ilt ON ilt.line_id = il.line_id
        JOIN ar.invoice inv ON inv.invoice_id = il.invoice_id
        JOIN ar.customer c ON c.customer_id = inv.customer_id
        JOIN LATERAL (
            SELECT fiscal_period_id
            FROM gl.fiscal_period fp
            WHERE fp.organization_id = inv.organization_id
              AND inv.invoice_date BETWEEN fp.start_date AND fp.end_date
            ORDER BY fp.start_date DESC, fp.fiscal_period_id
            LIMIT 1
        ) fp ON true
        WHERE ilt.line_tax_id IS NULL
          AND il.tax_amount > 0
          AND inv.organization_id = :org_id
          AND inv.status NOT IN ('DRAFT', 'VOID')
    """),
        {
            "org_id": org_id,
            "vat_code_id": vat_code_id,
            "vat_jur_id": vat_jurisdiction_id,
            "vat_box": vat_return_box,
        },
    )

    result.details["rows_inserted"] = inserted.rowcount  # type: ignore[attr-defined]
    result.log_summary()
    return result


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill tax transactions from AR/AP data"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run", action="store_true", help="Report what would be done"
    )
    group.add_argument(
        "--execute", action="store_true", help="Actually run the backfill"
    )
    parser.add_argument("--step", type=int, help="Run only a specific step (1-4)")
    args = parser.parse_args()

    dry_run = args.dry_run

    with SessionLocal() as db:
        org_id = get_org_id(db)
        logger.info("Organization: %s", org_id)
        logger.info("Mode: %s", "DRY RUN" if dry_run else "EXECUTE")
        logger.info("")

        results: list[StepResult] = []

        if args.step is None or args.step == 1:
            results.append(step1_fix_tax_code_accounts(db, org_id, dry_run=dry_run))

        if args.step is None or args.step == 2:
            results.append(step2_fix_jurisdiction_accounts(db, org_id, dry_run=dry_run))

        if args.step is None or args.step == 3:
            results.append(step3_generate_tax_periods(db, org_id, dry_run=dry_run))

        if args.step is None or args.step == 4:
            results.append(step4a_backfill_output_tax(db, org_id, dry_run=dry_run))
            results.append(step4b_backfill_input_tax(db, org_id, dry_run=dry_run))
            results.append(step4c_backfill_orphan_lines(db, org_id, dry_run=dry_run))

        if not dry_run:
            db.commit()
            logger.info("All changes committed.")
        else:
            logger.info("DRY RUN complete — no changes made.")

        # Summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("SUMMARY")
        logger.info("=" * 60)
        for r in results:
            logger.info("  Step %d: %s", r.step, r.name)
            for k, v in r.details.items():
                logger.info("    %-35s %s", k, v)


if __name__ == "__main__":
    main()
