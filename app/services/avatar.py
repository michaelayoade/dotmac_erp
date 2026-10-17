"""
Avatar upload and deletion service.

Avatars are platform-plane files owned by ``dotmac-files``: uploads go
through ``prepare_upload`` (which writes the object once under the
canonical platform prefix) and are staged into ``platform_stored_files``
in the caller's transaction, so the ``person.avatar_url`` update and the
file record commit or fail together. The URL carries the file id
(``/files/avatars/<uuid>``); pre-cutover filename URLs keep working
through the legacy object path until the backfill
(``scripts/backfill_avatar_files.py``) has rewritten them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from dotmac_files import (
    FileError,
    FilePolicy,
    prepare_upload,
    request_deletion,
    stage_file,
)
from dotmac_kernel.cache import PlatformScope
from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.services.storage import get_dotmac_files_provider

logger = logging.getLogger(__name__)

_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})


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


def avatar_file_id(avatar_url: str | None) -> UUID | None:
    """The staged file id behind a post-cutover avatar URL, else None."""
    if not avatar_url:
        return None
    tail = avatar_url.rsplit("/", 1)[-1]
    try:
        return UUID(tail)
    except ValueError:
        return None


async def save_avatar(file: UploadFile, person_id: str, db: Session) -> str:
    """Upload an avatar, stage its file record, and return its URL."""
    del person_id  # the file id, not the person, names the object now
    validate_avatar(file)

    content = await file.read()
    if len(content) > settings.avatar_max_size_bytes:
        raise HTTPException(status_code=400, detail="Avatar file is too large")
    policy = FilePolicy(
        max_bytes=settings.avatar_max_size_bytes,
        allowed_extensions=_EXTENSIONS,
        allowed_media_types=frozenset(get_allowed_types()),
    )
    try:
        prepared = prepare_upload(
            get_dotmac_files_provider(),
            scope=PlatformScope(),
            policy=policy,
            original_filename=file.filename
            or f"avatar{_get_extension(file.content_type or '')}",
            declared_media_type=file.content_type or "",
            chunks=iter([content]),
        )
        stage_file(db, prepared=prepared)
    except FileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Return the proxied download URL (goes through auth)
    return f"/files/avatars/{prepared.id}"


def delete_avatar(avatar_url: str | None, db: Session | None = None) -> None:
    """Retire an avatar: staged files by id, legacy objects by filename."""
    if not avatar_url:
        return

    file_id = avatar_file_id(avatar_url)
    if file_id is not None:
        if db is not None:
            try:
                request_deletion(
                    db,
                    scope=PlatformScope(),
                    file_id=file_id,
                    now=datetime.now(timezone.utc),
                )
            except FileError:
                logger.warning(
                    "avatar file %s could not be marked for deletion", file_id
                )
        return

    # Pre-cutover filename URLs: delete the legacy object directly. This
    # branch retires once the backfill has rewritten every avatar_url.
    from app.services.file_upload import get_avatar_upload

    svc = get_avatar_upload()
    for prefix in ("/files/avatars", settings.avatar_url_prefix):
        if avatar_url.startswith(prefix):
            filename = avatar_url.replace(prefix + "/", "", 1)
            if filename:
                svc.delete(filename)
                return
