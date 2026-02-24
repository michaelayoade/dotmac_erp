"""
Support Web Routes.

HTML template routes for helpdesk/support ticket management.
"""

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.support.web import support_web_service
from app.web.deps import WebAuthContext, get_db, require_support_access

router = APIRouter(prefix="/support", tags=["support-web"])


def _manual_ticket_creation_disabled_response(request: Request):
    """Manual ticket creation is disabled; tickets are CRM-synced."""
    from app.templates import templates

    return templates.TemplateResponse(
        "errors/404.html",
        {
            "request": request,
            "message": "Manual ticket creation is disabled. Tickets are synced from CRM.",
        },
        status_code=404,
    )


# ============================================================================
# SLA Dashboard & Reports
# ============================================================================


@router.get("/dashboard", response_class=HTMLResponse)
def sla_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
):
    """SLA dashboard with metrics and reports."""
    return support_web_service.sla_dashboard_response(
        request,
        auth,
        db,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/reports/breaches", response_class=HTMLResponse)
def breached_tickets(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    breach_type: str = Query(default="all"),
    include_resolved: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Breached tickets report."""
    return support_web_service.breached_tickets_response(
        request,
        auth,
        db,
        breach_type=breach_type,
        include_resolved=include_resolved,
    )


@router.get("/reports/aging", response_class=HTMLResponse)
def aging_report(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """Ticket aging report."""
    return support_web_service.aging_report_response(
        request,
        auth,
        db,
        status_filter=status,
    )


# ============================================================================
# Ticket List
# ============================================================================


@router.get("/tickets", response_class=HTMLResponse)
def list_tickets(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    search: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assigned_to: str | None = None,
    category: str | None = None,
    team: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    """Support tickets list page."""
    return support_web_service.list_tickets_response(
        request,
        auth,
        db,
        search=search,
        status=status,
        priority=priority,
        assigned_to=assigned_to,
        category_id=category,
        team_id=team,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page,
    )


# ============================================================================
# Ticket Create & Static Sub-Paths (must be before /tickets/{ticket_id})
# ============================================================================


@router.get("/tickets/archived", response_class=HTMLResponse)
def archived_tickets(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    """Archived tickets list page."""
    return support_web_service.archived_tickets_response(
        request,
        auth,
        db,
        search=search,
        page=page,
        per_page=per_page,
    )


@router.get("/tickets/new", response_class=HTMLResponse)
def new_ticket_form(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """New ticket form page."""
    return _manual_ticket_creation_disabled_response(request)


@router.post("/tickets")
async def create_ticket(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Create a new support ticket."""
    return _manual_ticket_creation_disabled_response(request)


# ============================================================================
# Ticket Detail & Edit
# ============================================================================


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def view_ticket(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Ticket detail page."""
    return support_web_service.ticket_detail_response(request, auth, db, ticket_id)


@router.get("/tickets/{ticket_id}/edit", response_class=HTMLResponse)
def edit_ticket_form(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Edit ticket form page."""
    return support_web_service.ticket_form_response(
        request, auth, db, ticket_id=ticket_id
    )


@router.post("/tickets/{ticket_id}")
def update_ticket(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    subject: str | None = Form(default=None),
    description: str | None = Form(default=None),
    priority: str | None = Form(default=None),
    raised_by_email: str | None = Form(default=None),
    assigned_to_id: str | None = Form(default=None),
    project_id: str | None = Form(default=None),
    customer_id: str | None = Form(default=None),
    category_id: str | None = Form(default=None),
    team_id: str | None = Form(default=None),
    contact_email: str | None = Form(default=None),
    contact_phone: str | None = Form(default=None),
    contact_address: str | None = Form(default=None),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Update a ticket."""
    return support_web_service.update_ticket_response(
        request,
        auth,
        db,
        ticket_id,
        subject=subject,
        description=description,
        priority=priority,
        raised_by_email=raised_by_email,
        assigned_to_id=assigned_to_id if assigned_to_id else None,
        project_id=project_id if project_id else None,
        customer_id=customer_id if customer_id else None,
        category_id=category_id if category_id else None,
        team_id=team_id if team_id else None,
        contact_email=contact_email,
        contact_phone=contact_phone,
        contact_address=contact_address,
        files=files,
    )


# ============================================================================
# Ticket Actions
# ============================================================================


@router.post("/tickets/{ticket_id}/status")
def update_ticket_status(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    status: str = Form(...),
    notes: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Update ticket status."""
    return support_web_service.update_status_response(
        request, auth, db, ticket_id, status, notes
    )


@router.post("/tickets/{ticket_id}/assign")
def assign_ticket(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    assigned_to_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Assign ticket to an employee."""
    form_data = getattr(request.state, "csrf_form", None)
    if form_data:
        assigned_to_id = (
            form_data.get("assigned_to_id") or assigned_to_id or ""
        ).strip()

    if not assigned_to_id:
        return RedirectResponse(
            url=f"/support/tickets/{ticket_id}?error=no_assignee_selected",
            status_code=303,
        )

    return support_web_service.assign_ticket_response(
        request, auth, db, ticket_id, assigned_to_id
    )


@router.post("/tickets/{ticket_id}/resolve")
def resolve_ticket(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    resolution: str = Form(...),
    db: Session = Depends(get_db),
):
    """Mark ticket as resolved."""
    return support_web_service.resolve_ticket_response(
        request, auth, db, ticket_id, resolution
    )


@router.post("/tickets/{ticket_id}/archive")
def archive_ticket(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Archive (soft delete) a ticket."""
    return support_web_service.archive_ticket_response(request, auth, db, ticket_id)


@router.post("/tickets/{ticket_id}/delete")
def delete_ticket(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Hard delete a ticket."""
    if not auth.is_admin:
        return RedirectResponse(
            url=f"/support/tickets/{ticket_id}?error=forbidden",
            status_code=303,
        )
    return support_web_service.delete_ticket_response(request, auth, db, ticket_id)


@router.post("/tickets/{ticket_id}/restore")
def restore_ticket(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Restore an archived ticket."""
    return support_web_service.restore_ticket_response(request, auth, db, ticket_id)


# ============================================================================
# Comments
# ============================================================================


@router.post("/tickets/{ticket_id}/comments")
def add_comment(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    content: str = Form(...),
    is_internal: bool = Form(default=False),
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Add a comment to a ticket."""
    return support_web_service.add_comment_response(
        request, auth, db, ticket_id, content, is_internal, files
    )


@router.post("/tickets/{ticket_id}/comments/{comment_id}/delete")
def delete_comment(
    request: Request,
    ticket_id: str,
    comment_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Delete a comment."""
    return support_web_service.delete_comment_response(
        request, auth, db, ticket_id, comment_id
    )


# ============================================================================
# Attachments
# ============================================================================


@router.post("/tickets/{ticket_id}/attachments")
async def upload_attachment(
    request: Request,
    ticket_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Upload an attachment to a ticket."""
    # Get the file from the multipart form
    form = await request.form()
    file = form.get("file")
    if not file:
        return RedirectResponse(
            url=f"/support/tickets/{ticket_id}?error=No+file+provided",
            status_code=303,
        )

    return await support_web_service.upload_attachment_response(
        request, auth, db, ticket_id, file
    )


@router.get("/tickets/{ticket_id}/attachments/{attachment_id}")
def download_attachment(
    request: Request,
    ticket_id: str,
    attachment_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Download an attachment."""
    return support_web_service.download_attachment_response(
        request, auth, db, ticket_id, attachment_id
    )


@router.post("/tickets/{ticket_id}/attachments/{attachment_id}/delete")
def delete_attachment(
    request: Request,
    ticket_id: str,
    attachment_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Delete an attachment."""
    return support_web_service.delete_attachment_response(
        request, auth, db, ticket_id, attachment_id
    )


# ============================================================================
# Categories
# ============================================================================


@router.get("/categories", response_class=HTMLResponse)
def list_categories(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """List ticket categories."""
    return support_web_service.list_categories_response(request, auth, db)


@router.get("/categories/new", response_class=HTMLResponse)
def new_category_form(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """New category form."""
    return support_web_service.category_form_response(request, auth, db)


@router.post("/categories")
def create_category(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    category_code: str = Form(...),
    category_name: str = Form(...),
    description: str | None = Form(default=None),
    color: str | None = Form(default=None),
    icon: str | None = Form(default=None),
    default_priority: str | None = Form(default=None),
    response_hours: int | None = Form(default=None),
    resolution_hours: int | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Create a new category."""
    return support_web_service.create_category_response(
        request,
        auth,
        db,
        category_code=category_code,
        category_name=category_name,
        description=description,
        color=color,
        icon=icon,
        default_priority=default_priority,
        response_hours=response_hours,
        resolution_hours=resolution_hours,
    )


# ============================================================================
# Category Edit
# ============================================================================


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse)
def edit_category_form(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Edit category form."""
    return support_web_service.category_form_response(
        request, auth, db, category_id=category_id
    )


@router.post("/categories/{category_id}")
def update_category(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    category_name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    color: str | None = Form(default=None),
    icon: str | None = Form(default=None),
    default_priority: str | None = Form(default=None),
    response_hours: int | None = Form(default=None),
    resolution_hours: int | None = Form(default=None),
    is_active: bool = Form(default=True),
    db: Session = Depends(get_db),
):
    """Update a category."""
    return support_web_service.update_category_response(
        request,
        auth,
        db,
        category_id,
        category_name=category_name,
        description=description,
        color=color,
        icon=icon,
        default_priority=default_priority,
        response_hours=response_hours,
        resolution_hours=resolution_hours,
        is_active=is_active,
    )


# ============================================================================
# Teams
# ============================================================================


@router.get("/teams", response_class=HTMLResponse)
def list_teams(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """List support teams."""
    return support_web_service.list_teams_response(request, auth, db)


@router.get("/teams/new", response_class=HTMLResponse)
def new_team_form(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """New team form."""
    return support_web_service.team_form_response(request, auth, db)


@router.post("/teams")
def create_team(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    team_code: str = Form(...),
    team_name: str = Form(...),
    description: str | None = Form(default=None),
    lead_id: str | None = Form(default=None),
    auto_assign: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    """Create a new team."""
    return support_web_service.create_team_response(
        request,
        auth,
        db,
        team_code=team_code,
        team_name=team_name,
        description=description,
        lead_id=lead_id if lead_id else None,
        auto_assign=auto_assign,
    )


@router.get("/teams/{team_id}", response_class=HTMLResponse)
def view_team(
    request: Request,
    team_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Team detail page."""
    return support_web_service.team_detail_response(request, auth, db, team_id)


@router.get("/teams/{team_id}/edit", response_class=HTMLResponse)
def edit_team_form(
    request: Request,
    team_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Edit team form."""
    return support_web_service.team_form_response(request, auth, db, team_id=team_id)


@router.post("/teams/{team_id}")
def update_team(
    request: Request,
    team_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    team_name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    lead_id: str | None = Form(default=None),
    auto_assign: bool = Form(default=False),
    is_active: bool = Form(default=True),
    db: Session = Depends(get_db),
):
    """Update a team."""
    return support_web_service.update_team_response(
        request,
        auth,
        db,
        team_id,
        team_name=team_name,
        description=description,
        lead_id=lead_id if lead_id else None,
        auto_assign=auto_assign,
        is_active=is_active,
    )


@router.post("/teams/{team_id}/members")
def add_team_member(
    request: Request,
    team_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    employee_id: str = Form(...),
    role: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Add a member to a team."""
    return support_web_service.add_team_member_response(
        request, auth, db, team_id, employee_id, role
    )


@router.post("/teams/{team_id}/members/{member_id}/remove")
def remove_team_member(
    request: Request,
    team_id: str,
    member_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Remove a member from a team."""
    return support_web_service.remove_team_member_response(
        request, auth, db, team_id, member_id
    )


@router.post("/teams/{team_id}/members/{member_id}/toggle-availability")
def toggle_member_availability(
    request: Request,
    team_id: str,
    member_id: str,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Toggle a team member's availability for ticket assignment."""
    return support_web_service.toggle_member_availability_response(
        request, auth, db, team_id, member_id
    )


@router.post("/teams/{team_id}/members/{member_id}/weight")
def update_member_weight(
    request: Request,
    team_id: str,
    member_id: str,
    weight: int = Form(...),
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Update a team member's assignment weight."""
    return support_web_service.update_member_weight_response(
        request, auth, db, team_id, member_id, weight
    )


# ============================================================================
# Bulk Operations
# ============================================================================


@router.post("/tickets/bulk/status")
async def bulk_update_status(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Bulk update status for selected tickets."""
    form = await request.form()
    ticket_ids = form.getlist("ticket_ids")
    new_status = form.get("status", "")
    notes = form.get("notes")

    return support_web_service.bulk_update_status_response(
        request,
        auth,
        db,
        ticket_ids=list(ticket_ids),
        new_status=new_status,
        notes=notes if notes else None,
    )


@router.post("/tickets/bulk/assign")
async def bulk_assign(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Bulk assign selected tickets to an employee."""
    form = await request.form()
    ticket_ids = form.getlist("ticket_ids")
    assigned_to_id = form.get("assigned_to_id", "")

    if not assigned_to_id:
        return RedirectResponse(
            url="/support/tickets?error=no_assignee_selected",
            status_code=303,
        )

    return support_web_service.bulk_assign_response(
        request,
        auth,
        db,
        ticket_ids=list(ticket_ids),
        assigned_to_id=assigned_to_id,
    )


@router.post("/tickets/bulk/archive")
async def bulk_archive(
    request: Request,
    auth: WebAuthContext = Depends(require_support_access),
    db: Session = Depends(get_db),
):
    """Bulk archive selected tickets."""
    form = await request.form()
    ticket_ids = form.getlist("ticket_ids")

    return support_web_service.bulk_archive_response(
        request,
        auth,
        db,
        ticket_ids=list(ticket_ids),
    )
