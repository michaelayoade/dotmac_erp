# ruff: noqa: F401
"""
Banking web view service.

Provides view-focused data for banking web routes.
"""

from __future__ import annotations

import builtins
import csv
import json
import logging
import re
from datetime import date
from datetime import datetime as _datetime
from decimal import Decimal
from io import BytesIO, StringIO
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import UploadFile
from starlette.responses import Response

from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.banking.bank_reconciliation import (
    BankReconciliation,
    BankReconciliationLine,
    ReconciliationStatus,
)
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    BankStatementLineMatch,
    BankStatementStatus,
)
from app.models.finance.gl.account import Account
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.schemas.finance.banking import BankStatementImport
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.banking import (
    bank_statement_service,
)
from app.services.htmx import htmx_response, is_htmx_request
from app.services.finance.banking.payment_metadata import (
    PaymentMetadata,
    resolve_payment_metadata,
    resolve_payment_metadata_batch,
)
from app.services.finance.common.sorting import apply_sort
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service
from app.services.formatters import format_currency as _base_format_currency
from app.services.formatters import format_date as _format_date
from app.services.formatters import parse_date as _parse_date
from app.services.formatters import parse_decimal as _parse_decimal
from app.services.imports.formats import (
    SPREADSHEET_EXTENSIONS,
    spreadsheet_formats_label,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)

# Human-friendly labels for source_document_type values
_SOURCE_TYPE_LABELS: dict[str, str] = {
    "CUSTOMER_PAYMENT": "Receipt",
    "SUPPLIER_PAYMENT": "Payment",
    "AR_INVOICE": "Invoice",
    "CUSTOMER_INVOICE": "Invoice",
    "INVOICE": "Invoice",
    "SUPPLIER_INVOICE": "Bill",
    "AP_INVOICE": "Bill",
    "EXPENSE": "Expense",
    "EXPENSE_CLAIM": "Expense",
    "JOURNAL_ENTRY": "Journal",
}


def _build_match_detail(
    db: Session,
    entry: JournalEntry,
    source_url: str,
    *,
    metadata: PaymentMetadata | None = None,
) -> dict[str, str]:
    """Build a match detail dict for a journal entry.

    If *metadata* is not provided, resolves it from the entry's source document.
    Falls back to the journal description when no payment metadata is available.
    """
    if metadata is None:
        try:
            metadata = resolve_payment_metadata(
                db,
                getattr(entry, "source_document_type", None),
                getattr(entry, "source_document_id", None),
            )
        except Exception:
            logger.debug(
                "Could not resolve payment metadata for entry %s",
                getattr(entry, "entry_id", None),
            )

    src_type = getattr(entry, "source_document_type", None) or ""
    type_label = _SOURCE_TYPE_LABELS.get(src_type, "GL Entry")

    if metadata:
        return {
            "label": metadata.counterparty_name or type_label,
            "sub": metadata.payment_number or "",
            "type": type_label,
            "url": source_url,
        }

    # Fallback: use journal description
    desc = getattr(entry, "description", "") or ""
    return {
        "label": desc[:60] if desc else type_label,
        "sub": "",
        "type": type_label,
        "url": source_url,
    }


def _format_currency(
    amount: Decimal | None,
    currency: str | None = None,
) -> str:
    """Format currency with em-dash for None values."""
    return str(_base_format_currency(amount, currency, none_value="\u2014"))


def _parse_account_status(value: str | None) -> BankAccountStatus | None:
    """Parse bank account status enum value.

    Logs warning on parse failure for debugging.
    """
    if not value:
        return None
    try:
        return BankAccountStatus(value)
    except ValueError:
        logger.warning("Invalid bank account status value: %r", value)
        return None


def _parse_statement_status(value: str | None) -> BankStatementStatus | None:
    """Parse bank statement status enum value.

    Logs warning on parse failure for debugging.
    """
    if not value:
        return None
    status_map = {
        "in_progress": BankStatementStatus.processing,
        "processing": BankStatementStatus.processing,
    }
    if value in status_map:
        return status_map[value]
    try:
        return BankStatementStatus(value)
    except ValueError:
        logger.warning("Invalid bank statement status value: %r", value)
        return None


def _statement_status_label(status: BankStatementStatus) -> str:
    if status == BankStatementStatus.processing:
        return "in_progress"
    if status == BankStatementStatus.closed:
        return "reconciled"
    return str(status.value)


def _parse_reconciliation_status(
    value: str | None,
) -> ReconciliationStatus | None:
    """Parse reconciliation status enum value.

    Logs warning on parse failure for debugging.
    """
    if not value:
        return None
    try:
        return ReconciliationStatus(value)
    except ValueError:
        logger.warning("Invalid reconciliation status value: %r", value)
        return None


