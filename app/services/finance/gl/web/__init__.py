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
from app.web.deps import WebAuthContext

from app.services.finance.gl.web.base import (
    # Parsing utilities
    parse_date,
    format_date,
    format_currency,
    ifrs_label,
    parse_category,
    parse_status,
    # View transformers
    category_option_view,
    account_form_view,
    account_detail_view,
    journal_entry_view,
    journal_line_view,
    period_option_view,
    fiscal_year_option_view,
    # Data classes
    TrialBalanceTotals,
)

# Import the modular service components
from app.services.finance.gl.web.account_web import AccountWebService
from app.services.finance.gl.web.journal_web import JournalWebService
from app.services.finance.gl.web.period_web import PeriodWebService


class GLWebService(AccountWebService, JournalWebService, PeriodWebService):
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
        service = get_account_bulk_service(db, auth.organization_id, auth.user_id)
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
        service = get_account_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_export(req.ids, req.format)

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
        service = get_account_bulk_service(db, auth.organization_id, auth.user_id)
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
        service = get_account_bulk_service(db, auth.organization_id, auth.user_id)
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
        service = get_journal_bulk_service(db, auth.organization_id, auth.user_id)
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
        service = get_journal_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_export(req.ids, req.format)

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
        service = get_journal_bulk_service(db, auth.organization_id, auth.user_id)
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
        service = get_journal_bulk_service(db, auth.organization_id, auth.user_id)
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
    "PeriodWebService",
    "GLWebService",
    # Singleton
    "gl_web_service",
]
