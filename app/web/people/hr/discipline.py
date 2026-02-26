"""Discipline Management Web Routes - HR Admin Interface.

Provides HTML template routes for discipline case management:
- Case listing with filters
- Case detail and workflow actions
- Case creation forms
"""

from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.people.discipline.web import discipline_web_service
from app.web.deps import (
    WebAuthContext,
    get_db,
    require_discipline_cases_create,
    require_discipline_cases_read,
    require_discipline_cases_update,
    require_discipline_workflow_manage,
)

router = APIRouter(prefix="/discipline", tags=["discipline-web"])


# =============================================================================
# Case List and Detail
# =============================================================================


@router.get("", response_class=HTMLResponse)
def list_cases(
    request: Request,
    status: str | None = None,
    violation_type: str | None = None,
    severity: str | None = None,
    employee_id: str | None = None,
    include_closed: bool = False,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_discipline_cases_read),
    db: Session = Depends(get_db),
):
    """List disciplinary cases with filters."""
    return discipline_web_service.list_cases_response(
        request=request,
        auth=auth,
        db=db,
        status=status,
        violation_type=violation_type,
        severity=severity,
        employee_id=employee_id,
        include_closed=include_closed,
        page=page,
    )


@router.get("/new", response_class=HTMLResponse)
def new_case_form(
    request: Request,
    auth: WebAuthContext = Depends(require_discipline_cases_create),
    db: Session = Depends(get_db),
):
    """Render form to create a new disciplinary case."""
    return discipline_web_service.case_new_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/employees/search")
def discipline_employee_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=8, ge=1, le=20),
    auth: WebAuthContext = Depends(require_discipline_cases_read),
    db: Session = Depends(get_db),
):
    """Search active employees for discipline typeahead."""
    payload = discipline_web_service.employee_typeahead(
        db=db,
        organization_id=str(auth.organization_id),
        query=q,
        limit=limit,
    )
    return JSONResponse(payload)


@router.post("/new")
async def create_case(
    request: Request,
    employee_id: str | None = Form(None),
    violation_type: str | None = Form(None),
    severity: str | None = Form(None),
    subject: str | None = Form(None),
    description: str | None = Form(None),
    incident_date: str | None = Form(None),
    reported_by_id: str | None = Form(None),
    auth: WebAuthContext = Depends(require_discipline_cases_create),
    db: Session = Depends(get_db),
):
    """Create a new disciplinary case."""
    form = getattr(request.state, "csrf_form", None)
    if not hasattr(form, "get"):
        try:
            form = await request.form()
        except Exception:
            form = None

    def _value(name: str, fallback: str | None = None) -> str | None:
        raw = fallback
        if form and hasattr(form, "get"):
            candidate = form.get(name)
            if isinstance(candidate, str) and candidate:
                raw = candidate
        if isinstance(raw, str):
            raw = raw.strip()
        return raw or None

    employee_id = _value("employee_id", employee_id)
    violation_type = _value("violation_type", violation_type)
    severity = _value("severity", severity)
    subject = _value("subject", subject)
    description = _value("description", description)
    incident_date = _value("incident_date", incident_date)
    reported_by_id = _value("reported_by_id", reported_by_id)
    employee_name: str | None = None
    reported_by_name: str | None = None
    if form and hasattr(form, "get"):
        employee_name = _value("employee_name", employee_name)
        reported_by_name = _value("reported_by_name", reported_by_name)

    if not employee_id or not violation_type or not severity or not subject:
        return discipline_web_service.case_new_form_response(
            request=request,
            auth=auth,
            db=db,
            error="Employee, violation type, severity, and subject are required.",
            form_data={
                "employee_id": employee_id or "",
                "employee_name": employee_name or "",
                "violation_type": violation_type or "",
                "severity": severity or "",
                "subject": subject or "",
                "description": description or "",
                "incident_date": incident_date or "",
                "reported_by_id": reported_by_id or "",
                "reported_by_name": reported_by_name or "",
            },
        )
    try:
        return discipline_web_service.case_create_response(
            auth=auth,
            db=db,
            employee_id=employee_id,
            violation_type=violation_type,
            severity=severity,
            subject=subject,
            description=description,
            incident_date=incident_date,
            reported_by_id=reported_by_id,
        )
    except Exception as exc:
        message = getattr(exc, "detail", None) or str(exc)
        return discipline_web_service.case_new_form_response(
            request=request,
            auth=auth,
            db=db,
            error=message,
            form_data={
                "employee_id": employee_id or "",
                "employee_name": employee_name or "",
                "violation_type": violation_type or "",
                "severity": severity or "",
                "subject": subject or "",
                "description": description or "",
                "incident_date": incident_date or "",
                "reported_by_id": reported_by_id or "",
                "reported_by_name": reported_by_name or "",
            },
        )


