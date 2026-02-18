"""
Avatar upload and deletion service.

Delegates to the unified FileUploadService which stores files in S3.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.services.file_upload import FileUploadError, get_avatar_upload

logger = logging.getLogger(__name__)


def get_allowed_types() -> set[str]:
    return set(settings.avatar_allowed_types.split(","))


def _get_extension(content_type: str) -> str:
    """Map content type to file extension."""
    mapping: dict[str, str] = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }
    return mapping.get(content_type, ".jpg")


def validate_avatar(file: UploadFile) -> None:
    allowed_types = get_allowed_types()
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}",
        )


async def save_avatar(file: UploadFile, person_id: str) -> str:
    """Upload an avatar and return its download URL."""
    validate_avatar(file)

    content = await file.read()
    svc = get_avatar_upload()
    try:
        result = svc.save(
            file_data=content,
            content_type=file.content_type,
            prefix=person_id,
            original_filename=file.filename,
        )
    except FileUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Return the proxied download URL (goes through auth)
    return f"/files/avatars/{result.filename}"


def delete_avatar(avatar_url: str | None) -> None:
    """Delete an avatar file from S3."""
    if not avatar_url:
        return

    svc = get_avatar_upload()

    # Handle both old /static/avatars/ and new /files/avatars/ URL prefixes
    for prefix in ("/files/avatars", settings.avatar_url_prefix):
        if avatar_url.startswith(prefix):
            filename = avatar_url.replace(prefix + "/", "", 1)
            if filename:
                svc.delete(filename)
                return
