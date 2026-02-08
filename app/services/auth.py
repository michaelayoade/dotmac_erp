import hashlib
import logging
import os
import secrets
import time
from datetime import UTC, datetime
from typing import Any, TypeVar

import redis
from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session

from app.models.auth import (
    ApiKey,
    AuthProvider,
    MFAMethod,
    MFAMethodType,
    SessionStatus,
    UserCredential,
)
from app.models.auth import (
    Session as AuthSession,
)
from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.person import Person
from app.schemas.auth import (
    ApiKeyCreate,
    ApiKeyGenerateRequest,
    ApiKeyUpdate,
    MFAMethodCreate,
    MFAMethodUpdate,
    SessionCreate,
    SessionUpdate,
    UserCredentialCreate,
    UserCredentialUpdate,
)
from app.services import settings_spec
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _apply_ordering(
    query: Query[T], order_by: str, order_dir: str, allowed_columns: dict[str, Any]
) -> Query[T]:
    if order_by not in allowed_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order_by. Allowed: {', '.join(sorted(allowed_columns))}",
        )
    column = allowed_columns[order_by]
    if order_dir == "desc":
        return query.order_by(column.desc())
    return query.order_by(column.asc())


def _apply_pagination(query: Query[T], limit: int, offset: int) -> Query[T]:
    return query.limit(limit).offset(offset)


def hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_API_KEY_WINDOW_SECONDS = 60
_API_KEY_MAX_PER_WINDOW = 5
_REDIS_CLIENT: redis.Redis | None = None


def _auth_setting(db: Session, key: str) -> str | None:
    setting = (
        db.query(DomainSetting)
        .filter(DomainSetting.domain == SettingDomain.auth)
        .filter(DomainSetting.key == key)
        .filter(DomainSetting.is_active.is_(True))
        .first()
    )
    if not setting:
        return None
    if setting.value_text is not None:
        return setting.value_text
    if setting.value_json is not None:
        return str(setting.value_json)
    return None


def _auth_int_setting(db: Session, key: str, default: int) -> int:
    value = _auth_setting(db, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_redis_client() -> redis.Redis | None:
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True)
        client.ping()
        _REDIS_CLIENT = client
        return client
    except redis.RedisError:
        return None


def _validate_enum(value: Any, enum_cls: type, label: str) -> Any:
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}") from exc


def _ensure_person(db: Session, person_id: str) -> None:
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")


def _validate_local_credentials(
    provider: AuthProvider, username: str | None, password_hash: str | None
) -> None:
    if provider == AuthProvider.local and (not username or not password_hash):
        raise HTTPException(
            status_code=400,
            detail="Local credentials require username and password_hash",
        )


class UserCredentials(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: UserCredentialCreate) -> UserCredential:
        _ensure_person(db, str(payload.person_id))
        data = payload.model_dump()
        fields_set = payload.model_fields_set
        if "provider" not in fields_set:
            default_provider = settings_spec.resolve_value(
                db, SettingDomain.auth, "default_auth_provider"
            )
            if default_provider:
                data["provider"] = _validate_enum(
                    default_provider, AuthProvider, "provider"
                )
        provider = data.get("provider", AuthProvider.local)
        _validate_local_credentials(
            provider, data.get("username"), data.get("password_hash")
        )
        credential = UserCredential(**data)
        db.add(credential)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="User credential already exists"
            ) from exc
        db.refresh(credential)
        return credential

    @staticmethod
    def get(db: Session, credential_id: str) -> UserCredential:
        credential = db.get(UserCredential, coerce_uuid(credential_id))
        if not credential:
            raise HTTPException(status_code=404, detail="User credential not found")
        return credential

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        provider: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[UserCredential]:
        query = db.query(UserCredential)
        if person_id:
            query = query.filter(UserCredential.person_id == coerce_uuid(person_id))
        if provider:
            query = query.filter(
                UserCredential.provider
                == _validate_enum(provider, AuthProvider, "provider")
            )
        if is_active is None:
            query = query.filter(UserCredential.is_active.is_(True))
        else:
            query = query.filter(UserCredential.is_active == is_active)
        query = _apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": UserCredential.created_at,
                "username": UserCredential.username,
                "last_login_at": UserCredential.last_login_at,
            },
        )
        return _apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(
        db: Session, credential_id: str, payload: UserCredentialUpdate
    ) -> UserCredential:
        credential = db.get(UserCredential, coerce_uuid(credential_id))
        if not credential:
            raise HTTPException(status_code=404, detail="User credential not found")
        data = payload.model_dump(exclude_unset=True)
        if "person_id" in data:
            _ensure_person(db, str(data["person_id"]))
        provider = data.get("provider", credential.provider)
        username = data.get("username", credential.username)
        password_hash = data.get("password_hash", credential.password_hash)
        _validate_local_credentials(provider, username, password_hash)
        for key, value in data.items():
            setattr(credential, key, value)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="User credential already exists"
            ) from exc
        db.refresh(credential)
        return credential

    @staticmethod
    def delete(db: Session, credential_id: str) -> None:
        credential = db.get(UserCredential, coerce_uuid(credential_id))
        if not credential:
            raise HTTPException(status_code=404, detail="User credential not found")
        credential.is_active = False
        db.commit()


