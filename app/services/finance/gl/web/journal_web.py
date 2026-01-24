"""
GL Journal Web Service - Journal entry web view methods.

Provides view-focused data and operations for GL journal entry web routes.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.common import coerce_uuid
from app.services.finance.platform.org_context import org_context_service
from app.templates import templates
from app.web.deps import base_context, WebAuthContext
from app.services.finance.gl.web.base import (
    format_date,
    format_currency,
    journal_entry_view,
    journal_line_view,
    parse_date,
    parse_status,
    period_option_view,
)

logger = logging.getLogger(__name__)


class JournalWebService:
    """Web service methods for GL journal entries."""

    # =========================================================================
    # Context Methods
    # =========================================================================

    @staticmethod
    def list_journals_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        """Get context for journal listing page."""
        logger.debug(
            "list_journals_context: org=%s search=%r status=%s page=%d",
            organization_id, search, status, page
        )
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = parse_status(status)
        from_date = parse_date(start_date)
        to_date = parse_date(end_date)

        query = db.query(JournalEntry).filter(JournalEntry.organization_id == org_id)

        if status_value:
            query = query.filter(JournalEntry.status == status_value)
        if from_date:
            query = query.filter(JournalEntry.posting_date >= from_date)
        if to_date:
            query = query.filter(JournalEntry.posting_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (JournalEntry.journal_number.ilike(search_pattern))
                | (JournalEntry.description.ilike(search_pattern))
                | (JournalEntry.reference.ilike(search_pattern))
            )

        total_count = query.with_entities(func.count(JournalEntry.journal_entry_id)).scalar() or 0
        entries = (
            query.order_by(JournalEntry.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        entries_view = []
        for entry in entries:
            entries_view.append(
                {
                    "journal_entry_id": entry.journal_entry_id,
                    "entry_number": entry.journal_number,
                    "entry_date": format_date(entry.entry_date),
                    "description": entry.description,
                    "source_module": entry.source_module or "MANUAL",
                    "total_debit": format_currency(entry.total_debit, entry.currency_code),
                    "total_credit": format_currency(entry.total_credit, entry.currency_code),
                    "status": entry.status.value,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        logger.debug("list_journals_context: found %d entries", total_count)

        return {
            "entries": entries_view,
            "search": search,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def journal_form_context(
        db: Session,
        organization_id: str,
        journal_entry_id: Optional[str] = None,
    ) -> dict:
        """Get context for journal create/edit form."""
        logger.debug(
            "journal_form_context: org=%s journal_entry_id=%s",
            organization_id, journal_entry_id
        )
        org_id = coerce_uuid(organization_id)

        entry = None
        lines_view = []

        if journal_entry_id:
            entry = db.get(JournalEntry, coerce_uuid(journal_entry_id))
            if entry and entry.organization_id == org_id:
                lines = (
                    db.query(JournalEntryLine)
                    .filter(JournalEntryLine.journal_entry_id == entry.journal_entry_id)
                    .order_by(JournalEntryLine.line_number)
                    .all()
                )

                account_ids = [line.account_id for line in lines]
                accounts = db.query(Account).filter(Account.account_id.in_(account_ids)).all()
                account_map = {a.account_id: (a.account_code, a.account_name) for a in accounts}

                for line in lines:
                    acct = account_map.get(line.account_id, ("", ""))
                    lines_view.append(journal_line_view(line, acct[1], acct[0]))
            else:
                entry = None

        # Get available accounts for selection
        accounts = (
            db.query(Account)
            .filter(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
                Account.is_posting_allowed.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )

        accounts_view = [
            {
                "account_id": str(a.account_id),
                "account_code": a.account_code,
                "account_name": a.account_name,
                "display": f"{a.account_code} - {a.account_name}",
            }
            for a in accounts
        ]

        # Get open periods
        periods = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.status == PeriodStatus.OPEN,
            )
            .order_by(FiscalPeriod.start_date.desc())
            .all()
        )

        return {
            "entry": journal_entry_view(entry) if entry else None,
            "lines": lines_view,
            "accounts": accounts_view,
            "periods": [period_option_view(p) for p in periods],
            "journal_types": [jt.value for jt in JournalType],
            "today": format_date(date.today()),
        }

    @staticmethod
    def journal_detail_context(
        db: Session,
        organization_id: str,
        journal_entry_id: str,
    ) -> dict:
        """Get context for journal detail page."""
        logger.debug(
            "journal_detail_context: org=%s journal_entry_id=%s",
            organization_id, journal_entry_id
        )
        org_id = coerce_uuid(organization_id)
        entry = db.get(JournalEntry, coerce_uuid(journal_entry_id))

        if not entry or entry.organization_id != org_id:
            return {"entry": None, "lines": []}

        lines = (
            db.query(JournalEntryLine)
            .filter(JournalEntryLine.journal_entry_id == entry.journal_entry_id)
            .order_by(JournalEntryLine.line_number)
            .all()
        )

        account_ids = [line.account_id for line in lines]
        accounts = db.query(Account).filter(Account.account_id.in_(account_ids)).all()
        account_map = {a.account_id: (a.account_code, a.account_name) for a in accounts}

        lines_view = []
        for line in lines:
            acct = account_map.get(line.account_id, ("", ""))
            lines_view.append(journal_line_view(line, acct[1], acct[0]))

        return {
            "entry": journal_entry_view(entry),
            "lines": lines_view,
        }

    # =========================================================================
    # Business Logic Methods
    # =========================================================================

    @staticmethod
    def create_journal(
        db: Session,
        organization_id: str,
        user_id: str,
        journal_type: str,
        fiscal_period_id: str,
        entry_date: str,
        posting_date: str,
        description: str,
        reference: str = "",
        currency_code: str = settings.default_functional_currency_code,
        exchange_rate: str = "1.0",
        lines_json: str = "[]",
    ) -> tuple[Optional[JournalEntry], Optional[str]]:
        """Create a new journal entry with lines. Returns (entry, error)."""
        logger.debug(
            "create_journal: org=%s type=%s date=%s",
            organization_id, journal_type, entry_date
        )
        org_id = coerce_uuid(organization_id)
        uid = coerce_uuid(user_id)

        # Validate journal type
        try:
            journal_type_enum = JournalType(journal_type)
        except ValueError:
            return None, f"Invalid journal type: {journal_type}"

        # Validate fiscal period
        period_id = coerce_uuid(fiscal_period_id)
        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            return None, "Invalid fiscal period"

        # Parse dates
        entry_dt = parse_date(entry_date)
        posting_dt = parse_date(posting_date)
        if not entry_dt:
            return None, "Invalid entry date"
        if not posting_dt:
            return None, "Invalid posting date"

        # Parse exchange rate
        try:
            rate = Decimal(exchange_rate)
        except (ValueError, TypeError):
            rate = Decimal("1.0")

        # Parse lines
        try:
            lines_data = json.loads(lines_json) if lines_json else []
        except json.JSONDecodeError:
            return None, "Invalid journal lines format"

        if not lines_data:
            return None, "Journal entry must have at least one line"

        # Validate lines and calculate totals
        total_debit = Decimal("0")
        total_credit = Decimal("0")
        validated_lines = []

        for idx, line_data in enumerate(lines_data):
            account_id = line_data.get("account_id")
            if not account_id:
                return None, f"Line {idx + 1}: Account is required"

            account = db.get(Account, coerce_uuid(account_id))
            if not account or account.organization_id != org_id:
                return None, f"Line {idx + 1}: Invalid account"

            try:
                debit = Decimal(str(line_data.get("debit", "0") or "0"))
                credit = Decimal(str(line_data.get("credit", "0") or "0"))
            except (ValueError, TypeError):
                return None, f"Line {idx + 1}: Invalid amount"

            if debit == 0 and credit == 0:
                return None, f"Line {idx + 1}: Either debit or credit must be non-zero"

            if debit != 0 and credit != 0:
                return None, f"Line {idx + 1}: Cannot have both debit and credit on same line"

            total_debit += debit
            total_credit += credit
            validated_lines.append({
                "account_id": coerce_uuid(account_id),
                "description": line_data.get("description", ""),
                "debit": debit,
                "credit": credit,
            })

        # Check balance
        if total_debit != total_credit:
            return None, f"Journal is out of balance. Debit: {total_debit}, Credit: {total_credit}"

        # Generate journal number
        count = (
            db.query(JournalEntry)
            .filter(JournalEntry.organization_id == org_id)
            .count()
        )
        journal_number = f"JE-{count + 1:06d}"

        try:
            entry = JournalEntry(
                organization_id=org_id,
                journal_number=journal_number,
                journal_type=journal_type_enum,
                entry_date=entry_dt,
                posting_date=posting_dt,
                fiscal_period_id=period_id,
                description=description,
                reference=reference or None,
                currency_code=currency_code,
                exchange_rate=rate,
                total_debit=total_debit,
                total_credit=total_credit,
                total_debit_functional=total_debit * rate,
                total_credit_functional=total_credit * rate,
                status=JournalStatus.DRAFT,
                created_by_user_id=uid,
            )
            db.add(entry)
            db.flush()

            # Create lines
            for idx, line_data in enumerate(validated_lines):
                line = JournalEntryLine(
                    journal_entry_id=entry.journal_entry_id,
                    line_number=idx + 1,
                    account_id=line_data["account_id"],
                    description=line_data["description"] or None,
                    debit_amount=line_data["debit"],
                    credit_amount=line_data["credit"],
                    debit_amount_functional=line_data["debit"] * rate,
                    credit_amount_functional=line_data["credit"] * rate,
                )
                db.add(line)

            db.commit()
            db.refresh(entry)
            logger.info("create_journal: created %s for org %s", entry.journal_number, org_id)
            return entry, None

        except Exception as e:
            db.rollback()
            logger.exception("create_journal: failed for org %s", org_id)
            return None, f"Failed to create journal entry: {str(e)}"

    @staticmethod
    def update_journal(
        db: Session,
        organization_id: str,
        entry_id: str,
        journal_type: str,
        fiscal_period_id: str,
        entry_date: str,
        posting_date: str,
        description: str,
        reference: str = "",
        currency_code: str = settings.default_functional_currency_code,
        exchange_rate: str = "1.0",
        lines_json: str = "[]",
    ) -> tuple[Optional[JournalEntry], Optional[str]]:
        """Update a journal entry. Only DRAFT entries can be updated. Returns (entry, error)."""
        logger.debug(
            "update_journal: org=%s entry_id=%s",
            organization_id, entry_id
        )
        org_id = coerce_uuid(organization_id)
        ent_id = coerce_uuid(entry_id)

        entry = db.get(JournalEntry, ent_id)
        if not entry or entry.organization_id != org_id:
            return None, "Journal entry not found"

        if entry.status != JournalStatus.DRAFT:
            return None, f"Cannot edit journal entry with status: {entry.status.value}"

        # Validate journal type
        try:
            journal_type_enum = JournalType(journal_type)
        except ValueError:
            return None, f"Invalid journal type: {journal_type}"

        # Validate fiscal period
        period_id = coerce_uuid(fiscal_period_id)
        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            return None, "Invalid fiscal period"

        # Parse dates
        entry_dt = parse_date(entry_date)
        posting_dt = parse_date(posting_date)
        if not entry_dt:
            return None, "Invalid entry date"
        if not posting_dt:
            return None, "Invalid posting date"

        # Parse exchange rate
        try:
            rate = Decimal(exchange_rate)
        except (ValueError, TypeError):
            rate = Decimal("1.0")

        # Parse lines
        try:
            lines_data = json.loads(lines_json) if lines_json else []
        except json.JSONDecodeError:
            return None, "Invalid journal lines format"

        if not lines_data:
            return None, "Journal entry must have at least one line"

        # Validate lines and calculate totals
        total_debit = Decimal("0")
        total_credit = Decimal("0")
        validated_lines = []

        for idx, line_data in enumerate(lines_data):
            account_id = line_data.get("account_id")
            if not account_id:
                return None, f"Line {idx + 1}: Account is required"

            account = db.get(Account, coerce_uuid(account_id))
            if not account or account.organization_id != org_id:
                return None, f"Line {idx + 1}: Invalid account"

            try:
                debit = Decimal(str(line_data.get("debit", "0") or "0"))
                credit = Decimal(str(line_data.get("credit", "0") or "0"))
            except (ValueError, TypeError):
                return None, f"Line {idx + 1}: Invalid amount"

            if debit == 0 and credit == 0:
                return None, f"Line {idx + 1}: Either debit or credit must be non-zero"

            if debit != 0 and credit != 0:
                return None, f"Line {idx + 1}: Cannot have both debit and credit on same line"

            total_debit += debit
            total_credit += credit
            validated_lines.append({
                "account_id": coerce_uuid(account_id),
                "description": line_data.get("description", ""),
                "debit": debit,
                "credit": credit,
            })

        # Check balance
        if total_debit != total_credit:
            return None, f"Journal is out of balance. Debit: {total_debit}, Credit: {total_credit}"

        try:
            # Update header
            entry.journal_type = journal_type_enum
            entry.fiscal_period_id = period_id
            entry.entry_date = entry_dt
            entry.posting_date = posting_dt
            entry.description = description
            entry.reference = reference or None
            entry.currency_code = currency_code
            entry.exchange_rate = rate
            entry.total_debit = total_debit
            entry.total_credit = total_credit
            entry.total_debit_functional = total_debit * rate
            entry.total_credit_functional = total_credit * rate

            # Delete existing lines
            db.query(JournalEntryLine).filter(
                JournalEntryLine.journal_entry_id == ent_id
            ).delete()

            # Create new lines
            for idx, line_data in enumerate(validated_lines):
                line = JournalEntryLine(
                    journal_entry_id=ent_id,
                    line_number=idx + 1,
                    account_id=line_data["account_id"],
                    description=line_data["description"] or None,
                    debit_amount=line_data["debit"],
                    credit_amount=line_data["credit"],
                    debit_amount_functional=line_data["debit"] * rate,
                    credit_amount_functional=line_data["credit"] * rate,
                )
                db.add(line)

            db.commit()
            db.refresh(entry)
            logger.info("update_journal: updated %s for org %s", entry.journal_number, org_id)
            return entry, None

        except Exception as e:
            db.rollback()
            logger.exception("update_journal: failed for org %s", org_id)
            return None, f"Failed to update journal entry: {str(e)}"

    @staticmethod
    def delete_journal(
        db: Session,
        organization_id: str,
        entry_id: str,
    ) -> Optional[str]:
        """Delete a journal entry. Only DRAFT entries can be deleted. Returns error message or None."""
        logger.debug(
            "delete_journal: org=%s entry_id=%s",
            organization_id, entry_id
        )
        org_id = coerce_uuid(organization_id)
        ent_id = coerce_uuid(entry_id)

        entry = db.get(JournalEntry, ent_id)
        if not entry or entry.organization_id != org_id:
            return "Journal entry not found"

        if entry.status != JournalStatus.DRAFT:
            return f"Cannot delete journal entry with status: {entry.status.value}. Only DRAFT entries can be deleted."

        try:
            # Lines will be cascade deleted due to relationship
            db.delete(entry)
            db.commit()
            logger.info("delete_journal: deleted %s for org %s", ent_id, org_id)
            return None

        except Exception as e:
            db.rollback()
            logger.exception("delete_journal: failed for org %s", org_id)
            return f"Failed to delete journal entry: {str(e)}"

    # =========================================================================
    # Response Methods
    # =========================================================================

    def list_journals_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
    ) -> HTMLResponse:
        """Render journal entries list page."""
        context = base_context(request, auth, "Journal Entries", "gl")
        context.update(
            self.list_journals_context(
                db,
                str(auth.organization_id),
                search=search,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
        )
        return templates.TemplateResponse(request, "finance/gl/journals.html", context)

    def journal_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new journal entry form page."""
        context = base_context(request, auth, "New Journal Entry", "gl")
        context.update(self.journal_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(request, "finance/gl/journal_form.html", context)

    def journal_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
    ) -> HTMLResponse:
        """Render journal entry detail page."""
        context = base_context(request, auth, "Journal Entry Details", "gl")
        context.update(
            self.journal_detail_context(
                db,
                str(auth.organization_id),
                entry_id,
            )
        )
        return templates.TemplateResponse(request, "finance/gl/journal_detail.html", context)

    def journal_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
    ) -> HTMLResponse:
        """Render edit journal entry form page."""
        context = base_context(request, auth, "Edit Journal Entry", "gl")
        context.update(
            self.journal_form_context(
                db,
                str(auth.organization_id),
                journal_entry_id=entry_id,
            )
        )
        return templates.TemplateResponse(request, "finance/gl/journal_form.html", context)

    def create_journal_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        journal_type: str,
        fiscal_period_id: str,
        entry_date: str,
        posting_date: str,
        description: str,
        reference: str,
        currency_code: Optional[str],
        exchange_rate: str,
        lines_json: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle journal entry creation form submission."""
        default_currency = currency_code or org_context_service.get_functional_currency(
            db,
            auth.organization_id,
        )

        entry, error = self.create_journal(
            db,
            str(auth.organization_id),
            str(auth.user_id),
            journal_type=journal_type,
            fiscal_period_id=fiscal_period_id,
            entry_date=entry_date,
            posting_date=posting_date,
            description=description,
            reference=reference,
            currency_code=default_currency,
            exchange_rate=exchange_rate,
            lines_json=lines_json,
        )

        if error or entry is None:
            context = base_context(request, auth, "New Journal Entry", "gl")
            context.update(self.journal_form_context(db, str(auth.organization_id)))
            context["error"] = error or "Journal entry creation failed"
            context["form_data"] = {
                "journal_type": journal_type,
                "fiscal_period_id": fiscal_period_id,
                "entry_date": entry_date,
                "posting_date": posting_date,
                "description": description,
                "reference": reference,
                "currency_code": default_currency,
                "exchange_rate": exchange_rate,
                "lines_json": lines_json,
            }
            return templates.TemplateResponse(request, "finance/gl/journal_form.html", context)

        return RedirectResponse(url=f"/finance/gl/journals/{entry.journal_entry_id}", status_code=303)

    def update_journal_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
        journal_type: str,
        fiscal_period_id: str,
        entry_date: str,
        posting_date: str,
        description: str,
        reference: str,
        currency_code: Optional[str],
        exchange_rate: str,
        lines_json: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle journal entry update form submission."""
        default_currency = currency_code or org_context_service.get_functional_currency(
            db,
            auth.organization_id,
        )

        _, error = self.update_journal(
            db,
            str(auth.organization_id),
            entry_id=entry_id,
            journal_type=journal_type,
            fiscal_period_id=fiscal_period_id,
            entry_date=entry_date,
            posting_date=posting_date,
            description=description,
            reference=reference,
            currency_code=default_currency,
            exchange_rate=exchange_rate,
            lines_json=lines_json,
        )

        if error:
            context = base_context(request, auth, "Edit Journal Entry", "gl")
            context.update(
                self.journal_form_context(
                    db,
                    str(auth.organization_id),
                    journal_entry_id=entry_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(request, "finance/gl/journal_form.html", context)

        return RedirectResponse(url=f"/finance/gl/journals/{entry_id}", status_code=303)

    def delete_journal_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle journal entry deletion."""
        error = self.delete_journal(db, str(auth.organization_id), entry_id)

        if error:
            context = base_context(request, auth, "Journal Entry Details", "gl")
            context.update(
                self.journal_detail_context(
                    db,
                    str(auth.organization_id),
                    entry_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(request, "finance/gl/journal_detail.html", context)

        return RedirectResponse(url="/finance/gl/journals", status_code=303)
