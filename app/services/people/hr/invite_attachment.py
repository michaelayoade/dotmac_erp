"""Default attachment support for employee access invite emails.

New uploads are ``dotmac-files`` tenant records; the setting metadata
then carries the canonical tenant storage key in ``s3_key``. Legacy
metadata keys keep their existing storage branch until
``scripts/backfill_hr_invite_attachment_files.py`` rewrites them.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain_settings import (
    DomainSetting,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.services.storage import get_storage

DEFAULT_INVITE_ATTACHMENT_KEY = "hr_default_employee_invite_attachment"


def staged_file_id(metadata: dict[str, Any] | None) -> uuid.UUID | None:
    """The staged file id for post-cutover metadata, else None."""
    s3_key = str((metadata or {}).get("s3_key") or "")
    if not s3_key.startswith("tenants/"):
        return None
    try:
        return uuid.UUID(s3_key.rsplit("/", 1)[-1])
    except ValueError:
        return None


def stage_default_invite_attachment(
    db: Session,
    organization_id: uuid.UUID,
    *,
    file_data: bytes,
    content_type: str | None,
    original_filename: str | None,
):
    """Upload and stage one invite attachment on the tenant files plane."""
    from dotmac_files import FileError, prepare_upload
    from dotmac_files.providers import StorageError

    from app.models.finance.core_org.organization import Organization
    from app.services.file_object_cleanup import (
        CleanupUnsafeReferenceView,
        stage_tenant_file_if_unreserved,
    )
    from app.services.file_upload import (
        FileStorageError,
        FileUploadError,
        get_hr_invite_attachment_upload,
        policy_from_upload_config,
        resolve_declared_media_type,
    )
    from app.services.storage import get_dotmac_files_provider
    from app.services.tenant_projection import reconcile_organization_tenant
    from app.tenancy import OrganizationTenantContext

    config = get_hr_invite_attachment_upload().config
    policy = policy_from_upload_config(config)
    scope = OrganizationTenantContext.for_organization(organization_id).tenant_scope
    try:
        prepared = prepare_upload(
            get_dotmac_files_provider(),
            scope=scope,
            policy=policy,
            original_filename=original_filename or "welcome-pack",
            declared_media_type=resolve_declared_media_type(
                original_filename, content_type
            ),
            chunks=iter([file_data]),
        )
    except FileError as exc:
        raise FileUploadError(str(exc)) from exc
    except StorageError as exc:
        raise FileStorageError(str(exc)) from exc
    if db.get_bind().dialect.name == "postgresql":
        organization = db.get(Organization, organization_id)
        if organization is not None:
            reconcile_organization_tenant(db, organization)
            db.flush()
    try:
        stage_tenant_file_if_unreserved(db, prepared=prepared)
    except CleanupUnsafeReferenceView as exc:
        raise FileUploadError(str(exc)) from exc
    return prepared


def delete_invite_attachment_object(
    db: Session, organization_id: uuid.UUID, metadata: dict[str, Any] | None
) -> None:
    """Delete an invite attachment object, staged or legacy, best effort."""
    if not metadata:
        return
    staged_id = staged_file_id(metadata)
    if staged_id is None:
        s3_key = str(metadata.get("s3_key") or "")
        if s3_key:
            get_storage().delete(s3_key)
        return
    from datetime import datetime, timezone

    from dotmac_files import FileError, request_deletion

    from app.tenancy import OrganizationTenantContext

    try:
        request_deletion(
            db,
            scope=OrganizationTenantContext.for_organization(
                organization_id
            ).tenant_scope,
            file_id=staged_id,
            now=datetime.now(timezone.utc),
        )
    except FileError:
        pass


def read_invite_attachment_bytes(
    db: Session, organization_id: uuid.UUID, metadata: dict[str, Any]
) -> bytes:
    """Read invite attachment bytes (staged files only)."""
    staged_id = staged_file_id(metadata)
    if staged_id is None:
        raise FileNotFoundError("Invite attachment not found")

    from dotmac_files import download_target, open_object

    from app.services.storage import get_dotmac_files_provider
    from app.tenancy import OrganizationTenantContext

    scope = OrganizationTenantContext.for_organization(organization_id).tenant_scope
    target = download_target(db, scope=scope, file_id=staged_id)
    readable = open_object(get_dotmac_files_provider(), target=target)
    try:
        return readable.read()
    finally:
        readable.close()


def get_default_invite_attachment_metadata(
    db: Session,
    organization_id: uuid.UUID,
) -> dict[str, Any] | None:
    setting = db.scalar(
        select(DomainSetting).where(
            DomainSetting.domain == SettingDomain.settings,
            DomainSetting.key == DEFAULT_INVITE_ATTACHMENT_KEY,
            DomainSetting.organization_id == organization_id,
            DomainSetting.is_active.is_(True),
        )
    )
    metadata = setting.value_json if setting else None
    return metadata if isinstance(metadata, dict) else None


def set_default_invite_attachment_metadata(
    db: Session,
    organization_id: uuid.UUID,
    metadata: dict[str, Any],
) -> None:
    setting = db.scalar(
        select(DomainSetting).where(
            DomainSetting.domain == SettingDomain.settings,
            DomainSetting.key == DEFAULT_INVITE_ATTACHMENT_KEY,
            DomainSetting.organization_id == organization_id,
        )
    )
    if setting:
        setting.value_type = SettingValueType.json
        setting.value_text = None
        setting.value_json = metadata
        setting.is_active = True
    else:
        db.add(
            DomainSetting(
                domain=SettingDomain.settings,
                key=DEFAULT_INVITE_ATTACHMENT_KEY,
                organization_id=organization_id,
                scope=SettingScope.ORG_SPECIFIC,
                value_type=SettingValueType.json,
                value_json=metadata,
                is_active=True,
            )
        )
    db.flush()


def clear_default_invite_attachment_metadata(
    db: Session,
    organization_id: uuid.UUID,
) -> dict[str, Any] | None:
    setting = db.scalar(
        select(DomainSetting).where(
            DomainSetting.domain == SettingDomain.settings,
            DomainSetting.key == DEFAULT_INVITE_ATTACHMENT_KEY,
            DomainSetting.organization_id == organization_id,
            DomainSetting.is_active.is_(True),
        )
    )
    if not setting:
        return None
    previous = setting.value_json if isinstance(setting.value_json, dict) else None
    setting.is_active = False
    db.flush()
    return previous


def load_default_invite_attachment(
    db: Session,
    organization_id: uuid.UUID,
) -> tuple[str, bytes, str] | None:
    metadata = get_default_invite_attachment_metadata(db, organization_id)
    if not metadata:
        return None

    s3_key = str(metadata.get("s3_key") or "")
    filename = str(metadata.get("filename") or "")
    content_type = str(metadata.get("content_type") or "application/octet-stream")
    if not s3_key or not filename:
        return None

    return (
        filename,
        read_invite_attachment_bytes(db, organization_id, metadata),
        content_type,
    )
