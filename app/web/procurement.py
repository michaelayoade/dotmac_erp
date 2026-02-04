"""
Procurement Web Routes.

Server-rendered HTML routes for procurement management.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.common import NotFoundError
from app.services.procurement.web.procurement_web import ProcurementWebService
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_procurement_access,
    templates,
)

router = APIRouter(prefix="/procurement", tags=["procurement-web"])


# =============================================================================
# Dashboard
# =============================================================================


@router.get("", response_class=HTMLResponse)
def procurement_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """Procurement management dashboard."""
    context = base_context(request, auth, "Procurement", "procurement", db=db)
    web_service = ProcurementWebService(db)
    context.update(web_service.dashboard_context(auth.organization_id))
    return templates.TemplateResponse(request, "procurement/dashboard.html", context)


# =============================================================================
# Plans
# =============================================================================


@router.get("/plans", response_class=HTMLResponse)
def plan_list(
    request: Request,
    status: Optional[str] = None,
    fiscal_year: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """List procurement plans."""
    context = base_context(request, auth, "Procurement Plans", "procurement", db=db)
    web_service = ProcurementWebService(db)
    context.update(
        web_service.plan_list_context(
            auth.organization_id,
            status=status,
            fiscal_year=fiscal_year,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(request, "procurement/plans/list.html", context)


@router.get("/plans/new", response_class=HTMLResponse)
def plan_new(
    request: Request,
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """New procurement plan form."""
    context = base_context(request, auth, "New Plan", "procurement", db=db)
    web_service = ProcurementWebService(db)
    context.update(web_service.plan_form_context(auth.organization_id))
    return templates.TemplateResponse(request, "procurement/plans/form.html", context)


@router.get("/plans/{plan_id}", response_class=HTMLResponse)
def plan_detail(
    request: Request,
    plan_id: UUID,
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """Plan detail view."""
    context = base_context(request, auth, "Plan Details", "procurement", db=db)
    web_service = ProcurementWebService(db)
    try:
        context.update(web_service.plan_detail_context(auth.organization_id, plan_id))
        return templates.TemplateResponse(
            request, "procurement/plans/detail.html", context
        )
    except NotFoundError:
        return RedirectResponse(
            url="/procurement/plans?error=not_found", status_code=303
        )


# =============================================================================
# Requisitions
# =============================================================================


@router.get("/requisitions", response_class=HTMLResponse)
def requisition_list(
    request: Request,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """List purchase requisitions."""
    context = base_context(request, auth, "Requisitions", "procurement", db=db)
    web_service = ProcurementWebService(db)
    context.update(
        web_service.requisition_list_context(
            auth.organization_id,
            status=status,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(
        request, "procurement/requisitions/list.html", context
    )


@router.get("/requisitions/new", response_class=HTMLResponse)
def requisition_new(
    request: Request,
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """New requisition form."""
    context = base_context(request, auth, "New Requisition", "procurement", db=db)
    web_service = ProcurementWebService(db)
    context.update(web_service.requisition_form_context(auth.organization_id))
    return templates.TemplateResponse(
        request, "procurement/requisitions/form.html", context
    )


@router.get("/requisitions/{requisition_id}", response_class=HTMLResponse)
def requisition_detail(
    request: Request,
    requisition_id: UUID,
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """Requisition detail view."""
    context = base_context(request, auth, "Requisition Details", "procurement", db=db)
    web_service = ProcurementWebService(db)
    try:
        context.update(
            web_service.requisition_detail_context(auth.organization_id, requisition_id)
        )
        return templates.TemplateResponse(
            request,
            "procurement/requisitions/detail.html",
            context,
        )
    except NotFoundError:
        return RedirectResponse(
            url="/procurement/requisitions?error=not_found",
            status_code=303,
        )


# =============================================================================
# RFQs
# =============================================================================


@router.get("/rfqs", response_class=HTMLResponse)
def rfq_list(
    request: Request,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """List RFQs."""
    context = base_context(request, auth, "RFQs", "procurement", db=db)
    web_service = ProcurementWebService(db)
    context.update(
        web_service.rfq_list_context(
            auth.organization_id,
            status=status,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(request, "procurement/rfqs/list.html", context)


@router.get("/rfqs/new", response_class=HTMLResponse)
def rfq_new(
    request: Request,
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """New RFQ form."""
    context = base_context(request, auth, "New RFQ", "procurement", db=db)
    web_service = ProcurementWebService(db)
    context.update(web_service.rfq_form_context(auth.organization_id))
    return templates.TemplateResponse(request, "procurement/rfqs/form.html", context)


@router.get("/rfqs/{rfq_id}", response_class=HTMLResponse)
def rfq_detail(
    request: Request,
    rfq_id: UUID,
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """RFQ detail view."""
    context = base_context(request, auth, "RFQ Details", "procurement", db=db)
    web_service = ProcurementWebService(db)
    try:
        context.update(web_service.rfq_detail_context(auth.organization_id, rfq_id))
        return templates.TemplateResponse(
            request, "procurement/rfqs/detail.html", context
        )
    except NotFoundError:
        return RedirectResponse(
            url="/procurement/rfqs?error=not_found", status_code=303
        )


@router.get("/rfqs/{rfq_id}/evaluate", response_class=HTMLResponse)
def evaluation_matrix(
    request: Request,
    rfq_id: UUID,
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """Evaluation matrix view."""
    context = base_context(request, auth, "Bid Evaluation", "procurement", db=db)
    web_service = ProcurementWebService(db)
    try:
        context.update(
            web_service.evaluation_matrix_context(auth.organization_id, rfq_id)
        )
        return templates.TemplateResponse(
            request,
            "procurement/evaluations/matrix.html",
            context,
        )
    except NotFoundError:
        return RedirectResponse(
            url="/procurement/rfqs?error=not_found", status_code=303
        )


# =============================================================================
# Contracts
# =============================================================================


@router.get("/contracts", response_class=HTMLResponse)
def contract_list(
    request: Request,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """List contracts."""
    context = base_context(request, auth, "Contracts", "procurement", db=db)
    web_service = ProcurementWebService(db)
    context.update(
        web_service.contract_list_context(
            auth.organization_id,
            status=status,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(
        request, "procurement/contracts/list.html", context
    )


@router.get("/contracts/{contract_id}", response_class=HTMLResponse)
def contract_detail(
    request: Request,
    contract_id: UUID,
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """Contract detail view."""
    context = base_context(request, auth, "Contract Details", "procurement", db=db)
    web_service = ProcurementWebService(db)
    try:
        context.update(
            web_service.contract_detail_context(auth.organization_id, contract_id)
        )
        return templates.TemplateResponse(
            request,
            "procurement/contracts/detail.html",
            context,
        )
    except NotFoundError:
        return RedirectResponse(
            url="/procurement/contracts?error=not_found",
            status_code=303,
        )


# =============================================================================
# Vendors
# =============================================================================


@router.get("/vendors", response_class=HTMLResponse)
def vendor_list(
    request: Request,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """Vendor registry."""
    context = base_context(request, auth, "Vendor Registry", "procurement", db=db)
    web_service = ProcurementWebService(db)
    context.update(
        web_service.vendor_list_context(
            auth.organization_id,
            status=status,
            offset=offset,
            limit=limit,
        )
    )
    return templates.TemplateResponse(request, "procurement/vendors/list.html", context)


@router.get(
    "/vendors/{prequalification_id}/prequalification", response_class=HTMLResponse
)
def prequalification_detail(
    request: Request,
    prequalification_id: UUID,
    auth: WebAuthContext = Depends(require_procurement_access),
    db: Session = Depends(get_db),
):
    """Prequalification detail view."""
    context = base_context(
        request, auth, "Prequalification Details", "procurement", db=db
    )
    web_service = ProcurementWebService(db)
    try:
        context.update(
            web_service.prequalification_detail_context(
                auth.organization_id,
                prequalification_id,
            )
        )
        return templates.TemplateResponse(
            request,
            "procurement/vendors/prequalification.html",
            context,
        )
    except NotFoundError:
        return RedirectResponse(
            url="/procurement/vendors?error=not_found",
            status_code=303,
        )