@router.get("/{case_id}", response_class=HTMLResponse)
def case_detail(
    request: Request,
    case_id: UUID,
    auth: WebAuthContext = Depends(require_discipline_cases_read),
    db: Session = Depends(get_db),
):
    """View disciplinary case details."""
    return discipline_web_service.case_detail_response(
        request=request,
        auth=auth,
        db=db,
        case_id=case_id,
    )


# =============================================================================
# Workflow Operations
# =============================================================================


@router.post("/{case_id}/issue-query")
def issue_query(
    request: Request,
    case_id: UUID,
    query_text: str | None = Form(None),
    response_due_date: str | None = Form(None),
    auth: WebAuthContext = Depends(require_discipline_workflow_manage),
    db: Session = Depends(get_db),
):
    """Issue a query to the employee."""
    form = getattr(request.state, "csrf_form", None)
    if form:
        query_text = form.get("query_text") or query_text
        response_due_date = form.get("response_due_date") or response_due_date

    if not query_text or not response_due_date:
        message = quote("Query text and response due date are required.")
        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?error={message}",
            status_code=303,
        )
    return discipline_web_service.issue_query_response(
        auth=auth,
        db=db,
        case_id=case_id,
        query_text=query_text,
        response_due_date=response_due_date,
    )


@router.post("/{case_id}/schedule-hearing")
def schedule_hearing(
    request: Request,
    case_id: UUID,
    hearing_date: str | None = Form(None),
    hearing_location: str | None = Form(None),
    panel_chair_id: str | None = Form(None),
    auth: WebAuthContext = Depends(require_discipline_workflow_manage),
    db: Session = Depends(get_db),
):
    """Schedule a disciplinary hearing."""
    form = getattr(request.state, "csrf_form", None)
    if form:
        hearing_date = form.get("hearing_date") or hearing_date
        hearing_location = form.get("hearing_location") or hearing_location
        panel_chair_id = form.get("panel_chair_id") or panel_chair_id

    if not hearing_date:
        message = quote("Hearing date is required.")
        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?error={message}",
            status_code=303,
        )
    return discipline_web_service.schedule_hearing_response(
        auth=auth,
        db=db,
        case_id=case_id,
        hearing_date=hearing_date,
        hearing_location=hearing_location,
        panel_chair_id=panel_chair_id,
    )


@router.post("/{case_id}/record-hearing")
def record_hearing(
    request: Request,
    case_id: UUID,
    hearing_notes: str | None = Form(None),
    auth: WebAuthContext = Depends(require_discipline_workflow_manage),
    db: Session = Depends(get_db),
):
    """Record hearing notes."""
    form = getattr(request.state, "csrf_form", None)
    if form:
        hearing_notes = form.get("hearing_notes") or hearing_notes

    if not hearing_notes:
        message = quote("Hearing notes are required.")
        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?error={message}",
            status_code=303,
        )
    return discipline_web_service.record_hearing_response(
        auth=auth,
        db=db,
        case_id=case_id,
        hearing_notes=hearing_notes,
    )


@router.post("/{case_id}/decision")
def record_decision(
    request: Request,
    case_id: UUID,
    decision_summary: str | None = Form(None),
    action_type: str | None = Form(None),
    action_description: str | None = Form(None),
    effective_date: str | None = Form(None),
    end_date: str | None = Form(None),
    auth: WebAuthContext = Depends(require_discipline_workflow_manage),
    db: Session = Depends(get_db),
):
    """Record decision and actions."""
    form = getattr(request.state, "csrf_form", None)
    if form:
        decision_summary = form.get("decision_summary") or decision_summary
        action_type = form.get("action_type") or action_type
        action_description = form.get("action_description") or action_description
        effective_date = form.get("effective_date") or effective_date
        end_date = form.get("end_date") or end_date

    if not decision_summary:
        message = quote("Decision summary is required.")
        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?error={message}",
            status_code=303,
        )
    return discipline_web_service.record_decision_response(
        auth=auth,
        db=db,
        case_id=case_id,
        decision_summary=decision_summary,
        action_type=action_type,
        action_description=action_description,
        effective_date=effective_date,
        end_date=end_date,
    )


@router.post("/{case_id}/close")
def close_case(
    case_id: UUID,
    auth: WebAuthContext = Depends(require_discipline_workflow_manage),
    db: Session = Depends(get_db),
):
    """Close a disciplinary case."""
    return discipline_web_service.close_case_response(
        auth=auth,
        db=db,
        case_id=case_id,
    )


@router.post("/{case_id}/withdraw")
def withdraw_case(
    case_id: UUID,
    auth: WebAuthContext = Depends(require_discipline_workflow_manage),
    db: Session = Depends(get_db),
):
    """Withdraw a disciplinary case."""
    return discipline_web_service.withdraw_case_response(
        auth=auth,
        db=db,
        case_id=case_id,
    )


@router.post("/{case_id}/delete")
def delete_case(
    case_id: UUID,
    auth: WebAuthContext = Depends(require_discipline_cases_update),
    db: Session = Depends(get_db),
):
    """Delete a draft disciplinary case."""
    return discipline_web_service.delete_case_response(
        auth=auth,
        db=db,
        case_id=case_id,
    )


