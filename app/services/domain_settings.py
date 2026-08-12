import builtins
import logging
from types import FrameType
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session

from app.services.setting_domains import registry
from app.db.session_context import allow_cross_org
from app.models.domain_settings import (
    DomainSetting,
    DomainSettingHistory,
    SettingChangeAction,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.schemas.settings import (
    MASKED_VALUE,
    DomainSettingCreate,
    DomainSettingUpdate,
)
from app.services.common import coerce_uuid
from app.services.response import (
    ListResponseMixin,
    apply_ordering as _apply_ordering,
    apply_pagination as _apply_pagination,
)
from app.services.settings_cache import invalidate_setting_cache

logger = logging.getLogger(__name__)


class SettingsScopeRequired(RuntimeError):
    """A settings operation was handed no exact row scope.

    `DomainSetting` is organization-scoped, so an unscoped session cannot
    answer "which organization's row" — and this used to resolve that by
    granting `allow_cross_org` automatically. That made the fallback for
    "the caller forgot to scope this" into "see and write every tenant's
    rows": the lookup then matched on (domain, key) alone and returned
    whichever organization's row the index yielded first.

    Invisible on a single-tenant database, and a cross-tenant write the
    moment there are two. Missing scope now refuses instead. A caller must
    either use a tenant-scoped session or state `organization_id=None` for
    the platform-global row. An explicit cross-org infrastructure context
    also selects the platform-global row; it never means "whichever tenant
    row happens to be returned first".
    """


class _Ambient:
    """Marker for "no scope was stated" — distinct from an explicit `None`.

    A settings read has three possible intents and only two were expressible:

    * a UUID — this organization's value, falling back to the global row;
    * `None` — the GLOBAL value, deliberately;
    * ambient — the caller said nothing, so the session's context is used.

    They were conflated, so "I want the global row" and "I forgot to say" were
    the same call. This makes the third one nameable, which is what lets it be
    counted now and removed later. `settings_cache` already draws the same
    line — `_require_organization` refuses `None` outright, from the cache
    scope fix — and the resolver is catching up to its own codebase.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<ambient scope>"


AMBIENT = _Ambient()


def _resolve_operation_scope(
    db: Session,
    organization_id: "UUID | None | _Ambient",
    operation: str,
) -> UUID | None:
    """Resolve a list/write operation to one exact organization scope.

    Unlike reads with inheritance, these operations must never omit the
    organization predicate. `None` means only the platform-global row. An
    ambient tenant session means only that tenant. Cross-org infrastructure
    contexts are used by global seeders and likewise mean only the global
    row for this service; there is no valid "upsert every tenant" row.
    """
    if not isinstance(organization_id, _Ambient):
        if organization_id is not None:
            scoped_org = db.info.get("organization_id")
            if scoped_org is None or UUID(str(scoped_org)) != organization_id:
                raise SettingsScopeRequired(
                    f"{operation} was given organization_id={organization_id}, "
                    "but the session is not scoped to that organization. Open "
                    "it with `session_for_org(organization_id)`."
                )
        return organization_id

    if db.info.get("allow_cross_org"):
        return None
    scoped_org = db.info.get("organization_id")
    if scoped_org:
        return UUID(str(scoped_org))
    raise SettingsScopeRequired(
        f"{operation} needs an exact settings scope. Open a tenant session "
        "with `session_for_org(organization_id)`, or pass "
        "`organization_id=None` for the platform-global row."
    )


def _organization_predicate(organization_id: UUID | None):
    if organization_id is None:
        return DomainSetting.organization_id.is_(None)
    return DomainSetting.organization_id == organization_id


def _lock_setting_identity(
    db: Session,
    domain: SettingDomain,
    key: str,
    organization_id: UUID | None,
) -> None:
    """Serialize one PostgreSQL settings identity for find-or-create writes.

    Startup runs once in every Gunicorn worker. Without a lock, workers can
    all observe a missing row and then race to insert the same setting. The
    transaction-scoped advisory lock is held through the service's commit and
    is keyed by the complete setting identity, so unrelated settings do not
    block each other.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    identity = f"{domain.value}:{key}:{organization_id or 'global'}"
    db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:setting_identity, 0)"
            ")"
        ),
        {"setting_identity": identity},
    )


# Call sites already reported, so a hot path logs once rather than per request.
_reported_ambient_sites: set[tuple[str, int]] = set()


