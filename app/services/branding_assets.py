import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.services.upload_utils import write_upload_to_path


def _allowed_types() -> set[str]:
    return set(settings.branding_allowed_types.split(","))


def _get_extension(content_type: str) -> str:
    extensions = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
    }
    return extensions.get(content_type, ".png")


def _validate_asset(file: UploadFile) -> None:
    allowed_types = _allowed_types()
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}",
        )


async def save_branding_asset(file: UploadFile, org_id: str, asset_type: str) -> str:
    _validate_asset(file)

    upload_dir = Path(settings.branding_upload_dir) / org_id
    ext = _get_extension(file.content_type or "image/png")
    filename = f"{asset_type}_{uuid.uuid4().hex[:10]}{ext}"
    file_path = upload_dir / filename
    max_mb = settings.branding_max_size_bytes // 1024 // 1024
    await write_upload_to_path(
        file,
        file_path,
        settings.branding_max_size_bytes,
        error_detail=f"File too large. Maximum size: {max_mb}MB",
    )

    return f"{settings.branding_url_prefix}/{org_id}/{filename}"


def delete_branding_asset(asset_url: str | None) -> None:
    if not asset_url:
        return

    if asset_url.startswith(settings.branding_url_prefix):
        filename = asset_url.replace(settings.branding_url_prefix + "/", "")
        file_path = Path(settings.branding_upload_dir) / filename
        if file_path.exists():
            os.remove(file_path)