class MFAMethods(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: MFAMethodCreate) -> MFAMethod:
        _ensure_person(db, str(payload.person_id))
        if payload.is_primary:
            db.query(MFAMethod).filter(
                MFAMethod.person_id == payload.person_id,
                MFAMethod.is_primary.is_(True),
            ).update({"is_primary": False})
        method = MFAMethod(**payload.model_dump())
        db.add(method)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Primary MFA method already exists for this user",
            ) from exc
        db.refresh(method)
        return method

    @staticmethod
    def get(db: Session, method_id: str) -> MFAMethod:
        method = db.get(MFAMethod, coerce_uuid(method_id))
        if not method:
            raise HTTPException(status_code=404, detail="MFA method not found")
        return method

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        method_type: str | None,
        is_primary: bool | None,
        enabled: bool | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[MFAMethod]:
        query = db.query(MFAMethod)
        if person_id:
            query = query.filter(MFAMethod.person_id == coerce_uuid(person_id))
        if method_type:
            query = query.filter(
                MFAMethod.method_type
                == _validate_enum(method_type, MFAMethodType, "method_type")
            )
        if is_primary is not None:
            query = query.filter(MFAMethod.is_primary == is_primary)
        if enabled is not None:
            query = query.filter(MFAMethod.enabled == enabled)
        if is_active is None:
            query = query.filter(MFAMethod.is_active.is_(True))
        else:
            query = query.filter(MFAMethod.is_active == is_active)
        query = _apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": MFAMethod.created_at,
                "method_type": MFAMethod.method_type,
                "is_primary": MFAMethod.is_primary,
            },
        )
        return _apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, method_id: str, payload: MFAMethodUpdate) -> MFAMethod:
        method = db.get(MFAMethod, coerce_uuid(method_id))
        if not method:
            raise HTTPException(status_code=404, detail="MFA method not found")
        data = payload.model_dump(exclude_unset=True)
        if "person_id" in data:
            _ensure_person(db, str(data["person_id"]))
        if data.get("is_primary"):
            person_id = data.get("person_id", method.person_id)
            db.query(MFAMethod).filter(
                MFAMethod.person_id == person_id,
                MFAMethod.id != method.id,
                MFAMethod.is_primary.is_(True),
            ).update({"is_primary": False})
        for key, value in data.items():
            setattr(method, key, value)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Primary MFA method already exists for this user",
            ) from exc
        db.refresh(method)
        return method

    @staticmethod
    def delete(db: Session, method_id: str) -> None:
        method = db.get(MFAMethod, coerce_uuid(method_id))
        if not method:
            raise HTTPException(status_code=404, detail="MFA method not found")
        method.is_active = False
        method.enabled = False
        method.is_primary = False
        db.commit()


class Sessions(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: SessionCreate) -> AuthSession:
        _ensure_person(db, str(payload.person_id))
        data = payload.model_dump()
        session = AuthSession(**data)
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def get(db: Session, session_id: str) -> AuthSession:
        session = db.get(AuthSession, coerce_uuid(session_id))
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[AuthSession]:
        query = db.query(AuthSession)
        if person_id:
            query = query.filter(AuthSession.person_id == coerce_uuid(person_id))
        if status:
            query = query.filter(
                AuthSession.status == _validate_enum(status, SessionStatus, "status")
            )
        query = _apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": AuthSession.created_at,
                "last_seen_at": AuthSession.last_seen_at,
                "status": AuthSession.status,
            },
        )
        return _apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, session_id: str, payload: SessionUpdate) -> AuthSession:
        session = db.get(AuthSession, coerce_uuid(session_id))
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        data = payload.model_dump(exclude_unset=True)
        if "person_id" in data:
            _ensure_person(db, str(data["person_id"]))
        for key, value in data.items():
            setattr(session, key, value)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def delete(db: Session, session_id: str) -> None:
        session = db.get(AuthSession, coerce_uuid(session_id))
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session.status = SessionStatus.revoked
        session.revoked_at = datetime.now(UTC)
        db.commit()


