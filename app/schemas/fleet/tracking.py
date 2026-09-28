"""Schemas for Fleet tracker mapping endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TrackerMappingBase(BaseModel):
    """Fields shared by tracker create and read contracts."""

    provider: str = Field(default="TRACCAR", min_length=1, max_length=50)
    unique_id: str = Field(min_length=1, max_length=100)
    external_device_id: str | None = Field(default=None, max_length=100)
    tracker_manufacturer: str | None = Field(default=None, max_length=100)
    tracker_model: str | None = Field(default=None, max_length=100)
    protocol: str | None = Field(default=None, max_length=50)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        """Use one stable provider identity for indexes and lookups."""
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("provider must not be blank")
        return normalized

    @field_validator("unique_id")
    @classmethod
    def normalize_unique_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("unique_id must not be blank")
        return normalized

    @field_validator(
        "external_device_id",
        "tracker_manufacturer",
        "tracker_model",
        "protocol",
    )
    @classmethod
    def strip_optional_values(cls, value: str | None) -> str | None:
        """Trim identifiers and collapse optional blank strings to null."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class TrackerMappingCreate(TrackerMappingBase):
    """Create an active tracker assignment for a vehicle."""


class TrackerMappingUpdate(BaseModel):
    """Patch mutable metadata on the active tracker assignment."""

    provider: str | None = Field(default=None, min_length=1, max_length=50)
    unique_id: str | None = Field(default=None, min_length=1, max_length=100)
    external_device_id: str | None = Field(default=None, max_length=100)
    tracker_manufacturer: str | None = Field(default=None, max_length=100)
    tracker_model: str | None = Field(default=None, max_length=100)
    protocol: str | None = Field(default=None, max_length=50)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("provider must not be blank")
        return normalized

    @field_validator("unique_id")
    @classmethod
    def normalize_unique_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("unique_id must not be blank")
        return normalized

    @field_validator(
        "external_device_id",
        "tracker_manufacturer",
        "tracker_model",
        "protocol",
    )
    @classmethod
    def strip_optional_values(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_change(self) -> "TrackerMappingUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one tracker field must be supplied")
        if "provider" in self.model_fields_set and self.provider is None:
            raise ValueError("provider cannot be null")
        if "unique_id" in self.model_fields_set and self.unique_id is None:
            raise ValueError("unique_id cannot be null")
        return self


class TrackerMappingRead(TrackerMappingBase):
    """Safe ERP response for one durable tracker mapping."""

    model_config = ConfigDict(from_attributes=True)

    tracker_id: UUID
    organization_id: UUID
    vehicle_id: UUID
    is_active: bool
    assigned_at: datetime
    unassigned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    # Step 1 deliberately has no Traccar telemetry connection.
    connection_status: str = "NOT_CONNECTED"
    last_update: datetime | None = None
