"""
Post January 2026 DRAFT AP Invoices, Create VAT/WHT Returns, Analyze Overlap.

During January 2026 bank reconciliation, 437 simple 2-line journals were created
(DR Expense Payable / CR Bank) to match statement lines. These bypass the AP
invoice workflow and don't generate tax transactions. Meanwhile, 70 AP invoices
exist in DRAFT status with proper line items and tax codes (NGN 46.4M with
NGN 2.6M tax). Posting them generates INPUT VAT and WHT tax transactions needed
for accurate VAT return filing.

Tasks:
  1. Post 70 DRAFT AP invoices through DRAFT → SUBMITTED → APPROVED → POSTED
  2. Create VAT and WHT returns in PREPARED status for Jan 2026
  3. Analyze overlap between bank reconciliation journals and AP invoices

Usage:
    python scripts/post_jan_2026_ap_invoices.py --dry-run
    python scripts/post_jan_2026_ap_invoices.py --execute
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from decimal import Decimal
from uuid import UUID

sys.path.insert(0, ".")

from sqlalchemy import select, text  # noqa: E402

from app.db import SessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("post_jan_2026_ap")

# ── Constants ───────────────────────────────────────────────────────────────

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")

# SoD: different users for submit vs approve/post
SUBMIT_USER_ID = UUID("c8e5f2ee-4f9f-46d0-a6c7-22e4f717a58b")  # admin
APPROVE_USER_ID = UUID("4df36c5f-4a4e-4606-8268-e20d0177d22c")  # m.david

# Tax period identifiers
TAX_PERIOD_ID = UUID("a8be94eb-cb9e-407a-89ce-95693261cdd7")  # tax.tax_period 2026-01
FISCAL_PERIOD_ID = UUID(
    "09c14950-61e4-4504-88d7-ba1ec8f8cc88"
)  # gl.fiscal_period Jan 2026
JURISDICTION_ID = UUID("1bbcd230-c651-4c82-b7fc-f72e272b5fab")  # Nigeria Federal

JAN_2026_START = date(2026, 1, 1)
JAN_2026_END = date(2026, 1, 31)


# ── Task 1: Post DRAFT AP Invoices ─────────────────────────────────────────


def post_draft_invoices(*, execute: bool) -> dict[str, int | list[str]]:
    """Post all DRAFT AP invoices dated January 2026.

    Returns:
        Dict with submitted/approved/posted counts and error list.
    """
    from app.models.finance.ap.supplier_invoice import (
        SupplierInvoice,
        SupplierInvoiceStatus,
    )

    results: dict[str, int | list[str]] = {
        "found": 0,
        "submitted": 0,
        "approved": 0,
        "posted": 0,
        "errors": [],
    }
    errors: list[str] = []

    with SessionLocal() as db:
        # Find DRAFT AP invoices for January 2026
        stmt = (
            select(SupplierInvoice)
            .where(
                SupplierInvoice.organization_id == ORG_ID,
                SupplierInvoice.status == SupplierInvoiceStatus.DRAFT,
                SupplierInvoice.invoice_date >= JAN_2026_START,
                SupplierInvoice.invoice_date <= JAN_2026_END,
            )
            .order_by(SupplierInvoice.invoice_date, SupplierInvoice.invoice_number)
        )
        invoices = list(db.scalars(stmt).all())
        results["found"] = len(invoices)

        logger.info("=" * 60)
        logger.info("TASK 1: Post DRAFT AP Invoices (January 2026)")
        logger.info("=" * 60)
        logger.info("  Found %d DRAFT invoices", len(invoices))

        total_amount = sum(inv.total_amount for inv in invoices)
        total_tax = sum(inv.tax_amount for inv in invoices)
        logger.info("  Total amount:  NGN %s", f"{total_amount:,.2f}")
        logger.info("  Total tax:     NGN %s", f"{total_tax:,.2f}")
        logger.info("")

        if not execute:
            # Dry-run: list first 10 invoices as preview
            for inv in invoices[:10]:
                logger.info(
                    "    %s  %s  NGN %s  tax=%s",
                    inv.invoice_number,
                    inv.invoice_date,
                    f"{inv.total_amount:,.2f}",
                    f"{inv.tax_amount:,.2f}",
                )
            if len(invoices) > 10:
                logger.info("    ... and %d more", len(invoices) - 10)
            results["errors"] = errors
            return results

        # Execute: import services lazily to avoid circular imports
        from app.services.finance.ap.supplier_invoice import SupplierInvoiceService

        submitted = 0
        approved = 0
        posted = 0

        for i, inv in enumerate(invoices, 1):
            inv_num = inv.invoice_number
            inv_id = inv.invoice_id

            try:
                # Step 1: DRAFT → SUBMITTED
                SupplierInvoiceService.submit_invoice(
                    db=db,
                    organization_id=ORG_ID,
                    invoice_id=inv_id,
                    submitted_by_user_id=SUBMIT_USER_ID,
                )
                submitted += 1

                # Step 2: SUBMITTED → APPROVED (different user for SoD)
                SupplierInvoiceService.approve_invoice(
                    db=db,
                    organization_id=ORG_ID,
                    invoice_id=inv_id,
                    approved_by_user_id=APPROVE_USER_ID,
                )
                approved += 1

                # Step 3: APPROVED → POSTED (creates GL journals + tax txns)
                SupplierInvoiceService.post_invoice(
                    db=db,
                    organization_id=ORG_ID,
                    invoice_id=inv_id,
                    posted_by_user_id=APPROVE_USER_ID,
                )
                posted += 1

                if i % 10 == 0:
                    logger.info("  Processed %d / %d ...", i, len(invoices))

            except Exception as e:
                msg = f"{inv_num}: {e}"
                logger.warning("  FAILED %s", msg)
                errors.append(msg)

        results["submitted"] = submitted
        results["approved"] = approved
        results["posted"] = posted
        results["errors"] = errors

        logger.info("")
        logger.info("  RESULTS:")
        logger.info("    Submitted:  %d", submitted)
        logger.info("    Approved:   %d", approved)
        logger.info("    Posted:     %d", posted)
        logger.info("    Errors:     %d", len(errors))
        if errors:
            for err in errors[:20]:
                logger.info("      %s", err)

    return results


# ── Task 2: Create VAT and WHT Returns ─────────────────────────────────────


def create_tax_returns(*, execute: bool) -> None:
    """Create VAT and WHT returns in PREPARED status for January 2026."""
    from app.models.finance.tax.tax_return import TaxReturnType
    from app.services.finance.tax.tax_return import TaxReturnInput, TaxReturnService
    from app.services.finance.tax.tax_transaction import TaxTransactionService

    logger.info("")
    logger.info("=" * 60)
    logger.info("TASK 2: Create Tax Returns (January 2026)")
    logger.info("=" * 60)

    with SessionLocal() as db:
        # Show current tax transaction summary
        # Note: get_return_summary uses gl.fiscal_period_id, not tax.period_id
        summary = TaxTransactionService.get_return_summary(
            db=db,
            organization_id=ORG_ID,
            fiscal_period_id=FISCAL_PERIOD_ID,
        )
        logger.info("  Current tax transaction summary:")
        logger.info(
            "    Output tax:                 NGN %s", f"{summary.output_tax:,.2f}"
        )
        logger.info(
            "    Input tax (recoverable):    NGN %s",
            f"{summary.input_tax_recoverable:,.2f}",
        )
        logger.info(
            "    Input tax (non-recoverable): NGN %s",
            f"{summary.input_tax_non_recoverable:,.2f}",
        )
        logger.info(
            "    Withholding tax:            NGN %s", f"{summary.withholding_tax:,.2f}"
        )
        logger.info(
            "    Net payable:                NGN %s", f"{summary.net_payable:,.2f}"
        )
        logger.info("    Transaction count:          %d", summary.transaction_count)
        logger.info("")

        if not execute:
            logger.info("  DRY RUN — would create VAT + WHT returns in PREPARED status")
            return

        # Create VAT return
        vat_input = TaxReturnInput(
            tax_period_id=TAX_PERIOD_ID,
            jurisdiction_id=JURISDICTION_ID,
            return_type=TaxReturnType.VAT,
            adjustments=Decimal("0"),
        )
        try:
            vat_return = TaxReturnService.prepare_return(
                db=db,
                organization_id=ORG_ID,
                input=vat_input,
                prepared_by_user_id=SUBMIT_USER_ID,
            )
            logger.info("  VAT Return created:")
            logger.info("    Return ID:      %s", vat_return.return_id)
            logger.info("    Status:         %s", vat_return.status.value)
            logger.info(
                "    Output tax:     NGN %s", f"{vat_return.total_output_tax:,.2f}"
            )
            logger.info(
                "    Input tax:      NGN %s", f"{vat_return.total_input_tax:,.2f}"
            )
            logger.info(
                "    Net payable:    NGN %s", f"{vat_return.net_tax_payable:,.2f}"
            )
            logger.info("    Final amount:   NGN %s", f"{vat_return.final_amount:,.2f}")
        except Exception as e:
            logger.exception("  FAILED to create VAT return: %s", e)

        # Create WHT return
        wht_input = TaxReturnInput(
            tax_period_id=TAX_PERIOD_ID,
            jurisdiction_id=JURISDICTION_ID,
            return_type=TaxReturnType.WITHHOLDING,
            adjustments=Decimal("0"),
        )
        try:
            wht_return = TaxReturnService.prepare_return(
                db=db,
                organization_id=ORG_ID,
                input=wht_input,
                prepared_by_user_id=SUBMIT_USER_ID,
            )
            logger.info("")
            logger.info("  WHT Return created:")
            logger.info("    Return ID:      %s", wht_return.return_id)
            logger.info("    Status:         %s", wht_return.status.value)
            logger.info(
                "    Net payable:    NGN %s", f"{wht_return.net_tax_payable:,.2f}"
            )
            logger.info("    Final amount:   NGN %s", f"{wht_return.final_amount:,.2f}")
        except Exception as e:
            logger.exception("  FAILED to create WHT return: %s", e)


# ── Task 3: Overlap Analysis ───────────────────────────────────────────────


def analyze_overlap() -> None:
    """Analyze overlap between bank reconciliation journals and AP invoices.

    Matches the 437 simple 2-line bank reconciliation journals
    (DR Expense Payable / CR Bank) against the 70 AP invoices by amount
    and date proximity, identifying which journals are "backed" by proper
    AP invoices with tax treatment.
    """
    logger.info("")
    logger.info("=" * 60)
    logger.info("TASK 3: Overlap Analysis — Bank Journals vs AP Invoices")
    logger.info("=" * 60)

    with SessionLocal() as db:
        # Count reconciliation journals for Jan 2026
        # These are 2-line journals (DR expense payable / CR bank)
        recon_count = db.execute(
            text("""
                SELECT count(*)
                FROM gl.journal_entry je
                WHERE je.organization_id = :org_id
                  AND je.entry_date >= :start_date
                  AND je.entry_date <= :end_date
                  AND je.source_module = 'BANKING'
                  AND je.source_document_type = 'BANK_RECONCILIATION'
                  AND je.status = 'POSTED'
            """),
            {
                "org_id": str(ORG_ID),
                "start_date": JAN_2026_START,
                "end_date": JAN_2026_END,
            },
        ).scalar()
        logger.info("  Bank reconciliation journals (Jan 2026): %s", recon_count)

        # Count AP invoices for Jan 2026 (now POSTED after Task 1)
        ap_count = db.execute(
            text("""
                SELECT count(*), coalesce(sum(total_amount), 0)
                FROM ap.supplier_invoice si
                WHERE si.organization_id = :org_id
                  AND si.invoice_date >= :start_date
                  AND si.invoice_date <= :end_date
                  AND si.status IN ('POSTED', 'PARTIALLY_PAID', 'PAID')
            """),
            {
                "org_id": str(ORG_ID),
                "start_date": JAN_2026_START,
                "end_date": JAN_2026_END,
            },
        ).one()
        logger.info(
            "  Posted AP invoices (Jan 2026): %s  total: NGN %s",
            ap_count[0],
            f"{ap_count[1]:,.2f}",
        )

        # Overlap: match bank journal debit amounts to AP invoice totals
        # A match = same amount (within 0.01) and date within 7 days
        overlap_rows = db.execute(
            text("""
                WITH recon_journals AS (
                    -- Get the debit amount from each 2-line bank recon journal
                    SELECT
                        je.journal_entry_id,
                        je.entry_date,
                        je.description,
                        je.reference,
                        jl.debit_amount AS amount
                    FROM gl.journal_entry je
                    JOIN gl.journal_entry_line jl
                      ON jl.journal_entry_id = je.journal_entry_id
                    WHERE je.organization_id = :org_id
                      AND je.entry_date >= :start_date
                      AND je.entry_date <= :end_date
                      AND je.source_module = 'BANKING'
                      AND je.source_document_type = 'BANK_RECONCILIATION'
                      AND je.status = 'POSTED'
                      AND jl.debit_amount > 0
                ),
                ap_invoices AS (
                    SELECT
                        si.invoice_id,
                        si.invoice_number,
                        si.invoice_date,
                        si.total_amount,
                        si.tax_amount,
                        s.legal_name AS supplier_name
                    FROM ap.supplier_invoice si
                    JOIN ap.supplier s ON s.supplier_id = si.supplier_id
                    WHERE si.organization_id = :org_id
                      AND si.invoice_date >= :start_date
                      AND si.invoice_date <= :end_date
                      AND si.status IN ('POSTED', 'PARTIALLY_PAID', 'PAID')
                )
                SELECT
                    rj.journal_entry_id,
                    rj.entry_date AS journal_date,
                    rj.amount AS journal_amount,
                    rj.description AS journal_desc,
                    ai.invoice_number,
                    ai.invoice_date,
                    ai.total_amount AS invoice_amount,
                    ai.tax_amount AS invoice_tax,
                    ai.supplier_name,
                    abs(rj.amount - ai.total_amount) AS amount_diff,
                    abs(rj.entry_date - ai.invoice_date) AS date_diff_days
                FROM recon_journals rj
                JOIN ap_invoices ai
                  ON abs(rj.amount - ai.total_amount) <= 0.01
                  AND abs(rj.entry_date - ai.invoice_date) <= 7
                ORDER BY ai.invoice_number, rj.entry_date
            """),
            {
                "org_id": str(ORG_ID),
                "start_date": JAN_2026_START,
                "end_date": JAN_2026_END,
            },
        ).all()

        logger.info("")
        logger.info("  EXACT AMOUNT MATCHES (within 0.01, within 7 days):")
        logger.info("  Found %d matches", len(overlap_rows))
        logger.info("")

        matched_journal_ids: set[str] = set()
        matched_invoice_nums: set[str] = set()

        for row in overlap_rows:
            matched_journal_ids.add(str(row.journal_entry_id))
            matched_invoice_nums.add(row.invoice_number)
            logger.info(
                "    Journal %s (%s NGN %s) ↔ %s (%s NGN %s, tax=%s) [%s]",
                str(row.journal_entry_id)[:8],
                row.journal_date,
                f"{row.journal_amount:,.2f}",
                row.invoice_number,
                row.invoice_date,
                f"{row.invoice_amount:,.2f}",
                f"{row.invoice_tax:,.2f}",
                row.supplier_name or "—",
            )

        logger.info("")
        logger.info("  SUMMARY:")
        logger.info(
            "    Matched journals:  %d / %s", len(matched_journal_ids), recon_count
        )
        logger.info(
            "    Matched invoices:  %d / %s", len(matched_invoice_nums), ap_count[0]
        )
        logger.info(
            "    Unmatched journals (orphaned shortcuts): %d",
            (recon_count or 0) - len(matched_journal_ids),
        )
        logger.info(
            "    Unmatched invoices (no bank journal):    %d",
            (ap_count[0] or 0) - len(matched_invoice_nums),
        )

        # Broader fuzzy match: amount within 5% (catches rounding)
        fuzzy_rows = db.execute(
            text("""
                WITH recon_journals AS (
                    SELECT
                        je.journal_entry_id,
                        je.entry_date,
                        je.description,
                        jl.debit_amount AS amount
                    FROM gl.journal_entry je
                    JOIN gl.journal_entry_line jl
                      ON jl.journal_entry_id = je.journal_entry_id
                    WHERE je.organization_id = :org_id
                      AND je.entry_date >= :start_date
                      AND je.entry_date <= :end_date
                      AND je.source_module = 'BANKING'
                      AND je.source_document_type = 'BANK_RECONCILIATION'
                      AND je.status = 'POSTED'
                      AND jl.debit_amount > 0
                ),
                ap_invoices AS (
                    SELECT
                        si.invoice_id,
                        si.invoice_number,
                        si.invoice_date,
                        si.total_amount,
                        si.tax_amount
                    FROM ap.supplier_invoice si
                    WHERE si.organization_id = :org_id
                      AND si.invoice_date >= :start_date
                      AND si.invoice_date <= :end_date
                      AND si.status IN ('POSTED', 'PARTIALLY_PAID', 'PAID')
                )
                SELECT count(*) AS match_count
                FROM recon_journals rj
                JOIN ap_invoices ai
                  ON abs(rj.amount - ai.total_amount) / NULLIF(ai.total_amount, 0) <= 0.05
                  AND abs(rj.entry_date - ai.invoice_date) <= 7
            """),
            {
                "org_id": str(ORG_ID),
                "start_date": JAN_2026_START,
                "end_date": JAN_2026_END,
            },
        ).scalar()
        logger.info("    Fuzzy matches (within 5%% amount, 7 days): %s", fuzzy_rows)

        # Tax impact summary
        logger.info("")
        logger.info("  TAX IMPACT:")
        tax_summary = db.execute(
            text("""
                SELECT
                    tt.transaction_type,
                    count(*) AS txn_count,
                    coalesce(sum(tt.functional_tax_amount), 0) AS total_tax
                FROM tax.tax_transaction tt
                WHERE tt.organization_id = :org_id
                  AND tt.transaction_date >= :start_date
                  AND tt.transaction_date <= :end_date
                GROUP BY tt.transaction_type
                ORDER BY tt.transaction_type
            """),
            {
                "org_id": str(ORG_ID),
                "start_date": JAN_2026_START,
                "end_date": JAN_2026_END,
            },
        ).all()
        for row in tax_summary:
            logger.info(
                "    %s: %d transactions, NGN %s",
                row.transaction_type,
                row.txn_count,
                f"{row.total_tax:,.2f}",
            )


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post Jan 2026 DRAFT AP invoices and create tax returns"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview changes only")
    mode.add_argument("--execute", action="store_true", help="Apply changes")

    args = parser.parse_args()
    execute = args.execute

    logger.info("")
    logger.info("╔════════════════════════════════════════════════════════╗")
    logger.info("║  Post January 2026 AP Invoices & Create Tax Returns   ║")
    logger.info(
        "║  Mode: %s                                    ║",
        "EXECUTE " if execute else "DRY RUN ",
    )
    logger.info("╚════════════════════════════════════════════════════════╝")
    logger.info("")

    # Task 1: Post DRAFT invoices
    results = post_draft_invoices(execute=execute)

    # Task 2: Create tax returns (only meaningful after posting)
    create_tax_returns(execute=execute)

    # Task 3: Overlap analysis (read-only, always runs)
    analyze_overlap()

    logger.info("")
    logger.info("=" * 60)
    logger.info("DONE")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
