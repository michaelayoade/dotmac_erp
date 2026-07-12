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
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship
from sqlalchemy.orm.attributes import set_committed_value

from app.db import Base


class SettingValueType(enum.Enum):
    string = "string"
    integer = "integer"
    boolean = "boolean"
    json = "json"


class SettingDomain(enum.Enum):
    auth = "auth"
    audit = "audit"
    scheduler = "scheduler"
    automation = "automation"
    email = "email"
    features = "features"
    reporting = "reporting"
    payments = "payments"
    operations = "operations"
    support = "support"
    inventory = "inventory"
    projects = "projects"
    fleet = "fleet"
    procurement = "procurement"
    settings = "settings"
    payroll = "payroll"
    banking = "banking"
    coach = "coach"
    notifications = "notifications"
    expense = "expense"
    gl = "gl"


class SettingChangeAction(enum.Enum):
    """Types of setting change actions for history tracking."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class SettingScope(enum.Enum):
    """Scope of a setting - global or org-specific."""

    GLOBAL = "GLOBAL"
    ORG_SPECIFIC = "ORG_SPECIFIC"


class DomainSetting(Base):
    __tablename__ = "domain_settings"
    __table_args__ = (
        UniqueConstraint(
            "domain", "key", "organization_id", name="uq_domain_settings_domain_key_org"
        ),
        CheckConstraint(
            "(value_type = 'json' AND value_text IS NULL) "
            "OR (value_type IN ('string', 'integer') AND value_json IS NULL) "
            "OR (value_type = 'boolean')",
            name="ck_domain_settings_value_storage",
        ),
        Index("ix_domain_settings_org", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    domain: Mapped[SettingDomain] = mapped_column(Enum(SettingDomain), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
        nullable=True,
        comment="NULL = global setting, UUID = org-specific setting",
    )
    scope: Mapped[SettingScope] = mapped_column(
        Enum(SettingScope), default=SettingScope.GLOBAL, nullable=False
    )
    value_type: Mapped[SettingValueType] = mapped_column(
        Enum(SettingValueType), default=SettingValueType.string
    )
    value_text: Mapped[str | None] = mapped_column(Text)
    value_json: Mapped[dict | None] = mapped_column(JSON)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationship to history
    history: Mapped[list["DomainSettingHistory"]] = relationship(
        "DomainSettingHistory",
        back_populates="setting",
        order_by="DomainSettingHistory.changed_at.desc()",
    )


class DomainSettingHistory(Base):
    """
    Tracks all changes to domain settings for audit and rollback purposes.

    Each record captures the full state before and after a change, enabling:
    - Complete audit trail of who changed what and when
    - Point-in-time reconstruction of setting values
    - Rollback capability via the restore endpoint
    """

    __tablename__ = "domain_setting_history"
    __table_args__ = (
        Index("ix_domain_setting_history_domain_key", "domain", "key"),
        Index("ix_domain_setting_history_changed_at", "changed_at"),
        Index("ix_domain_setting_history_changed_by", "changed_by_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    setting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domain_settings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Setting identification (denormalized for queries after setting deletion)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Organization ID (NULL = global setting)",
    )

    # Change action
    action: Mapped[SettingChangeAction] = mapped_column(
        Enum(SettingChangeAction), nullable=False
    )

    # Old values (NULL for CREATE actions)
    old_value_type: Mapped[str | None] = mapped_column(String(20))
    old_value_text: Mapped[str | None] = mapped_column(Text)
    old_value_json: Mapped[dict | None] = mapped_column(JSON)
    old_is_secret: Mapped[bool | None] = mapped_column(Boolean)
    old_is_active: Mapped[bool | None] = mapped_column(Boolean)

    # New values (NULL for DELETE actions, reflects soft-delete for DELETE)
    new_value_type: Mapped[str | None] = mapped_column(String(20))
    new_value_text: Mapped[str | None] = mapped_column(Text)
    new_value_json: Mapped[dict | None] = mapped_column(JSON)
    new_is_secret: Mapped[bool | None] = mapped_column(Boolean)
    new_is_active: Mapped[bool | None] = mapped_column(Boolean)

    # Audit metadata
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="SET NULL"),
        nullable=True,
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    change_reason: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(45))  # IPv6 max
    user_agent: Mapped[str | None] = mapped_column(String(500))

    # Relationships
    setting: Mapped["DomainSetting | None"] = relationship(
        "DomainSetting", back_populates="history"
    )


# ─────────────────────────────────────────────────────────────────────────────
# At-rest encryption for secret settings
#
# Registered here, on the model, so the listeners are active whenever the model
# is — every entrypoint (web, Celery, scripts, tests) imports it. Encrypting at
# the ORM boundary means the ~170 places that read ``value_text`` need no change
# and none of them can be missed; a secret is ciphertext in the database and
# plaintext in memory, and callers never know the difference.
#
# The crypto module is imported lazily inside the listeners: it is a service and
# importing it at module scope would invert the model→service layering.
# ─────────────────────────────────────────────────────────────────────────────


def _encrypt_secret_before_write(_mapper, _connection, target: "DomainSetting") -> None:
    from app.services.settings_crypto import encrypt_value, should_encrypt

    if not should_encrypt(target) or not target.value_text:
        return

    session = object_session(target)
    target.value_text = encrypt_value(target.value_text, session)


def _decrypt_secret_on_load(target: "DomainSetting", context) -> None:
    from app.services.settings_crypto import decrypt_value, should_encrypt

    if not should_encrypt(target) or not target.value_text:
        return

    session = getattr(context, "session", None)
    plaintext = decrypt_value(target.value_text, session)

    # set_committed_value, not a plain assignment: the latter would mark the
    # instance dirty on every load, so a later unrelated flush would write the
    # decrypted plaintext straight back over the ciphertext.
    set_committed_value(target, "value_text", plaintext)


event.listen(DomainSetting, "before_insert", _encrypt_secret_before_write)
event.listen(DomainSetting, "before_update", _encrypt_secret_before_write)
event.listen(DomainSetting, "load", _decrypt_secret_on_load)
event.listen(DomainSetting, "refresh", lambda t, c, _a: _decrypt_secret_on_load(t, c))
