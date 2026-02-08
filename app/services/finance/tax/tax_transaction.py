"""
TaxTransactionService - Tax transaction recording and management.

Records and manages VAT/GST input/output transactions, withholding taxes,
and tax payments/refunds.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.finance.tax.tax_code import TaxCode
from app.models.finance.tax.tax_transaction import TaxTransaction, TaxTransactionType
from app.services.common import coerce_uuid
from app.services.finance.platform.org_context import org_context_service
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class TaxTransactionInput:
    """Input for creating a tax transaction."""

    fiscal_period_id: UUID
    tax_code_id: UUID
    jurisdiction_id: UUID
    transaction_type: TaxTransactionType
    transaction_date: date
    source_document_type: str
    source_document_id: UUID
    currency_code: str
    base_amount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    functional_base_amount: Decimal
    functional_tax_amount: Decimal
    source_document_line_id: UUID | None = None
    source_document_reference: str | None = None
    counterparty_type: str | None = None
    counterparty_id: UUID | None = None
    counterparty_name: str | None = None
    counterparty_tax_id: str | None = None
    exchange_rate: Decimal | None = None
    recoverable_amount: Decimal = Decimal("0")
    non_recoverable_amount: Decimal = Decimal("0")
    tax_return_period: str | None = None
    tax_return_box: str | None = None


@dataclass
class TaxTransactionCreateInput:
    """Input for creating a tax transaction from external requests."""

    fiscal_period_id: UUID
    tax_code_id: UUID
    transaction_type: TaxTransactionType | None
    transaction_date: date
    source_document_type: str
    source_document_id: UUID
    base_amount: Decimal
    tax_amount: Decimal
    is_input_tax: bool | None = None
    source_document_line_id: UUID | None = None
    source_document_reference: str | None = None
    counterparty_type: str | None = None
    counterparty_id: UUID | None = None
    counterparty_name: str | None = None
    counterparty_tax_id: str | None = None
    currency_code: str | None = None
    exchange_rate: Decimal | None = None
    tax_return_period: str | None = None
    tax_return_box: str | None = None


@dataclass
class TaxReturnSummary:
    """Summary for tax return period."""

    period: str
    output_tax: Decimal
    input_tax_recoverable: Decimal
    input_tax_non_recoverable: Decimal
    withholding_tax: Decimal
    net_payable: Decimal
    transaction_count: int


@dataclass
class TaxByCodeSummary:
    """Summary of tax by tax code."""

    tax_code_id: UUID
    tax_code: str
    tax_name: str
    base_amount: Decimal
    tax_amount: Decimal
    transaction_count: int


class TaxTransactionService(ListResponseMixin):
    """
    Service for tax transaction management.

    Handles recording of VAT/GST transactions, withholding taxes,
    and tax return preparation.
    """

    @staticmethod
    def create_transaction(
        db: Session,
        organization_id: UUID,
        input: TaxTransactionInput,
    ) -> TaxTransaction:
        """
        Create a new tax transaction.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Transaction input data

        Returns:
            Created TaxTransaction
        """
        org_id = coerce_uuid(organization_id)

        # Validate tax code exists
        tax_code = db.get(TaxCode, input.tax_code_id)
        if not tax_code or tax_code.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Tax code not found")

        # Auto-populate tax return box from tax code if not provided
        tax_return_box = input.tax_return_box or tax_code.tax_return_box

        transaction = TaxTransaction(
            organization_id=org_id,
            fiscal_period_id=input.fiscal_period_id,
            tax_code_id=input.tax_code_id,
            jurisdiction_id=input.jurisdiction_id,
            transaction_type=input.transaction_type,
            transaction_date=input.transaction_date,
            source_document_type=input.source_document_type,
            source_document_id=input.source_document_id,
            source_document_line_id=input.source_document_line_id,
            source_document_reference=input.source_document_reference,
            counterparty_type=input.counterparty_type,
            counterparty_id=input.counterparty_id,
            counterparty_name=input.counterparty_name,
            counterparty_tax_id=input.counterparty_tax_id,
            currency_code=input.currency_code,
            base_amount=input.base_amount,
            tax_rate=input.tax_rate,
            tax_amount=input.tax_amount,
            exchange_rate=input.exchange_rate,
            functional_base_amount=input.functional_base_amount,
            functional_tax_amount=input.functional_tax_amount,
            recoverable_amount=input.recoverable_amount,
            non_recoverable_amount=input.non_recoverable_amount,
            tax_return_period=input.tax_return_period,
            tax_return_box=tax_return_box,
            is_included_in_return=False,
        )

        db.add(transaction)
        db.commit()
        db.refresh(transaction)

        return transaction

    @staticmethod
    def create_transaction_from_input(
        db: Session,
        organization_id: UUID,
        input: TaxTransactionCreateInput,
    ) -> TaxTransaction:
        """
        Create a tax transaction from external inputs.

        Computes transaction type defaults, functional amounts, and
        recoverable vs non-recoverable portions based on tax code settings.
        """
        org_id = coerce_uuid(organization_id)

        tax_code = db.get(TaxCode, input.tax_code_id)
        if not tax_code or tax_code.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Tax code not found")

        tx_type = input.transaction_type
        if tx_type is None:
            if input.is_input_tax is None:
                tx_type = TaxTransactionType.INPUT
            else:
                tx_type = (
                    TaxTransactionType.INPUT
                    if input.is_input_tax
                    else TaxTransactionType.OUTPUT
                )

        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_base = (input.base_amount * exchange_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        functional_tax = (input.tax_amount * exchange_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        recoverable = Decimal("0")
        non_recoverable = Decimal("0")
        if tx_type == TaxTransactionType.INPUT:
            if tax_code.is_recoverable:
                recoverable = (input.tax_amount * tax_code.recovery_rate).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                non_recoverable = input.tax_amount - recoverable
            else:
                non_recoverable = input.tax_amount

        currency_code = (
            input.currency_code
            or org_context_service.get_functional_currency(db, organization_id)
        )

        input_data = TaxTransactionInput(
            fiscal_period_id=input.fiscal_period_id,
            tax_code_id=input.tax_code_id,
            jurisdiction_id=tax_code.jurisdiction_id,
            transaction_type=tx_type,
            transaction_date=input.transaction_date,
            source_document_type=input.source_document_type,
            source_document_id=input.source_document_id,
            source_document_line_id=input.source_document_line_id,
            source_document_reference=input.source_document_reference,
            counterparty_type=input.counterparty_type,
            counterparty_id=input.counterparty_id,
            counterparty_name=input.counterparty_name,
            counterparty_tax_id=input.counterparty_tax_id,
            currency_code=currency_code,
            base_amount=input.base_amount,
            tax_rate=tax_code.tax_rate,
            tax_amount=input.tax_amount,
            exchange_rate=exchange_rate,
            functional_base_amount=functional_base,
            functional_tax_amount=functional_tax,
            recoverable_amount=recoverable,
            non_recoverable_amount=non_recoverable,
            tax_return_period=input.tax_return_period,
            tax_return_box=input.tax_return_box,
        )

        return TaxTransactionService.create_transaction(db, org_id, input_data)

    @staticmethod
    def create_from_invoice_line(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        tax_code_id: UUID,
        invoice_id: UUID,
        invoice_line_id: UUID,
        invoice_number: str,
        transaction_date: date,
        is_purchase: bool,
        base_amount: Decimal,
        currency_code: str,
        counterparty_name: str | None = None,
        counterparty_tax_id: str | None = None,
        exchange_rate: Decimal = Decimal("1.0"),
    ) -> TaxTransaction:
        """
        Create tax transaction from an invoice line.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period
            tax_code_id: Tax code to apply
            invoice_id: Source invoice ID
            invoice_line_id: Source invoice line ID
            invoice_number: Invoice reference
            transaction_date: Transaction date
            is_purchase: True for purchase (input), False for sale (output)
            base_amount: Amount to calculate tax on
            currency_code: Invoice currency code
            counterparty_name: Supplier/customer name
            counterparty_tax_id: Supplier/customer tax ID
            exchange_rate: Exchange rate to functional currency

        Returns:
            Created TaxTransaction
        """
        org_id = coerce_uuid(organization_id)

        # Get tax code
        tax_code = db.get(TaxCode, tax_code_id)
        if not tax_code or tax_code.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Tax code not found")

        # Calculate tax
        tax_amount = (base_amount * tax_code.tax_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Calculate recoverable/non-recoverable for input tax
        if is_purchase and tax_code.is_recoverable:
            recoverable = (tax_amount * tax_code.recovery_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            non_recoverable = tax_amount - recoverable
        elif is_purchase:
            recoverable = Decimal("0")
            non_recoverable = tax_amount
        else:
            recoverable = Decimal("0")
            non_recoverable = Decimal("0")

        # Functional currency amounts
        functional_base = (base_amount * exchange_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        functional_tax = (tax_amount * exchange_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        input_data = TaxTransactionInput(
            fiscal_period_id=fiscal_period_id,
            tax_code_id=tax_code_id,
            jurisdiction_id=tax_code.jurisdiction_id,
            transaction_type=TaxTransactionType.INPUT
            if is_purchase
            else TaxTransactionType.OUTPUT,
            transaction_date=transaction_date,
            source_document_type="AP_INVOICE" if is_purchase else "AR_INVOICE",
            source_document_id=invoice_id,
            source_document_line_id=invoice_line_id,
            source_document_reference=invoice_number,
            counterparty_type="SUPPLIER" if is_purchase else "CUSTOMER",
            counterparty_name=counterparty_name,
            counterparty_tax_id=counterparty_tax_id,
            currency_code=currency_code,
            base_amount=base_amount,
            tax_rate=tax_code.tax_rate,
            tax_amount=tax_amount,
            exchange_rate=exchange_rate,
            functional_base_amount=functional_base,
            functional_tax_amount=functional_tax,
            recoverable_amount=recoverable,
            non_recoverable_amount=non_recoverable,
        )

        return TaxTransactionService.create_transaction(db, organization_id, input_data)

    @staticmethod
    def mark_included_in_return(
        db: Session,
        organization_id: UUID,
        transaction_ids: list[UUID],
        return_period: str,
    ) -> int:
        """
        Mark transactions as included in a tax return.

        Args:
            db: Database session
            organization_id: Organization scope
            transaction_ids: Transaction IDs to mark
            return_period: Tax return period reference

        Returns:
            Number of transactions updated
        """
        org_id = coerce_uuid(organization_id)

        updated = 0
        for txn_id in transaction_ids:
            transaction = db.get(TaxTransaction, coerce_uuid(txn_id))
            if transaction and transaction.organization_id == org_id:
                transaction.is_included_in_return = True
                transaction.tax_return_period = return_period
                updated += 1

        db.commit()
        return updated

    @staticmethod
    def get_return_summary(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> TaxReturnSummary:
        """
        Get tax return summary for a period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period

        Returns:
            TaxReturnSummary with aggregated amounts
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Output tax
        output_result = (
            db.query(func.sum(TaxTransaction.functional_tax_amount))
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period_id,
                TaxTransaction.transaction_type == TaxTransactionType.OUTPUT,
            )
            .scalar()
        )
        output_tax = output_result or Decimal("0")

        # Input tax (recoverable)
        input_rec_result = (
            db.query(func.sum(TaxTransaction.recoverable_amount))
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period_id,
                TaxTransaction.transaction_type == TaxTransactionType.INPUT,
            )
            .scalar()
        )
        input_recoverable = input_rec_result or Decimal("0")

        # Input tax (non-recoverable)
        input_non_rec_result = (
            db.query(func.sum(TaxTransaction.non_recoverable_amount))
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period_id,
                TaxTransaction.transaction_type == TaxTransactionType.INPUT,
            )
            .scalar()
        )
        input_non_recoverable = input_non_rec_result or Decimal("0")

        # Withholding tax
        wht_result = (
            db.query(func.sum(TaxTransaction.functional_tax_amount))
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period_id,
                TaxTransaction.transaction_type == TaxTransactionType.WITHHOLDING,
            )
            .scalar()
        )
        withholding_tax = wht_result or Decimal("0")

        # Transaction count
        count_result = (
            db.query(func.count(TaxTransaction.transaction_id))
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period_id,
            )
            .scalar()
        )

        net_payable = output_tax - input_recoverable

        return TaxReturnSummary(
            period=str(period_id),
            output_tax=output_tax,
            input_tax_recoverable=input_recoverable,
            input_tax_non_recoverable=input_non_recoverable,
            withholding_tax=withholding_tax,
            net_payable=net_payable,
            transaction_count=count_result or 0,
        )

    @staticmethod
    def get_summary_by_tax_code(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        transaction_type: TaxTransactionType | None = None,
    ) -> list[TaxByCodeSummary]:
        """
        Get tax summary grouped by tax code.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period
            transaction_type: Filter by transaction type

        Returns:
            List of TaxByCodeSummary
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        query = (
            db.query(
                TaxTransaction.tax_code_id,
                TaxCode.tax_code,
                TaxCode.tax_name,
                func.sum(TaxTransaction.functional_base_amount).label("base_amount"),
                func.sum(TaxTransaction.functional_tax_amount).label("tax_amount"),
                func.count(TaxTransaction.transaction_id).label("txn_count"),
            )
            .join(TaxCode)
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period_id,
            )
            .group_by(
                TaxTransaction.tax_code_id,
                TaxCode.tax_code,
                TaxCode.tax_name,
            )
        )

        if transaction_type:
            query = query.filter(TaxTransaction.transaction_type == transaction_type)

        results = query.all()

        return [
            TaxByCodeSummary(
                tax_code_id=row.tax_code_id,
                tax_code=row.tax_code,
                tax_name=row.tax_name,
                base_amount=row.base_amount or Decimal("0"),
                tax_amount=row.tax_amount or Decimal("0"),
                transaction_count=row.txn_count,
            )
            for row in results
        ]

    @staticmethod
    def get_summary_by_return_box(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> dict[str, Decimal]:
        """
        Get tax amounts grouped by tax return box.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period

        Returns:
            Dict of return_box -> tax_amount
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        results = (
            db.query(
                TaxTransaction.tax_return_box,
                func.sum(TaxTransaction.functional_tax_amount).label("tax_amount"),
            )
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period_id,
                TaxTransaction.tax_return_box.isnot(None),
            )
            .group_by(TaxTransaction.tax_return_box)
            .all()
        )

        return {row.tax_return_box: row.tax_amount or Decimal("0") for row in results}

    @staticmethod
    def get(
        db: Session,
        transaction_id: str,
        organization_id: UUID | None = None,
    ) -> TaxTransaction:
        """Get a tax transaction by ID."""
        transaction = db.get(TaxTransaction, coerce_uuid(transaction_id))
        if not transaction:
            raise HTTPException(status_code=404, detail="Tax transaction not found")
        if organization_id is not None and transaction.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Tax transaction not found")
        return transaction

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        fiscal_period_id: str | None = None,
        tax_code_id: str | None = None,
        transaction_type: TaxTransactionType | None = None,
        is_included_in_return: bool | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[TaxTransaction]:
        """List tax transactions with optional filters."""
        query = db.query(TaxTransaction)

        if organization_id:
            query = query.filter(
                TaxTransaction.organization_id == coerce_uuid(organization_id)
            )

        if fiscal_period_id:
            query = query.filter(
                TaxTransaction.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if tax_code_id:
            query = query.filter(TaxTransaction.tax_code_id == coerce_uuid(tax_code_id))

        if transaction_type:
            query = query.filter(TaxTransaction.transaction_type == transaction_type)

        if is_included_in_return is not None:
            query = query.filter(
                TaxTransaction.is_included_in_return == is_included_in_return
            )

        if start_date:
            query = query.filter(TaxTransaction.transaction_date >= start_date)

        if end_date:
            query = query.filter(TaxTransaction.transaction_date <= end_date)

        query = query.order_by(TaxTransaction.transaction_date.desc())
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def get_unreported_transactions(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
    ) -> builtins.list[TaxTransaction]:
        """Get transactions not yet included in a tax return."""
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        return (
            db.query(TaxTransaction)
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.fiscal_period_id == period_id,
                TaxTransaction.is_included_in_return == False,
            )
            .order_by(TaxTransaction.transaction_date)
            .all()
        )

    @staticmethod
    def get_vat_register(
        db: Session,
        organization_id: str,
        start_date: date,
        end_date: date,
        transaction_type: TaxTransactionType | None = None,
        tax_code_id: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[builtins.list[dict[str, Any]], int]:
        """
        Get VAT register - detailed list of all tax transactions.

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Start of date range
            end_date: End of date range
            transaction_type: Filter by INPUT/OUTPUT/WITHHOLDING
            tax_code_id: Filter by specific tax code
            page: Page number (1-based)
            limit: Records per page

        Returns:
            Tuple of (list of transaction dicts, total count)
        """
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        # Base query with join to tax code
        query = (
            db.query(TaxTransaction, TaxCode)
            .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
            .filter(
                TaxTransaction.organization_id == org_id,
                TaxTransaction.transaction_date >= start_date,
                TaxTransaction.transaction_date <= end_date,
            )
        )

        if transaction_type:
            query = query.filter(TaxTransaction.transaction_type == transaction_type)

        if tax_code_id:
            query = query.filter(TaxTransaction.tax_code_id == coerce_uuid(tax_code_id))

        # Get total count
        total = query.count()

        # Get paginated results
        results = (
            query.order_by(TaxTransaction.transaction_date.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        transactions = []
        for txn, code in results:
            transactions.append(
                {
                    "transaction_id": str(txn.transaction_id),
                    "transaction_date": txn.transaction_date.isoformat(),
                    "transaction_type": txn.transaction_type.value,
                    "tax_code": code.tax_code,
                    "tax_name": code.tax_name,
                    "tax_rate": str(code.tax_rate),
                    "source_document_type": txn.source_document_type,
                    "source_document_id": str(txn.source_document_id)
                    if txn.source_document_id
                    else None,
                    "source_document_reference": txn.source_document_reference,
                    "counterparty_name": txn.counterparty_name,
                    "counterparty_tax_id": txn.counterparty_tax_id,
                    "base_amount": str(txn.base_amount),
                    "tax_amount": str(txn.tax_amount),
                    "functional_tax_amount": str(txn.functional_tax_amount),
                    "recoverable_amount": str(txn.recoverable_amount)
                    if txn.recoverable_amount
                    else "0",
                    "non_recoverable_amount": str(txn.non_recoverable_amount)
                    if txn.non_recoverable_amount
                    else "0",
                    "currency_code": txn.currency_code,
                    "tax_return_box": txn.tax_return_box,
                    "is_included_in_return": txn.is_included_in_return,
                }
            )

        return transactions, total

    @staticmethod
    def get_tax_liability_summary(
        db: Session,
        organization_id: str,
        start_date: date,
        end_date: date,
        group_by: str = "period",
    ) -> builtins.list[dict[str, Any]]:
        """
        Get tax liability summary (Output Tax - Input Tax = Net Payable).

        Args:
            db: Database session
            organization_id: Organization scope
            start_date: Start of date range
            end_date: End of date range
            group_by: Grouping ('period', 'tax_code', 'month')

        Returns:
            List of summary dicts with output_tax, input_tax, net_payable
        """

        org_id = coerce_uuid(organization_id)

        if group_by == "month":
            period_expr = func.date_trunc("month", TaxTransaction.transaction_date)
            # Group by year-month
            results = (
                db.query(
                    period_expr.label("period"),
                    TaxTransaction.transaction_type,
                    func.sum(TaxTransaction.functional_tax_amount).label("tax_amount"),
                    func.sum(TaxTransaction.recoverable_amount).label("recoverable"),
                )
                .filter(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.transaction_date >= start_date,
                    TaxTransaction.transaction_date <= end_date,
                )
                .group_by(
                    period_expr,
                    TaxTransaction.transaction_type,
                )
                .order_by(period_expr)
                .all()
            )

            # Aggregate by period
            period_data: dict = {}
            for row in results:
                period_key = row.period.strftime("%Y-%m") if row.period else "Unknown"
                if period_key not in period_data:
                    period_data[period_key] = {
                        "period": period_key,
                        "output_tax": Decimal("0"),
                        "input_tax": Decimal("0"),
                        "input_tax_recoverable": Decimal("0"),
                        "net_payable": Decimal("0"),
                    }

                if row.transaction_type == TaxTransactionType.OUTPUT:
                    period_data[period_key]["output_tax"] += row.tax_amount or Decimal(
                        "0"
                    )
                elif row.transaction_type == TaxTransactionType.INPUT:
                    period_data[period_key]["input_tax"] += row.tax_amount or Decimal(
                        "0"
                    )
                    period_data[period_key]["input_tax_recoverable"] += (
                        row.recoverable or Decimal("0")
                    )

            # Calculate net payable
            for data in period_data.values():
                data["net_payable"] = data["output_tax"] - data["input_tax_recoverable"]
                # Convert decimals to strings for JSON
                data["output_tax"] = str(data["output_tax"])
                data["input_tax"] = str(data["input_tax"])
                data["input_tax_recoverable"] = str(data["input_tax_recoverable"])
                data["net_payable"] = str(data["net_payable"])

            return list(period_data.values())

        elif group_by == "tax_code":
            # Group by tax code
            results = (
                db.query(
                    TaxCode.tax_code,
                    TaxCode.tax_name,
                    TaxTransaction.transaction_type,
                    func.sum(TaxTransaction.functional_tax_amount).label("tax_amount"),
                    func.sum(TaxTransaction.recoverable_amount).label("recoverable"),
                )
                .join(TaxCode, TaxTransaction.tax_code_id == TaxCode.tax_code_id)
                .filter(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.transaction_date >= start_date,
                    TaxTransaction.transaction_date <= end_date,
                )
                .group_by(
                    TaxCode.tax_code,
                    TaxCode.tax_name,
                    TaxTransaction.transaction_type,
                )
                .order_by(TaxCode.tax_code)
                .all()
            )

            # Aggregate by tax code
            code_data: dict = {}
            for row in results:
                code_key = row.tax_code
                if code_key not in code_data:
                    code_data[code_key] = {
                        "tax_code": row.tax_code,
                        "tax_name": row.tax_name,
                        "output_tax": Decimal("0"),
                        "input_tax": Decimal("0"),
                        "input_tax_recoverable": Decimal("0"),
                        "net_payable": Decimal("0"),
                    }

                if row.transaction_type == TaxTransactionType.OUTPUT:
                    code_data[code_key]["output_tax"] += row.tax_amount or Decimal("0")
                elif row.transaction_type == TaxTransactionType.INPUT:
                    code_data[code_key]["input_tax"] += row.tax_amount or Decimal("0")
                    code_data[code_key]["input_tax_recoverable"] += (
                        row.recoverable or Decimal("0")
                    )

            # Calculate net payable and convert to strings
            for data in code_data.values():
                data["net_payable"] = data["output_tax"] - data["input_tax_recoverable"]
                data["output_tax"] = str(data["output_tax"])
                data["input_tax"] = str(data["input_tax"])
                data["input_tax_recoverable"] = str(data["input_tax_recoverable"])
                data["net_payable"] = str(data["net_payable"])

            return list(code_data.values())

        else:
            # Default: overall summary for the period
            output_result = (
                db.query(func.sum(TaxTransaction.functional_tax_amount))
                .filter(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.transaction_date >= start_date,
                    TaxTransaction.transaction_date <= end_date,
                    TaxTransaction.transaction_type == TaxTransactionType.OUTPUT,
                )
                .scalar()
            ) or Decimal("0")

            input_result = (
                db.query(func.sum(TaxTransaction.functional_tax_amount))
                .filter(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.transaction_date >= start_date,
                    TaxTransaction.transaction_date <= end_date,
                    TaxTransaction.transaction_type == TaxTransactionType.INPUT,
                )
                .scalar()
            ) or Decimal("0")

            recoverable_result = (
                db.query(func.sum(TaxTransaction.recoverable_amount))
                .filter(
                    TaxTransaction.organization_id == org_id,
                    TaxTransaction.transaction_date >= start_date,
                    TaxTransaction.transaction_date <= end_date,
                    TaxTransaction.transaction_type == TaxTransactionType.INPUT,
                )
                .scalar()
            ) or Decimal("0")

            net_payable = output_result - recoverable_result

            return [
                {
                    "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
                    "output_tax": str(output_result),
                    "input_tax": str(input_result),
                    "input_tax_recoverable": str(recoverable_result),
                    "net_payable": str(net_payable),
                }
            ]


# Module-level singleton instance
tax_transaction_service = TaxTransactionService()
