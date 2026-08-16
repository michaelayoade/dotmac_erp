import enum
import uuid
from typing import TYPE_CHECKING
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
from sqlalchemy.engine import Connection
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_core import CoreSchema
from sqlalchemy.orm import Mapped, Mapper, mapped_column, object_session, relationship
from sqlalchemy.orm.attributes import set_committed_value

from app.db import Base


class SettingValueType(enum.Enum):
    string = "string"
    integer = "integer"
    boolean = "boolean"
    json = "json"


class SettingDomain(str):
    """A setting domain — an OPEN, registered string, not a closed enum.

    ERP had 21 enum members and a PostgreSQL ``settingdomain`` type, so adding a
    domain meant an ``ALTER TYPE ... ADD VALUE`` migration — see
    ``alembic/versions/20260224_add_settingdomain_banking.py``, whose entire
    content is adding one member. A vocabulary whose members belong to modules
    is declared by those modules and validated by a registry; the layer that
    stores it never enumerates them (Governance ADR 0007).

    **The attributes below are LEGACY accessors, not authority, and they are
    temporary.** They exist only because ~331 existing call sites read
    ``SettingDomain.payments``; they are a SUBSET of the declared domains, never
    the definition of them. A new domain is declared by its owning module and
    needs no edit here — ``tests/architecture/test_setting_domains.py`` proves
    exactly that. ``operations`` is absent from both: it had no ``SettingSpec``
    and no reference anywhere. The accessors retire as callers move to
    module-owned constants.

    A ``str`` subclass, so a domain compares equal to its plain-string form in a
    query and ``.value`` keeps reading as it did under the enum.

    **One semantic differs from the enum: instances are NOT singletons.** Enum
    members were interned, so ``SettingDomain("gl") is SettingDomain.gl`` held
    by accident of that; here each construction is a distinct object. Compare
    domains with ``==``, never ``is``. Interning would restore identity but
    would also mean an unbounded cache keyed on untrusted input, since
    construction is deliberately open.
    """

    __slots__ = ()

    auth: "SettingDomain"
    audit: "SettingDomain"
    scheduler: "SettingDomain"
    automation: "SettingDomain"
    email: "SettingDomain"
    features: "SettingDomain"
    reporting: "SettingDomain"
    payments: "SettingDomain"
    support: "SettingDomain"
    inventory: "SettingDomain"
    projects: "SettingDomain"
    fleet: "SettingDomain"
    procurement: "SettingDomain"
    settings: "SettingDomain"
    payroll: "SettingDomain"
    banking: "SettingDomain"
    coach: "SettingDomain"
    notifications: "SettingDomain"
    expense: "SettingDomain"
    gl: "SettingDomain"

    @property
    def value(self) -> str:
        """Enum compatibility: call sites read ``domain.value`` throughout."""
        return str(self)

    def __repr__(self) -> str:
        return f"SettingDomain({str(self)!r})"

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: object, _handler: object
    ) -> "CoreSchema":
        """Present as a plain string to Pydantic.

        Pydantic handles an `enum.Enum` natively but cannot infer a schema for a
        bare `str` subclass, and this type appears as a field on the settings
        API schemas — without this, importing them raises
        `PydanticSchemaGenerationError` and every settings endpoint dies at
        import.

        Deliberately PERMISSIVE: it validates "is a string", not "is declared".
        Two reasons. Validating against the registry here would make this module
        import `app.services.setting_domains`, which imports this module — a
        cycle. And a response carrying a legacy value (`operations`) must still
        serialise; the registry gates WRITES and untrusted parses, which is
        where a rejection is meaningful.

        The visible consequence is intended: OpenAPI now describes `domain` as a
        string rather than a closed enum list, because it no longer is one.
        """
        from pydantic_core import core_schema

        return core_schema.no_info_after_validator_function(
            cls, core_schema.str_schema()
        )


for _name in (
    "auth",
    "audit",
    "scheduler",
    "automation",
    "email",
    "features",
    "reporting",
    "payments",
    "support",
    "inventory",
    "projects",
    "fleet",
    "procurement",
    "settings",
    "payroll",
    "banking",
    "coach",
    "notifications",
    "expense",
    "gl",
):
    setattr(SettingDomain, _name, SettingDomain(_name))
del _name


