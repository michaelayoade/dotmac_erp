"""
Authenticated file download endpoints.

All files are stored in S3 (MinIO) and streamed through the app
so that every download goes through authentication middleware.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.models.finance.automation.generated_document import GeneratedDocument
from app.services.finance.common.attachment import attachment_service
from app.services.storage import (
    StorageObjectMissing,
    StorageReadUnavailable,
    get_dotmac_files_provider,
    get_storage,
)

# Characters unsafe in Content-Disposition filenames
_UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f\x7f"\\]')
_INLINE_SAFE_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/csv",
    }
)

router = APIRouter(prefix="/files", tags=["files"])
legacy_router = APIRouter(prefix="/uploads", tags=["files"])


def _stream_s3_file(
    s3_key: str,
    *,
    filename: str | None = None,
    media_type: str | None = None,
    disposition: Literal["attachment", "inline", "auto"] = "attachment",
) -> StreamingResponse:
    """Stream a file from S3 as a FastAPI response."""
    try:
        storage = get_storage()
        if not storage.exists(s3_key):
            raise HTTPException(status_code=404, detail="File not found")

        chunks, content_type, content_length = storage.stream(s3_key)
    except StorageObjectMissing:
        raise HTTPException(status_code=404, detail="File not found")
    except StorageReadUnavailable:
        raise HTTPException(
            status_code=503,
            detail="File storage is temporarily unavailable",
            headers={"Retry-After": "5"},
        ) from None
    effective_type = media_type or content_type or "application/octet-stream"

    headers: dict[str, str] = {}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    if filename:
        effective_disposition = disposition
        if disposition == "auto":
            if effective_type in _INLINE_SAFE_MIME_TYPES or effective_type.startswith(
                "image/"
            ):
                effective_disposition = "inline"
            else:
                effective_disposition = "attachment"
        # RFC 6266 + RFC 5987 Content-Disposition — sanitize to prevent header injection
        safe_name = _UNSAFE_FILENAME_RE.sub("_", filename)
        # ASCII fallback + UTF-8 encoded version for non-ASCII filenames
        headers["Content-Disposition"] = (
            f'{effective_disposition}; filename="{safe_name}"; '
            f"filename*=UTF-8''{quote(safe_name)}"
        )

    return StreamingResponse(
        chunks,
        media_type=effective_type,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Resume downloads
# ---------------------------------------------------------------------------


def _stream_staged_resume(db: Session, organization_id: UUID, file_id: str):
    """Stream a post-cutover staged resume, or None when it is not staged."""
    try:
        staged_id = UUID(str(file_id))
    except (TypeError, ValueError):
        return None

    from dotmac_files import FileError, download_target, open_object

    from app.tenancy import OrganizationTenantContext

    scope = OrganizationTenantContext.for_organization(organization_id).tenant_scope
    try:
        target = download_target(db, scope=scope, file_id=staged_id)
        readable = open_object(get_dotmac_files_provider(), target=target)
    except FileError:
        return None

    def _chunks():
        with readable:
            while chunk := readable.read(64 * 1024):
                yield chunk

    filename = _UNSAFE_FILENAME_RE.sub("_", target.original_filename)
    return StreamingResponse(
        _chunks(),
        media_type=target.detected_media_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(target.size_bytes),
        },
    )


@router.get("/resumes/{org_id}/{filename}")
def download_resume(
    org_id: UUID,
    filename: str,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
):
    """Download a resume file (authenticated)."""
    if org_id != organization_id:
        raise HTTPException(status_code=404, detail="Resume not found")
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    staged = _stream_staged_resume(db, organization_id, Path(filename).stem)
    if staged is not None:
        return staged

    raise HTTPException(status_code=404, detail="Resume not found")


@router.get("/resumes/{file_id}")
def download_resume_by_id(
    file_id: str,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
):
    """Download a resume file by ID (authenticated)."""
    staged = _stream_staged_resume(db, organization_id, file_id)
    if staged is not None:
        return staged

    raise HTTPException(status_code=404, detail="Resume not found")


# ---------------------------------------------------------------------------
# Generated document downloads
# ---------------------------------------------------------------------------


@router.get("/generated/{document_id}")
def download_generated_document(
    document_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
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
    db: Session = Depends(get_db_with_org),
):
    """Download a finance attachment (authenticated)."""
    attachment = attachment_service.get(db, organization_id, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    from app.services.finance.common.attachment import staged_file_id

    file_id = staged_file_id(attachment)
    if file_id is not None:
        # Post-cutover staged attachment: stream through the module's
        # authorized target under the attachment's tenant scope.
        from dotmac_files import FileError, download_target, open_object

        from app.tenancy import OrganizationTenantContext

        scope = OrganizationTenantContext.for_organization(organization_id).tenant_scope
        try:
            target = download_target(db, scope=scope, file_id=file_id)
            readable = open_object(get_dotmac_files_provider(), target=target)
        except FileError:
            raise HTTPException(
                status_code=404, detail="Attachment not found"
            ) from None

        def _chunks():
            with readable:
                while chunk := readable.read(64 * 1024):
                    yield chunk

        filename = _UNSAFE_FILENAME_RE.sub("_", attachment.file_name)
        return StreamingResponse(
            _chunks(),
            media_type=attachment.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length": str(target.size_bytes),
            },
        )

    raise HTTPException(status_code=404, detail="Attachment not found")


# ---------------------------------------------------------------------------
# Dynamic form attachment downloads
# ---------------------------------------------------------------------------


@router.get("/form_attachments/{org_id}/staged/{file_id}")
def download_staged_form_attachment(
    org_id: UUID,
    file_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
):
    """Download a post-cutover staged dynamic form attachment."""
    if org_id != organization_id:
        raise HTTPException(status_code=404, detail="File not found")

    from dotmac_files import FileError, download_target, open_object

    from app.tenancy import OrganizationTenantContext

    scope = OrganizationTenantContext.for_organization(organization_id).tenant_scope
    try:
        target = download_target(db, scope=scope, file_id=file_id)
        readable = open_object(get_dotmac_files_provider(), target=target)
    except FileError:
        raise HTTPException(status_code=404, detail="File not found") from None

    def _chunks():
        with readable:
            while chunk := readable.read(64 * 1024):
                yield chunk

    filename = _UNSAFE_FILENAME_RE.sub("_", target.original_filename)
    return StreamingResponse(
        _chunks(),
        media_type=target.detected_media_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(target.size_bytes),
        },
    )


# ---------------------------------------------------------------------------
# Avatar downloads (public to authenticated users)
# ---------------------------------------------------------------------------


@router.get("/avatars/{filename}")
def download_avatar(
    filename: str,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
):
    """Download an avatar image (authenticated).

    URLs carry the ``dotmac-files`` file id and stream through the
    module's authorized target. (Pre-cutover filename URLs were
    retired with the legacy serving branches on 2026-10-27; the avatar
    backfill had already rewritten every stored URL.)
    """
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    try:
        file_id = UUID(filename)
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found") from None

    from dotmac_files import FileError, download_target, open_object

    from app.tenancy import platform_scope

    try:
        target = download_target(db, scope=platform_scope(), file_id=file_id)
        readable = open_object(get_dotmac_files_provider(), target=target)
    except FileError:
        raise HTTPException(status_code=404, detail="File not found") from None

    def _chunks():
        with readable:
            while chunk := readable.read(64 * 1024):
                yield chunk

    return StreamingResponse(
        _chunks(),
        media_type=target.detected_media_type,
        headers={"Content-Length": str(target.size_bytes)},
    )


# ---------------------------------------------------------------------------
# Branding asset downloads (public to authenticated users)
# ---------------------------------------------------------------------------


@router.get("/branding/{org_id}/{filename}")
def download_branding(
    org_id: UUID,
    filename: str,
):
    """Download a branding asset (public — logos/favicons are not sensitive).

    Branding assets are referenced from public pages (login, careers) so this
    endpoint must be accessible without authentication.  Path-traversal is
    prevented by validating the filename component.
    """
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
):
    """Legacy branding URL support (public, delegates to main handler)."""
    return download_branding(org_id, filename)
