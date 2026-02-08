"""
Remita Web Routes.

HTML pages for RRR (Remita Retrieval Reference) management.
"""

from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.remita.client import RemitaError
from app.services.remita.web.remita_web import get_remita_web_service
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_finance_access,
)

router = APIRouter(prefix="/remita", tags=["remita-web"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def remita_list(
    request: Request,
    status: str | None = None,
    biller: str | None = None,
    page: int = Query(default=1, ge=1),
    refresh_msg: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    RRR list page.

    Shows all generated RRRs with filtering by status and biller.
    """
    context = base_context(request, auth, "Remita RRRs", "banking", db=db)

    web_service = get_remita_web_service(db)
    context.update(
        web_service.list_context(
            organization_id=auth.organization_id,
            status_filter=status,
            biller_filter=biller,
            page=page,
        )
    )

    if refresh_msg:
        context["success_message"] = refresh_msg
    if error:
        context["error_message"] = error

    return templates.TemplateResponse(
        request,
        "finance/remita/index.html",
        context,
    )


@router.get("/generate", response_class=HTMLResponse)
def generate_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    RRR generation form page.

    Shows form to generate a new RRR for government payments.
    """
    context = base_context(request, auth, "Generate RRR", "banking", db=db)

    web_service = get_remita_web_service(db)
    context.update(web_service.generate_form_context_with_org(auth.organization_id))

    form_defaults = {}
    qp = request.query_params
    if qp:
        form_defaults = {
            "biller_id": qp.get("biller_id", ""),
            "biller_name": qp.get("biller_name", ""),
            "service_type_id": qp.get("service_type_id", ""),
            "service_name": qp.get("service_name", ""),
            "amount": qp.get("amount", ""),
            "payer_name": qp.get("payer_name", ""),
            "payer_email": qp.get("payer_email", ""),
            "payer_phone": qp.get("payer_phone", ""),
            "description": qp.get("description", ""),
            "source_type": qp.get("source_type", ""),
            "source_id": qp.get("source_id", ""),
        }
    if form_defaults:
        context["form_data"] = form_defaults

    return templates.TemplateResponse(
        request,
        "finance/remita/generate.html",
        context,
    )


@router.post("/generate", response_class=HTMLResponse)
def generate_rrr(
    request: Request,
    biller_id: str = Form(...),
    biller_name: str = Form(...),
    service_type_id: str = Form(...),
    service_name: str = Form(...),
    amount: str = Form(...),
    payer_name: str = Form(...),
    payer_email: str = Form(...),
    payer_phone: str | None = Form(None),
    description: str = Form(""),
    source_type: str | None = Form(None),
    source_id: str | None = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    Process RRR generation form.

    Calls Remita API to generate RRR and saves to database.
    """
    context = base_context(request, auth, "Generate RRR", "banking", db=db)
    web_service = get_remita_web_service(db)
    payer_defaults = web_service.generate_form_context_with_org(
        auth.organization_id
    ).get("payer_defaults", {})

    # Validate amount
    try:
        amount_decimal = Decimal(amount)
        if amount_decimal <= 0:
            raise ValueError("Amount must be positive")
    except (InvalidOperation, ValueError) as e:
        context.update(web_service.generate_form_context_with_org(auth.organization_id))
        context["error"] = f"Invalid amount: {str(e)}"
        context["form_data"] = {
            "biller_id": biller_id,
            "biller_name": biller_name,
            "service_type_id": service_type_id,
            "service_name": service_name,
            "amount": amount,
            "payer_name": payer_name,
            "payer_email": payer_email,
            "payer_phone": payer_phone,
            "description": description,
        }
        return templates.TemplateResponse(
            request,
            "finance/remita/generate.html",
            context,
        )

    try:
        rrr = web_service.generate_rrr(
            organization_id=auth.organization_id,
            biller_id=biller_id,
            biller_name=biller_name,
            service_type_id=service_type_id,
            service_name=service_name,
            amount=amount_decimal,
            payer_name=payer_name or payer_defaults.get("payer_name", ""),
            payer_email=payer_email or payer_defaults.get("payer_email", ""),
            payer_phone=payer_phone or payer_defaults.get("payer_phone"),
            description=description,
            created_by_id=auth.person_id,
        )
        if source_type and source_id:
            web_service.link_rrr(
                organization_id=auth.organization_id,
                rrr_id=rrr.id,
                source_type=source_type,
                source_id=UUID(source_id),
            )
        db.commit()
        return RedirectResponse(
            f"/finance/remita/{rrr.id}?success={'linked' if source_type and source_id else '1'}",
            status_code=303,
        )
    except RemitaError as e:
        context.update(web_service.generate_form_context_with_org(auth.organization_id))
        context["error"] = f"Remita API error: {e.message}"
        context["form_data"] = {
            "biller_id": biller_id,
            "biller_name": biller_name,
            "service_type_id": service_type_id,
            "service_name": service_name,
            "amount": amount,
            "payer_name": payer_name,
            "payer_email": payer_email,
            "payer_phone": payer_phone,
            "description": description,
        }
        return templates.TemplateResponse(
            request,
            "finance/remita/generate.html",
            context,
        )
    except Exception as e:
        db.rollback()
        context.update(web_service.generate_form_context_with_org(auth.organization_id))
        context["error"] = f"Error generating RRR: {str(e)}"
        return templates.TemplateResponse(
            request,
            "finance/remita/generate.html",
            context,
        )


@router.get("/{rrr_id}", response_class=HTMLResponse)
def rrr_detail(
    request: Request,
    rrr_id: UUID,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    RRR detail page.

    Shows RRR details and payment status with actions.
    """
    context = base_context(request, auth, "RRR Detail", "banking", db=db)

    web_service = get_remita_web_service(db)

    try:
        context.update(
            web_service.detail_context(
                organization_id=auth.organization_id,
                rrr_id=rrr_id,
            )
        )
        if success:
            if success == "linked":
                context["success_message"] = "RRR linked successfully"
            else:
                context["success_message"] = "RRR generated successfully"
        if error:
            context["error_message"] = error
    except ValueError as e:
        context["error"] = str(e)

    return templates.TemplateResponse(
        request,
        "finance/remita/detail.html",
        context,
    )


@router.post("/{rrr_id}/refresh", response_class=HTMLResponse)
def refresh_status(
    request: Request,
    rrr_id: UUID,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    Refresh RRR status from Remita API.
    """
    web_service = get_remita_web_service(db)

    try:
        web_service.refresh_status(
            organization_id=auth.organization_id,
            rrr_id=rrr_id,
        )
        db.commit()
    except (ValueError, RemitaError) as e:
        db.rollback()
        # Redirect with error
        return RedirectResponse(
            f"/finance/remita/{rrr_id}?error={str(e)}",
            status_code=303,
        )

    return RedirectResponse(
        f"/finance/remita/{rrr_id}",
        status_code=303,
    )


@router.post("/{rrr_id}/mark-paid", response_class=HTMLResponse)
def mark_paid(
    request: Request,
    rrr_id: UUID,
    payment_reference: str = Form(...),
    payment_channel: str = Form("Bank"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    Manually mark RRR as paid.
    """
    web_service = get_remita_web_service(db)

    try:
        web_service.mark_paid(
            organization_id=auth.organization_id,
            rrr_id=rrr_id,
            payment_reference=payment_reference,
            payment_channel=payment_channel,
        )
        db.commit()
    except ValueError as e:
        db.rollback()
        return RedirectResponse(
            f"/finance/remita/{rrr_id}?error={str(e)}",
            status_code=303,
        )

    return RedirectResponse(
        f"/finance/remita/{rrr_id}",
        status_code=303,
    )


@router.post("/{rrr_id}/link", response_class=HTMLResponse)
def link_source(
    request: Request,
    rrr_id: UUID,
    source_type: str = Form(...),
    source_id: str = Form(...),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Link an RRR to a source entity."""
    web_service = get_remita_web_service(db)

    try:
        source_uuid = UUID(source_id)
        web_service.link_rrr(
            organization_id=auth.organization_id,
            rrr_id=rrr_id,
            source_type=source_type,
            source_id=source_uuid,
        )
        db.commit()
    except ValueError as e:
        db.rollback()
        return RedirectResponse(
            f"/finance/remita/{rrr_id}?error={str(e)}",
            status_code=303,
        )

    return RedirectResponse(
        f"/finance/remita/{rrr_id}?success=linked",
        status_code=303,
    )


@router.get("/source-search", response_class=JSONResponse)
def source_search(
    request: Request,
    source_type: str = Query(...),
    q: str = Query(""),
    status: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    recent: bool = Query(False),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Search source entities for linking to an RRR."""
    web_service = get_remita_web_service(db)
    try:
        results = web_service.search_sources(
            organization_id=auth.organization_id,
            source_type=source_type,
            query=q,
            limit=10,
            status=status or None,
            date_from=date_from or None,
            date_to=date_to or None,
            recent=recent,
        )
        return JSONResponse({"items": results})
    except ValueError as e:
        return JSONResponse({"items": [], "error": str(e)}, status_code=400)


@router.post("/refresh-all", response_class=HTMLResponse)
def refresh_all_pending(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    Refresh status of all pending RRRs.
    """
    from app.services.remita.rrr_service import RemitaRRRService

    service = RemitaRRRService(db)

    try:
        results = service.refresh_pending_statuses(auth.organization_id)
        db.commit()

        # Build message
        parts = []
        if results["paid"] > 0:
            parts.append(f"{results['paid']} paid")
        if results["failed"] > 0:
            parts.append(f"{results['failed']} failed")
        if results["errors"] > 0:
            parts.append(f"{results['errors']} errors")

        if parts:
            msg = f"Refreshed {results['checked']} RRRs: " + ", ".join(parts)
        else:
            msg = f"Refreshed {results['checked']} RRRs, no status changes"

        return RedirectResponse(
            f"/finance/remita?refresh_msg={msg}",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            f"/finance/remita?error={str(e)}",
            status_code=303,
        )


@router.post("/{rrr_id}/cancel", response_class=HTMLResponse)
def cancel_rrr(
    request: Request,
    rrr_id: UUID,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    Cancel a pending RRR.
    """
    web_service = get_remita_web_service(db)

    try:
        web_service.cancel_rrr(
            organization_id=auth.organization_id,
            rrr_id=rrr_id,
        )
        db.commit()
    except ValueError as e:
        db.rollback()
        return RedirectResponse(
            f"/finance/remita/{rrr_id}?error={str(e)}",
            status_code=303,
        )

    return RedirectResponse(
        f"/finance/remita/{rrr_id}",
        status_code=303,
    )
