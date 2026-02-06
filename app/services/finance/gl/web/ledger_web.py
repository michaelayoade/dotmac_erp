"""
GL Ledger Web Service - Ledger transactions web view methods.

Provides view-focused data and operations for viewing all posted ledger entries.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account, NormalBalance
from app.models.finance.gl.journal_entry import JournalEntry
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from app.services.common import coerce_uuid
from app.services.finance.gl.web.base import format_currency, format_date, parse_date
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class LedgerWebService:
    """Web service methods for GL ledger transactions."""

    # =========================================================================
    # Context Methods
    # =========================================================================

    @staticmethod
    def list_ledger_context(
        db: Session,
        organization_id: str,
        account_id: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        search: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        """
        Get context for ledger transactions listing page.

        Args:
            db: Database session
            organization_id: Organization scope
            account_id: Optional filter by account
            start_date: Optional filter by start date
            end_date: Optional filter by end date
            search: Optional search term
            page: Page number (1-indexed)
            limit: Items per page

        Returns:
            Context dict for template rendering
        """
        logger.debug(
            "list_ledger_context: org=%s account=%s search=%r page=%d",
            organization_id,
            account_id,
            search,
            page,
        )
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        from_date = parse_date(start_date)
        to_date = parse_date(end_date)
        acct_id = coerce_uuid(account_id) if account_id else None

        # Build base query
        query = db.query(PostedLedgerLine).filter(
            PostedLedgerLine.organization_id == org_id
        )

        # Apply filters
        if acct_id:
            query = query.filter(PostedLedgerLine.account_id == acct_id)
        if from_date:
            query = query.filter(PostedLedgerLine.posting_date >= from_date)
        if to_date:
            query = query.filter(PostedLedgerLine.posting_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (PostedLedgerLine.description.ilike(search_pattern))
                | (PostedLedgerLine.journal_reference.ilike(search_pattern))
                | (PostedLedgerLine.account_code.ilike(search_pattern))
            )

        # Get total count
        total_count = (
            query.with_entities(func.count(PostedLedgerLine.ledger_line_id)).scalar()
            or 0
        )

        # Fetch lines ordered by posting date and posted_at
        lines = (
            query.order_by(
                PostedLedgerLine.posting_date.desc(),
                PostedLedgerLine.posted_at.desc(),
            )
            .limit(limit)
            .offset(offset)
            .all()
        )

        # Get account info if single account selected
        selected_account = None
        if acct_id:
            selected_account = db.get(Account, acct_id)

        # Get all accounts for dropdown
        accounts = (
            db.query(Account)
            .filter(Account.organization_id == org_id, Account.is_active == True)
            .order_by(Account.account_code)
            .all()
        )

        # Get account names for display (batch lookup)
        account_ids = {line.account_id for line in lines}
        account_map = {}
        if account_ids:
            account_rows = (
                db.query(Account.account_id, Account.account_code, Account.account_name)
                .filter(Account.account_id.in_(account_ids))
                .all()
            )
            account_map = {
                row.account_id: {"code": row.account_code, "name": row.account_name}
                for row in account_rows
            }

        # Get journal numbers for display (batch lookup)
        journal_ids = {line.journal_entry_id for line in lines}
        journal_map = {}
        if journal_ids:
            journal_rows = (
                db.query(JournalEntry.journal_entry_id, JournalEntry.journal_number)
                .filter(JournalEntry.journal_entry_id.in_(journal_ids))
                .all()
            )
            journal_map = {
                row.journal_entry_id: row.journal_number for row in journal_rows
            }

        # Calculate running balance if single account selected
        running_balance = Decimal("0")
        if selected_account:
            # Get opening balance (sum of all postings before the page's first item)
            # For a descending list, we need balance AFTER the last item on page
            if lines:
                first_line = lines[0]
                opening_query = db.query(
                    func.coalesce(func.sum(PostedLedgerLine.debit_amount), 0),
                    func.coalesce(func.sum(PostedLedgerLine.credit_amount), 0),
                ).filter(
                    PostedLedgerLine.organization_id == org_id,
                    PostedLedgerLine.account_id == acct_id,
                )

                # Apply same date filters
                if from_date:
                    opening_query = opening_query.filter(
                        PostedLedgerLine.posting_date >= from_date
                    )
                if to_date:
                    opening_query = opening_query.filter(
                        PostedLedgerLine.posting_date <= to_date
                    )

                # Get entries AFTER this page (more recent)
                opening_query = opening_query.filter(
                    (PostedLedgerLine.posting_date > first_line.posting_date)
                    | (
                        (PostedLedgerLine.posting_date == first_line.posting_date)
                        & (PostedLedgerLine.posted_at > first_line.posted_at)
                    )
                )

                result = opening_query.first()
                if result:
                    total_debit, total_credit = result
                    if selected_account.normal_balance == NormalBalance.DEBIT:
                        running_balance = total_debit - total_credit
                    else:
                        running_balance = total_credit - total_debit

        # Build lines view with running balance
        lines_view = []
        for line in lines:
            acct_info = account_map.get(
                line.account_id, {"code": line.account_code, "name": ""}
            )
            journal_number = journal_map.get(line.journal_entry_id, "")

            # Update running balance for this line
            if selected_account:
                if selected_account.normal_balance == NormalBalance.DEBIT:
                    running_balance += line.debit_amount - line.credit_amount
                else:
                    running_balance += line.credit_amount - line.debit_amount

            lines_view.append(
                {
                    "ledger_line_id": str(line.ledger_line_id),
                    "posting_date": format_date(line.posting_date),
                    "account_code": acct_info["code"],
                    "account_name": acct_info["name"],
                    "account_id": str(line.account_id),
                    "journal_entry_id": str(line.journal_entry_id),
                    "journal_number": journal_number,
                    "description": line.description or "",
                    "reference": line.journal_reference or "",
                    "debit": format_currency(line.debit_amount)
                    if line.debit_amount
                    else "",
                    "credit": format_currency(line.credit_amount)
                    if line.credit_amount
                    else "",
                    "balance": format_currency(running_balance)
                    if selected_account
                    else None,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        # Build account options for dropdown
        account_options = [
            {
                "account_id": str(a.account_id),
                "account_code": a.account_code,
                "account_name": a.account_name,
            }
            for a in accounts
        ]

        logger.debug("list_ledger_context: found %d entries", total_count)

        return {
            "lines": lines_view,
            "accounts": account_options,
            "selected_account_id": account_id,
            "selected_account": {
                "account_id": str(selected_account.account_id),
                "account_code": selected_account.account_code,
                "account_name": selected_account.account_name,
            }
            if selected_account
            else None,
            "show_balance_column": selected_account is not None,
            "search": search or "",
            "start_date": start_date or "",
            "end_date": end_date or "",
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    # =========================================================================
    # Response Methods
    # =========================================================================

    def list_ledger_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        search: Optional[str],
        page: int,
    ) -> HTMLResponse:
        """
        Get HTML response for ledger transactions list page.

        Args:
            request: FastAPI request
            auth: Authentication context
            db: Database session
            account_id: Optional filter by account
            start_date: Optional filter by start date
            end_date: Optional filter by end date
            search: Optional search term
            page: Page number

        Returns:
            Rendered HTML response
        """
        context = self.list_ledger_context(
            db=db,
            organization_id=str(auth.organization_id),
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            search=search,
            page=page,
        )

        return templates.TemplateResponse(
            request,
            "finance/gl/ledger.html",
            {
                **base_context(
                    request, auth, page_title="General Ledger", active_module="gl"
                ),
                **context,
            },
        )


# Module-level singleton
ledger_web_service = LedgerWebService()
