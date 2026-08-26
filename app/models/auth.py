import enum
import uuid
from datetime import datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AuthProvider(enum.Enum):
    local = "local"
    sso = "sso"


class MFAMethodType(enum.Enum):
    totp = "totp"
    sms = "sms"
    email = "email"


class SessionStatus(enum.Enum):
    active = "active"
    revoked = "revoked"
    expired = "expired"


class UserCredential(Base):
    __tablename__ = "user_credentials"
    __table_args__ = (
        UniqueConstraint(
            "person_id",
            "provider",
            name="uq_user_credentials_person_provider",
        ),
        UniqueConstraint(
            "provider",
            "username",
            name="uq_user_credentials_provider_username",
        ),
        CheckConstraint(
            "(provider != 'local') OR (username IS NOT NULL AND password_hash IS NOT NULL)",
            name="ck_user_credentials_local_requirements",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    provider: Mapped[AuthProvider] = mapped_column(
        Enum(AuthProvider), default=AuthProvider.local, nullable=False
    )
    username: Mapped[str | None] = mapped_column(String(150))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    password_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    person = relationship("Person")


class FederatedIdentity(Base):
    """Canonical ERP binding from an exact external subject to one Person."""

    __tablename__ = "federated_identities"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider_binding",
            "issuer",
            "subject",
            name="uq_federated_identity_org_provider_subject",
        ),
        UniqueConstraint(
            "organization_id",
            "person_id",
            "provider_binding",
            "issuer",
            name="uq_federated_identity_org_person_provider_issuer",
        ),
        UniqueConstraint(
            "person_id",
            "id",
            name="uq_federated_identity_person_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "person_id"],
            ["people.organization_id", "people.id"],
            ondelete="CASCADE",
            name="fk_federated_identity_person_org",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "core_org.organization.organization_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_binding: Mapped[str] = mapped_column(String(80), nullable=False)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    person = relationship("Person", foreign_keys=[person_id])


class MFAMethod(Base):
    __tablename__ = "mfa_methods"
    __table_args__ = (
        Index(
            "ix_mfa_methods_primary_per_person",
            "person_id",
            unique=True,
            postgresql_where=text("is_primary"),
            sqlite_where=text("is_primary"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    method_type: Mapped[MFAMethodType] = mapped_column(
        Enum(MFAMethodType), nullable=False
    )
    label: Mapped[str | None] = mapped_column(String(120))
    secret: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    person = relationship("Person")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_token_hash", "token_hash", unique=True),
        Index("ix_sessions_previous_token_hash", "previous_token_hash"),
        Index("ix_sessions_external_identity_binding", "external_identity_binding_id"),
        ForeignKeyConstraint(
            ["person_id", "external_identity_binding_id"],
            ["federated_identities.person_id", "federated_identities.id"],
            ondelete="RESTRICT",
            name="fk_sessions_external_identity_person",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), default=SessionStatus.active, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    previous_token_hash: Mapped[str | None] = mapped_column(String(255))
    external_identity_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    token_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    person = relationship("Person")


class OIDCLoginState(Base):
    """Opaque, single-use ERP OIDC ceremony held in PostgreSQL."""

    __tablename__ = "oidc_login_states"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "state_hash",
            name="uq_oidc_login_state_org_hash",
        ),
        Index("ix_oidc_login_state_expiry", "organization_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "core_org.organization.organization_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(255), nullable=False)
    nonce: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    return_to: Mapped[str] = mapped_column(String(512), nullable=False)
    issued_at: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_binding: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    label: Mapped[str | None] = mapped_column(String(120))
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Least-privilege scopes this key grants, e.g. ["crm:ncc:read", "crm:write"].
    # NULL / empty = unscoped (full access) so pre-existing keys keep working;
    # a non-empty list restricts the key to exactly those scopes.
    scopes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    person = relationship("Person")

    def has_scope(self, scope: str) -> bool:
        """True if this key may use ``scope``. An unscoped key (NULL/empty
        scopes) grants everything — that's the grandfathered default."""
        if not self.scopes:
            return True
        return scope in self.scopes
