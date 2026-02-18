"""
Branding asset upload and deletion service.

Delegates to the unified FileUploadService which stores files in S3.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.services.file_upload import FileUploadError, get_branding_upload

logger = logging.getLogger(__name__)


def _allowed_types() -> set[str]:
    return set(settings.branding_allowed_types.split(","))


def _validate_asset(file: UploadFile) -> None:
    allowed_types = _allowed_types()
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}",
        )


async def save_branding_asset(file: UploadFile, org_id: str, asset_type: str) -> str:
    """Upload a branding asset and return its download URL."""
    _validate_asset(file)

    content = await file.read()
    svc = get_branding_upload()
    try:
        result = svc.save(
            file_data=content,
            content_type=file.content_type,
            subdirs=(org_id,),
            prefix=asset_type,
            original_filename=file.filename,
        )
    except FileUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Return the proxied download URL (goes through auth)
    return f"/files/branding/{org_id}/{result.filename}"


def delete_branding_asset(asset_url: str | None) -> None:
    """Delete a branding asset from S3."""
    if not asset_url:
        return

    svc = get_branding_upload()

    # Handle both old /static/branding/ and new /files/branding/ URL prefixes
    for prefix in ("/files/branding", settings.branding_url_prefix):
        if asset_url.startswith(prefix):
            relative = asset_url.replace(prefix + "/", "", 1)
            if relative:
                svc.delete(relative)
                return