def _resolve_scope(
    db: Session, organization_id: "UUID | None | _Ambient"
) -> UUID | None:
    """Return the organization to scope a read to, warning if it was implied.

    Ambient scoping is why `get_by_key` could return the most recently updated
    row of ANY organization: with nothing in `db.info`, the query dropped its
    org predicate entirely and ran under `allow_cross_org`. That is masked
    while a deployment has one organization and stops being masked on the day
    it does not.
    """
    if not isinstance(organization_id, _Ambient):
        return organization_id
    import sys

    # Walk out of the settings layer to the CALLER that failed to state a
    # scope. Reporting the nearest frame names `resolve_value` every time —
    # true, useless, and it collapses every caller into one entry. The
    # migration needs the business call site, not the plumbing.
    # Annotated because `sys._getframe` is typed non-optional while `f_back`
    # is not, so the walk below would not type-check otherwise.
    frame: FrameType | None = sys._getframe(1)
    while frame is not None and frame.f_code.co_filename.endswith(
        ("/domain_settings.py", "/settings_spec.py", "/settings_cache.py")
    ):
        frame = frame.f_back
    if frame is None:  # pragma: no cover - only if called from nowhere
        return db.info.get("organization_id")
    site = (frame.f_code.co_filename, frame.f_lineno)
    if site not in _reported_ambient_sites:
        _reported_ambient_sites.add(site)
        logger.warning(
            "Settings read with ambient organization scope at %s:%d — pass "
            "organization_id= explicitly (None means the global row). This "
            "falls back to session context today and will be required.",
            frame.f_code.co_filename,
            frame.f_lineno,
        )
    return db.info.get("organization_id")


# Structured logger for settings audit trail
settings_audit_logger = logging.getLogger("dotmac.settings.audit")


def _log_setting_change(
    action: str,
    domain: SettingDomain,
    key: str,
    old_value: Any | None = None,
    new_value: Any | None = None,
    setting_id: str | None = None,
    is_secret: bool = False,
) -> None:
    """
    Log a setting change for audit purposes.

    Uses structured logging that can be captured by log aggregators.
    Masks secret values to prevent credential leakage.

    Args:
        action: Change type (CREATE, UPDATE, DELETE)
        domain: Setting domain
        key: Setting key
        old_value: Previous value (masked if secret)
        new_value: New value (masked if secret)
        setting_id: UUID of the setting
        is_secret: Whether this is a secret value
    """
    # Mask secret values
    masked_old = "***MASKED***" if is_secret and old_value else old_value
    masked_new = "***MASKED***" if is_secret and new_value else new_value

    settings_audit_logger.info(
        "Setting changed",
        extra={
            "action": action,
            "domain": domain.value,
            "key": key,
            "setting_id": str(setting_id) if setting_id else None,
            "old_value": masked_old,
            "new_value": masked_new,
            "is_secret": is_secret,
        },
    )


def _log_setting_attempt_failed(
    action: str,
    domain: SettingDomain | None,
    key: str | None,
    reason: str,
    attempted_value: Any | None = None,
    is_secret: bool = False,
) -> None:
    """
    Log a failed setting change attempt for security auditing.

    This captures validation failures, permission denials, and other
    unsuccessful attempts to modify settings.

    Args:
        action: Attempted action (CREATE, UPDATE, DELETE)
        domain: Setting domain (if known)
        key: Setting key (if known)
        reason: Why the attempt failed
        attempted_value: The value that was attempted (masked if secret)
        is_secret: Whether this is a secret value
    """
    masked_value = "***MASKED***" if is_secret and attempted_value else attempted_value

    settings_audit_logger.warning(
        "Setting change attempt failed",
        extra={
            "action": action,
            "domain": domain.value if domain else None,
            "key": key,
            "reason": reason,
            "attempted_value": masked_value,
            "is_secret": is_secret,
        },
    )


