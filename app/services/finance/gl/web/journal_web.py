"""
GL Journal Web Service - Journal entry web view methods.

Provides view-focused data and operations for GL journal entry web routes.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.journal_entry import JournalEntry, JournalType
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import JournalService
from app.services.finance.gl.web.base import (
    format_currency,
    format_date,
    journal_entry_view,
    journal_line_view,
    period_option_view,
)
from app.services.finance.platform.org_context import org_context_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

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
        search: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        """Get context for journal listing page."""
        logger.debug(
            "list_journals_context: org=%s search=%r status=%s page=%d",
            organization_id,
            search,
            status,
            page,
        )
        offset = (page - 1) * limit
        from app.services.finance.gl.journal_query import build_journal_query

        query = build_journal_query(
            db=db,
            organization_id=organization_id,
            search=search,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        total_count = (
            query.with_entities(func.count(JournalEntry.journal_entry_id)).scalar() or 0
        )
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
                    "total_debit": format_currency(
                        entry.total_debit, entry.currency_code
                    ),
                    "total_credit": format_currency(
                        entry.total_credit, entry.currency_code
                    ),
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
        journal_entry_id: str | None = None,
    ) -> dict:
        """Get context for journal create/edit form."""
        logger.debug(
            "journal_form_context: org=%s journal_entry_id=%s",
            organization_id,
            journal_entry_id,
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
                accounts = (
                    db.query(Account).filter(Account.account_id.in_(account_ids)).all()
                )
                account_map = {
                    a.account_id: (a.account_code, a.account_name) for a in accounts
                }

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
            organization_id,
            journal_entry_id,
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
    ) -> tuple[JournalEntry | None, str | None]:
        """Create a new journal entry with lines. Returns (entry, error)."""
        logger.debug(
            "create_journal: org=%s type=%s date=%s",
            organization_id,
            journal_type,
            entry_date,
        )
        org_id = coerce_uuid(organization_id)
        uid = coerce_uuid(user_id)

        payload = {
            "journal_type": journal_type,
            "fiscal_period_id": fiscal_period_id,
            "entry_date": entry_date,
            "posting_date": posting_date,
            "description": description,
            "reference": reference,
            "currency_code": currency_code,
            "exchange_rate": exchange_rate,
            "lines": lines_json,
        }

        try:
            input_data = JournalService.build_input_from_payload(db, org_id, payload)
            entry = JournalService.create_entry(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=uid,
            )
            return entry, None
        except HTTPException as e:
            return None, str(e.detail)
        except Exception as e:
            logger.exception("create_journal: failed for org %s", org_id)
            return None, str(e)

    @staticmethod
    def update_journal(
        db: Session,
        organization_id: str,
        user_id: str,
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
    ) -> tuple[JournalEntry | None, str | None]:
        """Update a journal entry. Only DRAFT entries can be updated. Returns (entry, error)."""
        logger.debug("update_journal: org=%s entry_id=%s", organization_id, entry_id)
        org_id = coerce_uuid(organization_id)
        uid = coerce_uuid(user_id)

        payload = {
            "journal_type": journal_type,
            "fiscal_period_id": fiscal_period_id,
            "entry_date": entry_date,
            "posting_date": posting_date,
            "description": description,
            "reference": reference,
            "currency_code": currency_code,
            "exchange_rate": exchange_rate,
            "lines": lines_json,
        }

        try:
            input_data = JournalService.build_input_from_payload(db, org_id, payload)
            entry = JournalService.update_journal(
                db=db,
                organization_id=org_id,
                journal_entry_id=coerce_uuid(entry_id),
                input=input_data,
                updated_by_user_id=uid,
            )
            return entry, None
        except HTTPException as e:
            return None, str(e.detail)
        except Exception as e:
            logger.exception("update_journal: failed for org %s", org_id)
            return None, str(e)

    @staticmethod
    def delete_journal(
        db: Session,
        organization_id: str,
        entry_id: str,
    ) -> str | None:
        """Delete a journal entry. Only DRAFT entries can be deleted. Returns error message or None."""
        logger.debug("delete_journal: org=%s entry_id=%s", organization_id, entry_id)
        org_id = coerce_uuid(organization_id)
        try:
            JournalService.delete_journal(
                db=db,
                organization_id=org_id,
                journal_entry_id=coerce_uuid(entry_id),
            )
            logger.info("delete_journal: deleted %s for org %s", entry_id, org_id)
            return None
        except HTTPException as e:
            return str(e.detail)
        except Exception as e:
            logger.exception("delete_journal: failed for org %s", org_id)
            return str(e)

    # =========================================================================
    # Response Methods
    # =========================================================================

    def list_journals_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
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
        return templates.TemplateResponse(
            request, "finance/gl/journal_form.html", context
        )

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
        return templates.TemplateResponse(
            request, "finance/gl/journal_detail.html", context
        )

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
        return templates.TemplateResponse(
            request, "finance/gl/journal_form.html", context
        )

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
        currency_code: str | None,
        exchange_rate: str,
        lines_json: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle journal entry creation form submission."""
        org_id = auth.organization_id
        assert org_id is not None
        default_currency = currency_code or org_context_service.get_functional_currency(
            db,
            org_id,
        )

        entry, error = self.create_journal(
            db,
            str(org_id),
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
            return templates.TemplateResponse(
                request, "finance/gl/journal_form.html", context
            )

        return RedirectResponse(
            url=f"/finance/gl/journals/{entry.journal_entry_id}", status_code=303
        )

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
        currency_code: str | None,
        exchange_rate: str,
        lines_json: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle journal entry update form submission."""
        org_id = auth.organization_id
        assert org_id is not None
        default_currency = currency_code or org_context_service.get_functional_currency(
            db,
            org_id,
        )

        _, error = self.update_journal(
            db,
            str(org_id),
            str(auth.user_id),
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
            return templates.TemplateResponse(
                request, "finance/gl/journal_form.html", context
            )

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
            return templates.TemplateResponse(
                request, "finance/gl/journal_detail.html", context
            )

        return RedirectResponse(url="/finance/gl/journals", status_code=303)

    def post_journal_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
    ) -> RedirectResponse:
        """Post journal entry to ledger."""
        try:
            JournalService.post_journal(
                db=db,
                organization_id=coerce_uuid(auth.organization_id),
                journal_entry_id=coerce_uuid(entry_id),
                posted_by_user_id=coerce_uuid(auth.user_id),
            )
            return RedirectResponse(
                url=f"/finance/gl/journals/{entry_id}?success=Journal+entry+posted+to+ledger",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/gl/journals/{entry_id}?error={str(e)}",
                status_code=303,
            )

    def reverse_journal_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
    ) -> RedirectResponse:
        """Reverse a posted journal entry."""
        try:
            reversal = JournalService.reverse_entry(
                db=db,
                organization_id=coerce_uuid(auth.organization_id),
                entry_id=coerce_uuid(entry_id),
                reversal_date=date.today(),
                reversed_by_user_id=coerce_uuid(auth.user_id),
            )
            return RedirectResponse(
                url=f"/finance/gl/journals/{reversal.journal_entry_id}?success=Journal+entry+reversed",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/gl/journals/{entry_id}?error={str(e)}",
                status_code=303,
            )
