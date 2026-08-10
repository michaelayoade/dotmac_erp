#!/usr/bin/env python3
"""
Review historical 2025 VAT data integrity month by month.

This is intentionally read-only. It highlights whether VAT can be
reconstructed from invoice tax-detail tables or whether history only
exists in tax.tax_transaction.

Usage:
    poetry run python scripts/review_2025_vat_history.py
    poetry run python scripts/review_2025_vat_history.py --year 2025
    poetry run python scripts/review_2025_vat_history.py --org-id <uuid>
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal
from uuid import UUID

sys.path.insert(0, ".")

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.ap.supplier_invoice_line_tax import SupplierInvoiceLineTax
from app.models.finance.ar.invoice import Invoice
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.models.finance.gl.journal_entry import JournalEntry
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.models.finance.tax.tax_transaction import TaxRecognitionBasis, TaxTransaction

DEFAULT_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def _month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--org-id", type=UUID, default=DEFAULT_ORG_ID)
    args = parser.parse_args()

    start_date = date(args.year, 1, 1)
    end_date = date(args.year + 1, 1, 1)

    with SessionLocal() as db:
        accrual_rows = {
            (month.strftime("%Y-%m"), txn_type): (int(count or 0), Decimal(total or 0))
            for month, txn_type, count, total in db.execute(
                select(
                    func.date_trunc("month", TaxTransaction.transaction_date).label(
                        "month"
                    ),
                    TaxTransaction.transaction_type,
                    func.count(TaxTransaction.transaction_id).label("count"),
                    func.sum(TaxTransaction.functional_tax_amount).label("total"),
                )
                .join(TaxCode, TaxCode.tax_code_id == TaxTransaction.tax_code_id)
                .where(
                    TaxTransaction.organization_id == args.org_id,
                    TaxTransaction.transaction_date >= start_date,
                    TaxTransaction.transaction_date < end_date,
                    TaxTransaction.recognition_basis == TaxRecognitionBasis.ACCRUAL,
                    TaxCode.tax_type.in_((TaxType.VAT, TaxType.GST)),
                )
                .group_by("month", TaxTransaction.transaction_type)
                .order_by("month", TaxTransaction.transaction_type)
            )
        }

        ar_tax_detail_rows = {
            month.strftime("%Y-%m"): (int(count or 0), Decimal(total or 0))
            for month, count, total in db.execute(
                select(
                    func.date_trunc("month", Invoice.invoice_date).label("month"),
                    func.count(func.distinct(Invoice.invoice_id)).label("count"),
                    func.sum(InvoiceLineTax.tax_amount).label("total"),
                )
                .join(InvoiceLine, InvoiceLine.invoice_id == Invoice.invoice_id)
                .join(InvoiceLineTax, InvoiceLineTax.line_id == InvoiceLine.line_id)
                .join(TaxCode, TaxCode.tax_code_id == InvoiceLineTax.tax_code_id)
                .where(
                    Invoice.organization_id == args.org_id,
                    Invoice.invoice_date >= start_date,
                    Invoice.invoice_date < end_date,
                    TaxCode.tax_type.in_((TaxType.VAT, TaxType.GST)),
                )
                .group_by("month")
                .order_by("month")
            )
        }

        ap_tax_detail_rows = {
            month.strftime("%Y-%m"): (int(count or 0), Decimal(total or 0))
            for month, count, total in db.execute(
                select(
                    func.date_trunc("month", SupplierInvoice.invoice_date).label(
                        "month"
                    ),
                    func.count(func.distinct(SupplierInvoice.invoice_id)).label(
                        "count"
                    ),
                    func.sum(SupplierInvoiceLineTax.tax_amount).label("total"),
                )
                .join(
                    SupplierInvoiceLine,
                    SupplierInvoiceLine.invoice_id == SupplierInvoice.invoice_id,
                )
                .join(
                    SupplierInvoiceLineTax,
                    SupplierInvoiceLineTax.line_id == SupplierInvoiceLine.line_id,
                )
                .join(
                    TaxCode, TaxCode.tax_code_id == SupplierInvoiceLineTax.tax_code_id
                )
                .where(
                    SupplierInvoice.organization_id == args.org_id,
                    SupplierInvoice.invoice_date >= start_date,
                    SupplierInvoice.invoice_date < end_date,
                    TaxCode.tax_type.in_((TaxType.VAT, TaxType.GST)),
                )
                .group_by("month")
                .order_by("month")
            )
        }

        deferred_rows = {
            (month.strftime("%Y-%m"), source_document_type): int(count or 0)
            for month, source_document_type, count in db.execute(
                select(
                    func.date_trunc("month", JournalEntry.posting_date).label("month"),
                    JournalEntry.source_document_type,
                    func.count(JournalEntry.journal_entry_id).label("count"),
                )
                .where(
                    JournalEntry.organization_id == args.org_id,
                    JournalEntry.posting_date >= start_date,
                    JournalEntry.posting_date < end_date,
                    JournalEntry.source_document_type.in_(
                        (
                            "AR_INVOICE_VAT_DEFERRAL",
                            "SUPPLIER_INVOICE_VAT_DEFERRAL",
                            "CUSTOMER_PAYMENT_VAT_RECLASS",
                            "SUPPLIER_PAYMENT_VAT_RECLASS",
                        )
                    ),
                )
                .group_by("month", JournalEntry.source_document_type)
                .order_by("month", JournalEntry.source_document_type)
            )
        }

    print(f"Historical VAT review for {args.year} / org {args.org_id}")
    print(
        "month | ar_accrual_rows | ar_accrual_tax | ar_tax_detail_invoices | ar_tax_detail_tax | "
        "ap_accrual_rows | ap_accrual_tax | ap_tax_detail_invoices | ap_tax_detail_tax | "
        "ar_deferrals | ap_deferrals | ar_cash_reclasses | ap_cash_reclasses"
    )
    for month in range(1, 13):
        key = _month_key(args.year, month)
        ar_accrual_count, ar_accrual_tax = accrual_rows.get(
            (key, "OUTPUT"), (0, Decimal("0"))
        )
        ap_accrual_count, ap_accrual_tax = accrual_rows.get(
            (key, "INPUT"), (0, Decimal("0"))
        )
        ar_detail_count, ar_detail_tax = ar_tax_detail_rows.get(key, (0, Decimal("0")))
        ap_detail_count, ap_detail_tax = ap_tax_detail_rows.get(key, (0, Decimal("0")))
        ar_deferrals = deferred_rows.get((key, "AR_INVOICE_VAT_DEFERRAL"), 0)
        ap_deferrals = deferred_rows.get((key, "SUPPLIER_INVOICE_VAT_DEFERRAL"), 0)
        ar_cash = deferred_rows.get((key, "CUSTOMER_PAYMENT_VAT_RECLASS"), 0)
        ap_cash = deferred_rows.get((key, "SUPPLIER_PAYMENT_VAT_RECLASS"), 0)

        print(
            f"{key} | "
            f"{ar_accrual_count} | {ar_accrual_tax:.2f} | {ar_detail_count} | {ar_detail_tax:.2f} | "
            f"{ap_accrual_count} | {ap_accrual_tax:.2f} | {ap_detail_count} | {ap_detail_tax:.2f} | "
            f"{ar_deferrals} | {ap_deferrals} | {ar_cash} | {ap_cash}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