# =============================================================================
# Witnesses
# =============================================================================


@router.post("/{case_id}/witnesses")
def add_witness(
    request: Request,
    case_id: UUID,
    employee_id: str | None = Form(None),
    external_name: str | None = Form(None),
    external_contact: str | None = Form(None),
    auth: WebAuthContext = Depends(require_discipline_cases_update),
    db: Session = Depends(get_db),
):
    """Add a witness to a case."""
    form = getattr(request.state, "csrf_form", None)
    if form:
        employee_id = form.get("employee_id") or employee_id
        external_name = form.get("external_name") or external_name
        external_contact = form.get("external_contact") or external_contact
    return discipline_web_service.add_witness_response(
        auth=auth,
        db=db,
        case_id=case_id,
        employee_id=employee_id,
        external_name=external_name,
        external_contact=external_contact,
    )


@router.post("/{case_id}/responses/{response_id}/acknowledge")
def acknowledge_response(
    case_id: UUID,
    response_id: UUID,
    auth: WebAuthContext = Depends(require_discipline_workflow_manage),
    db: Session = Depends(get_db),
):
    """Acknowledge an employee response."""
    return discipline_web_service.acknowledge_response_response(
        auth=auth,
        db=db,
        case_id=case_id,
        response_id=response_id,
    )


# =============================================================================
# Document Upload/Download
# =============================================================================


@router.post("/{case_id}/documents")
def upload_document(
    request: Request,
    case_id: UUID,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    title: str | None = Form(None),
    description: str | None = Form(None),
    auth: WebAuthContext = Depends(require_discipline_cases_update),
    db: Session = Depends(get_db),
):
    """Upload a document to a disciplinary case."""
    from fastapi.responses import RedirectResponse

    from app.models.people.discipline import DocumentType as DocType
    from app.services.people.discipline.attachment_service import (
        DisciplineAttachmentService,
    )

    # Parse document type
    try:
        doc_type = DocType(document_type)
    except ValueError:
        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?error=invalid_document_type",
            status_code=303,
        )

    service = DisciplineAttachmentService(db)

    try:
        service.save_file(
            organization_id=auth.organization_id,
            case_id=case_id,
            file_content=file.file,
            file_name=file.filename or "document",
            content_type=file.content_type or "application/octet-stream",
            document_type=doc_type,
            uploaded_by_id=auth.person_id,
            title=title,
            description=description,
        )

        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?success=document_uploaded",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?error={str(e)}",
            status_code=303,
        )


@router.get("/{case_id}/documents/{document_id}/download")
def download_document(
    case_id: UUID,
    document_id: UUID,
    auth: WebAuthContext = Depends(require_discipline_cases_read),
    db: Session = Depends(get_db),
):
    """Download a document from a disciplinary case."""
    import re

    from fastapi.responses import StreamingResponse

    from app.services.people.discipline.attachment_service import (
        DisciplineAttachmentService,
    )
    from app.services.storage import get_storage

    service = DisciplineAttachmentService(db)
    document = service.get_document(auth.organization_id, document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if document.case_id != case_id:
        raise HTTPException(status_code=404, detail="Document not found")

    if not document.file_path:
        raise HTTPException(status_code=404, detail="Document file not available")

    s3_key = document.file_path
    if not s3_key.startswith("discipline/"):
        s3_key = f"discipline/{s3_key}"

    storage = get_storage()
    if not storage.exists(s3_key):
        raise HTTPException(status_code=404, detail="File not found")

    chunks, content_type, content_length = storage.stream(s3_key)
    safe_name = re.sub(
        r'[\\x00-\\x1f\\x7f"\\\\]', "_", document.file_name or "document"
    )
    headers: dict[str, str] = {
        "Content-Disposition": f'attachment; filename="{safe_name}"'
    }
    if content_length is not None:
        headers["Content-Length"] = str(content_length)

    return StreamingResponse(
        chunks,
        media_type=content_type or document.mime_type or "application/octet-stream",
        headers=headers,
    )


@router.post("/{case_id}/documents/{document_id}/delete")
def delete_document(
    case_id: UUID,
    document_id: UUID,
    auth: WebAuthContext = Depends(require_discipline_cases_update),
    db: Session = Depends(get_db),
):
    """Delete a document from a disciplinary case."""
    from fastapi.responses import RedirectResponse

    from app.services.people.discipline.attachment_service import (
        DisciplineAttachmentService,
    )

    service = DisciplineAttachmentService(db)
    document = service.get_document(auth.organization_id, document_id)

    if not document or document.case_id != case_id:
        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?error=document_not_found",
            status_code=303,
        )

    service.delete(auth.organization_id, document_id)
    return RedirectResponse(
        url=f"/people/hr/discipline/{case_id}?success=document_deleted",
        status_code=303,
    )
