"""Employee lifecycle routes - onboarding, offboarding, bulk operations."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.hr.web.lifecycle_web import lifecycle_web_service
from app.web.deps import get_db, require_hr_access, WebAuthContext


router = APIRouter(tags=["lifecycle"])


@router.get("/employees/{employee_id}/onboarding/new", response_class=HTMLResponse)
def new_onboarding_form(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Render form to create onboarding checklist for employee."""
    return lifecycle_web_service.new_onboarding_form_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/onboarding/new")
async def create_onboarding(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create onboarding record for an employee."""
    return await lifecycle_web_service.create_onboarding_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/onboarding/start")
async def start_onboarding(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Start the onboarding process for an employee."""
    return await lifecycle_web_service.start_onboarding_response(
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/onboarding/activity/{activity_id}/toggle")
async def toggle_onboarding_activity(
    request: Request,
    employee_id: UUID,
    activity_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Toggle an onboarding activity completion status."""
    return await lifecycle_web_service.toggle_onboarding_activity_response(
        request=request,
        employee_id=employee_id,
        activity_id=activity_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/onboarding/complete")
async def complete_onboarding(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Mark onboarding as complete."""
    return await lifecycle_web_service.complete_onboarding_response(
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/user-credentials")
async def create_employee_user_credentials(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create user credentials for an employee."""
    return await lifecycle_web_service.create_employee_user_credentials_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/link-user")
async def link_employee_user(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Link an employee to an existing user (Person)."""
    return await lifecycle_web_service.link_employee_user_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.get("/people/search")
def search_people(
    query: str = Query("", min_length=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Search people by name or email for linking users."""
    return lifecycle_web_service.search_people_response(
        query=query,
        auth=auth,
        db=db,
    )


@router.post("/employees/bulk-update")
async def bulk_update_employees(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk update employees."""
    return await lifecycle_web_service.bulk_update_employees_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/employees/bulk-delete")
async def bulk_delete_employees(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk delete employees."""
    return await lifecycle_web_service.bulk_delete_employees_response(
        request=request,
        auth=auth,
        db=db,
    )


# =============================================================================
# Promotions
# =============================================================================


@router.get("/promotions", response_class=HTMLResponse)
def list_promotions(
    request: Request,
    employee_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List all promotions."""
    return lifecycle_web_service.list_promotions_response(
        request=request,
        employee_id=employee_id,
        page=page,
        auth=auth,
        db=db,
    )


@router.get("/employees/{employee_id}/promotions/new", response_class=HTMLResponse)
def new_promotion_form(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Render form to record a promotion for an employee."""
    return lifecycle_web_service.new_promotion_form_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/promotions/new")
async def create_promotion(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a promotion record for an employee."""
    return await lifecycle_web_service.create_promotion_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.get("/promotions/{promotion_id}", response_class=HTMLResponse)
def promotion_detail(
    request: Request,
    promotion_id: UUID,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View promotion details."""
    return lifecycle_web_service.promotion_detail_response(
        request=request,
        promotion_id=promotion_id,
        success=success,
        error=error,
        auth=auth,
        db=db,
    )


# =============================================================================
# Transfers
# =============================================================================


@router.get("/transfers", response_class=HTMLResponse)
def list_transfers(
    request: Request,
    employee_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List all transfers."""
    return lifecycle_web_service.list_transfers_response(
        request=request,
        employee_id=employee_id,
        page=page,
        auth=auth,
        db=db,
    )


@router.get("/employees/{employee_id}/transfers/new", response_class=HTMLResponse)
def new_transfer_form(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Render form to record a transfer for an employee."""
    return lifecycle_web_service.new_transfer_form_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.post("/employees/{employee_id}/transfers/new")
async def create_transfer(
    request: Request,
    employee_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a transfer record for an employee."""
    return await lifecycle_web_service.create_transfer_response(
        request=request,
        employee_id=employee_id,
        auth=auth,
        db=db,
    )


@router.get("/transfers/{transfer_id}", response_class=HTMLResponse)
def transfer_detail(
    request: Request,
    transfer_id: UUID,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View transfer details."""
    return lifecycle_web_service.transfer_detail_response(
        request=request,
        transfer_id=transfer_id,
        success=success,
        error=error,
        auth=auth,
        db=db,
    )
