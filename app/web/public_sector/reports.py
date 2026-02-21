"""
Public Sector – Report web routes.

Thin wrappers that delegate to IPSASWebService for budget comparison
and available balance dashboard.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.ipsas.web.ipsas_web import IPSASWebService
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_public_sector_access,
)

router = APIRouter(tags=["public-sector-reports"])


@router.get("/budget-comparison", response_class=HTMLResponse)
def budget_comparison(
    request: Request,
    fiscal_year_id: str | None = None,
    fund_id: str | None = None,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """IPSAS 24 Budget vs Actual statement page."""
    context = base_context(request, auth, "Budget Comparison", "ps_commitments", db=db)
    if fiscal_year_id:
        web_svc = IPSASWebService(db)
        context.update(
            web_svc.budget_comparison_context(
                auth.organization_id,
                UUID(fiscal_year_id),
                fund_id=UUID(fund_id) if fund_id else None,
            )
        )

    # Load fiscal years for selector
    from sqlalchemy import select

    from app.models.finance.gl.fiscal_year import FiscalYear

    fiscal_years = list(
        db.scalars(
            select(FiscalYear)
            .where(FiscalYear.organization_id == auth.organization_id)
            .order_by(FiscalYear.start_date.desc())
        ).all()
    )
    context["fiscal_years"] = fiscal_years
    context["selected_fiscal_year_id"] = fiscal_year_id
    context["selected_fund_id"] = fund_id

    # Load funds for filter
    from app.models.finance.ipsas.fund import Fund

    funds = list(
        db.scalars(
            select(Fund)
            .where(Fund.organization_id == auth.organization_id)
            .order_by(Fund.fund_code)
        ).all()
    )
    context["funds"] = funds

    return templates.TemplateResponse(
        request, "public_sector/budget_comparison.html", context
    )


@router.get("/available-balance", response_class=HTMLResponse)
def available_balance_dashboard(
    request: Request,
    fund_id: str | None = None,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Available balance dashboard page."""
    context = base_context(request, auth, "Available Balance", "ps_funds", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.available_balance_dashboard_context(
            auth.organization_id,
            fund_id=UUID(fund_id) if fund_id else None,
        )
    )
    return templates.TemplateResponse(
        request, "public_sector/available_balance.html", context
    )
