"""
TaxReportService - Tax reporting and analysis.

Generates tax reports by type, VAT returns, WHT reports, and exports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.models.finance.tax.tax_transaction import (
    TaxRecognitionBasis,
    TaxTransaction,
    TaxTransactionType,
)
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)

# Reporting basis for tax aggregations.
#
# - "accrual": queries tax.tax_transaction (recognized at invoice time).
#   Reflects the obligation as soon as a sale/purchase is recorded.
# - "cash":    derives from AR receipts and AP payments via prorated
#   invoice ratios. Reflects the obligation only on cash actually
#   received/paid. Required for Nigerian VAT (FIRS pay-when-paid).
TaxBasis = Literal["accrual", "cash"]


@dataclass
class TaxSummaryByType:
    """Summary of taxes by type."""

    tax_type: str
    tax_type_display: str
    total_output: Decimal  # Tax collected (sales)
    total_input: Decimal  # Tax paid (purchases)
    total_wht_collected: Decimal  # WHT withheld from suppliers
    total_wht_deducted: Decimal  # WHT deducted by customers
    net_payable: Decimal  # Output - Input (positive = owe tax)
    transaction_count: int


@dataclass
class TaxCodeSummary:
    """Summary for a specific tax code."""

    tax_code_id: UUID
    tax_code: str
    tax_name: str
    tax_type: str
    rate: Decimal
    total_base: Decimal
    total_tax: Decimal
    transaction_count: int


@dataclass
class TaxTransactionDetail:
    """Detailed tax transaction for reports."""

    transaction_id: UUID
    transaction_date: date
    tax_code: str
    tax_name: str
    transaction_type: str
    base_amount: Decimal
    tax_amount: Decimal
    currency_code: str
    source_document_type: str
    source_document_id: UUID | None
    reference: str | None
    counterparty_name: str | None
    counterparty_tax_id: str | None


@dataclass
class VATReturnData:
    """Data for VAT return filing (Nigerian FIRS format)."""

    period_start: date
    period_end: date
    # Box 1: Total value of taxable supplies (sales)
    box1_taxable_supplies: Decimal = field(default_factory=lambda: Decimal("0"))
    # Box 2: Output VAT on sales
    box2_output_vat: Decimal = field(default_factory=lambda: Decimal("0"))
    # Box 3: Total value of taxable purchases
    box3_taxable_purchases: Decimal = field(default_factory=lambda: Decimal("0"))
    # Box 4: Input VAT on purchases
    box4_input_vat: Decimal = field(default_factory=lambda: Decimal("0"))
    # Box 5: Net VAT payable (Box 2 - Box 4)
    box5_net_vat: Decimal = field(default_factory=lambda: Decimal("0"))
    # Box 6: Zero-rated supplies
    box6_zero_rated: Decimal = field(default_factory=lambda: Decimal("0"))
    # Box 7: Exempt supplies
    box7_exempt: Decimal = field(default_factory=lambda: Decimal("0"))
    # Breakdown by rate
    rate_breakdown: list = field(default_factory=list)


@dataclass
class WHTReportData:
    """Withholding tax report data."""

    period_start: date
    period_end: date
    # WHT we withheld from suppliers (AP payments)
    wht_withheld_from_suppliers: Decimal = field(default_factory=lambda: Decimal("0"))
    wht_withheld_count: int = 0
    # WHT deducted by customers (AR receipts)
    wht_deducted_by_customers: Decimal = field(default_factory=lambda: Decimal("0"))
    wht_deducted_count: int = 0
    # Net WHT position
    net_wht_payable: Decimal = field(
        default_factory=lambda: Decimal("0")
    )  # What we owe to tax authority
    # Breakdown by WHT rate
    by_rate: list = field(default_factory=list)
    # Transaction details
    transactions: list = field(default_factory=list)


@dataclass
class StampDutyReportData:
    """Stamp duty report data."""

    period_start: date
    period_end: date
    stamp_duty_on_sales: Decimal = field(default_factory=lambda: Decimal("0"))
    sales_count: int = 0
    stamp_duty_on_purchases: Decimal = field(default_factory=lambda: Decimal("0"))
    purchase_count: int = 0
    total_stamp_duty: Decimal = field(default_factory=lambda: Decimal("0"))
    by_code: list = field(default_factory=list)
    transactions: list = field(default_factory=list)


class TaxReportService:
    """
    Service for generating tax reports.

    Provides various tax analysis reports including:
    - Tax summary by type
    - VAT return data
    - WHT reports
    - Tax register exports
    """

    @staticmethod
    def get_tax_summary_by_type(
        db: Session,
        organization_id: UUID,
        start_date: date,
        end_date: date,
        basis: TaxBasis = "accrual",
    ) -> list[TaxSummaryByType]:
        """
        Get tax summary grouped by tax type.

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Report period start
            end_date: Report period end
            basis: ``"accrual"`` reads tax.tax_transaction (invoice time);
                ``"cash"`` derives from AR/AP payment allocations.

        Returns:
            List of TaxSummaryByType objects
        """
        org_id = coerce_uuid(organization_id)

        if basis == "cash":
            return _tax_summary_by_type_cash(db, org_id, start_date, end_date)

        # Query tax transactions grouped by tax type (accrual basis)
        results = list(
            db.execute(
                select(
                    TaxCode.tax_type,
                    TaxTransaction.transaction_type,
                    func.sum(TaxTransaction.tax_amount).label("total_tax"),
                    func.sum(TaxTransaction.base_amount).label("total_base"),
                    func.count(TaxTransaction.transaction_id).label(
                        "transaction_count"
                    ),
                )
                .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
                .where(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.transaction_date >= start_date,
                    TaxTransaction.transaction_date <= end_date,
                    TaxTransaction.recognition_basis == TaxRecognitionBasis.ACCRUAL,
                )
                .group_by(TaxCode.tax_type, TaxTransaction.transaction_type)
            )
        )

        # Aggregate by tax type
        type_data: dict[TaxType, dict] = {}
        for tax_type, txn_type, total_tax, _total_base, count in results:
            if tax_type not in type_data:
                type_data[tax_type] = {
                    "output": Decimal("0"),
                    "input": Decimal("0"),
                    "wht_collected": Decimal("0"),
                    "wht_deducted": Decimal("0"),
                    "count": 0,
                }

            type_data[tax_type]["count"] += count

            if txn_type == TaxTransactionType.OUTPUT:
                type_data[tax_type]["output"] += total_tax or Decimal("0")
            elif txn_type == TaxTransactionType.INPUT:
                type_data[tax_type]["input"] += total_tax or Decimal("0")
            elif txn_type == TaxTransactionType.WITHHOLDING:
                # Positive = we withheld (AP), Negative = deducted from us (AR)
                if total_tax and total_tax > 0:
                    type_data[tax_type]["wht_collected"] += total_tax
                else:
                    type_data[tax_type]["wht_deducted"] += abs(
                        total_tax or Decimal("0")
                    )

        # Convert to summary objects
        summaries = []
        type_display = {
            TaxType.VAT: "Value Added Tax (VAT)",
            TaxType.GST: "Goods and Services Tax (GST)",
            TaxType.SALES_TAX: "Sales Tax",
            TaxType.WITHHOLDING: "Withholding Tax (WHT)",
            TaxType.INCOME_TAX: "Income Tax",
            TaxType.EXCISE: "Excise Duty",
            TaxType.CUSTOMS: "Customs Duty",
            TaxType.OTHER: "Other Taxes",
        }

        for tax_type, data in type_data.items():
            net_payable = data["output"] - data["input"]
            summaries.append(
                TaxSummaryByType(
                    tax_type=tax_type.value,
                    tax_type_display=type_display.get(tax_type, tax_type.value),
                    total_output=data["output"],
                    total_input=data["input"],
                    total_wht_collected=data["wht_collected"],
                    total_wht_deducted=data["wht_deducted"],
                    net_payable=net_payable,
                    transaction_count=data["count"],
                )
            )

        return sorted(summaries, key=lambda s: s.tax_type)

    @staticmethod
    def get_tax_summary_by_code(
        db: Session,
        organization_id: UUID,
        start_date: date,
        end_date: date,
        tax_type: TaxType | None = None,
        basis: TaxBasis = "accrual",
    ) -> list[TaxCodeSummary]:
        """
        Get tax summary grouped by tax code.

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Report period start
            end_date: Report period end
            tax_type: Optional filter by tax type
            basis: Cash basis is only meaningful for VAT — for other tax
                types the cash branch falls through to accrual since
                tax_transaction is the only source of per-code detail.

        Returns:
            List of TaxCodeSummary objects
        """
        # Per-code breakdown is hard to derive on cash basis without
        # tax_code linkage on payments, so we keep the existing accrual
        # query. Caller should use get_vat_return_data() for cash-basis
        # VAT totals; this method remains accrual.
        del basis
        org_id = coerce_uuid(organization_id)

        query = (
            select(
                TaxCode.tax_code_id,
                TaxCode.tax_code,
                TaxCode.tax_name,
                TaxCode.tax_type,
                TaxCode.tax_rate,
                func.sum(TaxTransaction.base_amount).label("total_base"),
                func.sum(TaxTransaction.tax_amount).label("total_tax"),
                func.count(TaxTransaction.transaction_id).label("transaction_count"),
            )
            .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
            .where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date <= end_date,
            )
        )

        if tax_type:
            query = query.where(TaxCode.tax_type == tax_type)

        results = list(
            db.execute(
                query.group_by(
                    TaxCode.tax_code_id,
                    TaxCode.tax_code,
                    TaxCode.tax_name,
                    TaxCode.tax_type,
                    TaxCode.tax_rate,
                ).order_by(TaxCode.tax_type, TaxCode.tax_code)
            )
        )

        return [
            TaxCodeSummary(
                tax_code_id=row.tax_code_id,
                tax_code=row.tax_code,
                tax_name=row.tax_name,
                tax_type=row.tax_type.value,
                rate=row.tax_rate,
                total_base=row.total_base or Decimal("0"),
                total_tax=row.total_tax or Decimal("0"),
                transaction_count=row.transaction_count,
            )
            for row in results
        ]

    @staticmethod
    def get_vat_return_data(
        db: Session,
        organization_id: UUID,
        start_date: date,
        end_date: date,
        basis: TaxBasis = "accrual",
    ) -> VATReturnData:
        """
        Get data for VAT return filing (Nigerian FIRS format).

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Return period start
            end_date: Return period end
            basis: ``"accrual"`` recognizes VAT at invoice time (the
                tax.tax_transaction subledger). ``"cash"`` recognizes only
                on cash actually received/paid — required for Nigerian
                FIRS treatment.

        Returns:
            VATReturnData with all boxes populated
        """
        org_id = coerce_uuid(organization_id)

        if basis == "cash":
            return _vat_return_data_cash(db, org_id, start_date, end_date)

        # Query VAT transactions only (accrual basis)
        results = list(
            db.execute(
                select(
                    TaxCode.tax_rate,
                    TaxTransaction.transaction_type,
                    func.sum(TaxTransaction.base_amount).label("total_base"),
                    func.sum(TaxTransaction.tax_amount).label("total_tax"),
                )
                .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
                .where(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.transaction_date >= start_date,
                    TaxTransaction.transaction_date <= end_date,
                    TaxCode.tax_type == TaxType.VAT,
                    TaxTransaction.recognition_basis == TaxRecognitionBasis.ACCRUAL,
                )
                .group_by(TaxCode.tax_rate, TaxTransaction.transaction_type)
            )
        )

        return_data = VATReturnData(
            period_start=start_date,
            period_end=end_date,
        )
        rate_breakdown = []

        for rate, txn_type, total_base, total_tax in results:
            base = total_base or Decimal("0")
            tax = total_tax or Decimal("0")

            if txn_type == TaxTransactionType.OUTPUT:
                if rate == Decimal("0"):
                    return_data.box6_zero_rated += base
                else:
                    return_data.box1_taxable_supplies += base
                    return_data.box2_output_vat += tax
            elif txn_type == TaxTransactionType.INPUT:
                return_data.box3_taxable_purchases += base
                return_data.box4_input_vat += tax

            rate_breakdown.append(
                {
                    "rate": float(rate),
                    "transaction_type": txn_type.value,
                    "base_amount": float(base),
                    "tax_amount": float(tax),
                }
            )

        # Calculate net VAT
        return_data.box5_net_vat = (
            return_data.box2_output_vat - return_data.box4_input_vat
        )
        return_data.rate_breakdown = rate_breakdown

        return return_data

    @staticmethod
    def get_wht_report(
        db: Session,
        organization_id: UUID,
        start_date: date,
        end_date: date,
        include_transactions: bool = False,
        basis: TaxBasis = "accrual",
    ) -> WHTReportData:
        """
        Get withholding tax report.

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Report period start
            end_date: Report period end
            include_transactions: Include transaction details
            basis: ``"cash"`` reads WHT amounts from customer/supplier
                payment headers (when the cash actually moved).
                ``"accrual"`` reads from tax.tax_transaction (recognized
                at invoice time).

        Returns:
            WHTReportData with summary and optional details
        """
        org_id = coerce_uuid(organization_id)

        if basis == "cash":
            return _wht_report_cash(
                db, org_id, start_date, end_date, include_transactions
            )

        def _source_module(source_document_type: str | None) -> str:
            if not source_document_type:
                return "OTHER"
            prefix = source_document_type.split("_", 1)[0]
            if prefix in {"AP", "AR"}:
                return prefix
            if source_document_type.startswith("CUSTOMER_PAYMENT"):
                return "AR"
            if source_document_type.startswith("SUPPLIER_PAYMENT"):
                return "AP"
            return "OTHER"

        # Query WHT transactions
        results = list(
            db.execute(
                select(
                    TaxCode.tax_code,
                    TaxCode.tax_name,
                    TaxCode.tax_rate,
                    TaxTransaction.source_document_type,
                    func.sum(TaxTransaction.base_amount).label("total_base"),
                    func.sum(TaxTransaction.tax_amount).label("total_tax"),
                    func.count(TaxTransaction.transaction_id).label("count"),
                )
                .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
                .where(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.transaction_date >= start_date,
                    TaxTransaction.transaction_date <= end_date,
                    TaxCode.tax_type == TaxType.WITHHOLDING,
                    TaxTransaction.recognition_basis == TaxRecognitionBasis.ACCRUAL,
                )
                .group_by(
                    TaxCode.tax_code,
                    TaxCode.tax_name,
                    TaxCode.tax_rate,
                    TaxTransaction.source_document_type,
                )
            )
        )

        report = WHTReportData(
            period_start=start_date,
            period_end=end_date,
        )
        by_rate = []
        by_rate_totals: dict[
            tuple[str, str, Decimal, str], dict[str, Decimal | int]
        ] = {}

        for (
            tax_code,
            tax_name,
            rate,
            source_document_type,
            total_base,
            total_tax,
            count,
        ) in results:
            tax_amount = total_tax or Decimal("0")
            source_module = _source_module(source_document_type)
            key = (tax_code, tax_name, rate, source_module)
            entry = by_rate_totals.setdefault(
                key,
                {
                    "total_base": Decimal("0"),
                    "total_tax": Decimal("0"),
                    "count": 0,
                },
            )
            entry["total_base"] += total_base or Decimal("0")
            entry["total_tax"] += tax_amount
            entry["count"] += int(count or 0)

            if source_module == "AP":
                # WHT withheld from suppliers
                report.wht_withheld_from_suppliers += tax_amount
                report.wht_withheld_count += count
            elif source_module == "AR":
                # WHT deducted by customers
                report.wht_deducted_by_customers += tax_amount
                report.wht_deducted_count += count

        for (tax_code, tax_name, rate, source_module), entry in by_rate_totals.items():
            by_rate.append(
                {
                    "tax_code": tax_code,
                    "tax_name": tax_name,
                    "rate": float(rate),
                    "source_module": source_module,
                    "base_amount": float(entry["total_base"] or 0),
                    "tax_amount": float(entry["total_tax"] or 0),
                    "count": int(entry["count"]),
                }
            )

        report.by_rate = by_rate
        # Net position: what we withheld (owe to tax authority) minus what was deducted from us (receivable)
        report.net_wht_payable = (
            report.wht_withheld_from_suppliers - report.wht_deducted_by_customers
        )

        # Include transaction details if requested
        if include_transactions:
            transactions = list(
                db.execute(
                    select(TaxTransaction, TaxCode)
                    .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
                    .where(
                        TaxTransaction.organization_id == org_id,
                        TaxTransaction.transaction_date >= start_date,
                        TaxTransaction.transaction_date <= end_date,
                        TaxCode.tax_type == TaxType.WITHHOLDING,
                    )
                    .order_by(TaxTransaction.transaction_date.desc())
                )
            )

            report.transactions = [
                {
                    "transaction_id": str(txn.transaction_id),
                    "transaction_date": txn.transaction_date.isoformat(),
                    "tax_code": code.tax_code,
                    "tax_name": code.tax_name,
                    "rate": float(code.tax_rate),
                    "source_module": _source_module(txn.source_document_type),
                    "source_document_type": txn.source_document_type,
                    "base_amount": float(txn.base_amount),
                    "tax_amount": float(txn.tax_amount),
                    "counterparty_name": txn.counterparty_name,
                    "counterparty_tax_id": txn.counterparty_tax_id,
                    "reference": txn.reference,
                }
                for txn, code in transactions
            ]

        return report

    @staticmethod
    def get_stamp_duty_report(
        db: Session,
        organization_id: UUID,
        start_date: date,
        end_date: date,
        include_transactions: bool = False,
    ) -> StampDutyReportData:
        """
        Get stamp duty report.

        Historical stamp duty is sourced from AR/AP invoice headers and
        linked tax codes rather than tax.tax_transaction, because stamp
        duty has not historically been recorded in that subledger.
        """
        org_id = coerce_uuid(organization_id)

        report = StampDutyReportData(
            period_start=start_date,
            period_end=end_date,
        )

        ar_results = list(
            db.execute(
                select(
                    TaxCode.tax_code,
                    TaxCode.tax_name,
                    TaxCode.tax_rate,
                    func.sum(Invoice.stamp_duty_amount).label("total_stamp_duty"),
                    func.count(Invoice.invoice_id).label("count"),
                )
                .join(TaxCode, TaxCode.tax_code_id == Invoice.stamp_duty_code_id)
                .where(
                    Invoice.organization_id == org_id,
                    Invoice.invoice_date >= start_date,
                    Invoice.invoice_date <= end_date,
                    Invoice.stamp_duty_amount > Decimal("0"),
                    TaxCode.tax_type == TaxType.STAMP_DUTY,
                )
                .group_by(
                    TaxCode.tax_code,
                    TaxCode.tax_name,
                    TaxCode.tax_rate,
                )
            )
        )

        ap_results = list(
            db.execute(
                select(
                    TaxCode.tax_code,
                    TaxCode.tax_name,
                    TaxCode.tax_rate,
                    func.sum(SupplierInvoice.stamp_duty_amount).label(
                        "total_stamp_duty"
                    ),
                    func.count(SupplierInvoice.invoice_id).label("count"),
                )
                .join(
                    TaxCode,
                    TaxCode.tax_code_id == SupplierInvoice.stamp_duty_code_id,
                )
                .where(
                    SupplierInvoice.organization_id == org_id,
                    SupplierInvoice.invoice_date >= start_date,
                    SupplierInvoice.invoice_date <= end_date,
                    SupplierInvoice.stamp_duty_amount > Decimal("0"),
                    TaxCode.tax_type == TaxType.STAMP_DUTY,
                )
                .group_by(
                    TaxCode.tax_code,
                    TaxCode.tax_name,
                    TaxCode.tax_rate,
                )
            )
        )

        by_code: list[dict[str, object]] = []

        for tax_code, tax_name, rate, total_stamp_duty, count in ar_results:
            stamp_duty_amount = total_stamp_duty or Decimal("0")
            invoice_count = int(count or 0)
            report.stamp_duty_on_sales += stamp_duty_amount
            report.sales_count += invoice_count
            by_code.append(
                {
                    "tax_code": tax_code,
                    "tax_name": tax_name,
                    "rate": rate * 100 if rate is not None else Decimal("0"),
                    "source_module": "AR",
                    "stamp_duty_amount": stamp_duty_amount,
                    "count": invoice_count,
                }
            )

        for tax_code, tax_name, rate, total_stamp_duty, count in ap_results:
            stamp_duty_amount = total_stamp_duty or Decimal("0")
            invoice_count = int(count or 0)
            report.stamp_duty_on_purchases += stamp_duty_amount
            report.purchase_count += invoice_count
            by_code.append(
                {
                    "tax_code": tax_code,
                    "tax_name": tax_name,
                    "rate": rate * 100 if rate is not None else Decimal("0"),
                    "source_module": "AP",
                    "stamp_duty_amount": stamp_duty_amount,
                    "count": invoice_count,
                }
            )

        report.total_stamp_duty = (
            report.stamp_duty_on_sales + report.stamp_duty_on_purchases
        )
        report.by_code = sorted(
            by_code,
            key=lambda item: (
                str(item["tax_code"]),
                str(item["source_module"]),
            ),
        )

        if include_transactions:
            ar_transactions = list(
                db.execute(
                    select(
                        Invoice.invoice_date,
                        Invoice.invoice_number,
                        Customer.legal_name,
                        Customer.tax_identification_number,
                        TaxCode.tax_code,
                        TaxCode.tax_name,
                        TaxCode.tax_rate,
                        Invoice.stamp_duty_amount,
                        Invoice.stamp_duty_treatment,
                    )
                    .join(TaxCode, TaxCode.tax_code_id == Invoice.stamp_duty_code_id)
                    .outerjoin(Customer, Customer.customer_id == Invoice.customer_id)
                    .where(
                        Invoice.organization_id == org_id,
                        Invoice.invoice_date >= start_date,
                        Invoice.invoice_date <= end_date,
                        Invoice.stamp_duty_amount > Decimal("0"),
                        TaxCode.tax_type == TaxType.STAMP_DUTY,
                    )
                    .order_by(
                        Invoice.invoice_date.desc(), Invoice.invoice_number.desc()
                    )
                )
            )
            ap_transactions = list(
                db.execute(
                    select(
                        SupplierInvoice.invoice_date,
                        SupplierInvoice.invoice_number,
                        Supplier.legal_name,
                        Supplier.tax_identification_number,
                        TaxCode.tax_code,
                        TaxCode.tax_name,
                        TaxCode.tax_rate,
                        SupplierInvoice.stamp_duty_amount,
                    )
                    .join(
                        TaxCode,
                        TaxCode.tax_code_id == SupplierInvoice.stamp_duty_code_id,
                    )
                    .outerjoin(
                        Supplier,
                        Supplier.supplier_id == SupplierInvoice.supplier_id,
                    )
                    .where(
                        SupplierInvoice.organization_id == org_id,
                        SupplierInvoice.invoice_date >= start_date,
                        SupplierInvoice.invoice_date <= end_date,
                        SupplierInvoice.stamp_duty_amount > Decimal("0"),
                        TaxCode.tax_type == TaxType.STAMP_DUTY,
                    )
                    .order_by(
                        SupplierInvoice.invoice_date.desc(),
                        SupplierInvoice.invoice_number.desc(),
                    )
                )
            )

            report.transactions = [
                {
                    "transaction_date": invoice_date,
                    "reference": invoice_number,
                    "counterparty_name": counterparty_name,
                    "counterparty_tax_id": counterparty_tax_id,
                    "tax_code": tax_code,
                    "tax_name": tax_name,
                    "rate": tax_rate * 100 if tax_rate is not None else Decimal("0"),
                    "stamp_duty_amount": stamp_duty_amount,
                    "source_module": "AR",
                    "treatment": stamp_duty_treatment,
                }
                for (
                    invoice_date,
                    invoice_number,
                    counterparty_name,
                    counterparty_tax_id,
                    tax_code,
                    tax_name,
                    tax_rate,
                    stamp_duty_amount,
                    stamp_duty_treatment,
                ) in ar_transactions
            ] + [
                {
                    "transaction_date": invoice_date,
                    "reference": invoice_number,
                    "counterparty_name": counterparty_name,
                    "counterparty_tax_id": counterparty_tax_id,
                    "tax_code": tax_code,
                    "tax_name": tax_name,
                    "rate": tax_rate * 100 if tax_rate is not None else Decimal("0"),
                    "stamp_duty_amount": stamp_duty_amount,
                    "source_module": "AP",
                    "treatment": None,
                }
                for (
                    invoice_date,
                    invoice_number,
                    counterparty_name,
                    counterparty_tax_id,
                    tax_code,
                    tax_name,
                    tax_rate,
                    stamp_duty_amount,
                ) in ap_transactions
            ]
            report.transactions.sort(
                key=lambda item: (item["transaction_date"], item["reference"] or ""),
                reverse=True,
            )

        return report

    @staticmethod
    def get_tax_register(
        db: Session,
        organization_id: UUID,
        start_date: date,
        end_date: date,
        tax_type: TaxType | None = None,
        transaction_type: TaxTransactionType | None = None,
        limit: int = 1000,
        offset: int = 0,
        basis: TaxBasis = "accrual",
    ) -> list[TaxTransactionDetail]:
        """
        Get detailed tax register for export.

        Note:
            ``basis`` is accepted for API symmetry but the register reads
            tax.tax_transaction directly (per-row detail). Cash-basis
            totals come from get_vat_return_data() / get_wht_report().

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Report period start
            end_date: Report period end
            tax_type: Optional filter by tax type
            transaction_type: Optional filter by transaction type
            limit: Maximum records to return
            offset: Pagination offset

        Returns:
            List of TaxTransactionDetail objects
        """
        del basis
        org_id = coerce_uuid(organization_id)

        query = (
            select(TaxTransaction, TaxCode)
            .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
            .where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date <= end_date,
                TaxTransaction.recognition_basis == TaxRecognitionBasis.ACCRUAL,
            )
        )

        if tax_type:
            query = query.where(TaxCode.tax_type == tax_type)

        if transaction_type:
            query = query.where(TaxTransaction.transaction_type == transaction_type)

        results = db.execute(
            query.order_by(TaxTransaction.transaction_date.desc())
            .limit(limit)
            .offset(offset)
        ).all()

        return [
            TaxTransactionDetail(
                transaction_id=txn.transaction_id,
                transaction_date=txn.transaction_date,
                tax_code=code.tax_code,
                tax_name=code.tax_name,
                transaction_type=txn.transaction_type.value,
                base_amount=txn.base_amount,
                tax_amount=txn.tax_amount,
                currency_code=txn.currency_code,
                source_document_type=txn.source_document_type,
                source_document_id=txn.source_document_id,
                reference=txn.reference,
                counterparty_name=txn.counterparty_name,
                counterparty_tax_id=txn.counterparty_tax_id,
            )
            for txn, code in results
        ]


# ---------------------------------------------------------------------------
# Cash-basis branches
# ---------------------------------------------------------------------------
#
# These are private helpers split out from the static methods to keep each
# branch readable. They consume the prorate helpers in rpt/common which
# walk AR/AP payment_allocation × invoice ratios.


def _vat_return_data_cash(
    db: Session,
    org_id: UUID,
    start_date: date,
    end_date: date,
) -> VATReturnData:
    """Build VATReturnData from cash-basis tax transactions."""
    results = list(
        db.execute(
            select(
                TaxCode.tax_rate,
                TaxTransaction.transaction_type,
                func.sum(TaxTransaction.base_amount).label("total_base"),
                func.sum(TaxTransaction.tax_amount).label("total_tax"),
            )
            .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
            .where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date <= end_date,
                TaxCode.tax_type == TaxType.VAT,
                TaxTransaction.recognition_basis == TaxRecognitionBasis.CASH,
            )
            .group_by(TaxCode.tax_rate, TaxTransaction.transaction_type)
        )
    )

    if not results:
        from app.services.finance.rpt.common import _cash_basis_vat_totals

        totals = _cash_basis_vat_totals(db, org_id, start_date, end_date)
        return VATReturnData(
            period_start=start_date,
            period_end=end_date,
            box1_taxable_supplies=totals["net_output_base"],
            box2_output_vat=totals["net_output_vat"],
            box3_taxable_purchases=totals["input_base"],
            box4_input_vat=totals["input_vat"],
            box5_net_vat=totals["net_vat_payable"],
            box6_zero_rated=totals["output_zero_rated"],
            box7_exempt=Decimal("0"),
            rate_breakdown=totals["rate_breakdown"],
        )

    return_data = VATReturnData(period_start=start_date, period_end=end_date)
    rate_breakdown = []
    for rate, txn_type, total_base, total_tax in results:
        base = total_base or Decimal("0")
        tax = total_tax or Decimal("0")
        if txn_type == TaxTransactionType.OUTPUT:
            if rate == Decimal("0"):
                return_data.box6_zero_rated += base
            else:
                return_data.box1_taxable_supplies += base
                return_data.box2_output_vat += tax
        elif txn_type == TaxTransactionType.INPUT:
            return_data.box3_taxable_purchases += base
            return_data.box4_input_vat += tax

        rate_breakdown.append(
            {
                "rate": float(rate),
                "transaction_type": txn_type.value,
                "base_amount": float(base),
                "tax_amount": float(tax),
            }
        )

    return_data.box5_net_vat = return_data.box2_output_vat - return_data.box4_input_vat
    return_data.rate_breakdown = rate_breakdown
    return return_data


def _tax_summary_by_type_cash(
    db: Session,
    org_id: UUID,
    start_date: date,
    end_date: date,
) -> list[TaxSummaryByType]:
    """Build TaxSummaryByType list on cash basis.

    Currently only emits a VAT row (the cash-basis concept has no clean
    application to income tax / excise / customs) plus a WHT row from
    payment headers.
    """
    from app.services.finance.rpt.common import (
        _cash_basis_vat_totals,
        _cash_basis_wht_totals,
    )

    cash_vat_summary = db.execute(
        select(
            TaxTransaction.transaction_type,
            func.sum(TaxTransaction.tax_amount).label("total_tax"),
        )
        .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
        .where(
            TaxTransaction.organization_id == org_id,
            TaxTransaction.transaction_date >= start_date,
            TaxTransaction.transaction_date <= end_date,
            TaxCode.tax_type == TaxType.VAT,
            TaxTransaction.recognition_basis == TaxRecognitionBasis.CASH,
        )
        .group_by(TaxTransaction.transaction_type)
    ).all()
    if cash_vat_summary:
        vat_output = Decimal("0")
        vat_input = Decimal("0")
        for txn_type, total_tax in cash_vat_summary:
            if txn_type == TaxTransactionType.OUTPUT:
                vat_output += total_tax or Decimal("0")
            elif txn_type == TaxTransactionType.INPUT:
                vat_input += total_tax or Decimal("0")
        vat = {
            "net_output_vat": vat_output,
            "input_vat": vat_input,
            "net_vat_payable": vat_output - vat_input,
        }
    else:
        vat = _cash_basis_vat_totals(db, org_id, start_date, end_date)
    wht = _cash_basis_wht_totals(db, org_id, start_date, end_date)

    summaries: list[TaxSummaryByType] = []

    # VAT
    summaries.append(
        TaxSummaryByType(
            tax_type=TaxType.VAT.value,
            tax_type_display="Value Added Tax (VAT)",
            total_output=vat["net_output_vat"],
            total_input=vat["input_vat"],
            total_wht_collected=Decimal("0"),
            total_wht_deducted=Decimal("0"),
            net_payable=vat["net_vat_payable"],
            transaction_count=0,
        )
    )

    # Withholding
    summaries.append(
        TaxSummaryByType(
            tax_type=TaxType.WITHHOLDING.value,
            tax_type_display="Withholding Tax (WHT)",
            total_output=Decimal("0"),
            total_input=Decimal("0"),
            total_wht_collected=wht["wht_withheld_from_suppliers"],
            total_wht_deducted=wht["wht_deducted_by_customers"],
            net_payable=wht["net_wht_payable"],
            transaction_count=0,
        )
    )

    return summaries


def _wht_report_cash(
    db: Session,
    org_id: UUID,
    start_date: date,
    end_date: date,
    include_transactions: bool,
) -> WHTReportData:
    """Build WHTReportData from payment header WHT amounts.

    AR side: customer_payment.wht_amount (deducted by customers)
    AP side: supplier_payment.withholding_tax_amount (we withheld)
    """
    from app.models.finance.ap.supplier_payment import (
        APPaymentStatus,
        SupplierPayment,
    )
    from app.models.finance.ar.customer_payment import (
        CustomerPayment,
        PaymentStatus,
    )
    from app.services.finance.rpt.common import _cash_basis_wht_totals

    totals = _cash_basis_wht_totals(db, org_id, start_date, end_date)

    report = WHTReportData(
        period_start=start_date,
        period_end=end_date,
        wht_withheld_from_suppliers=totals["wht_withheld_from_suppliers"],
        wht_deducted_by_customers=totals["wht_deducted_by_customers"],
        net_wht_payable=totals["net_wht_payable"],
    )

    if not include_transactions:
        return report

    excluded_ar = {PaymentStatus.VOID, PaymentStatus.BOUNCED, PaymentStatus.REVERSED}
    excluded_ap = {
        APPaymentStatus.VOID,
        APPaymentStatus.REJECTED,
        APPaymentStatus.DRAFT,
    }

    ar_rows = list(
        db.execute(
            select(CustomerPayment)
            .where(
                CustomerPayment.organization_id == org_id,
                CustomerPayment.payment_date >= start_date,
                CustomerPayment.payment_date <= end_date,
                CustomerPayment.wht_amount > 0,
                CustomerPayment.status.notin_(excluded_ar),
            )
            .order_by(CustomerPayment.payment_date.desc())
        ).scalars()
    )
    ap_rows = list(
        db.execute(
            select(SupplierPayment)
            .where(
                SupplierPayment.organization_id == org_id,
                SupplierPayment.payment_date >= start_date,
                SupplierPayment.payment_date <= end_date,
                SupplierPayment.withholding_tax_amount > 0,
                SupplierPayment.status.notin_(excluded_ap),
            )
            .order_by(SupplierPayment.payment_date.desc())
        ).scalars()
    )

    write_off_rows = list(
        db.execute(
            select(TaxTransaction)
            .where(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date <= end_date,
                TaxTransaction.source_document_type == "CUSTOMER_PAYMENT_WHT_WRITE_OFF",
                TaxTransaction.transaction_type == TaxTransactionType.WITHHOLDING,
            )
            .order_by(TaxTransaction.transaction_date.desc())
        ).scalars()
    )

    transactions: list[dict] = []
    report.wht_deducted_count = len(ar_rows) + len(write_off_rows)
    for cp in ar_rows:
        transactions.append(
            {
                "transaction_id": str(cp.payment_id),
                "transaction_date": cp.payment_date.isoformat(),
                "source_module": "AR",
                "source_document_type": "CUSTOMER_PAYMENT",
                "base_amount": float(cp.gross_amount or 0),
                "tax_amount": float(cp.wht_amount or 0),
                "reference": cp.reference,
                "certificate_number": cp.wht_certificate_number,
            }
        )

    for txn in write_off_rows:
        transactions.append(
            {
                "transaction_id": str(txn.transaction_id),
                "transaction_date": txn.transaction_date.isoformat(),
                "source_module": "AR",
                "source_document_type": txn.source_document_type,
                "base_amount": float(txn.base_amount or 0),
                "tax_amount": float(txn.tax_amount or 0),
                "reference": txn.source_document_reference,
                "certificate_number": None,
            }
        )

    report.wht_withheld_count = len(ap_rows)
    for sp in ap_rows:
        transactions.append(
            {
                "transaction_id": str(sp.payment_id),
                "transaction_date": sp.payment_date.isoformat(),
                "source_module": "AP",
                "source_document_type": "SUPPLIER_PAYMENT",
                "base_amount": float(sp.gross_amount or 0),
                "tax_amount": float(sp.withholding_tax_amount or 0),
                "reference": sp.reference,
            }
        )

    report.transactions = transactions
    return report


# Module-level singleton instance
tax_report_service = TaxReportService()
