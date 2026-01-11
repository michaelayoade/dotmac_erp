"""
Banking web view service.

Provides view-focused data for banking web routes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.ifrs.banking.bank_account import BankAccount, BankAccountStatus
from app.models.ifrs.banking.bank_reconciliation import (
    BankReconciliation,
    BankReconciliationLine,
    ReconciliationStatus,
)
from app.models.ifrs.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    BankStatementStatus,
)
from app.models.ifrs.gl.account import Account
from app.models.ifrs.gl.journal_entry import JournalEntry, JournalStatus
from app.models.ifrs.gl.journal_entry_line import JournalEntryLine
from app.services.common import coerce_uuid


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_date(value: Optional[date | datetime]) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%d")


def _format_currency(amount: Optional[Decimal], currency: str = "USD") -> str:
    if amount is None:
        return ""
    value = Decimal(str(amount))
    if currency == "USD":
        return f"${value:,.2f}"
    return f"{currency} {value:,.2f}"


def _parse_account_status(value: Optional[str]) -> Optional[BankAccountStatus]:
    if not value:
        return None
    try:
        return BankAccountStatus(value)
    except ValueError:
        return None


def _parse_statement_status(value: Optional[str]) -> Optional[BankStatementStatus]:
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
        return None


def _statement_status_label(status: BankStatementStatus) -> str:
    if status == BankStatementStatus.processing:
        return "in_progress"
    if status == BankStatementStatus.closed:
        return "reconciled"
    return str(status.value)


def _parse_reconciliation_status(value: Optional[str]) -> Optional[ReconciliationStatus]:
    if not value:
        return None
    try:
        return ReconciliationStatus(value)
    except ValueError:
        return None


def _account_view(account: BankAccount) -> dict:
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
        "currency_code": account.currency_code,
        "gl_account_id": account.gl_account_id,
        "status": account.status.value if account.status else "",
        "last_statement_balance": account.last_statement_balance,
        "last_statement_date": _format_date(account.last_statement_date),
        "last_reconciled_date": _format_date(account.last_reconciled_date),
        "last_reconciled_balance": account.last_reconciled_balance,
        "contact_name": account.contact_name,
        "contact_phone": account.contact_phone,
        "contact_email": account.contact_email,
        "notes": account.notes,
        "allow_overdraft": account.allow_overdraft,
        "overdraft_limit": account.overdraft_limit,
    }


def _statement_view(statement: BankStatement) -> dict:
    account = statement.bank_account
    return {
        "statement_id": statement.statement_id,
        "statement_number": statement.statement_number,
        "statement_date": _format_date(statement.statement_date),
        "period_start": _format_date(statement.period_start),
        "period_end": _format_date(statement.period_end),
        "opening_balance": statement.opening_balance,
        "closing_balance": statement.closing_balance,
        "matched_lines": statement.matched_lines,
        "unmatched_lines": statement.unmatched_lines,
        "total_lines": statement.total_lines,
        "total_credits": statement.total_credits,
        "total_debits": statement.total_debits,
        "bank_account_id": statement.bank_account_id,
        "bank_name": account.bank_name if account else "",
        "account_number": account.account_number if account else "",
        "status": _statement_status_label(statement.status),
    }


def _statement_line_view(line: BankStatementLine) -> dict:
    return {
        "line_id": line.line_id,
        "line_number": line.line_number,
        "transaction_date": _format_date(line.transaction_date),
        "transaction_type": line.transaction_type.value if line.transaction_type else "",
        "amount": line.amount,
        "description": line.description,
        "reference": line.reference,
        "payee_payer": line.payee_payer,
        "bank_reference": line.bank_reference,
        "running_balance": line.running_balance,
        "is_matched": line.is_matched,
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


def _gl_line_view(line: JournalEntryLine, entry: JournalEntry) -> dict:
    return {
        "line_id": line.line_id,
        "entry_date": _format_date(entry.entry_date),
        "description": line.description or entry.description,
        "reference": entry.reference,
        "debit_amount": line.debit_amount,
        "credit_amount": line.credit_amount,
    }


def _line_amount(line: BankReconciliationLine) -> Decimal:
    amount = line.statement_amount
    if amount is None:
        amount = line.gl_amount
    if amount is None:
        return Decimal("0")
    return Decimal(str(amount))


class BankingWebService:
    """View service for banking web routes."""

    @staticmethod
    def list_accounts_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        status: Optional[str],
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
        total_balance = (
            query.with_entities(
                func.coalesce(func.sum(BankAccount.last_statement_balance), 0)
            ).scalar()
            or Decimal("0")
        )
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
        account_id: Optional[str] = None,
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

        return {
            "account": _account_view(account) if account else None,
            "gl_accounts": gl_accounts,
        }

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

        return {"account": _account_view(account) if account else None}

    @staticmethod
    def list_statements_context(
        db: Session,
        organization_id: str,
        account_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
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
            query = query.filter(BankStatement.bank_account_id == coerce_uuid(account_id))
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
        unmatched_lines = sum(statement.unmatched_lines or 0 for statement in statements)

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "statements": [_statement_view(statement) for statement in statements],
            "accounts": [_account_view(account) for account in accounts],
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
        return {"accounts": [_account_view(account) for account in accounts]}

    @staticmethod
    def statement_detail_context(
        db: Session,
        organization_id: str,
        statement_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        statement = db.get(BankStatement, coerce_uuid(statement_id))
        if not statement or statement.organization_id != org_id:
            return {"statement": None, "lines": []}

        lines = [_statement_line_view(line) for line in statement.lines]
        return {"statement": _statement_view(statement), "lines": lines}

    @staticmethod
    def list_reconciliations_context(
        db: Session,
        organization_id: str,
        account_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
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
            query.with_entities(func.count(BankReconciliation.reconciliation_id)).scalar()
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
            1 for recon in reconciliations if recon.status == ReconciliationStatus.pending_review
        )
        approved_count = sum(
            1 for recon in reconciliations if recon.status == ReconciliationStatus.approved
        )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "reconciliations": [_reconciliation_view(recon) for recon in reconciliations],
            "accounts": [_account_view(account) for account in accounts],
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
        }

    @staticmethod
    def reconciliation_form_context(
        db: Session,
        organization_id: str,
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
        return {"accounts": [_account_view(account) for account in accounts]}

    @staticmethod
    def reconciliation_detail_context(
        db: Session,
        organization_id: str,
        reconciliation_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        reconciliation = db.get(BankReconciliation, coerce_uuid(reconciliation_id))
        if not reconciliation or reconciliation.organization_id != org_id:
            return {
                "reconciliation": None,
                "lines": [],
                "unmatched_statement_lines": [],
                "unmatched_gl_lines": [],
            }

        bank_account = reconciliation.bank_account

        statement_lines = (
            db.query(BankStatementLine)
            .join(BankStatement, BankStatementLine.statement_id == BankStatement.statement_id)
            .filter(
                BankStatement.bank_account_id == reconciliation.bank_account_id,
                BankStatementLine.is_matched.is_(False),
                BankStatementLine.transaction_date >= reconciliation.period_start,
                BankStatementLine.transaction_date <= reconciliation.period_end,
            )
            .order_by(BankStatementLine.transaction_date, BankStatementLine.line_number)
            .all()
        )

        gl_lines = []
        if bank_account:
            gl_lines = (
                db.query(JournalEntryLine, JournalEntry)
                .join(JournalEntry, JournalEntryLine.journal_entry_id == JournalEntry.journal_entry_id)
                .filter(
                    JournalEntryLine.account_id == bank_account.gl_account_id,
                    JournalEntry.status == JournalStatus.POSTED,
                    JournalEntry.entry_date >= reconciliation.period_start,
                    JournalEntry.entry_date <= reconciliation.period_end,
                )
                .order_by(JournalEntry.entry_date, JournalEntryLine.line_number)
                .all()
            )

        unmatched_statement_lines = [
            _statement_line_view(line) for line in statement_lines
        ]
        unmatched_gl_lines = [_gl_line_view(line, entry) for line, entry in gl_lines]

        return {
            "reconciliation": _reconciliation_view(reconciliation),
            "lines": [_reconciliation_line_view(line) for line in reconciliation.lines],
            "unmatched_statement_lines": unmatched_statement_lines,
            "unmatched_gl_lines": unmatched_gl_lines,
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

        total_matched = sum((_line_amount(line) for line in matched_lines), Decimal("0"))
        total_deposits = sum((_line_amount(line) for line in outstanding_deposits), Decimal("0"))
        total_payments = sum((_line_amount(line) for line in outstanding_payments), Decimal("0"))
        total_adjustments = sum((_line_amount(line) for line in adjustments), Decimal("0"))

        statement_balance = Decimal(str(reconciliation.statement_closing_balance))
        gl_balance = Decimal(str(reconciliation.gl_closing_balance))
        adjusted_statement = statement_balance - total_payments + total_deposits
        adjusted_gl = gl_balance + total_adjustments
        difference = adjusted_statement - adjusted_gl

        report = {
            "reconciliation": recon_view,
            "summary": {
                "statement_balance": _format_currency(statement_balance, reconciliation.currency_code),
                "gl_balance": _format_currency(gl_balance, reconciliation.currency_code),
                "adjusted_book_balance": _format_currency(
                    adjusted_statement, reconciliation.currency_code
                ),
                "difference": _format_currency(difference, reconciliation.currency_code),
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
                "items": [_reconciliation_line_view(line) for line in outstanding_deposits],
            },
            "outstanding_payments": {
                "count": len(outstanding_payments),
                "total": _format_currency(total_payments, reconciliation.currency_code),
                "items": [_reconciliation_line_view(line) for line in outstanding_payments],
            },
            "adjustments": {
                "count": len(adjustments),
                "total": _format_currency(total_adjustments, reconciliation.currency_code),
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
        search: Optional[str] = None,
        payee_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        """Context for payees list page."""
        from app.models.ifrs.banking.payee import Payee, PayeeType

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
            accounts = db.query(Account).filter(Account.account_id.in_(account_ids)).all()
            account_map = {a.account_id: f"{a.account_code} - {a.account_name}" for a in accounts}

        payee_list = []
        for p in payees:
            payee_list.append({
                "payee_id": str(p.payee_id),
                "payee_name": p.payee_name,
                "payee_type": p.payee_type.value if p.payee_type else "",
                "name_patterns": p.name_patterns or "",
                "default_account": account_map.get(p.default_account_id, ""),
                "match_count": p.match_count,
                "last_matched": _format_date(p.last_matched_at) if p.last_matched_at else "Never",
            })

        return {
            "payees": payee_list,
            "payee_types": [{"value": t.value, "label": t.value.replace("_", " ").title()} for t in PayeeType],
            "search": search or "",
            "selected_type": payee_type or "",
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page,
            },
        }

    @staticmethod
    def payee_form_context(
        db: Session,
        organization_id: str,
        payee_id: Optional[str] = None,
    ) -> dict:
        """Context for payee create/edit form."""
        from app.models.ifrs.banking.payee import Payee, PayeeType

        org_id = coerce_uuid(organization_id)

        # Get GL accounts for dropdown
        accounts = (
            db.query(Account)
            .filter(Account.organization_id == org_id, Account.is_active == True)
            .order_by(Account.account_code)
            .all()
        )

        account_options = [
            {"value": str(a.account_id), "label": f"{a.account_code} - {a.account_name}"}
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
                "default_account_id": str(payee.default_account_id) if payee.default_account_id else "",
                "notes": payee.notes or "",
                "is_active": payee.is_active,
            }

        return {
            "payee": payee_data,
            "is_edit": payee is not None,
            "payee_types": [{"value": t.value, "label": t.value.replace("_", " ").title()} for t in PayeeType],
            "accounts": account_options,
        }

    # =========================================================================
    # Transaction Rule Context Methods
    # =========================================================================

    @staticmethod
    def list_rules_context(
        db: Session,
        organization_id: str,
        rule_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        """Context for transaction rules list page."""
        from app.models.ifrs.banking.transaction_rule import TransactionRule, RuleType, RuleAction

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
            query.order_by(TransactionRule.priority.desc(), TransactionRule.rule_name)
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        # Get GL accounts for display
        account_map = {}
        account_ids = [r.target_account_id for r in rules if r.target_account_id]
        if account_ids:
            accounts = db.query(Account).filter(Account.account_id.in_(account_ids)).all()
            account_map = {a.account_id: f"{a.account_code} - {a.account_name}" for a in accounts}

        rule_list = []
        for r in rules:
            rule_list.append({
                "rule_id": str(r.rule_id),
                "rule_name": r.rule_name,
                "description": r.description or "",
                "rule_type": r.rule_type.value if r.rule_type else "",
                "action": r.action.value if r.action else "",
                "target_account": account_map.get(r.target_account_id, ""),
                "priority": r.priority,
                "auto_apply": r.auto_apply,
                "is_active": r.is_active,
                "match_count": r.match_count,
                "success_rate": f"{r.success_rate:.0f}%" if r.success_count + r.reject_count > 0 else "N/A",
            })

        return {
            "rules": rule_list,
            "rule_types": [{"value": t.value, "label": t.value.replace("_", " ").title()} for t in RuleType],
            "selected_type": rule_type or "",
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page,
            },
        }

    @staticmethod
    def rule_form_context(
        db: Session,
        organization_id: str,
        rule_id: Optional[str] = None,
    ) -> dict:
        """Context for transaction rule create/edit form."""
        from app.models.ifrs.banking.transaction_rule import TransactionRule, RuleType, RuleAction
        from app.models.ifrs.banking.payee import Payee

        org_id = coerce_uuid(organization_id)

        # Get GL accounts for dropdown
        accounts = (
            db.query(Account)
            .filter(Account.organization_id == org_id, Account.is_active == True)
            .order_by(Account.account_code)
            .all()
        )

        account_options = [
            {"value": str(a.account_id), "label": f"{a.account_code} - {a.account_name}"}
            for a in accounts
        ]

        # Get bank accounts for dropdown
        bank_accounts = (
            db.query(BankAccount)
            .filter(BankAccount.organization_id == org_id, BankAccount.status == BankAccountStatus.active)
            .order_by(BankAccount.account_name)
            .all()
        )

        bank_account_options = [
            {"value": str(ba.bank_account_id), "label": f"{ba.bank_name} - {ba.account_name}"}
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
            {"value": str(p.payee_id), "label": p.payee_name}
            for p in payees
        ]

        rule = None
        if rule_id:
            rule = db.get(TransactionRule, coerce_uuid(rule_id))
            if rule and rule.organization_id != org_id:
                rule = None

        rule_data = None
        if rule:
            rule_data = {
                "rule_id": str(rule.rule_id),
                "rule_name": rule.rule_name,
                "description": rule.description or "",
                "rule_type": rule.rule_type.value if rule.rule_type else "",
                "conditions": rule.conditions or {},
                "action": rule.action.value if rule.action else "",
                "target_account_id": str(rule.target_account_id) if rule.target_account_id else "",
                "bank_account_id": str(rule.bank_account_id) if rule.bank_account_id else "",
                "payee_id": str(rule.payee_id) if rule.payee_id else "",
                "priority": rule.priority,
                "auto_apply": rule.auto_apply,
                "min_confidence": rule.min_confidence,
                "applies_to_credits": rule.applies_to_credits,
                "applies_to_debits": rule.applies_to_debits,
                "is_active": rule.is_active,
            }

        return {
            "rule": rule_data,
            "is_edit": rule is not None,
            "rule_types": [{"value": t.value, "label": t.value.replace("_", " ").title()} for t in RuleType],
            "actions": [{"value": a.value, "label": a.value.replace("_", " ").title()} for a in RuleAction],
            "accounts": account_options,
            "bank_accounts": bank_account_options,
            "payees": payee_options,
        }


banking_web_service = BankingWebService()
