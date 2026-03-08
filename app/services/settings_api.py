from datetime import UTC
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain, SettingValueType
from app.schemas.settings import DomainSettingUpdate
from app.services import settings_spec
from app.services.domain_settings import _log_setting_attempt_failed
from app.services.response import list_response


def _domain_allowed_keys(domain: SettingDomain) -> str:
    specs = settings_spec.list_specs(domain)
    return ", ".join(sorted(spec.key for spec in specs))


def _normalize_spec_setting(
    domain: SettingDomain, key: str, payload: DomainSettingUpdate
) -> DomainSettingUpdate:
    """
    Normalize and validate a setting update against its spec.

    Logs validation failures for security auditing.
    """
    spec = settings_spec.get_spec(domain, key)
    if not spec:
        allowed = _domain_allowed_keys(domain)
        _log_setting_attempt_failed(
            action="UPDATE",
            domain=domain,
            key=key,
            reason=f"Invalid setting key. Allowed: {allowed}",
        )
        raise HTTPException(
            status_code=400, detail=f"Invalid setting key. Allowed: {allowed}"
        )

    value = payload.value_text if payload.value_text is not None else payload.value_json
    if value is None:
        _log_setting_attempt_failed(
            action="UPDATE",
            domain=domain,
            key=key,
            reason="Value required",
            is_secret=spec.is_secret,
        )
        raise HTTPException(status_code=400, detail="Value required")

    coerced, error = settings_spec.coerce_value(spec, value)
    if error:
        _log_setting_attempt_failed(
            action="UPDATE",
            domain=domain,
            key=key,
            reason=error,
            attempted_value=value,
            is_secret=spec.is_secret,
        )
        raise HTTPException(status_code=400, detail=error)

    if isinstance(coerced, str) and spec.allowed:
        coerced = coerced.strip().lower()
    allowed_lower = {v.lower() for v in spec.allowed} if spec.allowed else None
    if allowed_lower and coerced not in allowed_lower:
        allowed = ", ".join(sorted(spec.allowed or []))
        _log_setting_attempt_failed(
            action="UPDATE",
            domain=domain,
            key=key,
            reason=f"Value must be one of: {allowed}",
            attempted_value=value,
            is_secret=spec.is_secret,
        )
        raise HTTPException(status_code=400, detail=f"Value must be one of: {allowed}")

    if spec.value_type == SettingValueType.integer:
        try:
            parsed = int(str(coerced))
        except (TypeError, ValueError) as exc:
            _log_setting_attempt_failed(
                action="UPDATE",
                domain=domain,
                key=key,
                reason="Value must be an integer",
                attempted_value=value,
                is_secret=spec.is_secret,
            )
            raise HTTPException(
                status_code=400, detail="Value must be an integer"
            ) from exc
        if spec.min_value is not None and parsed < spec.min_value:
            _log_setting_attempt_failed(
                action="UPDATE",
                domain=domain,
                key=key,
                reason=f"Value must be >= {spec.min_value}",
                attempted_value=value,
                is_secret=spec.is_secret,
            )
            raise HTTPException(
                status_code=400, detail=f"Value must be >= {spec.min_value}"
            )
        if spec.max_value is not None and parsed > spec.max_value:
            _log_setting_attempt_failed(
                action="UPDATE",
                domain=domain,
                key=key,
                reason=f"Value must be <= {spec.max_value}",
                attempted_value=value,
                is_secret=spec.is_secret,
            )
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
    items = _list_domain_settings(
        db, domain, is_active, order_by, order_dir, limit, offset
    )
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
    """Upsert a feature flag setting.

    Feature flags are managed via feature_flag_registry (not settings specs),
    so we validate against the registry instead of the spec system.
    """
    from app.services.feature_flag_service import FeatureFlagService

    ff_service = FeatureFlagService(db)
    registry = ff_service.get_registry_entry(key)
    if not registry:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown feature flag: {key}. Register it first.",
        )

    # Determine the boolean value from payload
    value = payload.value_text if payload.value_text is not None else payload.value_json
    if value is None:
        raise HTTPException(status_code=400, detail="Value required")
    enabled = str(value).lower() in ("true", "1", "yes", "on")

    # Use the features domain service to upsert the domain_settings row
    service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(SettingDomain.features)
    if not service:
        raise HTTPException(status_code=400, detail="Unknown settings domain")

    # Build a normalized payload
    normalized = DomainSettingUpdate(
        domain=SettingDomain.features,
        key=key,
        value_type=SettingValueType.boolean,
        value_text="true" if enabled else "false",
        is_active=True,
    )
    return service.upsert_by_key(db, key, normalized)


def get_features_setting(db: Session, key: str):
    """Get a feature flag setting by key.

    Validates against the feature flag registry instead of settings specs.
    """
    from app.services.feature_flag_service import FeatureFlagService

    ff_service = FeatureFlagService(db)
    registry = ff_service.get_registry_entry(key)
    if not registry:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown feature flag: {key}",
        )
    service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(SettingDomain.features)
    if not service:
        raise HTTPException(status_code=400, detail="Unknown settings domain")
    return service.get_by_key(db, key)


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


def list_payments_settings_response(
    db: Session,
    is_active: bool | None,
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
):
    return _list_domain_settings_response(
        db, SettingDomain.payments, is_active, order_by, order_dir, limit, offset
    )


def upsert_payments_setting(db: Session, key: str, payload: DomainSettingUpdate):
    return _upsert_domain_setting(db, SettingDomain.payments, key, payload)