def _account_view(account: BankAccount) -> dict:
    currency = account.currency_code
    return {
        "bank_account_id": account.bank_account_id,
        "bank_name": account.bank_name,
        "bank_code": account.bank_code,
        "branch_code": account.branch_code,
        "branch_name": account.branch_name,
        "account_name": account.account_name,
        "account_number": account.account_number,
        "account_type": account.account_type.value if account.account_type else "",
        "iban": account.iban,
        "currency_code": currency,
        "gl_account_id": account.gl_account_id,
        "status": account.status.value if account.status else "",
        "last_statement_balance": _format_currency(
            account.last_statement_balance, currency
        ),
        "last_statement_date": _format_date(account.last_statement_date),
        "last_reconciled_date": _format_date(account.last_reconciled_date),
        "last_reconciled_balance": _format_currency(
            account.last_reconciled_balance, currency
        ),
        "contact_name": account.contact_name,
        "contact_phone": account.contact_phone,
        "contact_email": account.contact_email,
        "notes": account.notes,
        "allow_overdraft": account.allow_overdraft,
        "overdraft_limit": _format_currency(account.overdraft_limit, currency)
        if account.overdraft_limit
        else None,
        "mono_account_id": account.mono_account_id,
        "mono_sync_from_date": _format_date(account.mono_sync_from_date),
        "mono_last_transaction_date": _format_date(account.mono_last_transaction_date),
        "mono_last_synced_at": account.mono_last_synced_at,
        "mono_last_sync_error": account.mono_last_sync_error,
        "mono_sync_buffer_days": account.mono_sync_buffer_days,
    }


def _statement_view(statement: BankStatement) -> dict:
    account = statement.bank_account
    currency = statement.currency_code
    return {
        "statement_id": statement.statement_id,
        "statement_number": statement.statement_number,
        "statement_date": _format_date(statement.statement_date),
        "period_start": _format_date(statement.period_start),
        "period_end": _format_date(statement.period_end),
        "period_start_iso": statement.period_start.isoformat()
        if statement.period_start
        else "",
        "period_end_iso": statement.period_end.isoformat()
        if statement.period_end
        else "",
        "opening_balance": _format_currency(statement.opening_balance, currency),
        "closing_balance": _format_currency(statement.closing_balance, currency),
        "opening_balance_raw": statement.opening_balance,
        "closing_balance_raw": statement.closing_balance,
        "matched_lines": statement.matched_lines,
        "unmatched_lines": statement.unmatched_lines,
        "total_lines": statement.total_lines,
        "total_credits": _format_currency(statement.total_credits, currency),
        "total_debits": _format_currency(statement.total_debits, currency),
        "currency_code": currency,
        "bank_account_id": statement.bank_account_id,
        "bank_name": account.bank_name if account else "",
        "account_number": account.account_number if account else "",
        "account_type": account.account_type if account else "",
        "status": _statement_status_label(statement.status),
    }


def _statement_line_view(line: BankStatementLine, currency: str = "") -> dict:
    return {
        "line_id": line.line_id,
        "line_number": line.line_number,
        "transaction_date": _format_date(line.transaction_date),
        "transaction_type": line.transaction_type.value
        if line.transaction_type
        else "",
        "amount": _format_currency(line.amount, currency),
        "raw_amount": float(line.amount) if line.amount is not None else 0.0,
        "description": line.description,
        "reference": line.reference,
        "payee_payer": line.payee_payer,
        "bank_reference": line.bank_reference,
        "running_balance": _format_currency(line.running_balance, currency),
        "is_matched": line.is_matched,
        "matched_journal_line_id": str(line.primary_journal_line_id)
        if line.primary_journal_line_id
        else None,
        # Categorization fields
        "categorization_status": line.categorization_status.value
        if line.categorization_status
        else None,
        "suggested_account_id": str(line.suggested_account_id)
        if line.suggested_account_id
        else None,
        "suggested_rule_id": str(line.suggested_rule_id)
        if line.suggested_rule_id
        else None,
        "suggested_confidence": line.suggested_confidence,
        "suggested_match_reason": line.suggested_match_reason,
    }


def _reconciliation_view(reconciliation: BankReconciliation) -> dict:
    account = reconciliation.bank_account
    return {
        "reconciliation_id": reconciliation.reconciliation_id,
        "bank_account_id": reconciliation.bank_account_id,
        "bank_name": account.bank_name if account else "",
        "account_number": account.account_number if account else "",
        "reconciliation_date": _format_date(reconciliation.reconciliation_date),
        "period_start": _format_date(reconciliation.period_start),
        "period_end": _format_date(reconciliation.period_end),
        "statement_opening_balance": reconciliation.statement_opening_balance,
        "statement_closing_balance": reconciliation.statement_closing_balance,
        "gl_opening_balance": reconciliation.gl_opening_balance,
        "gl_closing_balance": reconciliation.gl_closing_balance,
        "total_matched": reconciliation.total_matched,
        "total_adjustments": reconciliation.total_adjustments,
        "reconciliation_difference": reconciliation.reconciliation_difference,
        "status": reconciliation.status.value if reconciliation.status else "",
        "currency_code": reconciliation.currency_code,
    }


