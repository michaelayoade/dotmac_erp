"""License Pydantic models and enums.

Defines the structure of a DotMac ERP license file: the JSON payload carried
inside the signed envelope and the runtime status enum.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LicenseStatus(str, enum.Enum):
    """Runtime license validation status."""

    VALID = "VALID"
    EXPIRING_SOON = "EXPIRING_SOON"  # 0-30 days before expiry
    GRACE_PERIOD = "GRACE_PERIOD"  # Expired, within grace window
    EXPIRED = "EXPIRED"  # Past grace period
    INVALID = "INVALID"  # Signature mismatch / tampered
    MISSING = "MISSING"  # No license file found
    DEV_MODE = "DEV_MODE"  # License checks bypassed


class LicensePayload(BaseModel):
    """The JSON payload embedded inside a signed license file."""

    model_config = ConfigDict(from_attributes=True)

    version: int = Field(default=1, description="License format version")
    license_id: str = Field(..., description="Unique license identifier")
    customer_name: str = Field(..., description="Licensed customer name")
    customer_id: str = Field(..., description="Internal customer identifier")
    issued_at: datetime = Field(..., description="When the license was issued")
    expires_at: datetime = Field(..., description="When the license expires")
    grace_period_days: int = Field(
        default=30, description="Days after expiry before hard shutdown"
    )
    modules: list[str] = Field(
        default_factory=list, description="Licensed module names"
    )
    max_organizations: int = Field(
        default=1, description="Maximum number of organizations"
    )
    max_users: int = Field(default=50, description="Maximum number of active users")
    hardware_fingerprint: str | None = Field(
        default=None, description="Expected machine fingerprint (sha256:...)"
    )
    hardware_fingerprint_required: bool = Field(
        default=False,
        description="Whether hardware fingerprint must match",
    )
    features: dict[str, bool] = Field(
        default_factory=dict,
        description="Feature flags (sso_enabled, api_access, etc.)",
    )


class LicenseFile(BaseModel):
    """Parsed license file: payload + raw signature bytes."""

    model_config = ConfigDict(from_attributes=True)

    payload: LicensePayload
    payload_bytes: bytes = Field(
        ..., description="Raw base64-decoded payload for signature verification"
    )
    signature: bytes = Field(..., description="Ed25519 signature bytes")
