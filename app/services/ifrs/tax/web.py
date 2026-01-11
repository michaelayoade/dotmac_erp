"""
Tax web view service.

Provides view-focused data for tax web routes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ifrs.tax.tax_return import TaxReturn
from app.models.ifrs.tax.tax_transaction import TaxTransaction, TaxTransactionType
from app.services.common import coerce_uuid
from app.services.ifrs.tax.tax_return import TaxReturnBoxValue, tax_return_service
from app.services.ifrs.tax.tax_transaction import tax_transaction_service


def _format_date(value: Optional[date]) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _format_currency(amount: Optional[Decimal], currency: str = "USD") -> str:
    if amount is None:
        return ""
    value = Decimal(str(amount))
    if currency == "USD":
        return f"${value:,.2f}"
    return f"{currency} {value:,.2f}"


def _tax_return_view(tax_return: TaxReturn) -> dict:
    return {
        "return_id": tax_return.return_id,
        "tax_period_id": tax_return.tax_period_id,
        "jurisdiction_id": tax_return.jurisdiction_id,
        "return_type": tax_return.return_type.value,
        "return_reference": tax_return.return_reference,
        "status": tax_return.status.value,
        "total_output_tax": _format_currency(tax_return.total_output_tax),
        "total_input_tax": _format_currency(tax_return.total_input_tax),
        "net_tax_payable": _format_currency(tax_return.net_tax_payable),
        "adjustments": _format_currency(tax_return.adjustments),
        "final_amount": _format_currency(tax_return.final_amount),
        "filed_date": _format_date(tax_return.filed_date),
        "filing_reference": tax_return.filing_reference,
        "is_paid": tax_return.is_paid,
        "payment_date": _format_date(tax_return.payment_date),
        "payment_reference": tax_return.payment_reference,
        "is_amendment": tax_return.is_amendment,
        "original_return_id": tax_return.original_return_id,
        "amendment_reason": tax_return.amendment_reason,
        "prepared_at": _format_date(
            tax_return.prepared_at.date() if tax_return.prepared_at else None
        ),
        "reviewed_at": _format_date(
            tax_return.reviewed_at.date() if tax_return.reviewed_at else None
        ),
    }


def _box_value_view(box_value: TaxReturnBoxValue) -> dict:
    return {
        "box_number": box_value.box_number,
        "description": box_value.description,
        "amount": _format_currency(box_value.amount),
        "transaction_count": box_value.transaction_count,
    }


def _tax_transaction_view(txn: TaxTransaction) -> dict:
    """Format a tax transaction for web view."""
    return {
        "transaction_id": str(txn.transaction_id),
        "transaction_date": _format_date(txn.transaction_date),
        "transaction_type": txn.transaction_type.value,
        "tax_code_id": str(txn.tax_code_id),
        "source_document_type": txn.source_document_type,
        "source_document_id": str(txn.source_document_id),
        "source_document_reference": txn.source_document_reference or "",
        "counterparty_name": txn.counterparty_name or "",
        "counterparty_tax_id": txn.counterparty_tax_id or "",
        "currency_code": txn.currency_code,
        "base_amount": _format_currency(txn.base_amount, txn.currency_code),
        "base_amount_raw": float(txn.base_amount),
        "tax_rate": f"{txn.tax_rate * 100:.2f}%",
        "tax_rate_raw": float(txn.tax_rate),
        "tax_amount": _format_currency(txn.tax_amount, txn.currency_code),
        "tax_amount_raw": float(txn.tax_amount),
        "functional_base_amount": _format_currency(txn.functional_base_amount),
        "functional_tax_amount": _format_currency(txn.functional_tax_amount),
        "functional_tax_amount_raw": float(txn.functional_tax_amount),
        "recoverable_amount": _format_currency(txn.recoverable_amount),
        "non_recoverable_amount": _format_currency(txn.non_recoverable_amount),
        "is_included_in_return": txn.is_included_in_return,
        "tax_return_period": txn.tax_return_period or "",
    }


class TaxWebService:
    """View service for tax web routes."""

    @staticmethod
    def return_detail_context(
        db: Session,
        organization_id: str,
        return_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        tax_return = tax_return_service.get(db, return_id)

        if not tax_return or tax_return.organization_id != org_id:
            return {"tax_return": None, "box_values": []}

        box_values = tax_return_service.get_box_values(
            db,
            organization_id=org_id,
            return_id=tax_return.return_id,
        )

        return {
            "tax_return": _tax_return_view(tax_return),
            "box_values": [_box_value_view(box) for box in box_values],
        }

    @staticmethod
    def vat_register_context(
        db: Session,
        organization_id: str,
        start_date: date,
        end_date: date,
        transaction_type: Optional[str] = None,
        tax_code_id: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """
        Get context for VAT register page.

        Returns paginated list of tax transactions with filters.
        """
        org_id = coerce_uuid(organization_id)

        # Convert transaction type string to enum if provided
        txn_type = None
        if transaction_type:
            try:
                txn_type = TaxTransactionType(transaction_type)
            except ValueError:
                pass

        transactions, total_count = tax_transaction_service.get_vat_register(
            db=db,
            organization_id=str(org_id),
            start_date=start_date,
            end_date=end_date,
            transaction_type=txn_type,
            tax_code_id=tax_code_id,
            page=page,
            limit=limit,
        )

        # Calculate totals for the current filter
        total_output = sum(
            t["functional_tax_amount"]
            for t in transactions
            if t["transaction_type"] == "OUTPUT"
        )
        total_input = sum(
            t["functional_tax_amount"]
            for t in transactions
            if t["transaction_type"] == "INPUT"
        )

        # Calculate pagination
        total_pages = (total_count + limit - 1) // limit if limit > 0 else 1

        return {
            "transactions": transactions,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages,
            "start_date": _format_date(start_date),
            "end_date": _format_date(end_date),
            "transaction_type": transaction_type or "",
            "tax_code_id": tax_code_id or "",
            "summary": {
                "total_output_tax": _format_currency(Decimal(str(total_output))),
                "total_input_tax": _format_currency(Decimal(str(total_input))),
                "net_tax": _format_currency(Decimal(str(total_output - total_input))),
            },
            "transaction_types": [
                {"value": t.value, "label": t.value.replace("_", " ").title()}
                for t in TaxTransactionType
            ],
        }

    @staticmethod
    def tax_liability_context(
        db: Session,
        organization_id: str,
        start_date: date,
        end_date: date,
        group_by: str = "month",
    ) -> dict:
        """
        Get context for tax liability summary page.

        Returns aggregated tax data grouped by period, tax_code, or month.
        """
        org_id = coerce_uuid(organization_id)

        summary_data = tax_transaction_service.get_tax_liability_summary(
            db=db,
            organization_id=str(org_id),
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
        )

        # Calculate grand totals
        grand_output = sum(row.get("output_tax", 0) for row in summary_data)
        grand_input = sum(row.get("input_tax", 0) for row in summary_data)
        grand_net = sum(row.get("net_payable", 0) for row in summary_data)

        # Format amounts for display
        formatted_data = []
        for row in summary_data:
            formatted_row = {
                "group_key": row.get("group_key", ""),
                "output_tax": _format_currency(Decimal(str(row.get("output_tax", 0)))),
                "output_tax_raw": row.get("output_tax", 0),
                "input_tax": _format_currency(Decimal(str(row.get("input_tax", 0)))),
                "input_tax_raw": row.get("input_tax", 0),
                "net_payable": _format_currency(Decimal(str(row.get("net_payable", 0)))),
                "net_payable_raw": row.get("net_payable", 0),
                "transaction_count": row.get("transaction_count", 0),
            }
            formatted_data.append(formatted_row)

        return {
            "summary_data": formatted_data,
            "start_date": _format_date(start_date),
            "end_date": _format_date(end_date),
            "group_by": group_by,
            "grand_totals": {
                "output_tax": _format_currency(Decimal(str(grand_output))),
                "output_tax_raw": grand_output,
                "input_tax": _format_currency(Decimal(str(grand_input))),
                "input_tax_raw": grand_input,
                "net_payable": _format_currency(Decimal(str(grand_net))),
                "net_payable_raw": grand_net,
            },
            "group_options": [
                {"value": "month", "label": "By Month"},
                {"value": "tax_code", "label": "By Tax Code"},
                {"value": "period", "label": "By Fiscal Period"},
            ],
        }

    @staticmethod
    def transaction_detail_context(
        db: Session,
        organization_id: str,
        transaction_id: str,
    ) -> dict:
        """
        Get context for single tax transaction detail page.
        """
        org_id = coerce_uuid(organization_id)
        txn_id = coerce_uuid(transaction_id)

        transaction = tax_transaction_service.get(db, str(txn_id))

        if not transaction or transaction.organization_id != org_id:
            return {"transaction": None}

        # Build source document link based on type
        source_link = None
        if transaction.source_document_type == "INVOICE":
            source_link = f"/ifrs/ar/invoices/{transaction.source_document_id}"
        elif transaction.source_document_type == "SUPPLIER_INVOICE":
            source_link = f"/ifrs/ap/invoices/{transaction.source_document_id}"

        txn_view = _tax_transaction_view(transaction)
        txn_view["source_document_link"] = source_link

        return {
            "transaction": txn_view,
        }

    @staticmethod
    def return_transactions_context(
        db: Session,
        organization_id: str,
        return_id: str,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """
        Get context for return transactions page.

        Shows all transactions included in a specific tax return.
        """
        from app.services.ifrs.tax.tax_return import tax_return_service

        org_id = coerce_uuid(organization_id)

        # Get return details
        tax_return = tax_return_service.get(db, return_id)
        if not tax_return or tax_return.organization_id != org_id:
            return {
                "tax_return": None,
                "transactions": [],
                "total_count": 0,
            }

        # Get transactions
        transactions, total_count = tax_return_service.get_return_transactions(
            db=db,
            organization_id=org_id,
            return_id=tax_return.return_id,
            page=page,
            limit=limit,
        )

        # Format transactions for display
        formatted_txns = []
        for txn in transactions:
            formatted_txns.append({
                "transaction_id": str(txn.transaction_id),
                "transaction_date": _format_date(txn.transaction_date),
                "transaction_type": txn.transaction_type.value,
                "source_document_reference": txn.source_document_reference or "",
                "counterparty_name": txn.counterparty_name or "",
                "base_amount": _format_currency(txn.base_amount, txn.currency_code),
                "tax_amount": _format_currency(txn.tax_amount, txn.currency_code),
                "functional_tax_amount": _format_currency(txn.functional_tax_amount),
            })

        # Calculate pagination
        total_pages = (total_count + limit - 1) // limit if limit > 0 else 1

        return {
            "tax_return": _tax_return_view(tax_return),
            "transactions": formatted_txns,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages,
        }


tax_web_service = TaxWebService()
