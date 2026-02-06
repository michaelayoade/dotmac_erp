import logging
from typing import Any, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.domain_settings import (
    DomainSetting,
    DomainSettingHistory,
    SettingChangeAction,
    SettingDomain,
    SettingValueType,
)
from app.schemas.settings import DomainSettingCreate, DomainSettingUpdate
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin
from app.services.settings_cache import invalidate_setting_cache

logger = logging.getLogger(__name__)

# Structured logger for settings audit trail
settings_audit_logger = logging.getLogger("dotmac.settings.audit")


def _log_setting_change(
    action: str,
    domain: SettingDomain,
    key: str,
    old_value: Optional[Any] = None,
    new_value: Optional[Any] = None,
    setting_id: Optional[str] = None,
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
    domain: Optional[SettingDomain],
    key: Optional[str],
    reason: str,
    attempted_value: Optional[Any] = None,
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
    history = DomainSettingHistory(
        setting_id=setting.id,
        domain=setting.domain.value,
        key=setting.key,
        action=action,
        # Old values
        old_value_type=old_value_type,
        old_value_text=old_value_text,
        old_value_json=old_value_json,
        old_is_secret=old_is_secret,
        old_is_active=old_is_active,
        # New values (from current setting state)
        new_value_type=setting.value_type.value if setting.value_type else None,
        new_value_text=setting.value_text,
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


def _apply_ordering(
    query: Any, order_by: str, order_dir: str, allowed_columns: dict[str, Any]
) -> Any:
    if order_by not in allowed_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order_by. Allowed: {', '.join(sorted(allowed_columns))}",
        )
    column = allowed_columns[order_by]
    if order_dir == "desc":
        return query.order_by(column.desc())
    return query.order_by(column.asc())


def _apply_pagination(query: Any, limit: int, offset: int) -> Any:
    return query.limit(limit).offset(offset)


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
    ) -> DomainSetting:
        data = payload.model_dump()
        data["domain"] = self._resolve_domain(payload.domain)
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
        invalidate_setting_cache(setting.domain, setting.key)

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
    ) -> list[DomainSetting]:
        query = db.query(DomainSetting)
        effective_domain = self.domain or domain
        if effective_domain:
            query = query.filter(DomainSetting.domain == effective_domain)
        if is_active is None:
            query = query.filter(DomainSetting.is_active.is_(True))
        else:
            query = query.filter(DomainSetting.is_active == is_active)
        query = _apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": DomainSetting.created_at, "key": DomainSetting.key},
        )
        return _apply_pagination(query, limit, offset).all()

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
            value_text = (
                data["value_text"] if "value_text" in data else setting.value_text
            )
            value_json = (
                data["value_json"] if "value_json" in data else setting.value_json
            )
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
        invalidate_setting_cache(setting.domain, setting.key)

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

    def get_by_key(self, db: Session, key: str) -> DomainSetting:
        if not self.domain:
            raise HTTPException(status_code=400, detail="Setting domain is required")
        setting = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == self.domain)
            .filter(DomainSetting.key == key)
            .first()
        )
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
    ) -> DomainSetting:
        if not self.domain:
            raise HTTPException(status_code=400, detail="Setting domain is required")
        setting = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == self.domain)
            .filter(DomainSetting.key == key)
            .first()
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
            invalidate_setting_cache(self.domain, key)

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
        )

    def ensure_by_key(
        self,
        db: Session,
        key: str,
        value_type: SettingValueType,
        value_text: str | None = None,
        value_json: dict[str, Any] | List[Any] | bool | int | None = None,
        is_secret: bool = False,
    ) -> DomainSetting:
        if not self.domain:
            raise HTTPException(status_code=400, detail="Setting domain is required")
        existing = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == self.domain)
            .filter(DomainSetting.key == key)
            .first()
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
        return self.create(db, payload)

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
        invalidate_setting_cache(setting.domain, setting.key)

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
payroll_settings = DomainSettings(SettingDomain.payroll)


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

    Returns:
        Tuple of (history_entries, total_count)
    """
    query = db.query(DomainSettingHistory)

    if setting_id:
        query = query.filter(DomainSettingHistory.setting_id == coerce_uuid(setting_id))
    elif domain:
        query = query.filter(DomainSettingHistory.domain == domain.value)
        if key:
            query = query.filter(DomainSettingHistory.key == key)

    total = query.count()
    items = (
        query.order_by(DomainSettingHistory.changed_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    return items, total


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
        domain = SettingDomain(history.domain)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid domain in history: {history.domain}",
        ) from exc

    # Find existing setting
    setting = (
        db.query(DomainSetting)
        .filter(
            DomainSetting.domain == domain,
            DomainSetting.key == history.key,
        )
        .first()
    )

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
        # Create new setting (re-creating after deletion)
        setting = DomainSetting(
            domain=domain,
            key=history.key,
            value_type=SettingValueType(restore_value_type)
            if restore_value_type
            else SettingValueType.string,
            value_text=restore_value_text,
            value_json=restore_value_json,
            is_secret=restore_is_secret if restore_is_secret is not None else False,
            is_active=restore_is_active,
        )
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
    invalidate_setting_cache(domain, setting.key)

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
