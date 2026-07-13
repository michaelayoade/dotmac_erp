from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.domain_settings import SettingDomain, SettingValueType

# Matches the placeholder already used by the settings audit log
# (``app/services/domain_settings.py``), so a masked value reads the same
# wherever it surfaces.
MASKED_VALUE = "***MASKED***"


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

    @model_validator(mode="after")
    def _mask_secret_value(self) -> DomainSettingRead:
        """Never hand a secret's value back out of the settings API.

        Encrypting secrets at rest is defeated if the read endpoints return the
        plaintext anyway — the ORM decrypts on load, so ``value_text`` here is
        the live key. Callers that need to know whether a secret is *set* can
        read ``is_secret`` plus the presence of the setting; callers that need
        the value itself read it server-side through ``resolve_value``, not over
        the API.
        """
        if self.is_secret and self.value_text is not None:
            self.value_text = MASKED_VALUE
        return self


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

    @model_validator(mode="after")
    def _mask_secret_values(self) -> SettingHistoryRead:
        """Never serialize a secret's value, in either direction.

        History rows store ``value_text`` verbatim, so a secret setting's
        plaintext (Mono/Paystack keys, ``jwt_secret``, …) sits in the audit
        trail. ``is_secret`` is a display hint, not a mask — without this the
        history endpoints hand the live value straight back to any caller,
        defeating the write-only fields in the admin UI.

        Masked here, at the serialization boundary, so every endpoint that
        returns a history entry is covered rather than just the list route.
        The audit trail keeps *that* the value changed, and who changed it —
        which is what it is for — without disclosing the value itself.
        """
        if self.old_is_secret and self.old_value_text is not None:
            self.old_value_text = MASKED_VALUE
        if self.new_is_secret and self.new_value_text is not None:
            self.new_value_text = MASKED_VALUE
        return self


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