class SettingDomainType(TypeDecorator):
    """Stores a domain as ``VARCHAR(120)`` and loads it back as ``SettingDomain``.

    Without this a loaded row yields a plain ``str``, and every
    ``setting.domain.value`` in the codebase breaks. Storing is by value, so a
    caller may bind either a ``SettingDomain`` or a bare string.

    Deliberately NOT ``Enum``: a database enum re-imposes exactly the closed list
    this change removes, and costs an ``ALTER TYPE`` per new domain.
    """

    impl = String(120)
    cache_ok = True

    def process_bind_param(
        self, value: "SettingDomain | str | None", dialect: Dialect
    ) -> str | None:
        return None if value is None else str(value)

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> "SettingDomain | None":
        return None if value is None else SettingDomain(value)


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
    domain: Mapped[SettingDomain] = mapped_column(SettingDomainType, nullable=False)
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

    # Setting identification (denormalized for queries after setting deletion).
    # Width tracks `DomainSetting.domain` deliberately: a domain the live column
    # accepts must also be recordable here, or a valid write succeeds and then
    # fails the moment its change is recorded.
    domain: Mapped[str] = mapped_column(String(120), nullable=False)
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


def _require_declared_domain(
    _mapper: Mapper, _connection: Connection, target: "DomainSetting"
) -> None:
    """Reject a write naming a domain no installed module declares.

    At the ORM boundary rather than in ``DomainSettings``, because there are
    eight direct ``DomainSetting(...)`` constructors across six modules and only
    two of them are in that service — a service-level check would miss six. It
    sits beside the encryption listener above for exactly the same reason.

    Imported lazily: ``app.services.setting_domains`` imports this module for
    ``SettingDomain``, so a module-scope import would be circular.
    """
    from app.services.setting_domains import registry

    registry().require(target.domain)


class PlatformOwnedSettingError(ValueError):
    """An organization-scoped row was written for a PLATFORM-owned setting.

    Raised at the ORM boundary, not in ``DomainSettings``, for the reason
    ``_require_declared_domain`` gives above: there are eight direct
    ``DomainSetting(...)`` constructors across six modules and only two of them
    are in that service, so a service-level check would miss six of them, the
    settings import path and the history-restore path.

    A ``ValueError`` rather than an ``HTTPException``: a model listener that
    raised an HTTP status would invert the layering, and this fires in Celery
    tasks, seed scripts and admin tooling as well as in routes.
    """


def _require_platform_scope(
    _mapper: Mapper, _connection: Connection, target: "DomainSetting"
) -> None:
    """Refuse an organization row for a setting the platform owns.

    This is the WRITE half of the platform-ownership mechanism. The READ half
    is FOUR paths, and the count is stated because a docstring that named three
    of them was true of the code and still left the fourth uncovered:

    * ``settings_spec.resolve_value``
    * ``DomainSettings.get_by_key``
    * ``SettingsCache._get_setting_value_for_scope`` (single key, cached)
    * ``SettingsCache._load_domain_rows`` (the BULK path behind
      ``get_domain_settings``)

    The first three discard a caller-supplied organization for such a key. The
    fourth cannot — it selects both scopes in one statement and lets an
    ``ORDER BY`` decide — so it skips the organization row instead, which
    leaves the same value standing.

    Both halves are needed because ``public.domain_settings`` has no RLS
    policy: this listener covers every ORM writer, and the read-side override
    is what makes a row inserted outside the ORM inert instead of
    authoritative.

    Imported lazily, and from the leaf ``setting_scopes`` module rather than
    from ``settings_spec``, because ``settings_spec`` imports the service which
    imports this module.
    """
    if target.organization_id is None:
        return

    from app.services.setting_scopes import is_platform_owned

    if is_platform_owned(target.domain, target.key):
        raise PlatformOwnedSettingError(
            f"{target.domain}/{target.key} is platform-owned; an "
            "organization-scoped row may not exist for it. Write the platform "
            f"row through PUT /settings/{target.domain}/{target.key}, or "
            "narrow it with the corresponding webhook_tenant_* setting."
        )


event.listen(DomainSetting, "before_insert", _require_declared_domain)
event.listen(DomainSetting, "before_update", _require_declared_domain)
event.listen(DomainSetting, "before_insert", _require_platform_scope)
event.listen(DomainSetting, "before_update", _require_platform_scope)
event.listen(DomainSetting, "before_insert", _encrypt_secret_before_write)
event.listen(DomainSetting, "before_update", _encrypt_secret_before_write)
event.listen(DomainSetting, "load", _decrypt_secret_on_load)
event.listen(DomainSetting, "refresh", lambda t, c, _a: _decrypt_secret_on_load(t, c))