def _record_setting_history(
    db: Session,
    setting: DomainSetting,
    action: SettingChangeAction,
    old_value_type: str | None = None,
    old_value_text: str | None = None,
    old_value_json: object | None = None,
    old_is_secret: bool | None = None,
    old_is_active: bool | None = None,
    changed_by_id: str | None = None,
    change_reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> DomainSettingHistory:
    """
    Record a setting change in the history table.

    Args:
        db: Database session
        setting: The setting being changed
        action: Type of change (CREATE, UPDATE, DELETE)
        old_*: Previous values (None for CREATE)
        changed_by_id: User who made the change
        change_reason: Optional reason for the change
        ip_address: Request IP address
        user_agent: Request user agent

    Returns:
        The created history record
    """
    # Never persist a secret's value into the audit trail. The trail exists to
    # record *that* a value changed and *who* changed it — it does not need the
    # value, and storing it put a second plaintext copy of every payment key and
    # jwt_secret in the database, outliving even a rotation of the live setting.
    # (Responses are masked too, at the SettingHistoryRead boundary; this stops
    # the value ever reaching the table.)
    history = DomainSettingHistory(
        setting_id=setting.id,
        domain=setting.domain.value,
        key=setting.key,
        # The owning organization is part of a setting's identity, exactly like
        # domain and key, and is denormalized here for the same reason: the row
        # must stay identifiable after the setting itself is gone. Leaving it
        # NULL made every history row look platform-wide, which is what let
        # ``restore_from_history`` recreate an organization's setting as a
        # global row (and made the API's ownership check a no-op).
        organization_id=setting.organization_id,
        action=action,
        # Old values
        old_value_type=old_value_type,
        old_value_text=(
            MASKED_VALUE if old_is_secret and old_value_text else old_value_text
        ),
        old_value_json=old_value_json,
        old_is_secret=old_is_secret,
        old_is_active=old_is_active,
        # New values (from current setting state)
        new_value_type=setting.value_type.value if setting.value_type else None,
        new_value_text=(
            MASKED_VALUE
            if setting.is_secret and setting.value_text
            else setting.value_text
        ),
        new_value_json=setting.value_json,
        new_is_secret=setting.is_secret,
        new_is_active=setting.is_active,
        # Audit metadata
        changed_by_id=coerce_uuid(changed_by_id) if changed_by_id else None,
        change_reason=change_reason,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(history)
    return history


def _normalize_setting_values(
    value_type: SettingValueType,
    value_text: str | None,
    value_json: object | None,
) -> tuple[str | None, object | None]:
    raw_value = value_text if value_text is not None else value_json
    if raw_value is None:
        return None, None
    if value_type == SettingValueType.boolean:
        if isinstance(raw_value, bool):
            bool_value = raw_value
        elif isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                bool_value = True
            elif normalized in {"0", "false", "no", "off"}:
                bool_value = False
            else:
                raise HTTPException(status_code=400, detail="Value must be boolean")
        else:
            raise HTTPException(status_code=400, detail="Value must be boolean")
        return ("true" if bool_value else "false"), bool_value
    if value_type == SettingValueType.integer:
        try:
            int_value = int(str(raw_value))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="Value must be an integer"
            ) from exc
        return str(int_value), None
    if value_type == SettingValueType.string:
        return str(raw_value), None
    if value_type == SettingValueType.json:
        return None, raw_value
    return value_text, value_json


class DomainSettings(ListResponseMixin):
    def __init__(self, domain: SettingDomain | None = None) -> None:
        self.domain = domain

    def _resolve_domain(self, payload_domain: SettingDomain | None) -> SettingDomain:
        if self.domain and payload_domain and payload_domain != self.domain:
            raise HTTPException(status_code=400, detail="Setting domain mismatch")
        if self.domain:
            return self.domain
        if payload_domain:
            return payload_domain
        raise HTTPException(status_code=400, detail="Setting domain is required")

    def create(
        self,
        db: Session,
        payload: DomainSettingCreate,
        changed_by_id: str | None = None,
        change_reason: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        *,
        organization_id: "UUID | None | _Ambient" = AMBIENT,
    ) -> DomainSetting:
        data = payload.model_dump()
        data["domain"] = self._resolve_domain(payload.domain)
        if not isinstance(organization_id, _Ambient):
            data["organization_id"] = organization_id
            data["scope"] = (
                SettingScope.GLOBAL
                if organization_id is None
                else SettingScope.ORG_SPECIFIC
            )
        elif (
            not data.get("organization_id")
            and not db.info.get("allow_cross_org")
            and db.info.get("organization_id")
        ):
            data["organization_id"] = db.info["organization_id"]
            data["scope"] = SettingScope.ORG_SPECIFIC
        value_type = data.get("value_type") or SettingValueType.string
        value_text, value_json = _normalize_setting_values(
            value_type, data.get("value_text"), data.get("value_json")
        )
        data["value_type"] = value_type
        data["value_text"] = value_text
        # For JSON columns, SQLAlchemy serializes None to JSON 'null' instead of SQL NULL.
        # This breaks CHECK constraints that expect IS NULL. So we exclude the key entirely
        # when it should be NULL, letting the database use its default (NULL).
        if value_json is None:
            data.pop("value_json", None)
        else:
            data["value_json"] = value_json
        if value_text is None:
            data.pop("value_text", None)
        setting = DomainSetting(**data)
        db.add(setting)
        db.flush()  # Get the ID before recording history

        # Record history (CREATE has no old values)
        _record_setting_history(
            db,
            setting,
            SettingChangeAction.CREATE,
            changed_by_id=changed_by_id,
            change_reason=change_reason,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        db.commit()
        db.refresh(setting)

        # Invalidate cache for this setting
        invalidate_setting_cache(
            setting.domain, setting.key, organization_id=setting.organization_id
        )

        # Audit log
        _log_setting_change(
            action="CREATE",
            domain=setting.domain,
            key=setting.key,
            new_value=setting.value_text or setting.value_json,
            setting_id=str(setting.id),
            is_secret=setting.is_secret,
        )

        return setting

    def get(self, db: Session, setting_id: str) -> DomainSetting:
        setting = db.get(DomainSetting, coerce_uuid(setting_id))
        if not setting or (self.domain and setting.domain != self.domain):
            raise HTTPException(status_code=404, detail="Setting not found")
        return setting

    def list(
        self,
        db: Session,
        domain: SettingDomain | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
        *,
        organization_id: "UUID | None | _Ambient" = AMBIENT,
    ) -> list[DomainSetting]:
        org_id = _resolve_operation_scope(
            db, organization_id, "DomainSettingService.list"
        )
        stmt = select(DomainSetting)
        effective_domain = self.domain or domain
        if effective_domain:
            stmt = stmt.where(DomainSetting.domain == effective_domain)
        if is_active is None:
            stmt = stmt.where(DomainSetting.is_active.is_(True))
        else:
            stmt = stmt.where(DomainSetting.is_active == is_active)
        stmt = _apply_ordering(
            stmt,
            order_by,
            order_dir,
            {"created_at": DomainSetting.created_at, "key": DomainSetting.key},
        )
        stmt = _apply_pagination(stmt, limit, offset)
        stmt = stmt.where(_organization_predicate(org_id))
        with allow_cross_org(db):
            return list(db.scalars(stmt))

    def update(
        self,
        db: Session,
        setting_id: str,
        payload: DomainSettingUpdate,
        changed_by_id: str | None = None,
        change_reason: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> DomainSetting:
        setting = db.get(DomainSetting, coerce_uuid(setting_id))
        if not setting or (self.domain and setting.domain != self.domain):
            raise HTTPException(status_code=404, detail="Setting not found")

        # Capture old values for audit/history
        old_value = setting.value_text or setting.value_json
        old_value_type = setting.value_type.value if setting.value_type else None
        old_value_text = setting.value_text
        old_value_json = setting.value_json
        old_is_secret = setting.is_secret
        old_is_active = setting.is_active

        data = payload.model_dump(exclude_unset=True)
        if "domain" in data and data["domain"] != setting.domain:
            raise HTTPException(status_code=400, detail="Setting domain mismatch")
        if {"value_type", "value_text", "value_json"} & data.keys():
            value_type = data.get("value_type", setting.value_type)
            value_text = data.get("value_text", setting.value_text)
            value_json = data.get("value_json", setting.value_json)
            if "value_text" in data and "value_json" not in data:
                value_json = None
            if "value_json" in data and "value_text" not in data:
                value_text = None
            normalized_text, normalized_json = _normalize_setting_values(
                value_type, value_text, value_json
            )
            data["value_type"] = value_type
            data["value_text"] = normalized_text
            data["value_json"] = normalized_json
        for key, value in data.items():
            setattr(setting, key, value)

        # Record history before commit
        _record_setting_history(
            db,
            setting,
            SettingChangeAction.UPDATE,
            old_value_type=old_value_type,
            old_value_text=old_value_text,
            old_value_json=old_value_json,
            old_is_secret=old_is_secret,
            old_is_active=old_is_active,
            changed_by_id=changed_by_id,
            change_reason=change_reason,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        db.commit()
        db.refresh(setting)

        # Invalidate cache for this setting
        invalidate_setting_cache(
            setting.domain, setting.key, organization_id=setting.organization_id
        )

        # Audit log
        new_value = setting.value_text or setting.value_json
        _log_setting_change(
            action="UPDATE",
            domain=setting.domain,
            key=setting.key,
            old_value=old_value,
            new_value=new_value,
            setting_id=str(setting.id),
            is_secret=setting.is_secret,
        )

        return setting

    def get_by_key(
        self,
        db: Session,
        key: str,
        *,
        organization_id: "UUID | None | _Ambient" = AMBIENT,
        inherit: bool = True,
    ) -> DomainSetting:
        """Read one setting. `organization_id` is keyword-only on purpose.

        `AMBIENT` (the default, for now) uses the session's context and warns.
        An explicit `None` means the GLOBAL row and nothing else — stating "I
        have no organization" rather than failing to state anything.
        """
        if not self.domain:
            raise HTTPException(status_code=400, detail="Setting domain is required")
        org_id = _resolve_scope(db, organization_id)
        stmt = select(DomainSetting).where(
            DomainSetting.domain == self.domain,
            DomainSetting.key == key,
            DomainSetting.is_active.is_(True),
        )
        if organization_id is None:
            # Deliberately global: no org rows, so no cross-org row can win.
            stmt = stmt.where(DomainSetting.organization_id.is_(None))
        elif org_id and not inherit:
            # This organization's row and nothing else. For a value that
            # IDENTIFIES something the organization owns — a ledger account, a
            # bank account, a warehouse — a global row is not a valid answer,
            # and using one means posting to another organization's books.
            stmt = stmt.where(DomainSetting.organization_id == org_id)
        elif org_id:
            stmt = stmt.where(
                or_(
                    DomainSetting.organization_id == org_id,
                    DomainSetting.organization_id.is_(None),
                )
            ).order_by(
                case((DomainSetting.organization_id == org_id, 0), else_=1),
                DomainSetting.updated_at.desc(),
            )
        else:
            stmt = stmt.order_by(DomainSetting.updated_at.desc())
        with allow_cross_org(db):
            setting = db.scalar(stmt.limit(1))
        if not setting:
            raise HTTPException(status_code=404, detail="Setting not found")
        return setting

    def upsert_by_key(
        self,
        db: Session,
        key: str,
        payload: DomainSettingUpdate,
        changed_by_id: str | None = None,
        change_reason: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        *,
        organization_id: "UUID | None | _Ambient" = AMBIENT,
    ) -> DomainSetting:
        if not self.domain:
            raise HTTPException(status_code=400, detail="Setting domain is required")
        org_id = _resolve_operation_scope(
            db, organization_id, "DomainSettingService.upsert_by_key"
        )
        _lock_setting_identity(db, self.domain, key, org_id)
        with allow_cross_org(db):
            setting = db.scalar(
                select(DomainSetting).where(
                    DomainSetting.domain == self.domain,
                    DomainSetting.key == key,
                    _organization_predicate(org_id),
                )
            )
        if setting:
            # Capture old values for audit/history
            old_value = setting.value_text or setting.value_json
            old_value_type = setting.value_type.value if setting.value_type else None
            old_value_text = setting.value_text
            old_value_json = setting.value_json
            old_is_secret = setting.is_secret
            old_is_active = setting.is_active

            data = payload.model_dump(exclude_unset=True)
            data.pop("domain", None)
            data.pop("key", None)
            for field, value in data.items():
                setattr(setting, field, value)

            # Record history before commit
            _record_setting_history(
                db,
                setting,
                SettingChangeAction.UPDATE,
                old_value_type=old_value_type,
                old_value_text=old_value_text,
                old_value_json=old_value_json,
                old_is_secret=old_is_secret,
                old_is_active=old_is_active,
                changed_by_id=changed_by_id,
                change_reason=change_reason,
                ip_address=ip_address,
                user_agent=user_agent,
            )

            db.commit()
            db.refresh(setting)

            # Invalidate cache for this setting
            invalidate_setting_cache(
                self.domain, key, organization_id=setting.organization_id
            )

            # Audit log
            new_value = setting.value_text or setting.value_json
            _log_setting_change(
                action="UPDATE",
                domain=self.domain,
                key=key,
                old_value=old_value,
                new_value=new_value,
                setting_id=str(setting.id),
                is_secret=setting.is_secret,
            )

            return setting
        create_payload = DomainSettingCreate(
            domain=self.domain,
            key=key,
            value_type=payload.value_type or SettingValueType.string,
            value_text=payload.value_text,
            value_json=payload.value_json,
            is_secret=payload.is_secret or False,
            is_active=True if payload.is_active is None else payload.is_active,
        )
        return self.create(
            db,
            create_payload,
            changed_by_id=changed_by_id,
            change_reason=change_reason,
            ip_address=ip_address,
            user_agent=user_agent,
            organization_id=org_id,
        )

    def ensure_by_key(
        self,
        db: Session,
        key: str,
        value_type: SettingValueType,
        value_text: str | None = None,
        value_json: dict[str, Any] | builtins.list[Any] | bool | int | None = None,
        is_secret: bool = False,
        *,
        organization_id: "UUID | None | _Ambient" = AMBIENT,
    ) -> DomainSetting:
        if not self.domain:
            raise HTTPException(status_code=400, detail="Setting domain is required")
        org_id = _resolve_operation_scope(
            db, organization_id, "DomainSettingService.ensure_by_key"
        )
        _lock_setting_identity(db, self.domain, key, org_id)
        with allow_cross_org(db):
            existing = db.scalar(
                select(DomainSetting).where(
                    DomainSetting.domain == self.domain,
                    DomainSetting.key == key,
                    _organization_predicate(org_id),
                )
            )
        if existing:
            return existing
        payload = DomainSettingCreate(
            domain=self.domain,
            key=key,
            value_type=value_type,
            value_text=value_text,
            value_json=value_json,
            is_secret=is_secret,
            is_active=True,
        )
        return self.create(db, payload, organization_id=org_id)

    def delete(
        self,
        db: Session,
        setting_id: str,
        changed_by_id: str | None = None,
        change_reason: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> DomainSetting:
        setting = db.get(DomainSetting, coerce_uuid(setting_id))
        if not setting or (self.domain and setting.domain != self.domain):
            raise HTTPException(status_code=404, detail="Setting not found")

        # Capture values for audit/history before soft-delete
        old_value = setting.value_text or setting.value_json
        old_value_type = setting.value_type.value if setting.value_type else None
        old_value_text = setting.value_text
        old_value_json = setting.value_json
        old_is_secret = setting.is_secret
        old_is_active = setting.is_active

        setting.is_active = False

        # Record history before commit
        _record_setting_history(
            db,
            setting,
            SettingChangeAction.DELETE,
            old_value_type=old_value_type,
            old_value_text=old_value_text,
            old_value_json=old_value_json,
            old_is_secret=old_is_secret,
            old_is_active=old_is_active,
            changed_by_id=changed_by_id,
            change_reason=change_reason,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        db.commit()

        # Invalidate cache for this setting
        invalidate_setting_cache(
            setting.domain, setting.key, organization_id=setting.organization_id
        )

        # Audit log
        _log_setting_change(
            action="DELETE",
            domain=setting.domain,
            key=setting.key,
            old_value=old_value,
            setting_id=str(setting.id),
            is_secret=setting.is_secret,
        )

        return setting


settings = DomainSettings()
auth_settings = DomainSettings(SettingDomain.auth)
audit_settings = DomainSettings(SettingDomain.audit)
scheduler_settings = DomainSettings(SettingDomain.scheduler)
automation_settings = DomainSettings(SettingDomain.automation)
email_settings = DomainSettings(SettingDomain.email)
features_settings = DomainSettings(SettingDomain.features)
reporting_settings = DomainSettings(SettingDomain.reporting)
payments_settings = DomainSettings(SettingDomain.payments)
support_settings = DomainSettings(SettingDomain.support)
inventory_settings = DomainSettings(SettingDomain.inventory)
projects_settings = DomainSettings(SettingDomain.projects)
fleet_settings = DomainSettings(SettingDomain.fleet)
procurement_settings = DomainSettings(SettingDomain.procurement)
settings_settings = DomainSettings(SettingDomain.settings)
gl_settings = DomainSettings(SettingDomain.gl)
payroll_settings = DomainSettings(SettingDomain.payroll)
banking_settings = DomainSettings(SettingDomain.banking)
coach_settings = DomainSettings(SettingDomain.coach)
notifications_settings = DomainSettings(SettingDomain.notifications)
expense_settings = DomainSettings(SettingDomain.expense)


# =============================================================================
# History Service Functions
# =============================================================================


def list_setting_history(
    db: Session,
    domain: SettingDomain | None = None,
    key: str | None = None,
    setting_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    organization_id: UUID | str | None = None,
) -> tuple[list[DomainSettingHistory], int]:
    """
    List history entries for settings.

    Args:
        db: Database session
        domain: Filter by domain
        key: Filter by key (requires domain)
        setting_id: Filter by setting ID
        limit: Max entries to return
        offset: Offset for pagination
        organization_id: Restrict to one tenant's history. Callers that run on
            an RLS-bypass session MUST pass this — history rows carry an
            organization_id and are not otherwise scoped.

    Returns:
        Tuple of (history_entries, total_count)
    """
    stmt = select(DomainSettingHistory)

    if organization_id is not None:
        # Global settings carry a NULL organization_id and stay visible; a row
        # owned by *another* tenant does not.
        stmt = stmt.where(
            or_(
                DomainSettingHistory.organization_id == coerce_uuid(organization_id),
                DomainSettingHistory.organization_id.is_(None),
            )
        )

    if setting_id:
        stmt = stmt.where(DomainSettingHistory.setting_id == coerce_uuid(setting_id))
    elif domain:
        stmt = stmt.where(DomainSettingHistory.domain == domain.value)
        if key:
            stmt = stmt.where(DomainSettingHistory.key == key)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(
            stmt.order_by(DomainSettingHistory.changed_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )

    return items, int(total or 0)


def get_history_entry(db: Session, history_id: str) -> DomainSettingHistory | None:
    """
    Get a specific history entry by ID.

    Args:
        db: Database session
        history_id: History entry UUID

    Returns:
        History entry or None if not found
    """
    return db.get(DomainSettingHistory, coerce_uuid(history_id))


def _restore_target_organization(
    db: Session, history: DomainSettingHistory
) -> UUID | None:
    """
    Which organization does this history entry's setting belong to?

    Normally the history row says so directly. History written before
    ``_record_setting_history`` recorded the organization carries NULL, which is
    indistinguishable from a genuine platform-wide setting — so for those rows
    the linked setting is consulted instead. That link survives a soft delete
    (``is_active = False``); only a hard-deleted setting leaves no way back, and
    such an entry restores to the platform scope as before.
    """
    if history.organization_id is not None:
        return history.organization_id
    if history.setting_id is None:
        return None
    with allow_cross_org(db):
        origin = db.get(DomainSetting, history.setting_id)
    return origin.organization_id if origin is not None else None


def restore_from_history(
    db: Session,
    history_id: str,
    changed_by_id: str | None = None,
    change_reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> DomainSetting:
    """
    Restore a setting to a previous state from a history entry.

    Args:
        db: Database session
        history_id: History entry to restore from
        changed_by_id: User performing the restore
        change_reason: Reason for the restore
        ip_address: Request IP
        user_agent: Request user agent

    Returns:
        The restored setting

    Raises:
        HTTPException: If history entry not found or setting cannot be restored
    """
    history = get_history_entry(db, history_id)
    if not history:
        raise HTTPException(status_code=404, detail="History entry not found")

    # Get or create the setting
    try:
        domain = registry().require(history.domain)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid domain in history: {history.domain}",
        ) from exc

    target_org_id = _restore_target_organization(db, history)

    # Find the existing setting *in the scope the history entry belongs to*.
    #
    # The restore route runs on an RLS-bypass session, so without the explicit
    # organization predicate this lookup returned whichever organization's row
    # for (domain, key) the database happened to hand back first — restoring
    # one tenant's history could overwrite another tenant's live setting.
    # ``allow_cross_org`` is used for the same reason as in ``get_by_key``: the
    # ORM listener would otherwise drop the NULL-org platform row (and raise on
    # an unprimed session), so the predicate below is the scoping authority.
    stmt = select(DomainSetting).where(
        DomainSetting.domain == domain,
        DomainSetting.key == history.key,
    )
    if target_org_id is None:
        stmt = stmt.where(DomainSetting.organization_id.is_(None))
    else:
        stmt = stmt.where(DomainSetting.organization_id == target_org_id)
    with allow_cross_org(db):
        setting = db.scalar(stmt)

    # Determine what values to restore based on action type
    if history.action == SettingChangeAction.DELETE:
        # Restoring from DELETE means we use the old values (before deletion)
        restore_value_type = history.old_value_type
        restore_value_text = history.old_value_text
        restore_value_json = history.old_value_json
        restore_is_secret = history.old_is_secret
        restore_is_active = (
            history.old_is_active if history.old_is_active is not None else True
        )
    elif history.action == SettingChangeAction.UPDATE:
        # Restoring from UPDATE means we use the old values (before update)
        restore_value_type = history.old_value_type
        restore_value_text = history.old_value_text
        restore_value_json = history.old_value_json
        restore_is_secret = history.old_is_secret
        restore_is_active = (
            history.old_is_active if history.old_is_active is not None else True
        )
    else:  # CREATE
        # Restoring from CREATE would mean deleting (not typically wanted)
        raise HTTPException(
            status_code=400,
            detail="Cannot restore from CREATE action. Use delete instead.",
        )

    reason = change_reason or f"Restored from history entry {history_id}"

    if setting:
        # Update existing setting
        old_value_type = setting.value_type.value if setting.value_type else None
        old_value_text = setting.value_text
        old_value_json = setting.value_json
        old_is_secret = setting.is_secret
        old_is_active = setting.is_active

        # Apply restored values
        setting.value_type = (
            SettingValueType(restore_value_type)
            if restore_value_type
            else SettingValueType.string
        )
        setting.value_text = restore_value_text
        setting.value_json = restore_value_json
        setting.is_secret = (
            restore_is_secret if restore_is_secret is not None else False
        )
        setting.is_active = restore_is_active

        # Record this restore as an UPDATE in history
        _record_setting_history(
            db,
            setting,
            SettingChangeAction.UPDATE,
            old_value_type=old_value_type,
            old_value_text=old_value_text,
            old_value_json=old_value_json,
            old_is_secret=old_is_secret,
            old_is_active=old_is_active,
            changed_by_id=changed_by_id,
            change_reason=reason,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        db.commit()
        db.refresh(setting)
    else:
        # Create new setting (re-creating after deletion), in the organization
        # the history entry belongs to. Omitting organization_id here recreated
        # a deleted org-specific setting as a platform-wide row, which every
        # other organization then inherited as its fallback.
        create_kwargs: dict[str, Any] = {
            "domain": domain,
            "key": history.key,
            "organization_id": target_org_id,
            "scope": (
                SettingScope.GLOBAL
                if target_org_id is None
                else SettingScope.ORG_SPECIFIC
            ),
            "value_type": SettingValueType(restore_value_type)
            if restore_value_type
            else SettingValueType.string,
            "is_secret": restore_is_secret if restore_is_secret is not None else False,
            "is_active": restore_is_active,
        }
        # Same JSON-NULL workaround as ``DomainSettings.create``: SQLAlchemy
        # serializes a Python None into JSON 'null' for a JSON column, which the
        # ck_domain_settings_value_storage CHECK constraint rejects. Omitting the
        # key entirely lets the column fall back to SQL NULL. Passing them
        # unconditionally made this branch fail on every non-JSON setting.
        if restore_value_text is not None:
            create_kwargs["value_text"] = restore_value_text
        if restore_value_json is not None:
            create_kwargs["value_json"] = restore_value_json
        setting = DomainSetting(**create_kwargs)
        db.add(setting)
        db.flush()

        # Record this restore as a CREATE in history
        _record_setting_history(
            db,
            setting,
            SettingChangeAction.CREATE,
            changed_by_id=changed_by_id,
            change_reason=reason,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        db.commit()
        db.refresh(setting)

    # Invalidate cache
    invalidate_setting_cache(
        domain, setting.key, organization_id=setting.organization_id
    )

    # Audit log
    _log_setting_change(
        action="RESTORE",
        domain=setting.domain,
        key=setting.key,
        new_value=setting.value_text or setting.value_json,
        setting_id=str(setting.id),
        is_secret=setting.is_secret,
    )

    return setting
