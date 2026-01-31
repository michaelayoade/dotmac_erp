"""
Email Profile Model - Multi-profile SMTP Configuration.

Supports different email profiles per organization and module.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization
    from app.services.email import SMTPConfig


class EmailModule(str, enum.Enum):
    """Module identifier for email routing.

    Aligned with main application modules:
    - PEOPLE: HR, Payroll, Leave, Recruitment, Training, Onboarding
    - FINANCE: GL, AR, AP, Banking, Tax, FA, Expense
    - OPERATIONS: Support tickets, Projects, Scheduling
    - ADMIN: System settings, User management, Audit, Password resets
    """

    PEOPLE = "PEOPLE"  # HR, Payroll, Recruitment, Leave, Training
    FINANCE = "FINANCE"  # GL, AR, AP, Banking, Tax, Expense
    OPERATIONS = "OPERATIONS"  # Support, Projects, Scheduling
    ADMIN = "ADMIN"  # System administration, User management, Password resets


class EmailProfile(Base):
    """
    Email Profile - SMTP configuration for sending emails.

    Profiles can be:
    - System default (organization_id=NULL, is_default=True)
    - Organization default (organization_id set, is_default=True)
    - Module-specific (linked via ModuleEmailRouting)

    Resolution order: Module routing → Org default → System default → Environment vars
    """

    __tablename__ = "email_profile"
    __table_args__ = (
        Index("idx_email_profile_org", "organization_id"),
        Index("idx_email_profile_default", "organization_id", "is_default"),
        {"schema": "public"},  # Not module-specific
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Organization scope (NULL = system default)
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
        nullable=True,
        comment="NULL = system-wide profile, UUID = org-specific profile",
    )

    # Profile identification
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name, e.g., 'Payroll Emails', 'Default'",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # SMTP Settings
    smtp_host: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    smtp_port: Mapped[int] = mapped_column(
        Integer,
        default=587,
    )
    smtp_username: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    smtp_password: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Encrypted password (use vault in production)",
    )
    use_tls: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    use_ssl: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    # Sender settings
    from_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    from_name: Mapped[str] = mapped_column(
        String(255),
        default="Dotmac ERP",
    )
    reply_to: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    # Status
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Default profile for this organization (or system if org_id=NULL)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    # Connection pool settings
    pool_size: Mapped[int] = mapped_column(
        Integer,
        default=5,
        comment="Max concurrent connections for this profile",
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=30,
        comment="Connection timeout in seconds",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )

    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    module_routings: Mapped[list["ModuleEmailRouting"]] = relationship(
        "ModuleEmailRouting",
        back_populates="email_profile",
        cascade="all, delete-orphan",
    )

    def to_config_dict(self) -> "SMTPConfig":
        """
        Convert profile to config dict format used by email service.

        Returns a dict with keys: host, port, username, password, use_tls,
        use_ssl, from_email, from_name, reply_to.

        Note: username, password, and reply_to may be None.
        """
        return {
            "host": self.smtp_host,
            "port": self.smtp_port,
            "username": self.smtp_username,  # May be None
            "password": self.smtp_password,  # May be None
            "use_tls": self.use_tls,
            "use_ssl": self.use_ssl,
            "from_email": self.from_email,
            "from_name": self.from_name or "Dotmac ERP",  # Ensure non-None
            "reply_to": self.reply_to,  # May be None
        }

    def __repr__(self) -> str:
        return f"<EmailProfile {self.name} ({self.from_email})>"


class ModuleEmailRouting(Base):
    """
    Module Email Routing - Maps modules to email profiles.

    Allows different modules (payroll, HR, etc.) to use different
    email profiles within the same organization.
    """

    __tablename__ = "module_email_routing"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "module",
            name="uq_module_email_routing_org_module",
        ),
        Index("idx_module_email_routing_org", "organization_id"),
        {"schema": "public"},
    )

    routing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Organization scope
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Module
    module: Mapped[EmailModule] = mapped_column(
        Enum(EmailModule, name="email_module", create_constraint=True),
        nullable=False,
    )

    # Profile link
    email_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.email_profile.profile_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    email_profile: Mapped["EmailProfile"] = relationship(
        "EmailProfile",
        back_populates="module_routings",
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )

    def __repr__(self) -> str:
        return f"<ModuleEmailRouting {self.module.value} → {self.email_profile_id}>"