def get_payments_setting(db: Session, key: str):
    return _get_domain_setting(db, SettingDomain.payments, key)


# =============================================================================
# Settings Export/Import
# =============================================================================


def export_settings(
    db: Session,
    domains: list[SettingDomain] | None = None,
    include_secrets: bool = False,
) -> dict:
    """
    Export settings to a dictionary suitable for JSON serialization.

    Args:
        db: Database session
        domains: List of domains to export (None = all domains)
        include_secrets: If False (default), secret values are masked

    Returns:
        Dict with structure: {
            "version": "1.0",
            "exported_at": "ISO timestamp",
            "settings": {
                "domain_name": {
                    "key": {"value": ..., "value_type": ..., "is_secret": ...},
                    ...
                },
                ...
            }
        }
    """
    from datetime import datetime

    if domains is None:
        domains = list(SettingDomain)

    result: dict[str, Any] = {
        "version": "1.0",
        "exported_at": datetime.now(UTC).isoformat(),
        "settings": {},
    }

    for domain in domains:
        service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(domain)
        if not service:
            continue

        domain_settings = {}
        settings_list = service.list(db, None, True, "key", "asc", 1000, 0)

        for setting in settings_list:
            value = (
                setting.value_json
                if setting.value_json is not None
                else setting.value_text
            )

            # Mask secrets unless explicitly requested
            if setting.is_secret and not include_secrets:
                value = "***EXPORTED_SECRET_MASKED***"

            domain_settings[setting.key] = {
                "value": value,
                "value_type": setting.value_type.value,
                "is_secret": setting.is_secret,
            }

        if domain_settings:
            result["settings"][domain.value] = domain_settings

    return result


def import_settings(
    db: Session,
    data: dict,
    domains: list[SettingDomain] | None = None,
    skip_secrets: bool = True,
    dry_run: bool = False,
) -> dict:
    """
    Import settings from an exported dictionary.

    Args:
        db: Database session
        data: Exported settings dict (from export_settings)
        domains: List of domains to import (None = all in export)
        skip_secrets: If True (default), skip importing secret values
        dry_run: If True, validate but don't actually import

    Returns:
        Dict with import results: {
            "imported": [{"domain": ..., "key": ..., "status": "created"|"updated"}],
            "skipped": [{"domain": ..., "key": ..., "reason": ...}],
            "errors": [{"domain": ..., "key": ..., "error": ...}],
        }
    """
    result: dict[str, Any] = {
        "imported": [],
        "skipped": [],
        "errors": [],
    }

    # Validate version
    version = data.get("version", "unknown")
    if version != "1.0":
        result["errors"].append(
            {
                "domain": None,
                "key": None,
                "error": f"Unsupported export version: {version}",
            }
        )
        return result

    settings_data = data.get("settings", {})
    if not settings_data:
        return result

    for domain_str, domain_settings in settings_data.items():
        # Parse domain
        try:
            domain = SettingDomain(domain_str)
        except ValueError:
            result["errors"].append(
                {
                    "domain": domain_str,
                    "key": None,
                    "error": f"Unknown domain: {domain_str}",
                }
            )
            continue

        # Filter by requested domains
        if domains is not None and domain not in domains:
            continue

        service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(domain)
        if not service:
            result["errors"].append(
                {
                    "domain": domain_str,
                    "key": None,
                    "error": "Domain service not configured",
                }
            )
            continue

        for key, setting_data in domain_settings.items():
            # Get spec for validation
            spec = settings_spec.get_spec(domain, key)
            if not spec:
                result["skipped"].append(
                    {
                        "domain": domain_str,
                        "key": key,
                        "reason": "Unknown setting key",
                    }
                )
                continue

            # Skip secrets if requested
            is_secret = setting_data.get("is_secret", False)
            if is_secret and skip_secrets:
                result["skipped"].append(
                    {
                        "domain": domain_str,
                        "key": key,
                        "reason": "Secret value skipped (skip_secrets=True)",
                    }
                )
                continue

            # Skip masked values
            value = setting_data.get("value")
            if value == "***EXPORTED_SECRET_MASKED***":
                result["skipped"].append(
                    {
                        "domain": domain_str,
                        "key": key,
                        "reason": "Masked secret value",
                    }
                )
                continue

            if dry_run:
                # Just validate
                _, error = settings_spec.coerce_value(spec, value)
                if error:
                    result["errors"].append(
                        {
                            "domain": domain_str,
                            "key": key,
                            "error": error,
                        }
                    )
                else:
                    result["imported"].append(
                        {
                            "domain": domain_str,
                            "key": key,
                            "status": "validated (dry_run)",
                        }
                    )
            else:
                # Actually import
                try:
                    payload = DomainSettingUpdate(
                        value_type=spec.value_type,
                        value_text=str(value) if value is not None else None,
                        is_secret=spec.is_secret,
                    )
                    normalized = _normalize_spec_setting(domain, key, payload)
                    service.upsert_by_key(db, key, normalized)
                    result["imported"].append(
                        {
                            "domain": domain_str,
                            "key": key,
                            "status": "imported",
                        }
                    )
                except HTTPException as e:
                    result["errors"].append(
                        {
                            "domain": domain_str,
                            "key": key,
                            "error": str(e.detail),
                        }
                    )
                except Exception as e:
                    result["errors"].append(
                        {
                            "domain": domain_str,
                            "key": key,
                            "error": str(e),
                        }
                    )

    if not dry_run and result["imported"]:
        db.commit()

    return result
