"""
Lease (IFRS 16) Web Routes.

HTML template routes for lease contracts, schedules, and modifications.
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.lease import (
    lease_contract_service,
    lease_modification_service,
    lease_variable_payment_service,
)

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/lease", tags=["lease-web"])


# =============================================================================
# Lease Contracts
# =============================================================================

@router.get("/contracts", response_class=HTMLResponse)
def list_contracts(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    status: Optional[str] = None,
    lease_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Lease contracts list page."""
    limit = 50
    offset = (page - 1) * limit

    contracts = lease_contract_service.list(
        db=db,
        organization_id=str(auth.organization_id),
        status=status,
        lease_type=lease_type,
        limit=limit,
        offset=offset,
    )

    context = base_context(request, auth, "Lease Contracts", "lease")
    context.update({
        "contracts": contracts,
        "search": search,
        "status": status,
        "lease_type": lease_type,
        "page": page,
        "limit": limit,
        "offset": offset,
        "total_count": len(contracts),
        "total_pages": 1,
    })

    return templates.TemplateResponse(request, "ifrs/lease/contracts.html", context)


@router.get("/contracts/new", response_class=HTMLResponse)
def new_contract_form(request: Request, auth: WebAuthContext = Depends(require_web_auth), db: Session = Depends(get_db)):
    """New lease contract form page."""
    context = base_context(request, auth, "New Lease Contract", "lease")
    return templates.TemplateResponse(request, "ifrs/lease/contract_form.html", context)


@router.get("/contracts/{lease_id}", response_class=HTMLResponse)
def view_contract(request: Request, lease_id: str, auth: WebAuthContext = Depends(require_web_auth), db: Session = Depends(get_db)):
    """Lease contract detail page."""
    contract = lease_contract_service.get(db, lease_id)

    context = base_context(request, auth, "Lease Details", "lease")
    context["contract"] = contract

    return templates.TemplateResponse(request, "ifrs/lease/contract_detail.html", context)


@router.get("/contracts/{lease_id}/schedule", response_class=HTMLResponse)
def view_schedule(request: Request, lease_id: str, auth: WebAuthContext = Depends(require_web_auth), db: Session = Depends(get_db)):
    """Lease payment schedule page."""
    schedules = lease_variable_payment_service.get_scheduled_payments(
        db, UUID(lease_id), include_paid=True
    )

    context = base_context(request, auth, "Payment Schedule", "lease")
    context["lease_id"] = lease_id
    context["schedules"] = schedules

    return templates.TemplateResponse(request, "ifrs/lease/schedule.html", context)


# =============================================================================
# Lease Modifications
# =============================================================================

@router.get("/modifications", response_class=HTMLResponse)
def list_modifications(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    lease_id: Optional[str] = None,
    modification_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Lease modifications list page."""
    limit = 50
    offset = (page - 1) * limit

    modifications = lease_modification_service.list(
        db=db,
        organization_id=str(auth.organization_id),
        modification_type=modification_type,
        limit=limit,
        offset=offset,
    )

    context = base_context(request, auth, "Lease Modifications", "lease")
    context.update({
        "modifications": modifications,
        "lease_id": lease_id,
        "modification_type": modification_type,
        "page": page,
    })

    return templates.TemplateResponse(request, "ifrs/lease/modifications.html", context)


# =============================================================================
# Variable Payments
# =============================================================================

@router.get("/variable-payments", response_class=HTMLResponse)
def list_variable_payments(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    lease_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Variable payments list page."""
    context = base_context(request, auth, "Variable Payments", "lease")
    context["lease_id"] = lease_id

    return templates.TemplateResponse(request, "ifrs/lease/variable_payments.html", context)


@router.get("/overdue", response_class=HTMLResponse)
def overdue_payments(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    as_of_date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Overdue lease payments page."""
    check_date = date.fromisoformat(as_of_date) if as_of_date else None
    overdue = lease_variable_payment_service.get_overdue_payments(
        db, auth.organization_id, check_date
    )

    context = base_context(request, auth, "Overdue Payments", "lease")
    context["overdue_payments"] = overdue
    context["as_of_date"] = as_of_date

    return templates.TemplateResponse(request, "ifrs/lease/overdue.html", context)
