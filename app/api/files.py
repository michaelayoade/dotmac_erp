"""
Authenticated file download endpoints for private uploads.
"""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.config import settings
from app.db import SessionLocal
from app.models.finance.automation.generated_document import GeneratedDocument
from app.services.careers.resume_service import ResumeService
from app.services.finance.common.attachment import attachment_service

router = APIRouter(prefix="/files", tags=["files"])
legacy_router = APIRouter(prefix="/uploads", tags=["files"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _resolve_generated_path(relative_path: str) -> Path:
    base_dir = Path(settings.generated_docs_dir).resolve()
    full_path = (base_dir / relative_path).resolve()
    if base_dir != full_path and base_dir not in full_path.parents:
        raise HTTPException(status_code=400, detail="Invalid document path")
    return full_path


@router.get("/resumes/{org_id}/{filename}")
def download_resume(
    org_id: UUID,
    filename: str,
    organization_id: UUID = Depends(require_organization_id),
):
    """Download a resume file (authenticated)."""
    if org_id != organization_id:
        raise HTTPException(status_code=404, detail="Resume not found")
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    service = ResumeService()
    file_id = Path(filename).stem
    file_path = service.get_resume_path(org_id, file_id)
    if not file_path or file_path.name != filename or not file_path.exists():
        raise HTTPException(status_code=404, detail="Resume not found")

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
    )


@router.get("/resumes/{file_id}")
def download_resume_by_id(
    file_id: str,
    organization_id: UUID = Depends(require_organization_id),
):
    """Download a resume file by ID (authenticated)."""
    service = ResumeService()
    file_path = service.get_resume_path(organization_id, file_id)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Resume not found")

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
    )


@router.get("/generated/{document_id}")
def download_generated_document(
    document_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Download a generated document (authenticated)."""
    doc = db.get(GeneratedDocument, document_id)
    if not doc or doc.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.file_path:
        raise HTTPException(status_code=404, detail="Document file not available")

    file_path = _resolve_generated_path(doc.file_path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document file not found")

    filename = file_path.name
    if doc.document_number:
        filename = f"{doc.document_number}.pdf"

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/pdf",
    )


@router.get("/attachments/{attachment_id}")
def download_attachment(
    attachment_id: str,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Download a finance attachment (authenticated)."""
    attachment = attachment_service.get(db, organization_id, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    try:
        file_path = attachment_service.get_file_path(attachment)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid attachment path")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Attachment file not found")

    return FileResponse(
        path=str(file_path),
        filename=attachment.file_name,
        media_type=attachment.content_type or "application/octet-stream",
    )


@legacy_router.get("/resumes/{org_id}/{filename}")
def legacy_download_resume(
    org_id: UUID,
    filename: str,
    organization_id: UUID = Depends(require_organization_id),
):
    """Legacy resume URL support (authenticated)."""
    return download_resume(org_id, filename, organization_id=organization_id)


@legacy_router.get("/generated_docs/{org_id}/{filename}")
def legacy_download_generated(
    org_id: UUID,
    filename: str,
    organization_id: UUID = Depends(require_organization_id),
):
    """Legacy generated document URL support (authenticated)."""
    if org_id != organization_id:
        raise HTTPException(status_code=404, detail="Document not found")
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = _resolve_generated_path(f"{org_id}/{filename}")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document file not found")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/pdf",
    )