class ApiKeys(ListResponseMixin):
    @staticmethod
    def generate_with_rate_limit(
        db: Session, payload: ApiKeyGenerateRequest, request: Request | None
    ) -> dict[str, Any]:
        client_ip = "unknown"
        if request is not None and request.client:
            client_ip = request.client.host
        window_seconds = _auth_int_setting(
            db, "api_key_rate_window_seconds", _API_KEY_WINDOW_SECONDS
        )
        max_per_window = _auth_int_setting(
            db, "api_key_rate_max", _API_KEY_MAX_PER_WINDOW
        )
        redis_client = _get_redis_client()
        if not redis_client:
            raise HTTPException(
                status_code=503,
                detail="Rate limiting unavailable (Redis required)",
            )
        window = max(window_seconds, 1)
        key = f"api_key_rl:{client_ip}:{int(time.time() // window)}"
        try:
            count = int(redis_client.incr(key))  # type: ignore[arg-type]
            if count == 1:
                redis_client.expire(key, window)
            if count > max(max_per_window, 1):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
        except redis.RedisError as exc:
            raise HTTPException(
                status_code=503,
                detail="Rate limiting unavailable (Redis error)",
            ) from exc
        api_key, raw_key = ApiKeys.generate(db, payload)
        return {"key": raw_key, "api_key": api_key}

    @staticmethod
    def generate(db: Session, payload: ApiKeyGenerateRequest) -> tuple[ApiKey, str]:
        raw_key = secrets.token_urlsafe(32)
        data = payload.model_dump()
        data["key_hash"] = hash_api_key(raw_key)
        data.setdefault("is_active", True)
        if data.get("person_id"):
            _ensure_person(db, str(data["person_id"]))
        api_key = ApiKey(**data)
        db.add(api_key)
        db.commit()
        db.refresh(api_key)
        return api_key, raw_key

    @staticmethod
    def create(db: Session, payload: ApiKeyCreate) -> ApiKey:
        if payload.person_id:
            _ensure_person(db, str(payload.person_id))
        data = payload.model_dump()
        data["key_hash"] = hash_api_key(data["key_hash"])
        api_key = ApiKey(**data)
        db.add(api_key)
        db.commit()
        db.refresh(api_key)
        return api_key

    @staticmethod
    def get(db: Session, key_id: str) -> ApiKey:
        api_key = db.get(ApiKey, coerce_uuid(key_id))
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")
        return api_key

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[ApiKey]:
        query = db.query(ApiKey)
        if person_id:
            query = query.filter(ApiKey.person_id == coerce_uuid(person_id))
        if is_active is None:
            query = query.filter(ApiKey.is_active.is_(True))
        else:
            query = query.filter(ApiKey.is_active == is_active)
        query = _apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ApiKey.created_at, "label": ApiKey.label},
        )
        return _apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, key_id: str, payload: ApiKeyUpdate) -> ApiKey:
        api_key = db.get(ApiKey, coerce_uuid(key_id))
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")
        data = payload.model_dump(exclude_unset=True)
        if "person_id" in data and data["person_id"] is not None:
            _ensure_person(db, str(data["person_id"]))
        if "key_hash" in data and data["key_hash"]:
            data["key_hash"] = hash_api_key(data["key_hash"])
        for key, value in data.items():
            setattr(api_key, key, value)
        db.commit()
        db.refresh(api_key)
        return api_key

    @staticmethod
    def delete(db: Session, key_id: str) -> None:
        api_key = db.get(ApiKey, coerce_uuid(key_id))
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")
        api_key.is_active = False
        api_key.revoked_at = datetime.now(UTC)
        db.commit()

    @staticmethod
    def revoke(db: Session, key_id: str) -> None:
        ApiKeys.delete(db, key_id)


user_credentials = UserCredentials()
mfa_methods = MFAMethods()
sessions = Sessions()
api_keys = ApiKeys()
