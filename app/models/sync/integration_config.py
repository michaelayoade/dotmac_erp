"""
Integration Configuration Model - Per-organization external system credentials.

Stores configuration for external system integrations like ERPNext.
"""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class IntegrationType(str, enum.Enum):
    """Supported external integrations."""
    ERPNEXT = "ERPNEXT"
    QUICKBOOKS = "QUICKBOOKS"
    XERO = "XERO"
    SAGE = "SAGE"


class IntegrationConfig(Base):
    """
    Per-organization integration configuration.

    Stores credentials and settings for external system integrations.
    Sensitive fields (api_key, api_secret) should be encrypted at rest
    in production environments.
    """

    __tablename__ = "integration_config"
    __table_args__ = (
        Index("idx_integration_config_org", "organization_id"),
        Index("idx_integration_config_type", "integration_type"),
        {"schema": "sync"},
    )

    config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    # Integration type
    integration_type: Mapped[IntegrationType] = mapped_column(
        Enum(IntegrationType, name="integration_type"),
        nullable=False,
    )

    # Connection settings
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="API key - should be encrypted at rest",
    )
    api_secret: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="API secret - should be encrypted at rest",
    )

    # Additional settings (e.g., company name for ERPNext)
    company: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Company/tenant identifier in the external system",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last successful connection verification",
    )

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
