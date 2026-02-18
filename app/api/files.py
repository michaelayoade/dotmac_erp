"""
Authenticated file download endpoints.

All files are stored in S3 (MinIO) and streamed through the app
so that every download goes through authentication middleware.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.models.finance.automation.generated_document import GeneratedDocument
from app.services.careers.resume_service import ResumeService
from app.services.finance.common.attachment import attachment_service
from app.services.storage import get_storage

# Characters unsafe in Content-Disposition filenames
_UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f\x7f"\\]')

router = APIRouter(prefix="/files", tags=["files"])
legacy_router = APIRouter(prefix="/uploads", tags=["files"])


def get_db():  # noqa: ANN201
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _stream_s3_file(
    s3_key: str,
    *,
    filename: str | None = None,
    media_type: str | None = None,
) -> StreamingResponse:
    """Stream a file from S3 as a FastAPI response."""
    storage = get_storage()
    if not storage.exists(s3_key):
        raise HTTPException(status_code=404, detail="File not found")

    chunks, content_type, content_length = storage.stream(s3_key)
    effective_type = media_type or content_type or "application/octet-stream"

    headers: dict[str, str] = {}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    if filename:
        # RFC 6266 + RFC 5987 Content-Disposition — sanitize to prevent header injection
        safe_name = _UNSAFE_FILENAME_RE.sub("_", filename)
        # ASCII fallback + UTF-8 encoded version for non-ASCII filenames
        headers["Content-Disposition"] = (
            f"attachment; filename=\"{safe_name}\"; filename*=UTF-8''{quote(safe_name)}"
        )

    return StreamingResponse(
        chunks,
        media_type=effective_type,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Resume downloads
# ---------------------------------------------------------------------------


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
    # Try S3 key first
    s3_key = f"resumes/{org_id}/{filename}"
    storage = get_storage()
    if storage.exists(s3_key):
        return _stream_s3_file(s3_key, filename=filename)

    # Fallback to legacy path lookup
    file_path = service.get_resume_path(org_id, file_id)
    if not file_path or file_path.name != filename or not file_path.exists():
        raise HTTPException(status_code=404, detail="Resume not found")

    return _stream_s3_file(
        f"resumes/{org_id}/{file_path.name}", filename=file_path.name
    )


@router.get("/resumes/{file_id}")
def download_resume_by_id(
    file_id: str,
    organization_id: UUID = Depends(require_organization_id),
):
    """Download a resume file by ID (authenticated)."""
    service = ResumeService()
    file_path = service.get_resume_path(organization_id, file_id)
    if not file_path:
        raise HTTPException(status_code=404, detail="Resume not found")

    s3_key = f"resumes/{organization_id}/{file_path.name}"
    return _stream_s3_file(s3_key, filename=file_path.name)


# ---------------------------------------------------------------------------
# Generated document downloads
# ---------------------------------------------------------------------------


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

    # file_path may be an S3 key already or a legacy relative path
    s3_key = doc.file_path
    if not s3_key.startswith("generated_docs/"):
        s3_key = f"generated_docs/{doc.file_path}"

    filename = Path(doc.file_path).name
    if doc.document_number:
        filename = f"{doc.document_number}.pdf"

    return _stream_s3_file(s3_key, filename=filename, media_type="application/pdf")


# ---------------------------------------------------------------------------
# Finance attachment downloads
# ---------------------------------------------------------------------------


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

    # Build S3 key from the stored file_path
    file_path_str = attachment.file_path or ""
    if file_path_str.startswith("attachments/"):
        s3_key = file_path_str
    else:
        s3_key = f"attachments/{file_path_str}"

    return _stream_s3_file(
        s3_key,
        filename=attachment.file_name,
        media_type=attachment.content_type or "application/octet-stream",
    )


# ---------------------------------------------------------------------------
# Avatar downloads (public to authenticated users)
# ---------------------------------------------------------------------------


@router.get("/avatars/{filename}")
def download_avatar(
    filename: str,
    organization_id: UUID = Depends(require_organization_id),
):
    """Download an avatar image (authenticated)."""
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    s3_key = f"avatars/{filename}"
    return _stream_s3_file(s3_key, filename=filename)


# ---------------------------------------------------------------------------
# Branding asset downloads (public to authenticated users)
# ---------------------------------------------------------------------------


@router.get("/branding/{org_id}/{filename}")
def download_branding(
    org_id: UUID,
    filename: str,
    organization_id: UUID = Depends(require_organization_id),
):
    """Download a branding asset (authenticated, org-scoped)."""
    if org_id != organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    s3_key = f"branding/{org_id}/{filename}"
    return _stream_s3_file(s3_key, filename=filename)


# ---------------------------------------------------------------------------
# Legacy URL compatibility
# ---------------------------------------------------------------------------


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

    s3_key = f"generated_docs/{org_id}/{filename}"
    return _stream_s3_file(s3_key, filename=filename, media_type="application/pdf")


@legacy_router.get("/branding/{org_id}/{filename}")
def legacy_download_branding(
    org_id: UUID,
    filename: str,
    organization_id: UUID = Depends(require_organization_id),
):
    """Legacy branding URL support (authenticated, org-scoped)."""
    return download_branding(org_id, filename, organization_id=organization_id)
