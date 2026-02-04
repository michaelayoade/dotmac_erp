"""
IPSAS Web Service - Template context builders for IPSAS web routes.

Builds context dictionaries for Jinja2 templates, keeping route handlers thin.
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PAGE_SIZE = 25


class IPSASWebService:
    """Web service for building IPSAS template contexts."""

    def __init__(self, db: Session):
        self.db = db

    # ─── Funds ────────────────────────────────────────────────────────

    def fund_list_context(
        self,
        organization_id: UUID,
        status: Optional[str] = None,
        fund_type: Optional[str] = None,
        page: int = 1,
    ) -> dict:
        """Build context for fund list page."""
        from app.services.finance.ipsas.fund_service import FundService

        svc = FundService(self.db)
        offset = (page - 1) * PAGE_SIZE
        funds = svc.list_for_org(
            organization_id,
            status=status,
            fund_type=fund_type,
            limit=PAGE_SIZE,
            offset=offset,
        )
        total = svc.count_for_org(organization_id)

        return {
            "funds": funds,
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "total_pages": (total + PAGE_SIZE - 1) // PAGE_SIZE,
            "status_filter": status,
            "fund_type_filter": fund_type,
        }

    def fund_detail_context(self, organization_id: UUID, fund_id: UUID) -> dict:
        """Build context for fund detail page."""
        from app.services.finance.ipsas.available_balance_service import (
            AvailableBalanceService,
        )
        from app.services.finance.ipsas.fund_service import FundService

        svc = FundService(self.db)
        fund = svc.get_or_404(fund_id, organization_id=organization_id)
        balance = AvailableBalanceService(self.db).calculate_by_fund(
            fund.organization_id, fund.fund_id
        )

        return {
            "fund": fund,
            "balance": balance,
        }

    def fund_form_context(self, organization_id: UUID) -> dict:
        """Build context for fund create/edit form."""
        from app.models.finance.ipsas.enums import FundType

        return {
            "fund_types": [t.value for t in FundType],
        }

    # ─── Appropriations ───────────────────────────────────────────────

    def appropriation_list_context(
        self,
        organization_id: UUID,
        *,
        fiscal_year_id: Optional[UUID] = None,
        fund_id: Optional[UUID] = None,
        status: Optional[str] = None,
        page: int = 1,
    ) -> dict:
        """Build context for appropriation list page."""
        from app.services.finance.ipsas.appropriation_service import (
            AppropriationService,
        )

        svc = AppropriationService(self.db)
        offset = (page - 1) * PAGE_SIZE
        appropriations = svc.list_for_org(
            organization_id,
            fiscal_year_id=fiscal_year_id,
            fund_id=fund_id,
            status=status,
            limit=PAGE_SIZE,
            offset=offset,
        )
        total = svc.count_for_org(organization_id, fiscal_year_id=fiscal_year_id)

        return {
            "appropriations": appropriations,
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "total_pages": (total + PAGE_SIZE - 1) // PAGE_SIZE,
            "fiscal_year_id_filter": fiscal_year_id,
            "fund_id_filter": fund_id,
            "status_filter": status,
        }

    def appropriation_detail_context(
        self, organization_id: UUID, appropriation_id: UUID
    ) -> dict:
        """Build context for appropriation detail page."""
        from app.services.finance.ipsas.appropriation_service import (
            AppropriationService,
        )
        from app.services.finance.ipsas.available_balance_service import (
            AvailableBalanceService,
        )

        approp_svc = AppropriationService(self.db)
        approp = approp_svc.get_or_404(appropriation_id, organization_id=organization_id)
        allotments = approp_svc.list_allotments(
            approp.organization_id, appropriation_id=appropriation_id
        )

        balance_svc = AvailableBalanceService(self.db)
        available = balance_svc.calculate(
            approp.organization_id, appropriation_id=appropriation_id
        )

        return {
            "appropriation": approp,
            "allotments": allotments,
            "available_balance": available,
        }

    # ─── Commitments ──────────────────────────────────────────────────

    def commitment_list_context(
        self,
        organization_id: UUID,
        *,
        fund_id: Optional[UUID] = None,
        status: Optional[str] = None,
        page: int = 1,
    ) -> dict:
        """Build context for commitment register page."""
        from app.services.finance.ipsas.commitment_service import CommitmentService

        svc = CommitmentService(self.db)
        offset = (page - 1) * PAGE_SIZE
        commitments = svc.list_for_org(
            organization_id,
            fund_id=fund_id,
            status=status,
            limit=PAGE_SIZE,
            offset=offset,
        )
        total = svc.count_for_org(organization_id)

        return {
            "commitments": commitments,
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "total_pages": (total + PAGE_SIZE - 1) // PAGE_SIZE,
            "fund_id_filter": fund_id,
            "status_filter": status,
        }

    def commitment_detail_context(
        self, organization_id: UUID, commitment_id: UUID
    ) -> dict:
        """Build context for commitment detail page."""
        from app.services.finance.ipsas.commitment_service import CommitmentService

        svc = CommitmentService(self.db)
        commitment = svc.get_or_404(commitment_id, organization_id=organization_id)

        return {
            "commitment": commitment,
        }

    # ─── Virements ────────────────────────────────────────────────────

    def virement_list_context(
        self,
        organization_id: UUID,
        *,
        fiscal_year_id: Optional[UUID] = None,
        status: Optional[str] = None,
        page: int = 1,
    ) -> dict:
        """Build context for virement list page."""
        from app.services.finance.ipsas.virement_service import VirementService

        svc = VirementService(self.db)
        offset = (page - 1) * PAGE_SIZE
        virements = svc.list_for_org(
            organization_id,
            fiscal_year_id=fiscal_year_id,
            status=status,
            limit=PAGE_SIZE,
            offset=offset,
        )
        total = svc.count_for_org(organization_id)

        return {
            "virements": virements,
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "total_pages": (total + PAGE_SIZE - 1) // PAGE_SIZE,
            "fiscal_year_id_filter": fiscal_year_id,
            "status_filter": status,
        }

    # ─── Reports ──────────────────────────────────────────────────────

    def budget_comparison_context(
        self,
        organization_id: UUID,
        fiscal_year_id: UUID,
        *,
        fund_id: Optional[UUID] = None,
    ) -> dict:
        """Build context for budget comparison page."""
        from app.services.finance.ipsas.budget_comparison_service import (
            BudgetComparisonService,
        )

        svc = BudgetComparisonService(self.db)
        comparison = svc.generate_comparison(
            organization_id, fiscal_year_id, fund_id=fund_id
        )

        return {
            "comparison": comparison,
        }

    def available_balance_dashboard_context(
        self,
        organization_id: UUID,
        *,
        fund_id: Optional[UUID] = None,
    ) -> dict:
        """Build context for available balance dashboard."""
        from app.services.finance.ipsas.available_balance_service import (
            AvailableBalanceService,
        )
        from app.services.finance.ipsas.fund_service import FundService

        balance_svc = AvailableBalanceService(self.db)
        fund_svc = FundService(self.db)

        overall = balance_svc.calculate(organization_id, fund_id=fund_id)
        funds = fund_svc.list_for_org(organization_id, status="ACTIVE")

        # Per-fund breakdown
        fund_balances = []
        for fund in funds:
            fb = balance_svc.calculate(organization_id, fund_id=fund.fund_id)
            fund_balances.append(
                {
                    "fund": fund,
                    "balance": fb,
                }
            )

        return {
            "overall_balance": overall,
            "funds": funds,
            "fund_balances": fund_balances,
            "fund_id_filter": fund_id,
        }
