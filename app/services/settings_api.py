from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain, SettingValueType
from app.schemas.settings import DomainSettingUpdate
from app.services import settings_spec
from app.services.response import list_response


def _domain_allowed_keys(domain: SettingDomain) -> str:
    specs = settings_spec.list_specs(domain)
    return ", ".join(sorted(spec.key for spec in specs))


def _normalize_spec_setting(
    domain: SettingDomain, key: str, payload: DomainSettingUpdate
) -> DomainSettingUpdate:
    spec = settings_spec.get_spec(domain, key)
    if not spec:
        allowed = _domain_allowed_keys(domain)
        raise HTTPException(
            status_code=400, detail=f"Invalid setting key. Allowed: {allowed}"
        )
    value = payload.value_text if payload.value_text is not None else payload.value_json
    if value is None:
        raise HTTPException(status_code=400, detail="Value required")
    coerced, error = settings_spec.coerce_value(spec, value)
    if error:
        raise HTTPException(status_code=400, detail=error)
    if isinstance(coerced, str) and spec.allowed:
        coerced = coerced.strip().lower()
    allowed_lower = {v.lower() for v in spec.allowed} if spec.allowed else None
    if allowed_lower and coerced not in allowed_lower:
        allowed = ", ".join(sorted(spec.allowed))
        raise HTTPException(
            status_code=400, detail=f"Value must be one of: {allowed}"
        )
    if spec.value_type == SettingValueType.integer:
        try:
            parsed = int(str(coerced))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Value must be an integer") from exc
        if spec.min_value is not None and parsed < spec.min_value:
            raise HTTPException(
                status_code=400, detail=f"Value must be >= {spec.min_value}"
            )
        if spec.max_value is not None and parsed > spec.max_value:
            raise HTTPException(
                status_code=400, detail=f"Value must be <= {spec.max_value}"
            )
        coerced = parsed
    value_text, value_json = settings_spec.normalize_for_db(spec, coerced)
    data = payload.model_dump(exclude_unset=True)
    data["value_type"] = spec.value_type
    data["value_text"] = value_text
    data["value_json"] = value_json
    if spec.is_secret:
        data["is_secret"] = True
    return DomainSettingUpdate(**data)


def _list_domain_settings(
    db: Session,
    domain: SettingDomain,
    is_active: bool | None,
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
):
    service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(domain)
    if not service:
        raise HTTPException(status_code=400, detail="Unknown settings domain")
    return service.list(db, None, is_active, order_by, order_dir, limit, offset)


def _list_domain_settings_response(
    db: Session,
    domain: SettingDomain,
    is_active: bool | None,
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
):
    items = _list_domain_settings(db, domain, is_active, order_by, order_dir, limit, offset)
    return list_response(items, limit, offset)


def _upsert_domain_setting(
    db: Session, domain: SettingDomain, key: str, payload: DomainSettingUpdate
):
    normalized_payload = _normalize_spec_setting(domain, key, payload)
    service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(domain)
    if not service:
        raise HTTPException(status_code=400, detail="Unknown settings domain")
    return service.upsert_by_key(db, key, normalized_payload)


def _get_domain_setting(db: Session, domain: SettingDomain, key: str):
    spec = settings_spec.get_spec(domain, key)
    if not spec:
        allowed = _domain_allowed_keys(domain)
        raise HTTPException(
            status_code=400, detail=f"Invalid setting key. Allowed: {allowed}"
        )
    service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(domain)
    if not service:
        raise HTTPException(status_code=400, detail="Unknown settings domain")
    return service.get_by_key(db, key)


def list_auth_settings_response(
    db: Session,
    is_active: bool | None,
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
):
    return _list_domain_settings_response(
        db, SettingDomain.auth, is_active, order_by, order_dir, limit, offset
    )


def upsert_auth_setting(db: Session, key: str, payload: DomainSettingUpdate):
    return _upsert_domain_setting(db, SettingDomain.auth, key, payload)


def get_auth_setting(db: Session, key: str):
    return _get_domain_setting(db, SettingDomain.auth, key)


def list_audit_settings_response(
    db: Session,
    is_active: bool | None,
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
):
    return _list_domain_settings_response(
        db, SettingDomain.audit, is_active, order_by, order_dir, limit, offset
    )


def upsert_audit_setting(db: Session, key: str, payload: DomainSettingUpdate):
    return _upsert_domain_setting(db, SettingDomain.audit, key, payload)


def get_audit_setting(db: Session, key: str):
    return _get_domain_setting(db, SettingDomain.audit, key)


def list_scheduler_settings_response(
    db: Session,
    is_active: bool | None,
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
):
    return _list_domain_settings_response(
        db, SettingDomain.scheduler, is_active, order_by, order_dir, limit, offset
    )


def upsert_scheduler_setting(db: Session, key: str, payload: DomainSettingUpdate):
    return _upsert_domain_setting(db, SettingDomain.scheduler, key, payload)


def get_scheduler_setting(db: Session, key: str):
    return _get_domain_setting(db, SettingDomain.scheduler, key)


def list_email_settings_response(
    db: Session,
    is_active: bool | None,
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
):
    return _list_domain_settings_response(
        db, SettingDomain.email, is_active, order_by, order_dir, limit, offset
    )


def upsert_email_setting(db: Session, key: str, payload: DomainSettingUpdate):
    return _upsert_domain_setting(db, SettingDomain.email, key, payload)


def get_email_setting(db: Session, key: str):
    return _get_domain_setting(db, SettingDomain.email, key)


def list_features_settings_response(
    db: Session,
    is_active: bool | None,
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
):
    return _list_domain_settings_response(
        db, SettingDomain.features, is_active, order_by, order_dir, limit, offset
    )


def upsert_features_setting(db: Session, key: str, payload: DomainSettingUpdate):
    return _upsert_domain_setting(db, SettingDomain.features, key, payload)


def get_features_setting(db: Session, key: str):
    return _get_domain_setting(db, SettingDomain.features, key)


def list_automation_settings_response(
    db: Session,
    is_active: bool | None,
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
):
    return _list_domain_settings_response(
        db, SettingDomain.automation, is_active, order_by, order_dir, limit, offset
    )


def upsert_automation_setting(db: Session, key: str, payload: DomainSettingUpdate):
    return _upsert_domain_setting(db, SettingDomain.automation, key, payload)


def get_automation_setting(db: Session, key: str):
    return _get_domain_setting(db, SettingDomain.automation, key)


def list_reporting_settings_response(
    db: Session,
    is_active: bool | None,
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
):
    return _list_domain_settings_response(
        db, SettingDomain.reporting, is_active, order_by, order_dir, limit, offset
    )


def upsert_reporting_setting(db: Session, key: str, payload: DomainSettingUpdate):
    return _upsert_domain_setting(db, SettingDomain.reporting, key, payload)


def get_reporting_setting(db: Session, key: str):
    return _get_domain_setting(db, SettingDomain.reporting, key)
