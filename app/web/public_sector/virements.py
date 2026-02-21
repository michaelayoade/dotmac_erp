"""
Public Sector – Virement web routes.

Thin wrappers that delegate to IPSASWebService and VirementService.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.finance.ipsas.web.ipsas_web import IPSASWebService
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_public_sector_access,
)

router = APIRouter(tags=["public-sector-virements"])


@router.get("/virements", response_class=HTMLResponse)
def list_virements(
    request: Request,
    fiscal_year_id: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Virement list page."""
    context = base_context(request, auth, "Virements", "ps_funds", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.virement_list_context(
            auth.organization_id,
            fiscal_year_id=UUID(fiscal_year_id) if fiscal_year_id else None,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(
        request, "public_sector/virement_list.html", context
    )


@router.get("/virements/new", response_class=HTMLResponse)
def new_virement_form(
    request: Request,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Create virement form page."""
    from sqlalchemy import select

    from app.models.finance.ipsas.appropriation import Appropriation

    context = base_context(request, auth, "New Virement", "ps_funds", db=db)
    appropriations = list(
        db.scalars(
            select(Appropriation)
            .where(Appropriation.organization_id == auth.organization_id)
            .order_by(Appropriation.appropriation_code)
        ).all()
    )
    context["appropriations"] = appropriations
    return templates.TemplateResponse(
        request, "public_sector/virement_form.html", context
    )


@router.post("/virements/new")
def create_virement(
    request: Request,
    description: str = Form(...),
    from_appropriation_id: str = Form(...),
    to_appropriation_id: str = Form(...),
    amount: str = Form(...),
    currency_code: str = Form("NGN"),
    justification: str = Form(...),
    approval_authority: str | None = Form(None),
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Create a virement (form submission)."""
    from app.models.finance.ipsas.appropriation import Appropriation
    from app.schemas.finance.ipsas import VirementCreate
    from app.services.finance.ipsas.virement_service import VirementService

    from_approp = db.get(Appropriation, UUID(from_appropriation_id))
    fiscal_year_id = from_approp.fiscal_year_id if from_approp else None
    if not fiscal_year_id:
        return RedirectResponse(
            "/public-sector/virements?error=invalid_appropriation", status_code=303
        )

    data = VirementCreate(
        fiscal_year_id=fiscal_year_id,
        description=description,
        from_appropriation_id=UUID(from_appropriation_id),
        to_appropriation_id=UUID(to_appropriation_id),
        amount=Decimal(amount),
        currency_code=currency_code,
        justification=justification,
        approval_authority=approval_authority or None,
    )

    svc = VirementService(db)
    org_id = auth.organization_id
    if org_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    virement_number = (
        f"VIR-{org_id.hex[:6].upper()}-{svc.count_for_org(org_id) + 1:04d}"
    )
    svc.create(auth.organization_id, data, auth.user_id, virement_number)
    return RedirectResponse("/public-sector/virements", status_code=303)


@router.get("/virements/{virement_id}", response_class=HTMLResponse)
def view_virement(
    request: Request,
    virement_id: str,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Virement detail page."""
    context = base_context(request, auth, "Virement Detail", "ps_funds", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.virement_detail_context(auth.organization_id, UUID(virement_id))
    )
    return templates.TemplateResponse(
        request, "public_sector/virement_detail.html", context
    )


@router.post("/virements/{virement_id}/approve")
def approve_virement(
    request: Request,
    virement_id: str,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Approve a virement (form submission)."""
    from app.services.finance.ipsas.virement_service import VirementService

    svc = VirementService(db)
    svc.get_or_404(UUID(virement_id), organization_id=auth.organization_id)
    svc.approve(UUID(virement_id), auth.user_id)
    return RedirectResponse(f"/public-sector/virements/{virement_id}", status_code=303)


@router.post("/virements/{virement_id}/apply")
def apply_virement(
    request: Request,
    virement_id: str,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Apply an approved virement (form submission)."""
    from app.services.finance.ipsas.virement_service import VirementService

    svc = VirementService(db)
    svc.get_or_404(UUID(virement_id), organization_id=auth.organization_id)
    svc.apply(UUID(virement_id))
    return RedirectResponse(f"/public-sector/virements/{virement_id}", status_code=303)
