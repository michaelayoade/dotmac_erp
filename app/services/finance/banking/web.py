"""
Banking web view service.

Provides view-focused data for banking web routes.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
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
    BankStatementStatus,
)
from app.models.finance.gl.account import Account
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.schemas.finance.banking import BankStatementImport
from app.services.common import coerce_uuid
from app.services.finance.banking import (
    bank_statement_service,
)
from app.services.finance.banking.payment_metadata import (
    PaymentMetadata,
)
from app.services.finance.platform.currency_context import get_currency_context
from app.services.formatters import format_currency as _base_format_currency
from app.services.formatters import format_date as _format_date
from app.services.formatters import parse_date as _parse_date
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def _format_currency(
    amount: Decimal | None,
    currency: str | None = None,
) -> str:
    """Format currency with em-dash for None values."""
    return _base_format_currency(amount, currency, none_value="\u2014")


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
    currency = account.currency_code or "NGN"
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
        "description": line.description,
        "reference": line.reference,
        "payee_payer": line.payee_payer,
        "bank_reference": line.bank_reference,
        "running_balance": _format_currency(line.running_balance, currency),
        "is_matched": line.is_matched,
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


class BankingWebService:
    """View service for banking web routes."""

    @staticmethod
    def list_accounts_context(
        db: Session,
        organization_id: str,
        search: str | None,
        status: str | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_account_status(status)

        query = db.query(BankAccount).filter(BankAccount.organization_id == org_id)
        if status_value:
            query = query.filter(BankAccount.status == status_value)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    BankAccount.bank_name.ilike(search_pattern),
                    BankAccount.account_name.ilike(search_pattern),
                    BankAccount.account_number.ilike(search_pattern),
                    BankAccount.branch_name.ilike(search_pattern),
                )
            )

        total_count = (
            query.with_entities(func.count(BankAccount.bank_account_id)).scalar() or 0
        )
        accounts = (
            query.order_by(BankAccount.bank_name, BankAccount.account_name)
            .limit(limit)
            .offset(offset)
            .all()
        )

        active_count = (
            query.filter(BankAccount.status == BankAccountStatus.active)
            .with_entities(func.count(BankAccount.bank_account_id))
            .scalar()
            or 0
        )
        total_balance = query.with_entities(
            func.coalesce(func.sum(BankAccount.last_statement_balance), 0)
        ).scalar() or Decimal("0")
        pending_recon = (
            db.query(func.count(BankReconciliation.reconciliation_id))
            .filter(
                BankReconciliation.organization_id == org_id,
                BankReconciliation.status.in_(
                    [ReconciliationStatus.draft, ReconciliationStatus.pending_review]
                ),
            )
            .scalar()
            or 0
        )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "accounts": [_account_view(account) for account in accounts],
            "search": search,
            "status": status,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "active_count": active_count,
            "total_balance": _format_currency(total_balance),
            "pending_recon": pending_recon,
            "statuses": [s.value for s in BankAccountStatus],
        }

    @staticmethod
    def account_form_context(
        db: Session,
        organization_id: str,
        account_id: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        account = None
        if account_id:
            account = db.get(BankAccount, coerce_uuid(account_id))

        gl_accounts = (
            db.query(Account)
            .filter(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

        context = {
            "account": _account_view(account) if account else None,
            "gl_accounts": gl_accounts,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def account_detail_context(
        db: Session,
        organization_id: str,
        account_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        account = db.get(BankAccount, coerce_uuid(account_id))
        if not account or account.organization_id != org_id:
            account = None
        transactions: list[dict] = []
        if account:
            rows = (
                db.query(BankStatementLine, BankStatement)
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .filter(
                    BankStatement.organization_id == org_id,
                    BankStatement.bank_account_id == account.bank_account_id,
                )
                .order_by(
                    BankStatementLine.transaction_date.desc(),
                    BankStatementLine.line_number.desc(),
                )
                .limit(50)
                .all()
            )
            for line, statement in rows:
                view = _statement_line_view(line)
                view.update(
                    {
                        "statement_id": statement.statement_id,
                        "statement_number": statement.statement_number,
                        "statement_date": _format_date(statement.statement_date),
                    }
                )
                transactions.append(view)

        return {
            "account": _account_view(account) if account else None,
            "transactions": transactions,
        }

    @staticmethod
    def list_statements_context(
        db: Session,
        organization_id: str,
        account_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_statement_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        query = db.query(BankStatement).filter(BankStatement.organization_id == org_id)

        if account_id:
            query = query.filter(
                BankStatement.bank_account_id == coerce_uuid(account_id)
            )
        if status_value:
            query = query.filter(BankStatement.status == status_value)
        if from_date:
            query = query.filter(BankStatement.statement_date >= from_date)
        if to_date:
            query = query.filter(BankStatement.statement_date <= to_date)

        total_count = (
            query.with_entities(func.count(BankStatement.statement_id)).scalar() or 0
        )
        statements = (
            query.order_by(BankStatement.statement_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        accounts = (
            db.query(BankAccount)
            .filter(BankAccount.organization_id == org_id)
            .order_by(BankAccount.bank_name, BankAccount.account_number)
            .all()
        )

        total_lines = sum(statement.total_lines or 0 for statement in statements)
        matched_lines = sum(statement.matched_lines or 0 for statement in statements)
        unmatched_lines = sum(
            statement.unmatched_lines or 0 for statement in statements
        )

        total_pages = max(1, (total_count + limit - 1) // limit)

        account_views = [_account_view(account) for account in accounts]
        active_filters = _build_active_filters(
            account_id=account_id,
            accounts=account_views,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        return {
            "statements": [_statement_view(statement) for statement in statements],
            "accounts": account_views,
            "account_id": account_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "total_lines": total_lines,
            "matched_lines": matched_lines,
            "unmatched_lines": unmatched_lines,
            "active_filters": active_filters,
        }

    @staticmethod
    def statement_import_context(
        db: Session,
        organization_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        accounts = (
            db.query(BankAccount)
            .filter(BankAccount.organization_id == org_id)
            .order_by(BankAccount.bank_name, BankAccount.account_number)
            .all()
        )
        # Build JSON-safe account list for Alpine.js tojson serialization.
        accounts_json = [
            {
                "bank_account_id": str(a.bank_account_id),
                "bank_name": a.bank_name or "",
                "account_number": a.account_number or "",
            }
            for a in accounts
        ]
        # Build alias map from unified registry for client-side header matching
        from app.services.finance.banking.bank_statement import (
            BankStatementService,
        )
        from app.services.finance.import_export.base import build_alias_map

        alias_map = build_alias_map(BankStatementService._BANK_FIELD_TYPES)
        return {"accounts": accounts_json, "alias_map": alias_map}

    @staticmethod
    def statement_detail_context(
        db: Session,
        organization_id: str,
        statement_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        statement = db.get(BankStatement, coerce_uuid(statement_id))
        if not statement or statement.organization_id != org_id:
            return {"statement": None, "lines": [], "account_map": {}}

        currency = statement.currency_code
        lines = [_statement_line_view(line, currency) for line in statement.lines]

        # Build account name lookup for suggested accounts
        account_ids = [
            line.suggested_account_id
            for line in statement.lines
            if line.suggested_account_id
        ]
        account_map: dict[str, str] = {}
        if account_ids:
            accounts = (
                db.query(Account).filter(Account.account_id.in_(account_ids)).all()
            )
            account_map = {
                str(a.account_id): f"{a.account_code} - {a.account_name}"
                for a in accounts
            }

        # Categorization summary counts
        from app.models.finance.banking.bank_statement import CategorizationStatus

        cat_summary = {
            "suggested": 0,
            "accepted": 0,
            "rejected": 0,
            "auto_applied": 0,
            "flagged": 0,
        }
        for line in statement.lines:
            if line.categorization_status == CategorizationStatus.SUGGESTED:
                cat_summary["suggested"] += 1
            elif line.categorization_status == CategorizationStatus.ACCEPTED:
                cat_summary["accepted"] += 1
            elif line.categorization_status == CategorizationStatus.REJECTED:
                cat_summary["rejected"] += 1
            elif line.categorization_status == CategorizationStatus.AUTO_APPLIED:
                cat_summary["auto_applied"] += 1
            elif line.categorization_status == CategorizationStatus.FLAGGED:
                cat_summary["flagged"] += 1

        # GL transaction match suggestions for unmatched lines
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        recon_svc = BankReconciliationService()
        match_suggestions_raw = recon_svc.get_statement_match_suggestions(
            db, org_id, statement.statement_id
        )

        # Serialize to JSON-safe dict keyed by string line_id
        match_suggestions: dict[str, dict] = {}
        for line_id, suggestion in match_suggestions_raw.items():
            match_suggestions[str(line_id)] = {
                "journal_line_id": str(suggestion.journal_line_id),
                "confidence": suggestion.confidence,
                "counterparty_name": suggestion.counterparty_name,
                "payment_number": suggestion.payment_number,
            }

        # All GL candidates for manual match modal
        gl_result = recon_svc.get_gl_candidates_for_statement(
            db, org_id, statement.statement_id
        )

        return {
            "statement": _statement_view(statement),
            "lines": lines,
            "account_map": account_map,
            "categorization_summary": cat_summary,
            "match_suggestions": match_suggestions,
            "gl_candidates": gl_result.get("candidates", []),
            "gl_source_types": gl_result.get("source_types", []),
        }

    @staticmethod
    def list_reconciliations_context(
        db: Session,
        organization_id: str,
        account_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_reconciliation_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        query = db.query(BankReconciliation).filter(
            BankReconciliation.organization_id == org_id
        )

        if account_id:
            query = query.filter(
                BankReconciliation.bank_account_id == coerce_uuid(account_id)
            )
        if status_value:
            query = query.filter(BankReconciliation.status == status_value)
        if from_date:
            query = query.filter(BankReconciliation.reconciliation_date >= from_date)
        if to_date:
            query = query.filter(BankReconciliation.reconciliation_date <= to_date)

        total_count = (
            query.with_entities(
                func.count(BankReconciliation.reconciliation_id)
            ).scalar()
            or 0
        )
        reconciliations = (
            query.order_by(BankReconciliation.reconciliation_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        accounts = (
            db.query(BankAccount)
            .filter(BankAccount.organization_id == org_id)
            .order_by(BankAccount.bank_name, BankAccount.account_number)
            .all()
        )

        in_progress_count = sum(
            1 for recon in reconciliations if recon.status == ReconciliationStatus.draft
        )
        pending_review_count = sum(
            1
            for recon in reconciliations
            if recon.status == ReconciliationStatus.pending_review
        )
        approved_count = sum(
            1
            for recon in reconciliations
            if recon.status == ReconciliationStatus.approved
        )

        total_pages = max(1, (total_count + limit - 1) // limit)

        account_views = [_account_view(account) for account in accounts]
        active_filters = _build_active_filters(
            account_id=account_id,
            accounts=account_views,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        return {
            "reconciliations": [
                _reconciliation_view(recon) for recon in reconciliations
            ],
            "accounts": account_views,
            "account_id": account_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "in_progress_count": in_progress_count,
            "pending_review_count": pending_review_count,
            "approved_count": approved_count,
            "statuses": [s.value for s in ReconciliationStatus],
            "active_filters": active_filters,
        }

    @staticmethod
    def reconciliation_form_context(
        db: Session,
        organization_id: str,
        *,
        account_id: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        accounts = (
            db.query(BankAccount)
            .filter(
                BankAccount.organization_id == org_id,
                BankAccount.status == BankAccountStatus.active,
            )
            .order_by(BankAccount.bank_name, BankAccount.account_number)
            .all()
        )
        context: dict = {"accounts": [_account_view(account) for account in accounts]}
        if account_id:
            context["selected_account_id"] = account_id
        return context

    @staticmethod
    def reconciliation_detail_context(
        db: Session,
        organization_id: str,
        reconciliation_id: str,
    ) -> dict:
        from app.services.finance.banking.bank_reconciliation import (
            bank_reconciliation_service as recon_svc,
        )
        from app.services.finance.banking.payment_metadata import (
            resolve_payment_metadata_batch,
        )

        org_id = coerce_uuid(organization_id)
        reconciliation = db.get(BankReconciliation, coerce_uuid(reconciliation_id))
        if not reconciliation or reconciliation.organization_id != org_id:
            return {
                "reconciliation": None,
                "lines": [],
                "unmatched_statement_lines": [],
                "unmatched_gl_lines": [],
                "match_suggestions": {},
            }

        bank_account = reconciliation.bank_account

        statement_lines = (
            db.query(BankStatementLine)
            .join(
                BankStatement,
                BankStatementLine.statement_id == BankStatement.statement_id,
            )
            .filter(
                BankStatement.bank_account_id == reconciliation.bank_account_id,
                BankStatementLine.is_matched.is_(False),
                BankStatementLine.transaction_date >= reconciliation.period_start,
                BankStatementLine.transaction_date <= reconciliation.period_end,
            )
            .order_by(BankStatementLine.transaction_date, BankStatementLine.line_number)
            .all()
        )

        gl_lines: list[Any] = []
        if bank_account:
            gl_lines = (
                db.query(JournalEntryLine, JournalEntry)
                .join(
                    JournalEntry,
                    JournalEntryLine.journal_entry_id == JournalEntry.journal_entry_id,
                )
                .filter(
                    JournalEntryLine.account_id == bank_account.gl_account_id,
                    JournalEntry.status == JournalStatus.POSTED,
                    JournalEntry.entry_date >= reconciliation.period_start,
                    JournalEntry.entry_date <= reconciliation.period_end,
                )
                .order_by(JournalEntry.entry_date, JournalEntryLine.line_number)
                .all()
            )

        # Batch-resolve payment metadata for GL lines
        metadata_pairs: list[tuple[str | None, UUID | None]] = [
            (
                getattr(entry, "source_document_type", None),
                getattr(entry, "source_document_id", None),
            )
            for _line, entry in gl_lines
        ]
        metadata_map = resolve_payment_metadata_batch(db, metadata_pairs)

        # Build GL line views with metadata
        unmatched_statement_lines = [
            _statement_line_view(line) for line in statement_lines
        ]
        unmatched_gl_lines = []
        for line, entry in gl_lines:
            doc_id = getattr(entry, "source_document_id", None)
            meta = metadata_map.get(doc_id) if doc_id else None
            unmatched_gl_lines.append(_gl_line_view(line, entry, metadata=meta))

        # Generate match suggestions for draft/pending reconciliations
        match_suggestions: dict[str, dict] = {}
        if reconciliation.status in (
            ReconciliationStatus.draft,
            ReconciliationStatus.pending_review,
        ):
            try:
                raw_suggestions = recon_svc.get_match_suggestions(
                    db, org_id, reconciliation.reconciliation_id
                )
                for stmt_id, sug in raw_suggestions.items():
                    match_suggestions[str(stmt_id)] = {
                        "journal_line_id": str(sug.journal_line_id),
                        "confidence": round(sug.confidence, 1),
                        "counterparty_name": sug.counterparty_name or "",
                        "payment_number": sug.payment_number or "",
                    }
            except Exception:
                logger.exception("Failed to generate match suggestions")

        return {
            "reconciliation": _reconciliation_view(reconciliation),
            "lines": [_reconciliation_line_view(line) for line in reconciliation.lines],
            "unmatched_statement_lines": unmatched_statement_lines,
            "unmatched_gl_lines": unmatched_gl_lines,
            "match_suggestions": match_suggestions,
        }

    @staticmethod
    def reconciliation_report_context(
        db: Session,
        organization_id: str,
        reconciliation_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        reconciliation = db.get(BankReconciliation, coerce_uuid(reconciliation_id))
        if not reconciliation or reconciliation.organization_id != org_id:
            return {"report": None}

        recon_view = _reconciliation_view(reconciliation)

        matched_lines = [
            line
            for line in reconciliation.lines
            if not line.is_outstanding and not line.is_adjustment
        ]
        outstanding_deposits = [
            line
            for line in reconciliation.lines
            if line.is_outstanding and line.outstanding_type == "deposit"
        ]
        outstanding_payments = [
            line
            for line in reconciliation.lines
            if line.is_outstanding and line.outstanding_type == "payment"
        ]
        adjustments = [line for line in reconciliation.lines if line.is_adjustment]

        total_matched = sum(
            (_line_amount(line) for line in matched_lines), Decimal("0")
        )
        total_deposits = sum(
            (_line_amount(line) for line in outstanding_deposits), Decimal("0")
        )
        total_payments = sum(
            (_line_amount(line) for line in outstanding_payments), Decimal("0")
        )
        total_adjustments = sum(
            (_line_amount(line) for line in adjustments), Decimal("0")
        )

        statement_balance = Decimal(str(reconciliation.statement_closing_balance))
        gl_balance = Decimal(str(reconciliation.gl_closing_balance))
        adjusted_statement = statement_balance - total_payments + total_deposits
        adjusted_gl = gl_balance + total_adjustments
        difference = adjusted_statement - adjusted_gl

        report = {
            "reconciliation": recon_view,
            "summary": {
                "statement_balance": _format_currency(
                    statement_balance, reconciliation.currency_code
                ),
                "gl_balance": _format_currency(
                    gl_balance, reconciliation.currency_code
                ),
                "adjusted_book_balance": _format_currency(
                    adjusted_statement, reconciliation.currency_code
                ),
                "difference": _format_currency(
                    difference, reconciliation.currency_code
                ),
                "is_reconciled": difference == Decimal("0"),
            },
            "matched_items": {
                "count": len(matched_lines),
                "total": _format_currency(total_matched, reconciliation.currency_code),
                "items": [_reconciliation_line_view(line) for line in matched_lines],
            },
            "outstanding_deposits": {
                "count": len(outstanding_deposits),
                "total": _format_currency(total_deposits, reconciliation.currency_code),
                "items": [
                    _reconciliation_line_view(line) for line in outstanding_deposits
                ],
            },
            "outstanding_payments": {
                "count": len(outstanding_payments),
                "total": _format_currency(total_payments, reconciliation.currency_code),
                "items": [
                    _reconciliation_line_view(line) for line in outstanding_payments
                ],
            },
            "adjustments": {
                "count": len(adjustments),
                "total": _format_currency(
                    total_adjustments, reconciliation.currency_code
                ),
                "items": [_reconciliation_line_view(line) for line in adjustments],
            },
        }

        return {"report": report}

    # =========================================================================
    # Payee Context Methods
    # =========================================================================

    @staticmethod
    def list_payees_context(
        db: Session,
        organization_id: str,
        search: str | None = None,
        payee_type: str | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        """Context for payees list page."""
        from app.models.finance.banking.payee import Payee, PayeeType

        org_id = coerce_uuid(organization_id)

        query = db.query(Payee).filter(
            Payee.organization_id == org_id,
            Payee.is_active == True,
        )

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Payee.payee_name.ilike(search_pattern),
                    Payee.name_patterns.ilike(search_pattern),
                )
            )

        if payee_type:
            try:
                pt = PayeeType(payee_type)
                query = query.filter(Payee.payee_type == pt)
            except ValueError:
                pass

        total = query.count()
        payees = (
            query.order_by(Payee.payee_name)
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        # Get GL accounts for display
        account_map = {}
        account_ids = [p.default_account_id for p in payees if p.default_account_id]
        if account_ids:
            accounts = (
                db.query(Account).filter(Account.account_id.in_(account_ids)).all()
            )
            account_map = {
                a.account_id: f"{a.account_code} - {a.account_name}" for a in accounts
            }

        payee_list = []
        for p in payees:
            default_account_id = p.default_account_id
            payee_list.append(
                {
                    "payee_id": str(p.payee_id),
                    "payee_name": p.payee_name,
                    "payee_type": p.payee_type.value if p.payee_type else "",
                    "name_patterns": p.name_patterns or "",
                    "default_account": account_map.get(default_account_id, "")
                    if default_account_id
                    else "",
                    "match_count": p.match_count,
                    "last_matched": _format_date(p.last_matched_at)
                    if p.last_matched_at
                    else "Never",
                }
            )

        total_pages = (total + per_page - 1) // per_page
        return {
            "payees": payee_list,
            "payee_types": [
                {"value": t.value, "label": t.value.replace("_", " ").title()}
                for t in PayeeType
            ],
            "search": search or "",
            "selected_type": payee_type or "",
            "total_count": total,
            "total_pages": total_pages,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": total_pages,
            },
        }

    @staticmethod
    def payee_form_context(
        db: Session,
        organization_id: str,
        payee_id: str | None = None,
    ) -> dict:
        """Context for payee create/edit form."""
        from app.models.finance.banking.payee import Payee, PayeeType

        org_id = coerce_uuid(organization_id)

        # Get GL accounts for dropdown
        accounts = (
            db.query(Account)
            .filter(Account.organization_id == org_id, Account.is_active == True)
            .order_by(Account.account_code)
            .all()
        )

        account_options = [
            {
                "value": str(a.account_id),
                "label": f"{a.account_code} - {a.account_name}",
            }
            for a in accounts
        ]

        payee = None
        if payee_id:
            payee = db.get(Payee, coerce_uuid(payee_id))
            if payee and payee.organization_id != org_id:
                payee = None

        payee_data = None
        if payee:
            payee_data = {
                "payee_id": str(payee.payee_id),
                "payee_name": payee.payee_name,
                "payee_type": payee.payee_type.value if payee.payee_type else "",
                "name_patterns": payee.name_patterns or "",
                "default_account_id": str(payee.default_account_id)
                if payee.default_account_id
                else "",
                "notes": payee.notes or "",
                "is_active": payee.is_active,
            }

        return {
            "payee": payee_data,
            "is_edit": payee is not None,
            "payee_types": [
                {"value": t.value, "label": t.value.replace("_", " ").title()}
                for t in PayeeType
            ],
            "accounts": account_options,
        }

    # =========================================================================
    # Transaction Rule Context Methods
    # =========================================================================

    @staticmethod
    def list_rules_context(
        db: Session,
        organization_id: str,
        rule_type: str | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        """Context for transaction rules list page."""
        from app.models.finance.banking.transaction_rule import (
            RuleType,
            TransactionRule,
        )

        org_id = coerce_uuid(organization_id)

        query = db.query(TransactionRule).filter(
            TransactionRule.organization_id == org_id,
        )

        if rule_type:
            try:
                rt = RuleType(rule_type)
                query = query.filter(TransactionRule.rule_type == rt)
            except ValueError:
                pass

        total = query.count()
        rules = (
            query.order_by(TransactionRule.sort_order.asc(), TransactionRule.rule_name)
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        # Get GL accounts for display
        account_map = {}
        account_ids = [r.target_account_id for r in rules if r.target_account_id]
        if account_ids:
            accounts = (
                db.query(Account).filter(Account.account_id.in_(account_ids)).all()
            )
            account_map = {
                a.account_id: f"{a.account_code} - {a.account_name}" for a in accounts
            }

        rule_list = []
        for idx, r in enumerate(rules):
            target_account_id = r.target_account_id
            rule_list.append(
                {
                    "rule_id": str(r.rule_id),
                    "rule_name": r.rule_name,
                    "description": r.description or "",
                    "rule_type": r.rule_type.value if r.rule_type else "",
                    "action": r.action.value if r.action else "",
                    "target_account": account_map.get(target_account_id, "")
                    if target_account_id
                    else "",
                    "sort_order": r.sort_order,
                    "position": idx + 1,
                    "auto_apply": r.auto_apply,
                    "is_active": r.is_active,
                    "match_count": r.match_count,
                    "success_count": r.success_count,
                    "success_rate": f"{r.success_rate:.0f}%"
                    if r.success_count + r.reject_count > 0
                    else "N/A",
                }
            )

        total_pages = (total + per_page - 1) // per_page
        return {
            "rules": rule_list,
            "rule_types": [
                {"value": t.value, "label": t.value.replace("_", " ").title()}
                for t in RuleType
            ],
            "selected_type": rule_type or "",
            "total_count": total,
            "total_pages": total_pages,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": total_pages,
            },
        }

    @staticmethod
    def _normalize_to_combined(rule_type: str, conditions: dict) -> dict:
        """Normalize a single-type rule's conditions into COMBINED format.

        The visual builder always works with the COMBINED structure:
        ``{"operator": "AND", "rules": [{"type": "...", "conditions": {...}}]}``

        Legacy rules stored as single types get wrapped so the builder can
        display them.
        """
        if rule_type == "COMBINED":
            return conditions

        return {
            "operator": "AND",
            "rules": [{"type": rule_type, "conditions": conditions}],
        }

    @staticmethod
    def rule_form_context(
        db: Session,
        organization_id: str,
        rule_id: str | None = None,
    ) -> dict:
        """Context for transaction rule create/edit form."""
        from app.models.finance.banking.payee import Payee
        from app.models.finance.banking.transaction_rule import (
            RuleAction,
            RuleType,
            TransactionRule,
        )
        from app.models.finance.tax.tax_code import TaxCode

        org_id = coerce_uuid(organization_id)

        # Get GL accounts for dropdown
        accounts = (
            db.query(Account)
            .filter(Account.organization_id == org_id, Account.is_active == True)
            .order_by(Account.account_code)
            .all()
        )

        account_options = [
            {
                "value": str(a.account_id),
                "label": f"{a.account_code} - {a.account_name}",
            }
            for a in accounts
        ]

        # Get bank accounts for dropdown
        bank_accounts = (
            db.query(BankAccount)
            .filter(
                BankAccount.organization_id == org_id,
                BankAccount.status == BankAccountStatus.active,
            )
            .order_by(BankAccount.account_name)
            .all()
        )

        bank_account_options = [
            {
                "value": str(ba.bank_account_id),
                "label": f"{ba.bank_name} - {ba.account_name}",
            }
            for ba in bank_accounts
        ]

        # Get payees for dropdown
        payees = (
            db.query(Payee)
            .filter(Payee.organization_id == org_id, Payee.is_active == True)
            .order_by(Payee.payee_name)
            .all()
        )

        payee_options = [
            {"value": str(p.payee_id), "label": p.payee_name} for p in payees
        ]

        # Get tax codes for dropdown
        tax_codes = (
            db.query(TaxCode)
            .filter(TaxCode.organization_id == org_id, TaxCode.is_active == True)
            .order_by(TaxCode.tax_code)
            .all()
        )

        tax_code_options = [
            {
                "value": str(tc.tax_code_id),
                "label": f"{tc.tax_code} - {tc.tax_name} ({tc.tax_rate}%)",
            }
            for tc in tax_codes
        ]

        rule = None
        if rule_id:
            rule = db.get(TransactionRule, coerce_uuid(rule_id))
            if rule and rule.organization_id != org_id:
                rule = None

        rule_data = None
        if rule:
            raw_type = rule.rule_type.value if rule.rule_type else "COMBINED"
            raw_conditions = rule.conditions or {}
            combined_conditions = BankingWebService._normalize_to_combined(
                raw_type, raw_conditions
            )

            rule_data = {
                "rule_id": str(rule.rule_id),
                "rule_name": rule.rule_name,
                "description": rule.description or "",
                "rule_type": raw_type,
                "conditions": combined_conditions,
                "action": rule.action.value if rule.action else "",
                "target_account_id": str(rule.target_account_id)
                if rule.target_account_id
                else "",
                "tax_code_id": str(rule.tax_code_id) if rule.tax_code_id else "",
                "bank_account_id": str(rule.bank_account_id)
                if rule.bank_account_id
                else "",
                "payee_id": str(rule.payee_id) if rule.payee_id else "",
                "auto_apply": rule.auto_apply,
                "min_confidence": rule.min_confidence,
                "applies_to_credits": rule.applies_to_credits,
                "applies_to_debits": rule.applies_to_debits,
                "is_active": rule.is_active,
                "match_count": rule.match_count,
                "success_count": rule.success_count,
                "reject_count": rule.reject_count,
                "created_at": rule.created_at,
            }

        return {
            "rule": rule_data,
            "is_edit": rule is not None,
            "rule_types": [
                {"value": t.value, "label": t.value.replace("_", " ").title()}
                for t in RuleType
            ],
            "actions": [
                {"value": a.value, "label": a.value.replace("_", " ").title()}
                for a in RuleAction
            ],
            "accounts": account_options,
            "bank_accounts": bank_account_options,
            "payees": payee_options,
            "tax_codes": tax_code_options,
        }

    @staticmethod
    def build_rule_input(form_data: dict) -> dict:
        """Parse and validate raw form data into rule kwargs.

        Raises ``ValueError`` on invalid input.
        """
        from app.models.finance.banking.transaction_rule import RuleAction, RuleType

        rule_name = (form_data.get("rule_name") or "").strip()
        if not rule_name:
            raise ValueError("Rule name is required")

        # Parse conditions JSON from the visual builder
        conditions_raw = form_data.get("conditions_json", "")
        if not conditions_raw:
            raise ValueError("At least one matching condition is required")

        try:
            conditions = json.loads(conditions_raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Invalid conditions: {exc}") from exc

        # Validate conditions structure
        if not isinstance(conditions, dict):
            raise ValueError("Conditions must be a JSON object")
        sub_rules = conditions.get("rules", [])
        if not sub_rules:
            raise ValueError("At least one matching condition is required")

        valid_sub_types = {rt.value for rt in RuleType if rt != RuleType.COMBINED}
        for sr in sub_rules:
            if sr.get("type") not in valid_sub_types:
                raise ValueError(f"Invalid condition type: {sr.get('type')}")

        # Parse action
        action_str = form_data.get("action", "CATEGORIZE")
        try:
            action = RuleAction(action_str)
        except ValueError:
            raise ValueError(f"Invalid action: {action_str}")

        # Parse optional UUIDs
        target_account_id: UUID | None = None
        if action == RuleAction.CATEGORIZE:
            raw = form_data.get("target_account_id", "")
            if raw:
                target_account_id = UUID(raw)

        tax_code_id: UUID | None = None
        raw_tax = form_data.get("tax_code_id", "")
        if raw_tax:
            tax_code_id = UUID(raw_tax)

        bank_account_id: UUID | None = None
        raw_ba = form_data.get("bank_account_id", "")
        if raw_ba:
            bank_account_id = UUID(raw_ba)

        return {
            "rule_name": rule_name,
            "description": (form_data.get("description") or "").strip() or None,
            "rule_type": RuleType.COMBINED,
            "conditions": conditions,
            "action": action,
            "target_account_id": target_account_id,
            "tax_code_id": tax_code_id,
            "bank_account_id": bank_account_id,
            "auto_apply": form_data.get("auto_apply") == "on",
            "min_confidence": int(form_data.get("min_confidence", 80)),
            "applies_to_credits": form_data.get("applies_to_credits") == "on",
            "applies_to_debits": form_data.get("applies_to_debits") == "on",
            "is_active": form_data.get("is_active") == "on",
        }

    def create_rule_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        form_data: dict,
    ) -> HTMLResponse | RedirectResponse:
        """Handle POST for new rule creation."""
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        try:
            kwargs = self.build_rule_input(form_data)
        except (ValueError, TypeError) as exc:
            context = base_context(
                request, auth, "New Transaction Rule", "banking", db=db
            )
            context.update(self.rule_form_context(db, str(auth.organization_id)))
            context["error"] = str(exc)
            context["form_data"] = form_data
            return templates.TemplateResponse(
                request, "finance/banking/rule_form.html", context
            )

        # is_active isn't a create_rule() param; pop and set after creation
        is_active = kwargs.pop("is_active", True)

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        service = TransactionCategorizationService()
        rule = service.create_rule(
            db,
            organization_id=org_id,
            created_by=auth.person_id,
            **kwargs,
        )
        rule.is_active = is_active
        db.commit()
        return RedirectResponse(
            url="/finance/banking/rules?success=Rule+created",
            status_code=303,
        )

    def update_rule_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
        form_data: dict,
    ) -> HTMLResponse | RedirectResponse:
        """Handle POST for rule update."""
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        try:
            kwargs = self.build_rule_input(form_data)
        except (ValueError, TypeError) as exc:
            context = base_context(
                request, auth, "Edit Transaction Rule", "banking", db=db
            )
            context.update(
                self.rule_form_context(db, str(auth.organization_id), rule_id=rule_id)
            )
            context["error"] = str(exc)
            context["form_data"] = form_data
            return templates.TemplateResponse(
                request, "finance/banking/rule_form.html", context
            )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        service = TransactionCategorizationService()
        rule = service.update_rule(
            db,
            organization_id=org_id,
            rule_id=UUID(rule_id),
            **kwargs,
        )
        if not rule:
            context = base_context(
                request, auth, "Edit Transaction Rule", "banking", db=db
            )
            context.update(
                self.rule_form_context(db, str(auth.organization_id), rule_id=rule_id)
            )
            context["error"] = "Rule not found"
            return templates.TemplateResponse(
                request, "finance/banking/rule_form.html", context
            )

        db.commit()
        return RedirectResponse(
            url="/finance/banking/rules?success=Rule+updated",
            status_code=303,
        )

    def list_accounts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        status: str | None,
        page: int,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Bank Accounts", "banking", db=db)
        context.update(
            self.list_accounts_context(
                db,
                str(auth.organization_id),
                search=search,
                status=status,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/accounts.html", context
        )

    def account_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Bank Account", "banking", db=db)
        context.update(self.account_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/banking/account_form.html", context
        )

    def account_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Bank Account Details", "banking", db=db)
        context.update(
            self.account_detail_context(
                db,
                str(auth.organization_id),
                account_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/account_detail.html", context
        )

    def account_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Edit Bank Account", "banking", db=db)
        context.update(
            self.account_form_context(
                db,
                str(auth.organization_id),
                account_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/account_form.html", context
        )

    def list_statements_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Bank Statements", "banking", db=db)
        context.update(
            self.list_statements_context(
                db,
                str(auth.organization_id),
                account_id=account_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/statements.html", context
        )

    def statement_import_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        form_data: dict | None = None,
        errors: list[str] | None = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Import Bank Statement", "banking", db=db)
        context.update(self.statement_import_context(db, str(auth.organization_id)))
        form_payload = form_data or {}
        if not form_payload and request.query_params.get("account_id"):
            form_payload["bank_account_id"] = request.query_params.get("account_id")
        context["form_data"] = form_payload
        context["form_errors"] = errors or []
        return templates.TemplateResponse(
            request, "finance/banking/statement_import.html", context
        )

    async def statement_import_submit_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
        form_data = dict(form)
        csv_format = form_data.get("csv_format") or None
        errors: list[str] = []

        # Parse lines from file or manual entry.
        lines_data: list[dict] = []
        upload = form.get("statement_file")
        upload_file = upload if isinstance(upload, UploadFile) else None
        if upload_file and upload_file.filename:
            # CSRF middleware parses form data first, which can advance the file pointer.
            try:
                await upload_file.seek(0)
            except Exception:
                try:
                    upload_file.file.seek(0)
                except Exception:
                    logger.exception("Ignored exception")
            filename = upload_file.filename or ""
            lowered = filename.lower()
            if not (
                lowered.endswith(".csv")
                or lowered.endswith(".xlsx")
                or lowered.endswith(".xlsm")
            ):
                errors.append("Supported statement files: CSV, XLSX, XLSM.")
            else:
                # Limit upload size to avoid memory blowups.
                max_bytes = 10 * 1024 * 1024  # 10 MiB
                content = await upload_file.read(max_bytes + 1)
                if len(content) > max_bytes:
                    errors.append(
                        "Uploaded file is too large (max 10 MB). Please upload a smaller file."
                    )
                    content = b""
                if not content:
                    # Some middleware (e.g., CSRF) may have consumed the file stream.
                    # Fall back to reading from the underlying file handle.
                    try:
                        upload_file.file.seek(0)
                        content = upload_file.file.read(max_bytes + 1)
                        if len(content) > max_bytes:
                            errors.append(
                                "Uploaded file is too large (max 10 MB). Please upload a smaller file."
                            )
                            content = b""
                    except Exception:
                        content = content or b""
                if content:
                    # Avoid logging file contents (may contain PII).
                    logger.info(
                        "Statement import file read: filename=%s bytes=%s content_type=%s",
                        upload_file.filename,
                        len(content),
                        upload_file.content_type,
                    )
                if not content:
                    logger.warning(
                        "Statement import upload empty after read: filename=%s content_type=%s",
                        upload_file.filename,
                        upload_file.content_type,
                    )
                    errors.append(
                        "Uploaded file appears empty. Please re-select the file and try again."
                    )
                else:
                    # Resolve the org's configured date strftime so the
                    # parser can accept dates like DD/MM/YYYY, not just ISO.
                    # NOTE: We read from the DB directly because base_context()
                    # (which sets the formatting ContextVar) hasn't run yet.
                    from app.models.finance.core_org.organization import (
                        Organization,
                    )
                    from app.services.formatting_context import DATE_FORMAT_MAP

                    org_date_fmt: str | None = None
                    if auth.organization_id:
                        org = db.get(Organization, auth.organization_id)
                        if org and org.date_format:
                            org_date_fmt = DATE_FORMAT_MAP.get(org.date_format)
                    if lowered.endswith(".csv"):
                        rows, parse_errors = bank_statement_service.parse_csv_rows(
                            content, csv_format, date_format=org_date_fmt
                        )
                    else:
                        rows, parse_errors = bank_statement_service.parse_xlsx_rows(
                            content, csv_format, date_format=org_date_fmt
                        )
                    lines_data = rows
                    errors.extend(parse_errors)
                    if not rows and not parse_errors:
                        logger.warning(
                            "Statement import parsed zero rows: filename=%s csv_format=%s",
                            upload_file.filename,
                            csv_format,
                        )
        else:
            lines_data, manual_errors = self._parse_manual_lines(form)
            errors.extend(manual_errors)

        if not lines_data and not errors:
            errors.append("Please upload a CSV file or add at least one transaction.")

        payload_data = {
            "bank_account_id": form_data.get("bank_account_id"),
            "statement_number": form_data.get("statement_number") or None,
            "statement_date": form_data.get("statement_date") or None,
            "period_start": form_data.get("period_start"),
            "period_end": form_data.get("period_end"),
            "opening_balance": form_data.get("opening_balance") or None,
            "closing_balance": form_data.get("closing_balance") or None,
            "import_source": "csv"
            if upload_file and upload_file.filename
            else "manual",
            "import_filename": upload_file.filename if upload_file else None,
            "lines": lines_data,
        }

        payload = None
        if not errors:
            try:
                payload = BankStatementImport.model_validate(payload_data)
            except ValidationError as exc:
                errors.extend(self._format_validation_errors(exc))

        if errors or payload is None:
            # Preserve select fields for the form.
            preserved = {
                "bank_account_id": form_data.get("bank_account_id"),
                "statement_number": form_data.get("statement_number"),
                "statement_date": form_data.get("statement_date"),
                "period_start": form_data.get("period_start"),
                "period_end": form_data.get("period_end"),
                "opening_balance": form_data.get("opening_balance"),
                "closing_balance": form_data.get("closing_balance"),
                "csv_format": csv_format,
            }
            return self.statement_import_form_response(
                request, auth, db, form_data=preserved, errors=errors
            )

        line_inputs, line_errors = bank_statement_service.build_line_inputs(
            payload.lines
        )
        if line_errors:
            preserved = {
                "bank_account_id": form_data.get("bank_account_id"),
                "statement_number": form_data.get("statement_number"),
                "statement_date": form_data.get("statement_date"),
                "period_start": form_data.get("period_start"),
                "period_end": form_data.get("period_end"),
                "opening_balance": form_data.get("opening_balance"),
                "closing_balance": form_data.get("closing_balance"),
                "csv_format": csv_format,
            }
            return self.statement_import_form_response(
                request, auth, db, form_data=preserved, errors=line_errors
            )

        if auth.organization_id is None:
            raise HTTPException(status_code=400, detail="Organization is required")

        result = bank_statement_service.import_statement(
            db=db,
            organization_id=auth.organization_id,
            bank_account_id=payload.bank_account_id,
            statement_number=payload.statement_number,
            statement_date=payload.statement_date,
            period_start=payload.period_start,
            period_end=payload.period_end,
            opening_balance=payload.opening_balance,
            closing_balance=payload.closing_balance,
            lines=line_inputs,
            import_source=payload.import_source,
            import_filename=payload.import_filename,
            imported_by=auth.user_id,
        )
        db.commit()
        return RedirectResponse(
            url=f"/finance/banking/statements/{result.statement.statement_id}?saved=1",
            status_code=303,
        )

    @staticmethod
    def _parse_manual_lines(form) -> tuple[list[dict], list[str]]:
        pattern = re.compile(r"^lines\\[(\\d+)\\]\\[(.+)\\]$")
        lines: dict[int, dict] = {}
        errors: list[str] = []

        for key, value in form.items():
            match = pattern.match(key)
            if not match:
                continue
            line_index = int(match.group(1))
            field = match.group(2)
            lines.setdefault(line_index, {})[field] = value

        if not lines:
            return [], []

        results: list[dict] = []
        for idx in sorted(lines):
            data = lines[idx]
            # Skip rows that are entirely empty.
            if not any(str(v).strip() for v in data.values() if v is not None):
                continue

            line_number = data.get("line_number") or idx
            result = {
                "line_number": int(line_number),
                "transaction_date": data.get("transaction_date"),
                "transaction_type": data.get("transaction_type"),
                "amount": data.get("amount"),
                "debit": data.get("debit"),
                "credit": data.get("credit"),
                "description": data.get("description"),
                "reference": data.get("reference"),
                "payee_payer": data.get("payee_payer"),
                "bank_reference": data.get("bank_reference"),
                "check_number": data.get("check_number"),
                "bank_category": data.get("bank_category"),
                "bank_code": data.get("bank_code"),
                "value_date": data.get("value_date"),
                "running_balance": data.get("running_balance"),
                "transaction_id": data.get("transaction_id"),
            }
            results.append(result)

        if not results:
            errors.append("Please add at least one transaction line.")
        return results, errors

    @staticmethod
    def _format_validation_errors(exc: ValidationError) -> list[str]:
        errors: list[str] = []
        for err in exc.errors():
            loc = " -> ".join(str(item) for item in err.get("loc", []))
            msg = err.get("msg", "Invalid value")
            if loc:
                errors.append(f"{loc}: {msg}")
            else:
                errors.append(msg)
        return errors

    def statement_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        statement_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Bank Statement", "banking", db=db)
        context.update(
            self.statement_detail_context(
                db,
                str(auth.organization_id),
                statement_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/statement_detail.html", context
        )

    def apply_rules_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        statement_id: str,
    ) -> RedirectResponse:
        """Handle POST to apply categorization rules to a statement."""
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        service = TransactionCategorizationService()
        result = service.apply_rules_to_statement(db, org_id, coerce_uuid(statement_id))
        db.commit()

        msg = (
            f"{result.categorized_count}+suggested,"
            f"{result.high_confidence_count}+auto-applied,"
            f"{result.no_match_count}+no+match"
        )
        return RedirectResponse(
            url=f"/finance/banking/statements/{statement_id}?success={msg}",
            status_code=303,
        )

    def accept_suggestion_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        statement_id: str,
        line_id: str,
    ) -> RedirectResponse:
        """Handle POST to accept a categorization suggestion."""
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        service = TransactionCategorizationService()
        try:
            service.accept_suggestion(
                db, org_id, coerce_uuid(line_id), accepted_by=auth.person_id
            )
            db.commit()
        except ValueError as exc:
            logger.warning("Accept suggestion failed: %s", exc)
            return RedirectResponse(
                url=f"/finance/banking/statements/{statement_id}?error={exc}",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/finance/banking/statements/{statement_id}?success=Suggestion+accepted",
            status_code=303,
        )

    def reject_suggestion_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        statement_id: str,
        line_id: str,
    ) -> RedirectResponse:
        """Handle POST to reject a categorization suggestion."""
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        service = TransactionCategorizationService()
        try:
            service.reject_suggestion(db, org_id, coerce_uuid(line_id))
            db.commit()
        except ValueError as exc:
            logger.warning("Reject suggestion failed: %s", exc)
            return RedirectResponse(
                url=f"/finance/banking/statements/{statement_id}?error={exc}",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/finance/banking/statements/{statement_id}?success=Suggestion+rejected",
            status_code=303,
        )

    def delete_statement_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        statement_id: str,
    ) -> RedirectResponse:
        """Handle POST to delete a bank statement batch."""
        try:
            deleted = bank_statement_service.delete(
                db,
                coerce_uuid(auth.organization_id),
                coerce_uuid(statement_id),
            )
            if not deleted:
                return RedirectResponse(
                    url=f"/finance/banking/statements/{statement_id}?error=Statement+not+found",
                    status_code=303,
                )
            db.commit()
        except ValueError as exc:
            logger.warning("Delete statement failed: %s", exc)
            return RedirectResponse(
                url=f"/finance/banking/statements/{statement_id}?error={exc}",
                status_code=303,
            )

        return RedirectResponse(
            url="/finance/banking/statements?success=Statement+deleted",
            status_code=303,
        )

    async def match_statement_line_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        statement_id: str,
        line_id: str,
    ) -> Response:
        """Accept a GL transaction match for a statement line (JSON from Alpine.js)."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        body = await request.json()
        journal_line_id = body.get("journal_line_id")
        if not journal_line_id:
            return JSONResponse(
                content={"detail": "journal_line_id is required"}, status_code=400
            )

        svc = BankReconciliationService()
        user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)

        try:
            svc.match_statement_line(
                db=db,
                organization_id=org_id,
                statement_line_id=UUID(line_id),
                journal_line_id=UUID(str(journal_line_id)),
                matched_by=user_id,
            )
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError) as e:
            logger.warning("Statement line match failed: %s", e)
            return JSONResponse(content={"detail": str(e)}, status_code=400)

        return JSONResponse(content={"status": "ok"}, status_code=200)

    async def unmatch_statement_line_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        statement_id: str,
        line_id: str,
    ) -> Response:
        """Remove a direct match from a statement line (JSON from Alpine.js)."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        svc = BankReconciliationService()

        try:
            svc.unmatch_statement_line(
                db=db,
                organization_id=org_id,
                statement_line_id=UUID(line_id),
            )
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError) as e:
            logger.warning("Statement line unmatch failed: %s", e)
            return JSONResponse(content={"detail": str(e)}, status_code=400)

        return JSONResponse(content={"status": "ok"}, status_code=200)

    async def bulk_delete_statements_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk delete statements request."""
        from app.schemas.bulk_actions import BulkActionRequest
        from app.services.finance.banking.bulk import get_statement_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_statement_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_delete(req.ids)

    async def bulk_export_statements_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk export statements request."""
        from app.schemas.bulk_actions import BulkExportRequest
        from app.services.finance.banking.bulk import get_statement_bulk_service

        body = await request.json()
        req = BulkExportRequest(**body)
        service = get_statement_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_export(req.ids, req.format)

    def list_reconciliations_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Bank Reconciliations", "banking", db=db)
        context.update(
            self.list_reconciliations_context(
                db,
                str(auth.organization_id),
                account_id=account_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request,
            "finance/banking/reconciliations.html",
            context,
        )

    def reconciliation_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        account_id: str | None = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Reconciliation", "banking", db=db)
        context.update(
            self.reconciliation_form_context(
                db, str(auth.organization_id), account_id=account_id
            )
        )
        return templates.TemplateResponse(
            request,
            "finance/banking/reconciliation_form.html",
            context,
        )

    def reconciliation_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Bank Reconciliation", "banking", db=db)
        context.update(
            self.reconciliation_detail_context(
                db,
                str(auth.organization_id),
                reconciliation_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/reconciliation.html", context
        )

    def reconciliation_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Reconciliation Report", "banking", db=db)
        context.update(
            self.reconciliation_report_context(
                db,
                str(auth.organization_id),
                reconciliation_id,
            )
        )
        return templates.TemplateResponse(
            request,
            "finance/banking/reconciliation_report.html",
            context,
        )

    def list_payees_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        payee_type: str | None,
        page: int,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Payees", "banking", db=db)
        context.update(
            self.list_payees_context(
                db,
                str(auth.organization_id),
                search=search,
                payee_type=payee_type,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/payees.html", context
        )

    def payee_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Payee", "banking", db=db)
        context.update(self.payee_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/banking/payee_form.html", context
        )

    def payee_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payee_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Edit Payee", "banking", db=db)
        context.update(
            self.payee_form_context(
                db,
                str(auth.organization_id),
                payee_id=payee_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/payee_form.html", context
        )

    def list_rules_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_type: str | None,
        page: int,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Transaction Rules", "banking", db=db)
        context.update(
            self.list_rules_context(
                db,
                str(auth.organization_id),
                rule_type=rule_type,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/rules.html", context
        )

    def rule_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Transaction Rule", "banking", db=db)
        context.update(self.rule_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/banking/rule_form.html", context
        )

    def rule_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Edit Transaction Rule", "banking", db=db)
        context.update(
            self.rule_form_context(
                db,
                str(auth.organization_id),
                rule_id=rule_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/rule_form.html", context
        )

    def reorder_rules_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
        direction: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle POST to reorder a rule up or down.

        Returns the #results-container partial for HTMX swap.
        """
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        service = TransactionCategorizationService()
        service.swap_rule_order(db, org_id, UUID(rule_id), direction)
        db.commit()

        # Check if this is an HTMX request — return partial
        if request.headers.get("HX-Request"):
            context = base_context(request, auth, "Transaction Rules", "banking", db=db)
            context.update(self.list_rules_context(db, str(org_id)))
            return templates.TemplateResponse(
                request,
                "finance/banking/_rules_table.html",
                context,
            )

        # Fallback: full redirect
        return RedirectResponse(
            url="/finance/banking/rules?success=Record+saved+successfully",
            status_code=303,
        )

    # ─── Reconciliation POST handlers ───────────────────────────────────

    async def create_reconciliation_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        redirect_cls: type,
    ) -> Any:
        """Create a reconciliation from the web form and redirect to detail page."""
        from decimal import Decimal, InvalidOperation
        from uuid import UUID as _UUID

        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
            ReconciliationInput,
        )

        form = await request.form()
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            bank_account_id = _UUID(str(form.get("bank_account_id", "")))
            inp = ReconciliationInput(
                reconciliation_date=date.fromisoformat(
                    str(form.get("reconciliation_date", ""))
                ),
                period_start=date.fromisoformat(str(form.get("period_start", ""))),
                period_end=date.fromisoformat(str(form.get("period_end", ""))),
                statement_opening_balance=Decimal(
                    str(form.get("statement_opening_balance", "0"))
                ),
                statement_closing_balance=Decimal(
                    str(form.get("statement_closing_balance", "0"))
                ),
                notes=str(form.get("notes", "")) or None,
            )
        except (ValueError, InvalidOperation) as e:
            logger.warning("Invalid reconciliation form data: %s", e)
            # Re-render form with error
            context = base_context(
                request, auth, "New Reconciliation", "banking", db=db
            )
            context.update(self.reconciliation_form_context(db, str(org_id)))
            context["error"] = f"Invalid form data: {e}"
            return templates.TemplateResponse(
                request,
                "finance/banking/reconciliation_form.html",
                context,
            )

        svc = BankReconciliationService()
        try:
            user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)
            recon = svc.create_reconciliation(
                db=db,
                organization_id=org_id,
                bank_account_id=bank_account_id,
                input=inp,
                prepared_by=user_id,
            )
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError) as e:
            logger.warning("Reconciliation creation failed: %s", e)
            context = base_context(
                request, auth, "New Reconciliation", "banking", db=db
            )
            context.update(self.reconciliation_form_context(db, str(org_id)))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request,
                "finance/banking/reconciliation_form.html",
                context,
            )

        return redirect_cls(
            url=f"/finance/banking/reconciliations/{recon.reconciliation_id}",
            status_code=303,
        )

    async def reconciliation_action_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
        action: str,
    ) -> Response:
        """Handle reconciliation lifecycle actions (auto-match, submit, approve, reject)."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        await request.form()  # consume form body for CSRF validation
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        svc = BankReconciliationService()
        recon_uuid = UUID(reconciliation_id)
        user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)

        try:
            if action == "auto_match":
                svc.auto_match(
                    db=db,
                    organization_id=org_id,
                    reconciliation_id=recon_uuid,
                    created_by=user_id,
                )
            elif action == "submit":
                svc.submit_for_review(db, org_id, recon_uuid)
            elif action == "approve":
                if not user_id:
                    raise HTTPException(status_code=401, detail="User ID required")
                svc.approve(
                    db=db,
                    organization_id=org_id,
                    reconciliation_id=recon_uuid,
                    approved_by=user_id,
                )
            elif action == "reject":
                if not user_id:
                    raise HTTPException(status_code=401, detail="User ID required")
                notes = request.query_params.get("notes", "Rejected via UI")
                svc.reject(
                    db=db,
                    organization_id=org_id,
                    reconciliation_id=recon_uuid,
                    rejected_by=user_id,
                    notes=notes,
                )
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError) as e:
            logger.warning("Reconciliation %s failed: %s", action, e)
            raise HTTPException(status_code=400, detail=str(e))

        # HTMX requests get a 200 + HX-Refresh header
        if request.headers.get("HX-Request"):
            return Response(
                content="",
                status_code=200,
                headers={"HX-Refresh": "true"},
            )
        return RedirectResponse(
            url=f"/finance/banking/reconciliations/{reconciliation_id}",
            status_code=303,
        )

    async def reconciliation_match_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
    ) -> Response:
        """Add a single match from Alpine.js fetch (JSON body)."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        body = await request.json()
        svc = BankReconciliationService()
        user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)

        try:
            from app.models.finance.banking.bank_reconciliation import (
                ReconciliationMatchType,
            )
            from app.services.finance.banking.bank_reconciliation import (
                ReconciliationMatchInput,
            )

            match_type_str = body.get("match_type", "manual")
            try:
                match_type = ReconciliationMatchType(match_type_str)
            except ValueError:
                match_type = ReconciliationMatchType.manual

            match_input = ReconciliationMatchInput(
                statement_line_id=UUID(str(body["statement_line_id"])),
                journal_line_id=UUID(str(body["journal_line_id"])),
                match_type=match_type,
            )
            svc.add_match(
                db=db,
                organization_id=org_id,
                reconciliation_id=UUID(reconciliation_id),
                input=match_input,
                created_by=user_id,
            )
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError, KeyError) as e:
            logger.warning("Match creation failed: %s", e)
            return JSONResponse(content={"detail": str(e)}, status_code=400)

        return JSONResponse(content={"status": "ok"}, status_code=200)

    async def reconciliation_multi_match_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
    ) -> Response:
        """Add a multi-match from Alpine.js fetch (JSON body)."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        body = await request.json()
        svc = BankReconciliationService()
        user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)

        try:
            from decimal import Decimal

            svc.add_multi_match(
                db=db,
                organization_id=org_id,
                reconciliation_id=UUID(reconciliation_id),
                statement_line_ids=[UUID(s) for s in body["statement_line_ids"]],
                journal_line_ids=[UUID(s) for s in body["journal_line_ids"]],
                tolerance=Decimal(str(body.get("tolerance", "0.01"))),
                notes=body.get("notes"),
                created_by=user_id,
            )
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError, KeyError) as e:
            logger.warning("Multi-match creation failed: %s", e)
            return JSONResponse(content={"detail": str(e)}, status_code=400)

        return JSONResponse(content={"status": "ok"}, status_code=200)

    # ─────────────────────────────────────────────────────────────
    # Bank Account Create / Update (form POST handlers)
    # ─────────────────────────────────────────────────────────────

    async def create_account_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> Response:
        """Handle POST to create a new bank account from form data."""
        from app.models.finance.banking.bank_account import BankAccountType
        from app.services.finance.banking.bank_account import (
            BankAccountInput,
            bank_account_service,
        )

        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        form = await request.form()
        try:
            data = BankAccountInput(
                bank_name=str(form.get("bank_name", "")),
                account_number=str(form.get("account_number", "")),
                account_name=str(form.get("account_name", "")),
                gl_account_id=UUID(str(form["gl_account_id"])),
                currency_code=str(form.get("currency_code", "NGN")),
                account_type=BankAccountType(str(form.get("account_type", "checking"))),
                bank_code=str(form.get("bank_code", "")) or None,
                branch_code=str(form.get("branch_code", "")) or None,
                branch_name=str(form.get("branch_name", "")) or None,
                iban=str(form.get("iban", "")) or None,
                contact_name=str(form.get("contact_name", "")) or None,
                contact_phone=str(form.get("contact_phone", "")) or None,
                contact_email=str(form.get("contact_email", "")) or None,
                notes=str(form.get("notes", "")) or None,
                allow_overdraft="allow_overdraft" in form,
                overdraft_limit=Decimal(str(form["overdraft_limit"]))
                if form.get("overdraft_limit")
                else None,
            )
            account = bank_account_service.create(
                db, org_id, data, coerce_uuid(user_id) if user_id else None
            )
            db.commit()
            return RedirectResponse(
                url=f"/finance/banking/accounts/{account.bank_account_id}",
                status_code=303,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Bank account creation failed: %s", exc)
            return RedirectResponse(
                url=f"/finance/banking/accounts/new?error={exc}",
                status_code=303,
            )

    async def update_account_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
    ) -> Response:
        """Handle POST to update an existing bank account from form data."""
        from app.models.finance.banking.bank_account import BankAccountType
        from app.services.finance.banking.bank_account import (
            BankAccountInput,
            bank_account_service,
        )

        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Load existing account to preserve account_number (read-only in edit)
        existing = db.get(BankAccount, coerce_uuid(account_id))
        if not existing or existing.organization_id != coerce_uuid(org_id):
            raise HTTPException(status_code=404, detail="Bank account not found")

        form = await request.form()
        try:
            data = BankAccountInput(
                bank_name=str(form.get("bank_name", "")),
                account_number=existing.account_number,
                account_name=str(form.get("account_name", "")),
                gl_account_id=UUID(str(form["gl_account_id"])),
                currency_code=str(form.get("currency_code", existing.currency_code)),
                account_type=BankAccountType(str(form.get("account_type", "checking"))),
                bank_code=str(form.get("bank_code", "")) or None,
                branch_code=str(form.get("branch_code", "")) or None,
                branch_name=str(form.get("branch_name", "")) or None,
                iban=str(form.get("iban", "")) or None,
                contact_name=str(form.get("contact_name", "")) or None,
                contact_phone=str(form.get("contact_phone", "")) or None,
                contact_email=str(form.get("contact_email", "")) or None,
                notes=str(form.get("notes", "")) or None,
                allow_overdraft="allow_overdraft" in form,
                overdraft_limit=Decimal(str(form["overdraft_limit"]))
                if form.get("overdraft_limit")
                else None,
            )
            bank_account_service.update(
                db,
                org_id,
                coerce_uuid(account_id),
                data,
                coerce_uuid(user_id) if user_id else None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/finance/banking/accounts/{account_id}",
                status_code=303,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Bank account update failed: %s", exc)
            return RedirectResponse(
                url=f"/finance/banking/accounts/{account_id}/edit?error={exc}",
                status_code=303,
            )

    # ─────────────────────────────────────────────────────────────
    # Payee Create / Update (form POST handlers)
    # ─────────────────────────────────────────────────────────────

    async def create_payee_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> Response:
        """Handle POST to create a new payee from form data."""
        from app.models.finance.banking.payee import Payee, PayeeType

        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        form = await request.form()
        try:
            payee = Payee(
                organization_id=coerce_uuid(org_id),
                payee_name=str(form.get("payee_name", "")),
                payee_type=PayeeType(str(form.get("payee_type", "OTHER"))),
                is_active="is_active" in form,
                name_patterns=str(form.get("name_patterns", "")) or None,
                default_account_id=UUID(str(form["default_account_id"]))
                if form.get("default_account_id")
                else None,
                default_tax_code_id=UUID(str(form["default_tax_code_id"]))
                if form.get("default_tax_code_id")
                else None,
                supplier_id=UUID(str(form["supplier_id"]))
                if form.get("supplier_id")
                else None,
                customer_id=UUID(str(form["customer_id"]))
                if form.get("customer_id")
                else None,
                notes=str(form.get("notes", "")) or None,
                created_by=coerce_uuid(user_id) if user_id else None,
            )
            db.add(payee)
            db.flush()
            db.commit()
            logger.info("Created payee %s: %s", payee.payee_id, payee.payee_name)
            return RedirectResponse(
                url="/finance/banking/payees",
                status_code=303,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Payee creation failed: %s", exc)
            return RedirectResponse(
                url=f"/finance/banking/payees/new?error={exc}",
                status_code=303,
            )

    async def update_payee_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payee_id: str,
    ) -> Response:
        """Handle POST to update an existing payee from form data."""
        from app.models.finance.banking.payee import Payee, PayeeType

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        form = await request.form()
        payee = db.get(Payee, coerce_uuid(payee_id))
        if not payee or payee.organization_id != coerce_uuid(org_id):
            raise HTTPException(status_code=404, detail="Payee not found")

        try:
            payee.payee_name = str(form.get("payee_name", payee.payee_name))
            ptype_raw = str(form.get("payee_type", ""))
            if ptype_raw:
                payee.payee_type = PayeeType(ptype_raw)
            payee.is_active = "is_active" in form
            payee.name_patterns = str(form.get("name_patterns", "")) or None
            payee.default_account_id = (
                UUID(str(form["default_account_id"]))
                if form.get("default_account_id")
                else None
            )
            payee.default_tax_code_id = (
                UUID(str(form["default_tax_code_id"]))
                if form.get("default_tax_code_id")
                else None
            )
            payee.supplier_id = (
                UUID(str(form["supplier_id"])) if form.get("supplier_id") else None
            )
            payee.customer_id = (
                UUID(str(form["customer_id"])) if form.get("customer_id") else None
            )
            payee.notes = str(form.get("notes", "")) or None
            db.flush()
            db.commit()
            logger.info("Updated payee %s: %s", payee.payee_id, payee.payee_name)
            return RedirectResponse(
                url="/finance/banking/payees",
                status_code=303,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Payee update failed: %s", exc)
            return RedirectResponse(
                url=f"/finance/banking/payees/{payee_id}?error={exc}",
                status_code=303,
            )


banking_web_service = BankingWebService()
