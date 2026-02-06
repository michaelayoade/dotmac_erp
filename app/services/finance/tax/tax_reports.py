"""
TaxReportService - Tax reporting and analysis.

Generates tax reports by type, VAT returns, WHT reports, and exports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.models.finance.tax.tax_transaction import TaxTransaction, TaxTransactionType
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


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
    source_document_id: Optional[UUID]
    reference: Optional[str]
    counterparty_name: Optional[str]
    counterparty_tax_id: Optional[str]


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
    ) -> list[TaxSummaryByType]:
        """
        Get tax summary grouped by tax type.

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Report period start
            end_date: Report period end

        Returns:
            List of TaxSummaryByType objects
        """
        org_id = coerce_uuid(organization_id)

        # Query tax transactions grouped by tax type
        results = (
            db.query(
                TaxCode.tax_type,
                TaxTransaction.transaction_type,
                func.sum(TaxTransaction.tax_amount).label("total_tax"),
                func.sum(TaxTransaction.base_amount).label("total_base"),
                func.count(TaxTransaction.transaction_id).label("transaction_count"),
            )
            .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date <= end_date,
            )
            .group_by(TaxCode.tax_type, TaxTransaction.transaction_type)
            .all()
        )

        # Aggregate by tax type
        type_data: dict[TaxType, dict] = {}
        for tax_type, txn_type, total_tax, total_base, count in results:
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
        tax_type: Optional[TaxType] = None,
    ) -> list[TaxCodeSummary]:
        """
        Get tax summary grouped by tax code.

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Report period start
            end_date: Report period end
            tax_type: Optional filter by tax type

        Returns:
            List of TaxCodeSummary objects
        """
        org_id = coerce_uuid(organization_id)

        query = (
            db.query(
                TaxCode.tax_code_id,
                TaxCode.tax_code,
                TaxCode.tax_name,
                TaxCode.tax_type,
                TaxCode.tax_rate,
                func.sum(TaxTransaction.base_amount).label("total_base"),
                func.sum(TaxTransaction.tax_amount).label("total_tax"),
                func.count(TaxTransaction.transaction_id).label("count"),
            )
            .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date <= end_date,
            )
        )

        if tax_type:
            query = query.filter(TaxCode.tax_type == tax_type)

        results = (
            query.group_by(
                TaxCode.tax_code_id,
                TaxCode.tax_code,
                TaxCode.tax_name,
                TaxCode.tax_type,
                TaxCode.tax_rate,
            )
            .order_by(TaxCode.tax_type, TaxCode.tax_code)
            .all()
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
    ) -> VATReturnData:
        """
        Get data for VAT return filing (Nigerian FIRS format).

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Return period start
            end_date: Return period end

        Returns:
            VATReturnData with all boxes populated
        """
        org_id = coerce_uuid(organization_id)

        # Query VAT transactions only
        results = (
            db.query(
                TaxCode.tax_rate,
                TaxTransaction.transaction_type,
                func.sum(TaxTransaction.base_amount).label("total_base"),
                func.sum(TaxTransaction.tax_amount).label("total_tax"),
            )
            .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date <= end_date,
                TaxCode.tax_type == TaxType.VAT,
            )
            .group_by(TaxCode.tax_rate, TaxTransaction.transaction_type)
            .all()
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
    ) -> WHTReportData:
        """
        Get withholding tax report.

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Report period start
            end_date: Report period end
            include_transactions: Include transaction details

        Returns:
            WHTReportData with summary and optional details
        """
        org_id = coerce_uuid(organization_id)

        def _source_module(source_document_type: Optional[str]) -> str:
            if not source_document_type:
                return "OTHER"
            prefix = source_document_type.split("_", 1)[0]
            return prefix if prefix in {"AP", "AR"} else "OTHER"

        # Query WHT transactions
        results = (
            db.query(
                TaxCode.tax_code,
                TaxCode.tax_name,
                TaxCode.tax_rate,
                TaxTransaction.source_document_type,
                func.sum(TaxTransaction.base_amount).label("total_base"),
                func.sum(TaxTransaction.tax_amount).label("total_tax"),
                func.count(TaxTransaction.transaction_id).label("count"),
            )
            .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date <= end_date,
                TaxCode.tax_type == TaxType.WITHHOLDING,
            )
            .group_by(
                TaxCode.tax_code,
                TaxCode.tax_name,
                TaxCode.tax_rate,
                TaxTransaction.source_document_type,
            )
            .all()
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
            transactions = (
                db.query(TaxTransaction, TaxCode)
                .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
                .filter(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.transaction_date >= start_date,
                    TaxTransaction.transaction_date <= end_date,
                    TaxCode.tax_type == TaxType.WITHHOLDING,
                )
                .order_by(TaxTransaction.transaction_date.desc())
                .all()
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
    def get_tax_register(
        db: Session,
        organization_id: UUID,
        start_date: date,
        end_date: date,
        tax_type: Optional[TaxType] = None,
        transaction_type: Optional[TaxTransactionType] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[TaxTransactionDetail]:
        """
        Get detailed tax register for export.

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
        org_id = coerce_uuid(organization_id)

        query = (
            db.query(TaxTransaction, TaxCode)
            .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date <= end_date,
            )
        )

        if tax_type:
            query = query.filter(TaxCode.tax_type == tax_type)

        if transaction_type:
            query = query.filter(TaxTransaction.transaction_type == transaction_type)

        results = (
            query.order_by(TaxTransaction.transaction_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

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


# Module-level singleton instance
tax_report_service = TaxReportService()
