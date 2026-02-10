"""
Discipline Management API Router.

Thin API wrapper for Discipline Management endpoints. All business logic is in services.
"""

import logging
from datetime import date
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.models.people.discipline import CaseStatus, SeverityLevel, ViolationType
from app.schemas.people.discipline import (
    CaseActionRead,
    # Filters
    CaseListFilter,
    CaseResponseCreate,
    CaseResponseRead,
    # Related entities
    CaseWitnessCreate,
    CaseWitnessRead,
    DecideAppealRequest,
    # Case
    DisciplinaryCaseCreate,
    DisciplinaryCaseDetail,
    DisciplinaryCaseListResponse,
    DisciplinaryCaseRead,
    DisciplinaryCaseUpdate,
    FileAppealRequest,
    # Workflow
    IssueQueryRequest,
    RecordDecisionRequest,
    RecordHearingNotesRequest,
    ScheduleHearingRequest,
)
from app.services.people.discipline import DisciplineService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/discipline",
    tags=["discipline"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Case CRUD
# =============================================================================


@router.get("/cases", response_model=DisciplinaryCaseListResponse)
def list_cases(
    org_id: UUID = Depends(require_organization_id),
    status: CaseStatus | None = Query(None),
    violation_type: ViolationType | None = Query(None),
    severity: SeverityLevel | None = Query(None),
    employee_id: UUID | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    include_closed: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List disciplinary cases with filters."""
    service = DisciplineService(db)
    filters = CaseListFilter(
        status=status,
        violation_type=violation_type,
        severity=severity,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
        include_closed=include_closed,
    )
    cases, total = service.list_cases(
        org_id, filters=filters, offset=offset, limit=limit
    )

    # Convert to response models with employee names
    items = []
    for case in cases:
        item = DisciplinaryCaseRead.model_validate(case)
        item.employee_name = case.employee.full_name if case.employee else None
        items.append(item)

    return DisciplinaryCaseListResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/cases", response_model=DisciplinaryCaseRead, status_code=status.HTTP_201_CREATED
)
def create_case(
    data: DisciplinaryCaseCreate,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a new disciplinary case."""
    service = DisciplineService(db)
    case = service.create_case(org_id, data)
    db.commit()
    return DisciplinaryCaseRead.model_validate(case)


@router.get("/cases/{case_id}", response_model=DisciplinaryCaseDetail)
def get_case(
    case_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a disciplinary case with all details."""
    service = DisciplineService(db)
    case = service.get_case_detail(case_id)

    # Verify organization
    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    # Build response with related entities
    response = DisciplinaryCaseDetail.model_validate(case)
    response.employee_name = case.employee.full_name if case.employee else None
    response.reported_by_name = case.reported_by.full_name if case.reported_by else None
    response.investigating_officer_name = (
        case.investigating_officer.full_name if case.investigating_officer else None
    )
    response.panel_chair_name = case.panel_chair.full_name if case.panel_chair else None

    # Add related entities
    response.witnesses = [CaseWitnessRead.model_validate(w) for w in case.witnesses]
    response.actions = [CaseActionRead.model_validate(a) for a in case.actions]
    response.responses = [CaseResponseRead.model_validate(r) for r in case.responses]

    return response


@router.patch("/cases/{case_id}", response_model=DisciplinaryCaseRead)
def update_case(
    case_id: UUID,
    data: DisciplinaryCaseUpdate,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a disciplinary case (only in DRAFT status)."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    case = service.update_case(case_id, data)
    db.commit()
    return DisciplinaryCaseRead.model_validate(case)


# =============================================================================
# Workflow Operations
# =============================================================================


@router.post("/cases/{case_id}/issue-query", response_model=DisciplinaryCaseRead)
def issue_query(
    case_id: UUID,
    data: IssueQueryRequest,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Issue a formal query to the employee."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    case = service.issue_query(case_id, data)
    db.commit()
    return DisciplinaryCaseRead.model_validate(case)


@router.post("/cases/{case_id}/respond", response_model=CaseResponseRead)
def submit_response(
    case_id: UUID,
    data: CaseResponseCreate,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Submit employee response to a query."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    response = service.record_response(case_id, data)
    db.commit()
    return CaseResponseRead.model_validate(response)


@router.post("/cases/{case_id}/schedule-hearing", response_model=DisciplinaryCaseRead)
def schedule_hearing(
    case_id: UUID,
    data: ScheduleHearingRequest,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Schedule a disciplinary hearing."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    case = service.schedule_hearing(case_id, data)
    db.commit()
    return DisciplinaryCaseRead.model_validate(case)


@router.post("/cases/{case_id}/record-hearing", response_model=DisciplinaryCaseRead)
def record_hearing_notes(
    case_id: UUID,
    data: RecordHearingNotesRequest,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Record hearing notes and mark hearing as completed."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    case = service.record_hearing_notes(case_id, data.hearing_notes)
    db.commit()
    return DisciplinaryCaseRead.model_validate(case)


@router.post("/cases/{case_id}/decision", response_model=DisciplinaryCaseRead)
def record_decision(
    case_id: UUID,
    data: RecordDecisionRequest,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Record the decision after hearing."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    case = service.record_decision(case_id, data)
    db.commit()
    return DisciplinaryCaseRead.model_validate(case)


@router.post("/cases/{case_id}/appeal", response_model=DisciplinaryCaseRead)
def file_appeal(
    case_id: UUID,
    data: FileAppealRequest,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """File an appeal against the decision."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    case = service.file_appeal(case_id, data)
    db.commit()
    return DisciplinaryCaseRead.model_validate(case)


@router.post("/cases/{case_id}/appeal-decision", response_model=DisciplinaryCaseRead)
def decide_appeal(
    case_id: UUID,
    data: DecideAppealRequest,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Record the decision on an appeal."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    case = service.decide_appeal(case_id, data)
    db.commit()
    return DisciplinaryCaseRead.model_validate(case)


@router.post("/cases/{case_id}/close", response_model=DisciplinaryCaseRead)
def close_case(
    case_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Close a case after decision or appeal."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    case = service.close_case(case_id)
    db.commit()
    return DisciplinaryCaseRead.model_validate(case)


@router.post("/cases/{case_id}/withdraw", response_model=DisciplinaryCaseRead)
def withdraw_case(
    case_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Withdraw a case."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    case = service.withdraw_case(case_id)
    db.commit()
    return DisciplinaryCaseRead.model_validate(case)


# =============================================================================
# Witnesses
# =============================================================================


@router.post(
    "/cases/{case_id}/witnesses",
    response_model=CaseWitnessRead,
    status_code=status.HTTP_201_CREATED,
)
def add_witness(
    case_id: UUID,
    data: CaseWitnessCreate,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Add a witness to a case."""
    service = DisciplineService(db)
    case = service.get_case_or_404(case_id)

    if case.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Case not found")

    witness = service.add_witness(case_id, data)
    db.commit()
    return CaseWitnessRead.model_validate(witness)


# =============================================================================
# Employee Actions Query
# =============================================================================


@router.get(
    "/employees/{employee_id}/active-actions", response_model=list[CaseActionRead]
)
def get_employee_active_actions(
    employee_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get active disciplinary actions for an employee."""
    service = DisciplineService(db)
    actions = service.get_active_actions_for_employee(org_id, employee_id)
    normalized = []
    for action in actions:
        issued_by_name = getattr(action, "issued_by_name", None)
        if issued_by_name is not None and not isinstance(issued_by_name, str):
            try:
                action.issued_by_name = None  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Ignored exception")
        normalized.append(CaseActionRead.model_validate(action))
    return normalized


@router.get("/employees/{employee_id}/has-investigation")
def check_active_investigation(
    employee_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Check if employee has an active disciplinary investigation."""
    service = DisciplineService(db)
    has_investigation = service.has_active_investigation(org_id, employee_id)
    return {"has_active_investigation": has_investigation}


# =============================================================================
# Document Upload/Download
# =============================================================================


@router.post("/cases/{case_id}/documents", status_code=status.HTTP_201_CREATED)
def upload_document(
    case_id: UUID,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    title: str | None = Form(None),
    description: str | None = Form(None),
    org_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Upload a document to a disciplinary case."""
    from app.models.people.discipline import DocumentType as DocType
    from app.services.people.discipline.attachment_service import (
        DisciplineAttachmentService,
    )

    person_id = UUID(auth["person_id"])

    # Parse document type
    try:
        doc_type = DocType(document_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document type: {document_type}",
        )

    service = DisciplineAttachmentService(db)

    try:
        document = service.save_file(
            organization_id=org_id,
            case_id=case_id,
            file_content=file.file,
            file_name=file.filename or "document",
            content_type=file.content_type or "application/octet-stream",
            document_type=doc_type,
            uploaded_by_id=person_id,
            title=title,
            description=description,
        )
        db.commit()

        return {
            "document_id": str(document.document_id),
            "title": document.title,
            "file_name": document.file_name,
            "file_size": document.file_size,
            "document_type": document.document_type.value,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("/cases/{case_id}/documents/{document_id}/download")
def download_document(
    case_id: UUID,
    document_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Download a document from a disciplinary case."""
    from fastapi.responses import FileResponse

    from app.services.people.discipline.attachment_service import (
        DisciplineAttachmentService,
    )

    service = DisciplineAttachmentService(db)
    document = service.get_document(org_id, document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Verify document belongs to the case
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


@router.delete(
    "/cases/{case_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_document(
    case_id: UUID,
    document_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a document from a disciplinary case."""
    from app.services.people.discipline.attachment_service import (
        DisciplineAttachmentService,
    )

    service = DisciplineAttachmentService(db)
    document = service.get_document(org_id, document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Verify document belongs to the case
    if document.case_id != case_id:
        raise HTTPException(status_code=404, detail="Document not found")

    deleted = service.delete(org_id, document_id)
    if deleted:
        db.commit()
    return None


# =============================================================================
# Letter Generation
# =============================================================================


@router.post("/cases/{case_id}/generate-query-letter")
def generate_query_letter(
    case_id: UUID,
    signatory_name: str = Form(...),
    signatory_title: str = Form(...),
    organization_name: str | None = Form(None),
    policy_violated: str | None = Form(None),
    response_instructions: str | None = Form(None),
    org_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Generate a query (show cause) letter PDF for a case."""
    from io import BytesIO

    from fastapi.responses import StreamingResponse

    from app.services.people.discipline.letter_service import (
        CaseNotFoundError,
        DisciplineLetterService,
        InvalidCaseStateError,
    )

    person_id = UUID(auth["person_id"])
    service = DisciplineLetterService(db)

    try:
        pdf_bytes, doc_record = service.generate_query_letter(
            case_id=case_id,
            user_id=person_id,
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            organization_name=organization_name,
            policy_violated=policy_violated,
            response_instructions=response_instructions,
        )
        db.commit()

        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=query_letter_{case_id}.pdf"
            },
        )
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidCaseStateError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/cases/{case_id}/actions/{action_id}/generate-warning-letter")
def generate_warning_letter(
    case_id: UUID,
    action_id: UUID,
    signatory_name: str = Form(...),
    signatory_title: str = Form(...),
    expected_improvement: str = Form(...),
    consequences_if_repeated: str = Form(...),
    organization_name: str | None = Form(None),
    improvement_deadline: str | None = Form(None),
    org_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Generate a warning letter PDF for a specific action."""
    from datetime import datetime
    from io import BytesIO

    from fastapi.responses import StreamingResponse

    from app.services.people.discipline.letter_service import (
        ActionNotFoundError,
        CaseNotFoundError,
        DisciplineLetterService,
        InvalidCaseStateError,
    )

    person_id = UUID(auth["person_id"])
    service = DisciplineLetterService(db)

    # Parse improvement deadline if provided
    deadline = None
    if improvement_deadline:
        try:
            deadline = datetime.strptime(improvement_deadline, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid improvement_deadline format. Use YYYY-MM-DD",
            )

    try:
        pdf_bytes, doc_record = service.generate_warning_letter(
            case_id=case_id,
            action_id=action_id,
            user_id=person_id,
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            expected_improvement=expected_improvement,
            consequences_if_repeated=consequences_if_repeated,
            organization_name=organization_name,
            improvement_deadline=deadline,
        )
        db.commit()

        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=warning_letter_{case_id}.pdf"
            },
        )
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ActionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidCaseStateError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/cases/{case_id}/actions/{action_id}/generate-termination-letter")
def generate_termination_letter(
    case_id: UUID,
    action_id: UUID,
    signatory_name: str = Form(...),
    signatory_title: str = Form(...),
    case_summary: str = Form(...),
    organization_name: str | None = Form(None),
    org_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Generate a termination letter PDF for a disciplinary termination."""
    from io import BytesIO

    from fastapi.responses import StreamingResponse

    from app.services.people.discipline.letter_service import (
        ActionNotFoundError,
        CaseNotFoundError,
        DisciplineLetterService,
        InvalidCaseStateError,
    )

    person_id = UUID(auth["person_id"])
    service = DisciplineLetterService(db)

    try:
        pdf_bytes, doc_record = service.generate_termination_letter(
            case_id=case_id,
            action_id=action_id,
            user_id=person_id,
            signatory_name=signatory_name,
            signatory_title=signatory_title,
            case_summary=case_summary,
            organization_name=organization_name,
        )
        db.commit()

        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=termination_letter_{case_id}.pdf"
            },
        )
    except CaseNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ActionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidCaseStateError as e:
        raise HTTPException(status_code=400, detail=str(e))
