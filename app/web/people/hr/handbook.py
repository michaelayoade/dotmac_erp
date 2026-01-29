"""HR Handbook Admin Web Routes.

Provides HR admin interface for managing:
- HR Policy documents
- Employee handbooks
- Acknowledgment tracking
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.hr.handbook_service import HRDocumentService
from app.services.people.hr.web.handbook_web import handbook_web_service
from app.web.deps import get_db, require_hr_access, WebAuthContext


router = APIRouter(prefix="/handbook", tags=["handbook"])


# ═══════════════════════════════════════════════════════════════════════════════
# Document List & CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/", response_class=HTMLResponse)
def documents_list(
    request: Request,
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List all HR documents."""
    return handbook_web_service.documents_list_response(
        request=request,
        auth=auth,
        db=db,
        category=category,
        status=status,
        search=search,
    )


@router.get("/new", response_class=HTMLResponse)
def new_document_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Form to create new document."""
    return handbook_web_service.document_form_response(
        request=request, auth=auth, db=db
    )


@router.post("/new")
async def create_document(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create new document with file upload."""
    return await handbook_web_service.save_document_response(
        request=request, auth=auth, db=db
    )


@router.get("/{document_id}", response_class=HTMLResponse)
def document_detail(
    request: Request,
    document_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View document details with acknowledgment stats."""
    return handbook_web_service.document_detail_response(
        request=request, auth=auth, db=db, document_id=document_id
    )


@router.get("/{document_id}/edit", response_class=HTMLResponse)
def edit_document_form(
    request: Request,
    document_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Form to edit document."""
    return handbook_web_service.document_form_response(
        request=request, auth=auth, db=db, document_id=document_id
    )


@router.post("/{document_id}/edit")
async def update_document(
    request: Request,
    document_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update document metadata."""
    return await handbook_web_service.save_document_response(
        request=request, auth=auth, db=db, document_id=document_id
    )


@router.post("/{document_id}/activate")
async def activate_document(
    request: Request,
    document_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Activate a document."""
    return await handbook_web_service.activate_document_response(
        request=request, auth=auth, db=db, document_id=document_id
    )


@router.post("/{document_id}/archive")
async def archive_document(
    request: Request,
    document_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Archive a document."""
    return await handbook_web_service.archive_document_response(
        request=request, auth=auth, db=db, document_id=document_id
    )


@router.get("/{document_id}/download")
def download_document(
    request: Request,
    document_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Download document file."""
    from fastapi import HTTPException

    service = HRDocumentService(db)
    document = service.get_document(auth.organization_id, document_id)
    file_path = service.get_document_path(document)

    # SECURITY: Validate file_path is within the upload directory
    # Resolve both paths to handle symlinks and relative components
    try:
        resolved_path = file_path.resolve(strict=True)
        resolved_upload_dir = service.UPLOAD_DIR.resolve()

        # Ensure the file is within the upload directory
        if not str(resolved_path).startswith(str(resolved_upload_dir)):
            raise HTTPException(status_code=403, detail="Access denied")

        # Verify file path contains expected org_id
        expected_org_path = resolved_upload_dir / str(auth.organization_id)
        if not str(resolved_path).startswith(str(expected_org_path)):
            raise HTTPException(status_code=403, detail="Access denied")

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=resolved_path,
        filename=document.file_name,
        media_type=document.content_type,
    )
