"""
Tax web view service.

Provides view-focused data for tax web routes.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.models.finance.tax.tax_period import TaxPeriodFrequency, TaxPeriodStatus
from app.models.finance.tax.tax_return import TaxReturn, TaxReturnStatus
from app.models.finance.tax.tax_transaction import TaxTransaction, TaxTransactionType
from app.services.common import coerce_uuid
from app.services.finance.platform.currency_context import get_currency_context
from app.services.formatters import format_currency as _format_currency
from app.services.formatters import format_date as _format_date
from app.services.finance.tax import (
    deferred_tax_service,
    tax_code_service,
    tax_jurisdiction_service,
    tax_period_service,
)
from app.services.finance.tax.seed import get_default_jurisdiction
from app.services.finance.tax.tax_master import TaxCodeInput
from app.services.finance.tax.tax_return import TaxReturnBoxValue, tax_return_service
from app.services.finance.tax.tax_transaction import tax_transaction_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def _safe_form_text(value: object) -> str:
    """Normalize form values to text for safe parsing."""
    if value is None:
        return ""
    if isinstance(value, UploadFile):
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _get_accounts(
    db: Session,
    organization_id: UUID,
    ifrs_category: IFRSCategory,
) -> list[Account]:
    """Get GL accounts by IFRS category for dropdowns."""
    return (
        db.query(Account)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .filter(
            Account.organization_id == organization_id,
            Account.is_active.is_(True),
            AccountCategory.ifrs_category == ifrs_category,
        )
        .order_by(Account.account_code)
        .all()
    )


def _tax_code_form_view(tax_code: TaxCode) -> dict:
    """Format tax code for form editing."""
    return {
        "tax_code_id": tax_code.tax_code_id,
        "tax_code": tax_code.tax_code,
        "tax_name": tax_code.tax_name,
        "description": tax_code.description,
        "tax_type": tax_code.tax_type,
        "jurisdiction_id": tax_code.jurisdiction_id,
        "tax_rate": tax_code.tax_rate,
        "effective_from": tax_code.effective_from,
        "effective_to": tax_code.effective_to,
        "is_compound": tax_code.is_compound,
        "is_inclusive": tax_code.is_inclusive,
        "is_recoverable": tax_code.is_recoverable,
        "recovery_rate": tax_code.recovery_rate,
        "applies_to_purchases": tax_code.applies_to_purchases,
        "applies_to_sales": tax_code.applies_to_sales,
        "tax_return_box": tax_code.tax_return_box,
        "reporting_code": tax_code.reporting_code,
        "tax_collected_account_id": tax_code.tax_collected_account_id,
        "tax_paid_account_id": tax_code.tax_paid_account_id,
        "tax_expense_account_id": tax_code.tax_expense_account_id,
        "is_active": tax_code.is_active,
    }


def _tax_code_list_view(tax_code: TaxCode) -> dict:
    """Format tax code for list display."""
    # Format rate display
    if tax_code.tax_rate < 1:
        rate_display = f"{tax_code.tax_rate * 100:.2f}%"
    else:
        rate_display = f"₦{tax_code.tax_rate:,.2f}"

    return {
        "tax_code_id": tax_code.tax_code_id,
        "tax_code": tax_code.tax_code,
        "tax_name": tax_code.tax_name,
        "tax_type": tax_code.tax_type.value.replace("_", " ").title(),
        "tax_rate": rate_display,
        "applies_to_sales": tax_code.applies_to_sales,
        "applies_to_purchases": tax_code.applies_to_purchases,
        "is_active": tax_code.is_active,
        "effective_from": _format_date(tax_code.effective_from),
        "effective_to": _format_date(tax_code.effective_to),
    }


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
                "net_payable": _format_currency(
                    Decimal(str(row.get("net_payable", 0)))
                ),
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
            source_link = f"/finance/ar/invoices/{transaction.source_document_id}"
        elif transaction.source_document_type == "SUPPLIER_INVOICE":
            source_link = f"/finance/ap/invoices/{transaction.source_document_id}"

        txn_view = _tax_transaction_view(transaction)
        txn_view["source_document_link"] = source_link

        context = {
            "transaction": txn_view,
        }
        context.update(get_currency_context(db, organization_id))
        return context

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
        from app.services.finance.tax.tax_return import tax_return_service

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
            formatted_txns.append(
                {
                    "transaction_id": str(txn.transaction_id),
                    "transaction_date": _format_date(txn.transaction_date),
                    "transaction_type": txn.transaction_type.value,
                    "source_document_reference": txn.source_document_reference or "",
                    "counterparty_name": txn.counterparty_name or "",
                    "base_amount": _format_currency(txn.base_amount, txn.currency_code),
                    "tax_amount": _format_currency(txn.tax_amount, txn.currency_code),
                    "functional_tax_amount": _format_currency(
                        txn.functional_tax_amount
                    ),
                }
            )

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

    def list_jurisdictions_response(
        self,
        request: Request,
        auth: WebAuthContext,
        country_code: Optional[str],
        page: int,
        db: Session,
    ) -> HTMLResponse:
        limit = 50
        offset = (page - 1) * limit

        jurisdictions = tax_jurisdiction_service.list(
            db=db,
            organization_id=str(auth.organization_id),
            country_code=country_code,
            limit=limit,
            offset=offset,
        )

        context = base_context(request, auth, "Tax Jurisdictions", "tax")
        context.update(
            {
                "jurisdictions": jurisdictions,
                "country_code": country_code,
                "page": page,
            }
        )

        return templates.TemplateResponse(
            request, "finance/tax/jurisdictions.html", context
        )

    def list_tax_codes_response(
        self,
        request: Request,
        auth: WebAuthContext,
        tax_type: Optional[str],
        jurisdiction_id: Optional[str],
        page: int,
        db: Session,
        is_active: Optional[bool] = None,
    ) -> HTMLResponse:
        limit = 50
        offset = (page - 1) * limit

        # Convert tax_type string to enum if provided
        tax_type_enum = None
        if tax_type:
            try:
                tax_type_enum = TaxType(tax_type)
            except ValueError:
                pass

        codes = tax_code_service.list(
            db=db,
            organization_id=str(auth.organization_id),
            tax_type=tax_type_enum,
            jurisdiction_id=jurisdiction_id,
            is_active=is_active,
            limit=limit,
            offset=offset,
        )

        # Format codes for display
        formatted_codes = [_tax_code_list_view(code) for code in codes]

        # Get filter options
        jurisdictions = tax_jurisdiction_service.list(
            db=db,
            organization_id=str(auth.organization_id),
            is_active=True,
            limit=100,
        )

        context = base_context(request, auth, "Tax Codes", "tax")
        context.update(
            {
                "codes": formatted_codes,
                "tax_type": tax_type,
                "jurisdiction_id": jurisdiction_id,
                "is_active": "true"
                if is_active is True
                else ("false" if is_active is False else ""),
                "page": page,
                "tax_types": list(TaxType),
                "tax_type_options": [
                    {"value": t.value, "label": t.value.replace("_", " ").title()}
                    for t in TaxType
                ],
                "jurisdictions": jurisdictions,
            }
        )

        return templates.TemplateResponse(request, "finance/tax/codes.html", context)

    def list_tax_periods_response(
        self,
        request: Request,
        auth: WebAuthContext,
        jurisdiction_id: Optional[str],
        frequency: Optional[str],
        status: Optional[str],
        year: Optional[int],
        page: int,
        db: Session,
    ) -> HTMLResponse:
        limit = 50
        offset = (page - 1) * limit

        status_value = None
        if status:
            try:
                status_value = TaxPeriodStatus(status)
            except ValueError:
                status_value = None

        frequency_value = None
        if frequency:
            try:
                frequency_value = TaxPeriodFrequency(frequency)
            except ValueError:
                frequency_value = None

        periods = tax_period_service.list(
            db=db,
            organization_id=str(auth.organization_id),
            jurisdiction_id=jurisdiction_id,
            status=status_value,
            frequency=frequency_value,
            year=year,
            limit=limit,
            offset=offset,
        )

        context = base_context(request, auth, "Tax Periods", "tax")
        context.update(
            {
                "periods": periods,
                "jurisdiction_id": jurisdiction_id,
                "frequency": frequency,
                "status": status,
                "year": year,
                "page": page,
            }
        )

        return templates.TemplateResponse(request, "finance/tax/periods.html", context)

    def overdue_periods_response(
        self,
        request: Request,
        auth: WebAuthContext,
        as_of_date: Optional[str],
        db: Session,
    ) -> HTMLResponse:
        check_date = date.fromisoformat(as_of_date) if as_of_date else None
        org_id = coerce_uuid(auth.organization_id)
        overdue = tax_period_service.get_overdue_periods(db, org_id, check_date)

        context = base_context(request, auth, "Overdue Tax Periods", "tax")
        context["overdue_periods"] = overdue
        context["as_of_date"] = as_of_date

        return templates.TemplateResponse(
            request, "finance/tax/overdue_periods.html", context
        )

    def list_tax_returns_response(
        self,
        request: Request,
        auth: WebAuthContext,
        period_id: Optional[str],
        status: Optional[str],
        page: int,
        db: Session,
    ) -> HTMLResponse:
        limit = 50
        offset = (page - 1) * limit

        org_id = coerce_uuid(auth.organization_id)
        status_value: Optional[TaxReturnStatus] = None
        if status:
            try:
                status_value = TaxReturnStatus(status)
            except ValueError:
                status_value = None

        returns = tax_return_service.list(
            db=db,
            organization_id=org_id,
            tax_period_id=period_id,
            status=status_value,
            limit=limit,
            offset=offset,
        )

        context = base_context(request, auth, "Tax Returns", "tax")
        context.update(
            {
                "returns": returns,
                "period_id": period_id,
                "status": status,
                "page": page,
            }
        )

        return templates.TemplateResponse(request, "finance/tax/returns.html", context)

    def view_tax_return_response(
        self,
        request: Request,
        auth: WebAuthContext,
        return_id: str,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Tax Return Details", "tax")
        context.update(
            self.return_detail_context(
                db,
                str(auth.organization_id),
                return_id,
            )
        )

        return templates.TemplateResponse(
            request, "finance/tax/return_detail.html", context
        )

    def new_return_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        periods = tax_period_service.list(
            db=db,
            organization_id=str(auth.organization_id),
            status=TaxPeriodStatus.OPEN,
            limit=100,
        )

        context = base_context(request, auth, "Prepare Tax Return", "tax")
        context["periods"] = periods

        return templates.TemplateResponse(
            request, "finance/tax/return_form.html", context
        )

    def deferred_tax_summary_response(
        self,
        request: Request,
        auth: WebAuthContext,
        as_of_date: Optional[str],
        db: Session,
    ) -> HTMLResponse:
        check_date = as_of_date or date.today().isoformat()

        org_id = coerce_uuid(auth.organization_id)
        summary = deferred_tax_service.get_summary(
            db=db,
            organization_id=org_id,
        )

        context = base_context(request, auth, "Deferred Tax Summary", "tax")
        context["summary"] = summary
        context["as_of_date"] = check_date

        return templates.TemplateResponse(request, "finance/tax/deferred.html", context)

    def vat_register_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: Optional[str],
        end_date: Optional[str],
        transaction_type: Optional[str],
        tax_code_id: Optional[str],
        page: int,
        db: Session,
    ) -> HTMLResponse:
        today = date.today()
        if not start_date:
            start = today.replace(day=1)
        else:
            start = date.fromisoformat(start_date)

        if not end_date:
            next_month = today.replace(day=28) + timedelta(days=4)
            end = next_month.replace(day=1) - timedelta(days=1)
        else:
            end = date.fromisoformat(end_date)

        context = base_context(request, auth, "VAT Register", "tax")
        context.update(
            self.vat_register_context(
                db=db,
                organization_id=str(auth.organization_id),
                start_date=start,
                end_date=end,
                transaction_type=transaction_type,
                tax_code_id=tax_code_id,
                page=page,
                limit=50,
            )
        )

        return templates.TemplateResponse(
            request, "finance/tax/vat_register.html", context
        )

    def tax_liability_summary_response(
        self,
        request: Request,
        auth: WebAuthContext,
        start_date: Optional[str],
        end_date: Optional[str],
        group_by: str,
        db: Session,
    ) -> HTMLResponse:
        today = date.today()
        if not start_date:
            start = (today.replace(day=1) - timedelta(days=365)).replace(day=1)
        else:
            start = date.fromisoformat(start_date)

        if not end_date:
            end = today
        else:
            end = date.fromisoformat(end_date)

        context = base_context(request, auth, "Tax Liability Summary", "tax")
        context.update(
            self.tax_liability_context(
                db=db,
                organization_id=str(auth.organization_id),
                start_date=start,
                end_date=end,
                group_by=group_by,
            )
        )

        return templates.TemplateResponse(
            request, "finance/tax/liability_summary.html", context
        )

    def view_tax_transaction_response(
        self,
        request: Request,
        auth: WebAuthContext,
        transaction_id: str,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Tax Transaction Details", "tax")
        context.update(
            self.transaction_detail_context(
                db=db,
                organization_id=str(auth.organization_id),
                transaction_id=transaction_id,
            )
        )

        return templates.TemplateResponse(
            request, "finance/tax/transaction_detail.html", context
        )

    def return_transactions_response(
        self,
        request: Request,
        auth: WebAuthContext,
        return_id: str,
        page: int,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Return Transactions", "tax")
        context.update(
            self.return_transactions_context(
                db=db,
                organization_id=str(auth.organization_id),
                return_id=return_id,
                page=page,
            )
        )

        return templates.TemplateResponse(
            request, "finance/tax/return_transactions.html", context
        )

    def recalculate_return_response(
        self,
        return_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        try:
            org_id = coerce_uuid(auth.organization_id)
            tax_return_service.recalculate(
                db=db,
                organization_id=org_id,
                return_id=coerce_uuid(return_id),
            )
        except Exception:
            pass

        return RedirectResponse(
            url=f"/tax/returns/{return_id}",
            status_code=303,
        )

    def review_return_response(
        self,
        return_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        try:
            if not auth.person_id:
                return RedirectResponse(
                    url=f"/tax/returns/{return_id}",
                    status_code=303,
                )
            org_id = coerce_uuid(auth.organization_id)
            tax_return_service.review_return(
                db=db,
                organization_id=org_id,
                return_id=coerce_uuid(return_id),
                reviewed_by_user_id=coerce_uuid(auth.person_id),
            )
        except Exception:
            pass

        return RedirectResponse(
            url=f"/tax/returns/{return_id}",
            status_code=303,
        )

    def file_return_response(
        self,
        return_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        try:
            if not auth.person_id:
                return RedirectResponse(
                    url=f"/tax/returns/{return_id}",
                    status_code=303,
                )
            org_id = coerce_uuid(auth.organization_id)
            tax_return_service.file_return(
                db=db,
                organization_id=org_id,
                return_id=coerce_uuid(return_id),
                filed_by_user_id=coerce_uuid(auth.person_id),
            )
        except Exception:
            pass

        return RedirectResponse(
            url=f"/tax/returns/{return_id}",
            status_code=303,
        )

    # ============================================================
    # Tax Code CRUD Methods
    # ============================================================

    def _get_tax_code_form_context(
        self,
        db: Session,
        auth: WebAuthContext,
        tax_code: Optional[TaxCode] = None,
        error: Optional[str] = None,
    ) -> dict:
        """Get common context for tax code form."""
        org_id = coerce_uuid(auth.organization_id)

        # Get jurisdictions for dropdown
        jurisdictions = tax_jurisdiction_service.list(
            db=db,
            organization_id=str(org_id),
            is_active=True,
            limit=100,
        )

        # Get default jurisdiction for pre-selection (new forms only)
        default_jurisdiction_id = None
        if tax_code is None:
            default_jurisdiction = get_default_jurisdiction(db, org_id)
            if default_jurisdiction:
                default_jurisdiction_id = str(default_jurisdiction.jurisdiction_id)

        # Get GL accounts by category
        liability_accounts = _get_accounts(db, org_id, IFRSCategory.LIABILITIES)
        asset_accounts = _get_accounts(db, org_id, IFRSCategory.ASSETS)
        expense_accounts = _get_accounts(db, org_id, IFRSCategory.EXPENSES)

        return {
            "tax_code": _tax_code_form_view(tax_code) if tax_code else None,
            "tax_types": list(TaxType),
            "jurisdictions": jurisdictions,
            "default_jurisdiction_id": default_jurisdiction_id,
            "liability_accounts": liability_accounts,
            "asset_accounts": asset_accounts,
            "expense_accounts": expense_accounts,
            "today": date.today().isoformat(),
            "error": error,
        }

    def new_tax_code_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: Optional[str] = None,
    ) -> HTMLResponse:
        """Display new tax code form."""
        context = base_context(request, auth, "New Tax Code", "tax")
        context.update(self._get_tax_code_form_context(db, auth, error=error))

        return templates.TemplateResponse(
            request, "finance/tax/code_form.html", context
        )

    async def create_tax_code_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle new tax code form submission."""
        form = await request.form()
        org_id = coerce_uuid(auth.organization_id)

        try:
            # Parse form data
            tax_code_str = _safe_form_text(form.get("tax_code")).strip()
            tax_name = _safe_form_text(form.get("tax_name")).strip()
            tax_type_str = _safe_form_text(form.get("tax_type"))
            jurisdiction_id_str = _safe_form_text(form.get("jurisdiction_id"))
            description = _safe_form_text(form.get("description")).strip() or None

            # Parse rate based on rate type
            rate_type = _safe_form_text(form.get("rate_type", "percentage"))
            if rate_type == "percentage":
                rate_percentage = _safe_form_text(form.get("tax_rate_percentage", "0"))
                tax_rate = Decimal(rate_percentage) / Decimal("100")
            else:
                rate_fixed = _safe_form_text(form.get("tax_rate_fixed", "0"))
                tax_rate = Decimal(rate_fixed)

            # Parse dates
            effective_from_str = _safe_form_text(form.get("effective_from"))
            effective_from = (
                date.fromisoformat(effective_from_str)
                if effective_from_str
                else date.today()
            )

            effective_to_str = _safe_form_text(form.get("effective_to"))
            effective_to = (
                date.fromisoformat(effective_to_str) if effective_to_str else None
            )

            # Parse booleans
            is_compound = _safe_form_text(form.get("is_compound")) == "true"
            is_inclusive = _safe_form_text(form.get("is_inclusive")) == "true"
            is_recoverable = _safe_form_text(form.get("is_recoverable")) == "true"

            recovery_rate_pct = _safe_form_text(form.get("recovery_rate", "100"))
            recovery_rate = (
                Decimal(recovery_rate_pct) / Decimal("100")
                if is_recoverable
                else Decimal("0")
            )

            applies_to_sales = _safe_form_text(form.get("applies_to_sales")) == "true"
            applies_to_purchases = (
                _safe_form_text(form.get("applies_to_purchases")) == "true"
            )

            # Parse optional fields
            tax_return_box = _safe_form_text(form.get("tax_return_box")).strip() or None
            reporting_code = _safe_form_text(form.get("reporting_code")).strip() or None

            # Parse GL account IDs
            tax_collected_account_id = (
                _safe_form_text(form.get("tax_collected_account_id")) or None
            )
            tax_paid_account_id = (
                _safe_form_text(form.get("tax_paid_account_id")) or None
            )
            tax_expense_account_id = (
                _safe_form_text(form.get("tax_expense_account_id")) or None
            )

            # Validation
            if not tax_code_str:
                raise ValueError("Tax code is required")
            if not tax_name:
                raise ValueError("Tax name is required")
            if not tax_type_str:
                raise ValueError("Tax type is required")
            if not jurisdiction_id_str:
                raise ValueError("Jurisdiction is required")
            if not applies_to_sales and not applies_to_purchases:
                raise ValueError("Tax must apply to at least Sales or Purchases")

            # Create input
            tax_input = TaxCodeInput(
                tax_code=tax_code_str,
                tax_name=tax_name,
                tax_type=TaxType(tax_type_str),
                jurisdiction_id=coerce_uuid(jurisdiction_id_str),
                tax_rate=tax_rate,
                effective_from=effective_from,
                description=description,
                effective_to=effective_to,
                is_compound=is_compound,
                is_inclusive=is_inclusive,
                is_recoverable=is_recoverable,
                recovery_rate=recovery_rate,
                applies_to_purchases=applies_to_purchases,
                applies_to_sales=applies_to_sales,
                tax_return_box=tax_return_box,
                reporting_code=reporting_code,
                tax_collected_account_id=coerce_uuid(tax_collected_account_id)
                if tax_collected_account_id
                else None,
                tax_paid_account_id=coerce_uuid(tax_paid_account_id)
                if tax_paid_account_id
                else None,
                tax_expense_account_id=coerce_uuid(tax_expense_account_id)
                if tax_expense_account_id
                else None,
            )

            # Create tax code
            tax_code_service.create_tax_code(db, org_id, tax_input)

            return RedirectResponse(url="/finance/tax/codes", status_code=303)

        except ValueError as e:
            return self.new_tax_code_form_response(request, auth, db, error=str(e))
        except Exception as e:
            error_msg = getattr(e, "detail", str(e))
            return self.new_tax_code_form_response(request, auth, db, error=error_msg)

    def edit_tax_code_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        tax_code_id: str,
        db: Session,
        error: Optional[str] = None,
    ) -> HTMLResponse | RedirectResponse:
        """Display edit tax code form."""
        org_id = coerce_uuid(auth.organization_id)

        tax_code = tax_code_service.get(db, tax_code_id)
        if not tax_code or tax_code.organization_id != org_id:
            return RedirectResponse(url="/finance/tax/codes", status_code=303)

        context = base_context(request, auth, "Edit Tax Code", "tax")
        context.update(self._get_tax_code_form_context(db, auth, tax_code, error))

        return templates.TemplateResponse(
            request, "finance/tax/code_form.html", context
        )

    async def update_tax_code_response(
        self,
        request: Request,
        auth: WebAuthContext,
        tax_code_id: str,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle edit tax code form submission."""
        form = await request.form()
        org_id = coerce_uuid(auth.organization_id)

        try:
            tax_code = tax_code_service.get(db, tax_code_id)
            if not tax_code or tax_code.organization_id != org_id:
                return RedirectResponse(url="/finance/tax/codes", status_code=303)

            # Parse form data
            tax_code_str = _safe_form_text(form.get("tax_code")).strip()
            tax_name = _safe_form_text(form.get("tax_name")).strip()
            tax_type_str = _safe_form_text(form.get("tax_type"))
            jurisdiction_id_str = _safe_form_text(form.get("jurisdiction_id"))
            description = _safe_form_text(form.get("description")).strip() or None

            # Parse rate based on rate type
            rate_type = _safe_form_text(form.get("rate_type", "percentage"))
            if rate_type == "percentage":
                rate_percentage = _safe_form_text(form.get("tax_rate_percentage", "0"))
                new_tax_rate = Decimal(rate_percentage) / Decimal("100")
            else:
                rate_fixed = _safe_form_text(form.get("tax_rate_fixed", "0"))
                new_tax_rate = Decimal(rate_fixed)

            # Parse dates
            effective_from_str = _safe_form_text(form.get("effective_from"))
            effective_from = (
                date.fromisoformat(effective_from_str)
                if effective_from_str
                else date.today()
            )

            effective_to_str = _safe_form_text(form.get("effective_to"))
            effective_to = (
                date.fromisoformat(effective_to_str) if effective_to_str else None
            )

            # Parse booleans
            is_compound = _safe_form_text(form.get("is_compound")) == "true"
            is_inclusive = _safe_form_text(form.get("is_inclusive")) == "true"
            is_recoverable = _safe_form_text(form.get("is_recoverable")) == "true"
            is_active = _safe_form_text(form.get("is_active")) == "true"

            recovery_rate_pct = _safe_form_text(form.get("recovery_rate", "100"))
            recovery_rate = (
                Decimal(recovery_rate_pct) / Decimal("100")
                if is_recoverable
                else Decimal("0")
            )

            applies_to_sales = _safe_form_text(form.get("applies_to_sales")) == "true"
            applies_to_purchases = (
                _safe_form_text(form.get("applies_to_purchases")) == "true"
            )

            # Parse optional fields
            tax_return_box = _safe_form_text(form.get("tax_return_box")).strip() or None
            reporting_code = _safe_form_text(form.get("reporting_code")).strip() or None

            # Parse GL account IDs
            tax_collected_account_id = (
                _safe_form_text(form.get("tax_collected_account_id")) or None
            )
            tax_paid_account_id = (
                _safe_form_text(form.get("tax_paid_account_id")) or None
            )
            tax_expense_account_id = (
                _safe_form_text(form.get("tax_expense_account_id")) or None
            )

            # Validation
            if not tax_code_str:
                raise ValueError("Tax code is required")
            if not tax_name:
                raise ValueError("Tax name is required")
            if not tax_type_str:
                raise ValueError("Tax type is required")
            if not jurisdiction_id_str:
                raise ValueError("Jurisdiction is required")
            if not applies_to_sales and not applies_to_purchases:
                raise ValueError("Tax must apply to at least Sales or Purchases")

            # Check for duplicate code (if changed)
            if tax_code_str != tax_code.tax_code:
                existing = tax_code_service.get_by_code(db, str(org_id), tax_code_str)
                if existing:
                    raise ValueError(f"Tax code '{tax_code_str}' already exists")

            # Update all fields
            tax_code.tax_code = tax_code_str
            tax_code.tax_name = tax_name
            tax_code.description = description
            tax_code.tax_type = TaxType(tax_type_str)
            tax_code.jurisdiction_id = coerce_uuid(jurisdiction_id_str)
            tax_code.tax_rate = new_tax_rate
            tax_code.effective_from = effective_from
            tax_code.effective_to = effective_to
            tax_code.is_compound = is_compound
            tax_code.is_inclusive = is_inclusive
            tax_code.is_recoverable = is_recoverable
            tax_code.recovery_rate = recovery_rate
            tax_code.applies_to_purchases = applies_to_purchases
            tax_code.applies_to_sales = applies_to_sales
            tax_code.tax_return_box = tax_return_box
            tax_code.reporting_code = reporting_code
            tax_code.tax_collected_account_id = (
                coerce_uuid(tax_collected_account_id)
                if tax_collected_account_id
                else None
            )
            tax_code.tax_paid_account_id = (
                coerce_uuid(tax_paid_account_id) if tax_paid_account_id else None
            )
            tax_code.tax_expense_account_id = (
                coerce_uuid(tax_expense_account_id) if tax_expense_account_id else None
            )
            tax_code.is_active = is_active

            db.commit()

            return RedirectResponse(url="/finance/tax/codes", status_code=303)

        except ValueError as e:
            return self.edit_tax_code_form_response(
                request, auth, tax_code_id, db, error=str(e)
            )
        except Exception as e:
            error_msg = getattr(e, "detail", str(e))
            return self.edit_tax_code_form_response(
                request, auth, tax_code_id, db, error=error_msg
            )

    def toggle_tax_code_response(
        self,
        auth: WebAuthContext,
        tax_code_id: str,
        db: Session,
    ) -> RedirectResponse:
        """Toggle tax code active/inactive status."""
        org_id = coerce_uuid(auth.organization_id)

        tax_code = tax_code_service.get(db, tax_code_id)
        if tax_code and tax_code.organization_id == org_id:
            tax_code.is_active = not tax_code.is_active
            db.commit()

        return RedirectResponse(url="/finance/tax/codes", status_code=303)

    # ============================================================
    # Tax Reports
    # ============================================================

    def tax_summary_by_type_page(
        self,
        request: Request,
        start_date_str: Optional[str],
        end_date_str: Optional[str],
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Tax summary by type report page."""
        from app.services.finance.tax.tax_reports import tax_report_service

        org_id = coerce_uuid(auth.organization_id)

        # Default to current month
        today = date.today()
        if start_date_str:
            try:
                start_date = date.fromisoformat(start_date_str)
            except ValueError:
                start_date = today.replace(day=1)
        else:
            start_date = today.replace(day=1)

        if end_date_str:
            try:
                end_date = date.fromisoformat(end_date_str)
            except ValueError:
                end_date = today
        else:
            end_date = today

        # Get tax summary by type
        summaries = tax_report_service.get_tax_summary_by_type(
            db, org_id, start_date, end_date
        )

        # Calculate totals
        total_output = sum(s.total_output for s in summaries)
        total_input = sum(s.total_input for s in summaries)
        total_wht_withheld = sum(s.total_wht_collected for s in summaries)
        net_position = total_output - total_input

        context = base_context(request, auth, "Tax Summary by Type", "tax")
        context.update(
            {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "summaries": summaries,
                "total_output": total_output,
                "total_input": total_input,
                "total_wht_withheld": total_wht_withheld,
                "net_position": net_position,
            }
        )
        context.update(get_currency_context(db, str(org_id)))

        return templates.TemplateResponse(
            request,
            "finance/reports/tax_by_type.html",
            context,
        )

    def wht_report_page(
        self,
        request: Request,
        start_date_str: Optional[str],
        end_date_str: Optional[str],
        include_details: bool,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """WHT report page."""
        from app.services.finance.tax.tax_reports import tax_report_service

        org_id = coerce_uuid(auth.organization_id)

        # Default to current month
        today = date.today()
        if start_date_str:
            try:
                start_date = date.fromisoformat(start_date_str)
            except ValueError:
                start_date = today.replace(day=1)
        else:
            start_date = today.replace(day=1)

        if end_date_str:
            try:
                end_date = date.fromisoformat(end_date_str)
            except ValueError:
                end_date = today
        else:
            end_date = today

        # Get WHT report data
        report = tax_report_service.get_wht_report(
            db, org_id, start_date, end_date, include_transactions=include_details
        )

        context = base_context(request, auth, "WHT Report", "tax")
        context.update(
            {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "report": report,
            }
        )
        context.update(get_currency_context(db, str(org_id)))

        return templates.TemplateResponse(
            request,
            "finance/reports/wht_report.html",
            context,
        )


tax_web_service = TaxWebService()
