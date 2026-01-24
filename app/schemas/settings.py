from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.domain_settings import SettingDomain
from app.models.domain_settings import SettingValueType


class DomainSettingBase(BaseModel):
    domain: SettingDomain
    key: str
    value_type: SettingValueType = SettingValueType.string
    value_text: str | None = None
    value_json: dict | list | bool | int | str | None = None
    is_secret: bool = False
    is_active: bool = True


class DomainSettingCreate(DomainSettingBase):
    pass


class DomainSettingUpdate(BaseModel):
    domain: SettingDomain | None = None
    key: str | None = None
    value_type: SettingValueType | None = None
    value_text: str | None = None
    value_json: dict | list | bool | int | str | None = None
    is_secret: bool | None = None
    is_active: bool | None = None


class DomainSettingRead(DomainSettingBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Settings Export/Import Schemas
# =============================================================================


class SettingsExportRequest(BaseModel):
    """Request body for exporting settings."""

    domains: list[SettingDomain] | None = None
    """List of domains to export (None = all domains)."""

    include_secrets: bool = False
    """If True, include actual secret values (use with caution)."""


class SettingsExportResponse(BaseModel):
    """Response containing exported settings."""

    version: str
    """Export format version."""

    exported_at: str
    """ISO timestamp of when the export was created."""

    settings: dict[str, dict[str, dict]]
    """Nested dict: domain -> key -> {value, value_type, is_secret}."""


class SettingsImportRequest(BaseModel):
    """Request body for importing settings."""

    data: dict
    """Exported settings data (from export endpoint)."""

    domains: list[SettingDomain] | None = None
    """List of domains to import (None = all in export)."""

    skip_secrets: bool = True
    """If True (default), skip importing secret values."""

    dry_run: bool = False
    """If True, validate but don't actually import."""


class SettingsImportResultItem(BaseModel):
    """Single item in import results."""

    domain: str | None
    key: str | None
    status: str | None = None
    reason: str | None = None
    error: str | None = None


class SettingsImportResponse(BaseModel):
    """Response containing import results."""

    imported: list[SettingsImportResultItem]
    """Successfully imported settings."""

    skipped: list[SettingsImportResultItem]
    """Settings that were skipped (unknown keys, masked secrets, etc.)."""

    errors: list[SettingsImportResultItem]
    """Settings that failed to import."""


# =============================================================================
# Settings History Schemas
# =============================================================================


class SettingHistoryRead(BaseModel):
    """Response model for a single history entry."""

    id: UUID
    setting_id: UUID | None
    domain: str
    key: str
    action: str

    # Old values
    old_value_type: str | None = None
    old_value_text: str | None = None
    old_value_json: dict | list | bool | int | str | None = None
    old_is_secret: bool | None = None
    old_is_active: bool | None = None

    # New values
    new_value_type: str | None = None
    new_value_text: str | None = None
    new_value_json: dict | list | bool | int | str | None = None
    new_is_secret: bool | None = None
    new_is_active: bool | None = None

    # Audit metadata
    changed_by_id: UUID | None = None
    changed_at: datetime
    change_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SettingHistoryListResponse(BaseModel):
    """Response model for listing history entries."""

    items: list[SettingHistoryRead]
    total: int
    limit: int
    offset: int


class RestoreSettingRequest(BaseModel):
    """Request to restore a setting from a history entry."""

    history_id: UUID
    """The history entry ID to restore from."""

    change_reason: str | None = None
    """Optional reason for the restore."""
