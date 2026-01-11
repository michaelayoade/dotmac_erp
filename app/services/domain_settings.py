from typing import Any, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.domain_settings import SettingValueType
from app.schemas.settings import DomainSettingCreate, DomainSettingUpdate
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


def _apply_ordering(query, order_by, order_dir, allowed_columns):
    if order_by not in allowed_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order_by. Allowed: {', '.join(sorted(allowed_columns))}",
        )
    column = allowed_columns[order_by]
    if order_dir == "desc":
        return query.order_by(column.desc())
    return query.order_by(column.asc())


def _apply_pagination(query, limit, offset):
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
            raise HTTPException(status_code=400, detail="Value must be an integer") from exc
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

    def create(self, db: Session, payload: DomainSettingCreate):
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
        db.commit()
        db.refresh(setting)
        return setting

    def get(self, db: Session, setting_id: str):
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
    ):
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

    def update(self, db: Session, setting_id: str, payload: DomainSettingUpdate):
        setting = db.get(DomainSetting, coerce_uuid(setting_id))
        if not setting or (self.domain and setting.domain != self.domain):
            raise HTTPException(status_code=404, detail="Setting not found")
        data = payload.model_dump(exclude_unset=True)
        if "domain" in data and data["domain"] != setting.domain:
            raise HTTPException(status_code=400, detail="Setting domain mismatch")
        if {"value_type", "value_text", "value_json"} & data.keys():
            value_type = data.get("value_type", setting.value_type)
            value_text = data["value_text"] if "value_text" in data else setting.value_text
            value_json = data["value_json"] if "value_json" in data else setting.value_json
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
        db.commit()
        db.refresh(setting)
        return setting

    def get_by_key(self, db: Session, key: str):
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

    def upsert_by_key(self, db: Session, key: str, payload: DomainSettingUpdate):
        if not self.domain:
            raise HTTPException(status_code=400, detail="Setting domain is required")
        setting = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == self.domain)
            .filter(DomainSetting.key == key)
            .first()
        )
        if setting:
            data = payload.model_dump(exclude_unset=True)
            data.pop("domain", None)
            data.pop("key", None)
            for field, value in data.items():
                setattr(setting, field, value)
            db.commit()
            db.refresh(setting)
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
        return self.create(db, create_payload)

    def ensure_by_key(
        self,
        db: Session,
        key: str,
        value_type: SettingValueType,
        value_text: str | None = None,
        value_json: dict[str, Any] | List[Any] | bool | int | None = None,
        is_secret: bool = False,
    ):
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

    def delete(self, db: Session, setting_id: str):
        setting = db.get(DomainSetting, setting_id)
        if not setting or (self.domain and setting.domain != self.domain):
            raise HTTPException(status_code=404, detail="Setting not found")
        setting.is_active = False
        db.commit()


settings = DomainSettings()
auth_settings = DomainSettings(SettingDomain.auth)
audit_settings = DomainSettings(SettingDomain.audit)
scheduler_settings = DomainSettings(SettingDomain.scheduler)
automation_settings = DomainSettings(SettingDomain.automation)
email_settings = DomainSettings(SettingDomain.email)
features_settings = DomainSettings(SettingDomain.features)
reporting_settings = DomainSettings(SettingDomain.reporting)
