"""Discipline Management Web Routes - HR Admin Interface.

Provides HTML template routes for discipline case management:
- Case listing with filters
- Case detail and workflow actions
- Case creation forms
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.discipline.web import discipline_web_service
from app.web.deps import get_db, require_hr_access, WebAuthContext


router = APIRouter(prefix="/discipline", tags=["discipline-web"])


# =============================================================================
# Case List and Detail
# =============================================================================


@router.get("", response_class=HTMLResponse)
def list_cases(
    request: Request,
    status: Optional[str] = None,
    violation_type: Optional[str] = None,
    severity: Optional[str] = None,
    employee_id: Optional[str] = None,
    include_closed: bool = False,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
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
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Render form to create a new disciplinary case."""
    return discipline_web_service.case_new_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/new")
def create_case(
    employee_id: str = Form(...),
    violation_type: str = Form(...),
    severity: str = Form(...),
    subject: str = Form(...),
    description: Optional[str] = Form(None),
    incident_date: Optional[str] = Form(None),
    reported_by_id: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new disciplinary case."""
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


@router.get("/{case_id}", response_class=HTMLResponse)
def case_detail(
    request: Request,
    case_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
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
    case_id: UUID,
    query_text: str = Form(...),
    response_due_date: str = Form(...),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Issue a query to the employee."""
    return discipline_web_service.issue_query_response(
        auth=auth,
        db=db,
        case_id=case_id,
        query_text=query_text,
        response_due_date=response_due_date,
    )


@router.post("/{case_id}/schedule-hearing")
def schedule_hearing(
    case_id: UUID,
    hearing_date: str = Form(...),
    hearing_location: Optional[str] = Form(None),
    panel_chair_id: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Schedule a disciplinary hearing."""
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
    case_id: UUID,
    hearing_notes: str = Form(...),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Record hearing notes."""
    return discipline_web_service.record_hearing_response(
        auth=auth,
        db=db,
        case_id=case_id,
        hearing_notes=hearing_notes,
    )


@router.post("/{case_id}/decision")
def record_decision(
    case_id: UUID,
    decision_summary: str = Form(...),
    action_type: Optional[str] = Form(None),
    action_description: Optional[str] = Form(None),
    effective_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Record decision and actions."""
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
    auth: WebAuthContext = Depends(require_hr_access),
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
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Withdraw a disciplinary case."""
    return discipline_web_service.withdraw_case_response(
        auth=auth,
        db=db,
        case_id=case_id,
    )


# =============================================================================
# Witnesses
# =============================================================================


@router.post("/{case_id}/witnesses")
def add_witness(
    case_id: UUID,
    employee_id: Optional[str] = Form(None),
    external_name: Optional[str] = Form(None),
    external_contact: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Add a witness to a case."""
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
    auth: WebAuthContext = Depends(require_hr_access),
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
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Upload a document to a disciplinary case."""
    from fastapi.responses import RedirectResponse
    from app.services.people.discipline.attachment_service import DisciplineAttachmentService
    from app.models.people.discipline import DocumentType as DocType

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
            organization_id=auth.org_id,
            case_id=case_id,
            file_content=file.file,
            file_name=file.filename or "document",
            content_type=file.content_type or "application/octet-stream",
            document_type=doc_type,
            uploaded_by_id=auth.person_id,
            title=title,
            description=description,
        )
        db.commit()

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
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Download a document from a disciplinary case."""
    from fastapi.responses import FileResponse
    from app.services.people.discipline.attachment_service import DisciplineAttachmentService

    service = DisciplineAttachmentService(db)
    document = service.get_document(auth.org_id, document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if document.case_id != case_id:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        file_path = service.get_file_path(document)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found on server")

        return FileResponse(
            path=str(file_path),
            filename=document.file_name,
            media_type=document.mime_type or "application/octet-stream",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{case_id}/documents/{document_id}/delete")
def delete_document(
    case_id: UUID,
    document_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a document from a disciplinary case."""
    from fastapi.responses import RedirectResponse
    from app.services.people.discipline.attachment_service import DisciplineAttachmentService

    service = DisciplineAttachmentService(db)
    document = service.get_document(auth.org_id, document_id)

    if not document or document.case_id != case_id:
        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?error=document_not_found",
            status_code=303,
        )

    deleted = service.delete(auth.org_id, document_id)
    if deleted:
        db.commit()

    return RedirectResponse(
        url=f"/people/hr/discipline/{case_id}?success=document_deleted",
        status_code=303,
    )