def _reconciliation_line_view(line: BankReconciliationLine) -> dict:
    return {
        "line_id": line.line_id,
        "transaction_date": _format_date(line.transaction_date),
        "description": line.description,
        "reference": line.reference,
        "statement_amount": line.statement_amount,
        "gl_amount": line.gl_amount,
        "difference": line.difference,
        "match_type": line.match_type.value if line.match_type else "",
        "adjustment_type": line.adjustment_type,
        "is_adjustment": line.is_adjustment,
        "is_outstanding": line.is_outstanding,
        "outstanding_type": line.outstanding_type,
    }


def _gl_line_view(
    line: JournalEntryLine,
    entry: JournalEntry,
    metadata: PaymentMetadata | None = None,
) -> dict:
    view: dict = {
        "line_id": line.line_id,
        "entry_date": _format_date(entry.entry_date),
        "description": line.description or entry.description,
        "reference": entry.reference,
        "debit_amount": line.debit_amount,
        "credit_amount": line.credit_amount,
        "signed_amount": float(
            (line.debit_amount or Decimal("0")) - (line.credit_amount or Decimal("0"))
        ),
        # Payment metadata (None if not from a payment)
        "source_type": None,
        "source_module": getattr(entry, "source_module", None),
        "payment_number": None,
        "counterparty_name": None,
        "counterparty_type": None,
        "invoice_numbers": [],
    }
    if metadata:
        view["source_type"] = metadata.source_type
        view["payment_number"] = metadata.payment_number
        view["counterparty_name"] = metadata.counterparty_name
        view["counterparty_type"] = metadata.counterparty_type
        view["invoice_numbers"] = metadata.invoice_numbers
    return view


def _line_amount(line: BankReconciliationLine) -> Decimal:
    amount = line.statement_amount
    if amount is None:
        amount = line.gl_amount
    if amount is None:
        return Decimal("0")
    return Decimal(str(amount))


def _gl_line_as_transaction(
    line: JournalEntryLine,
    entry: JournalEntry,
    bank_account: BankAccount,
    currency: str,
    metadata: PaymentMetadata | None = None,
) -> dict[str, Any]:
    """Transform a GL journal line into a transaction dict for the statements template.

    For a bank *asset* account:
    - Debit = money flowing IN  → displayed as CR (credit to the bank)
    - Credit = money flowing OUT → displayed as DR (debit from the bank)
    """
    debit = line.debit_amount or Decimal("0")
    credit = line.credit_amount or Decimal("0")

    if debit > 0:
        txn_type = "credit"  # money in
        amount = debit
    else:
        txn_type = "debit"  # money out
        amount = credit

    src_type = getattr(entry, "source_document_type", None) or ""
    source_label = _SOURCE_TYPE_LABELS.get(src_type, "Journal")

    counterparty = ""
    if metadata and metadata.counterparty_name:
        counterparty = metadata.counterparty_name

    return {
        "transaction_date": _format_date(entry.entry_date),
        "description": line.description or entry.description or "",
        "reference": entry.journal_number or "",
        "bank_name": bank_account.bank_name or "",
        "account_number": bank_account.account_number or "",
        "bank_account_id": str(bank_account.bank_account_id),
        "transaction_type": txn_type,
        "amount": _format_currency(amount, currency),
        "raw_amount": float(amount),
        "payee_payer": counterparty,
        "source_label": source_label,
        "journal_entry_id": str(entry.journal_entry_id),
        "is_matched": None,
    }


def _build_active_filters(
    *,
    account_id: str | None,
    accounts: list[dict],
    status: str | None,
    start_date: str | None,
    end_date: str | None,
    status_labels: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Build a list of active filter dicts for the compact_filters macro.

    Each dict has ``name``, ``value``, and ``display_value`` keys.
    """
    filters: list[dict[str, str]] = []
    if account_id:
        # Resolve account display name from the accounts list
        display = account_id
        for acc in accounts:
            acc_id = str(acc.get("bank_account_id", ""))
            if acc_id == account_id:
                display = (
                    f"{acc.get('bank_name', '')} - {acc.get('account_number', '')}"
                )
                break
        filters.append(
            {"name": "account_id", "value": account_id, "display_value": display}
        )
    if status:
        label = status
        if status_labels and status in status_labels:
            label = status_labels[status]
        else:
            label = status.replace("_", " ").title()
        filters.append({"name": "status", "value": status, "display_value": label})
    if start_date:
        filters.append(
            {
                "name": "start_date",
                "value": start_date,
                "display_value": f"From {start_date}",
            }
        )
    if end_date:
        filters.append(
            {"name": "end_date", "value": end_date, "display_value": f"To {end_date}"}
        )
    return filters


__all__ = [name for name in globals() if not name.startswith("__")]
