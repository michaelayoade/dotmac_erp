"""
Lease (IFRS 16) Web Routes.

HTML template routes for lease contracts, schedules, and modifications.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.lease import (
    lease_modification_service,
    lease_variable_payment_service,
)
from app.services.finance.lease.web import lease_web_service
from app.services.finance.platform.currency_context import get_currency_context
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_finance_access

router = APIRouter(prefix="/lease", tags=["lease-web"])


# =============================================================================
# Lease Contracts
# =============================================================================


@router.get("/contracts", response_class=HTMLResponse)
def list_contracts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    search: str | None = None,
    status: str | None = None,
    lease_type: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Lease contracts list page."""
    context = base_context(request, auth, "Lease Contracts", "lease")
    context.update(
        lease_web_service.list_contracts_context(
            db,
            str(auth.organization_id),
            search=search,
            status=status,
            lease_type=lease_type,
            page=page,
        )
    )

    return templates.TemplateResponse(request, "finance/lease/contracts.html", context)


@router.get("/contracts/new", response_class=HTMLResponse)
def new_contract_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New lease contract form page."""
    context = base_context(request, auth, "New Lease Contract", "lease")
    context.update(get_currency_context(db, str(auth.organization_id)))
    return templates.TemplateResponse(
        request, "finance/lease/contract_form.html", context
    )


@router.get("/contracts/{lease_id}", response_class=HTMLResponse)
def view_contract(
    request: Request,
    lease_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Lease contract detail page."""
    context = base_context(request, auth, "Lease Details", "lease")
    context.update(
        lease_web_service.contract_detail_context(
            db,
            str(auth.organization_id),
            lease_id,
        )
    )

    return templates.TemplateResponse(
        request, "finance/lease/contract_detail.html", context
    )


@router.get("/contracts/{lease_id}/schedule", response_class=HTMLResponse)
def view_schedule(
    request: Request,
    lease_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Lease payment schedule page."""
    context = base_context(request, auth, "Payment Schedule", "lease")
    context.update(
        lease_web_service.schedule_context(
            db,
            str(auth.organization_id),
            lease_id,
        )
    )
    context["lease_id"] = lease_id

    return templates.TemplateResponse(request, "finance/lease/schedule.html", context)


# =============================================================================
# Lease Modifications
# =============================================================================


@router.get("/modifications", response_class=HTMLResponse)
def list_modifications(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    lease_id: str | None = None,
    modification_type: str | None = None,
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
    context.update(
        {
            "modifications": modifications,
            "lease_id": lease_id,
            "modification_type": modification_type,
            "page": page,
        }
    )

    return templates.TemplateResponse(
        request, "finance/lease/modifications.html", context
    )


# =============================================================================
# Variable Payments
# =============================================================================


@router.get("/variable-payments", response_class=HTMLResponse)
def list_variable_payments(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    lease_id: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Variable payments list page."""
    context = base_context(request, auth, "Variable Payments", "lease")
    context["lease_id"] = lease_id

    return templates.TemplateResponse(
        request, "finance/lease/variable_payments.html", context
    )


@router.get("/overdue", response_class=HTMLResponse)
def overdue_payments(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    as_of_date: str | None = None,
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

    return templates.TemplateResponse(request, "finance/lease/overdue.html", context)
