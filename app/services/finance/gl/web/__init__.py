"""
GL Web Service - Modular web view services for GL module.

This module provides a facade that maintains backward compatibility
while organizing code into resource-specific submodules:
- base.py - Common utilities and view transformers
- account_web.py - Account-related methods
- journal_web.py - Journal-related methods
- period_web.py - Period-related methods

Usage:
    from app.services.finance.gl.web import gl_web_service
    # Or import the class:
    from app.services.finance.gl.web import GLWebService

For backward compatibility, the original import path also works:
    from app.services.finance.gl.web import gl_web_service
"""

from fastapi import Request
from sqlalchemy.orm import Session

from app.schemas.bulk_actions import BulkActionRequest, BulkExportRequest
from app.services.common import coerce_uuid

# Import the modular service components
from app.services.finance.gl.web.account_web import AccountWebService
from app.services.finance.gl.web.base import (
    # Data classes
    TrialBalanceTotals,
    account_detail_view,
    account_form_view,
    # View transformers
    category_option_view,
    fiscal_year_option_view,
    format_currency,
    format_date,
    ifrs_label,
    journal_entry_view,
    journal_line_view,
    parse_category,
    # Parsing utilities
    parse_date,
    parse_status,
    period_option_view,
)
from app.services.finance.gl.web.journal_web import JournalWebService
from app.services.finance.gl.web.ledger_web import LedgerWebService, ledger_web_service
from app.services.finance.gl.web.period_web import PeriodWebService
from app.web.deps import WebAuthContext


class GLWebService(
    AccountWebService, JournalWebService, LedgerWebService, PeriodWebService
):
    """
    Unified GL Web Service facade.

    Combines account, journal, and period web services into a single
    interface for backward compatibility.

    This class inherits from:
    - AccountWebService: Account listing, creation, editing
    - JournalWebService: Journal entry management
    - PeriodWebService: Fiscal periods and trial balance
    """

    # =====================================================================
    # Bulk Action Methods - Accounts
    # =====================================================================

    async def bulk_delete_accounts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk delete accounts request."""
        from app.services.finance.gl.bulk import get_account_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_account_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_delete(req.ids)

    async def bulk_export_accounts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk export accounts request."""
        from app.services.finance.gl.bulk import get_account_bulk_service

        body = await request.json()
        req = BulkExportRequest(**body)
        service = get_account_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_export(req.ids, req.format)

    async def export_all_accounts_response(
        self,
        auth: WebAuthContext,
        db: Session,
        search: str = "",
        status: str = "",
        category: str = "",
    ):
        """Export all accounts matching filters to CSV."""
        from app.services.finance.gl.bulk import get_account_bulk_service

        service = get_account_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        extra: dict[str, object] | None = {"category": category} if category else None
        return await service.export_all(
            search=search, status=status, extra_filters=extra
        )

    async def bulk_activate_accounts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk activate accounts request."""
        from app.services.finance.gl.bulk import get_account_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_account_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_activate(req.ids)

    async def bulk_deactivate_accounts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk deactivate accounts request."""
        from app.services.finance.gl.bulk import get_account_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_account_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_deactivate(req.ids)

    # =====================================================================
    # Bulk Action Methods - Journals
    # =====================================================================

    async def bulk_delete_journals_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk delete journals request."""
        from app.services.finance.gl.bulk import get_journal_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_journal_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_delete(req.ids)

    async def bulk_export_journals_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk export journals request."""
        from app.services.finance.gl.bulk import get_journal_bulk_service

        body = await request.json()
        req = BulkExportRequest(**body)
        service = get_journal_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_export(req.ids, req.format)

    async def export_all_journals_response(
        self,
        auth: WebAuthContext,
        db: Session,
        search: str = "",
        status: str = "",
        start_date: str = "",
        end_date: str = "",
    ):
        """Export all journals matching filters to CSV."""
        from app.services.finance.gl.bulk import get_journal_bulk_service

        service = get_journal_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.export_all(
            search=search,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

    async def bulk_approve_journals_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk approve journals request."""
        from app.services.finance.gl.bulk import get_journal_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_journal_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_approve(req.ids)

    async def bulk_post_journals_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk post journals request."""
        from app.services.finance.gl.bulk import get_journal_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_journal_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_post(req.ids)


# Module-level singleton for backward compatibility
gl_web_service = GLWebService()


__all__ = [
    # Utilities
    "parse_date",
    "format_date",
    "format_currency",
    "ifrs_label",
    "parse_category",
    "parse_status",
    # View transformers
    "category_option_view",
    "account_form_view",
    "account_detail_view",
    "journal_entry_view",
    "journal_line_view",
    "period_option_view",
    "fiscal_year_option_view",
    # Data classes
    "TrialBalanceTotals",
    # Service classes
    "AccountWebService",
    "JournalWebService",
    "LedgerWebService",
    "PeriodWebService",
    "GLWebService",
    # Singletons
    "gl_web_service",
    "ledger_web_service",
]
