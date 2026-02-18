import logging
import os
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain_settings import DomainSetting, SettingDomain

logger = logging.getLogger(__name__)


def is_openbao_ref(value: str | None) -> bool:
    if not value:
        return False
    return value.startswith(("bao://", "openbao://", "vault://"))


def _coerce_bool(value: object | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _openbao_allow_insecure(db: Session | None) -> bool:
    if db is not None:
        try:
            setting = db.scalar(
                select(DomainSetting).where(
                    DomainSetting.domain == SettingDomain.automation,
                    DomainSetting.key == "openbao_allow_insecure",
                    DomainSetting.is_active.is_(True),
                )
            )
            if setting:
                raw = (
                    setting.value_json
                    if setting.value_json is not None
                    else setting.value_text
                )
                return _coerce_bool(raw, default=False)
        except Exception:
            logger.exception("Ignored exception")
    return _coerce_bool(os.getenv("OPENBAO_ALLOW_INSECURE"), default=False)


def _openbao_config(db: Session | None):
    addr = os.getenv("OPENBAO_ADDR") or os.getenv("VAULT_ADDR")
    token = os.getenv("OPENBAO_TOKEN") or os.getenv("VAULT_TOKEN")
    namespace = os.getenv("OPENBAO_NAMESPACE") or os.getenv("VAULT_NAMESPACE")
    kv_version = os.getenv("OPENBAO_KV_VERSION", "2")
    if not addr:
        raise HTTPException(status_code=500, detail="OpenBao address not configured")
    if not token:
        raise HTTPException(status_code=500, detail="OpenBao token not configured")
    parsed = urlparse(addr)
    allow_insecure = _openbao_allow_insecure(db)
    if parsed.scheme and parsed.scheme != "https" and not allow_insecure:
        raise HTTPException(
            status_code=500,
            detail="OpenBao address must use https",
        )
    return addr.rstrip("/"), token, namespace, kv_version


def _parse_ref(reference: str) -> tuple[str, str, str]:
    parsed = urlparse(reference)
    mount = parsed.netloc
    path = parsed.path.lstrip("/")
    field = parsed.fragment or "value"
    if not mount or not path:
        raise HTTPException(status_code=500, detail="Invalid OpenBao reference")
    return mount, path, field


def resolve_openbao_ref(reference: str, db: Session | None = None) -> str:
    addr, token, namespace, kv_version = _openbao_config(db)
    mount, path, field = _parse_ref(reference)
    if str(kv_version) == "1":
        url = f"{addr}/v1/{mount}/{path}"
    else:
        if path.startswith("data/"):
            path = path[len("data/") :]
        if not path:
            raise HTTPException(status_code=500, detail="Invalid OpenBao reference")
        url = f"{addr}/v1/{mount}/data/{path}"
    headers = {"X-Vault-Token": token}
    if namespace:
        headers["X-Vault-Namespace"] = namespace
    try:
        response = httpx.get(url, headers=headers, timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=500, detail="OpenBao request failed") from exc
    payload = response.json()
    data = payload.get("data", {})
    if str(kv_version) == "1":
        secret_data = data
    else:
        secret_data = data.get("data", {})
    if field not in secret_data:
        raise HTTPException(status_code=500, detail="OpenBao secret field not found")
    return str(secret_data[field])


def resolve_secret(value: str | None, db: Session | None = None) -> str | None:
    if not value:
        return value
    if is_openbao_ref(value):
        return resolve_openbao_ref(value, db)
    return value
