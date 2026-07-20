"""OpenID Connect adapter for replaceable external authentication.

The provider authenticates a user and signs an ID token. ERP maps the
provider's opaque ``(issuer, subject)`` identity to a local person, then issues
an ERP-owned session. Provider roles, permissions, sessions, and cookies are
never authoritative in ERP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast
from urllib.parse import urlencode

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

import httpx
from fastapi import HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.auth import FederatedIdentity
from app.models.person import Person, PersonStatus
from app.net import get_request_host, get_request_scheme
from app.services.auth_flow import _jwt_secret
from app.services.common import coerce_uuid
from app.services.secrets import resolve_secret

OIDC_STATE_COOKIE = "erp_oidc_state"
OIDC_STATE_TTL_SECONDS = 600
_ALLOWED_ID_TOKEN_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
)


@dataclass(frozen=True)
class OIDCMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True)
class OIDCLogin:
    authorization_url: str
    state_cookie: str


@dataclass(frozen=True)
class OIDCAuthentication:
    person_id: str
    next_url: str


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


class OIDCClient:
    """Authorization Code + PKCE client using OIDC discovery and JWKS."""

    def _required_config(self, *, require_enabled: bool = True) -> tuple[str, str]:
        issuer = (settings.oidc_issuer or "").rstrip("/")
        client_id = settings.oidc_client_id or ""
        if (
            (require_enabled and not settings.oidc_enabled)
            or not issuer
            or not client_id
        ):
            raise HTTPException(
                status_code=503,
                detail="OIDC authentication is not fully configured",
            )
        return issuer, client_id

    def _discovery_url(self, issuer: str) -> str:
        return settings.oidc_discovery_url or (
            f"{issuer}/.well-known/openid-configuration"
        )

    def _get_metadata(self, issuer: str) -> OIDCMetadata:
        try:
            response = httpx.get(
                self._discovery_url(issuer),
                timeout=settings.oidc_request_timeout,
                follow_redirects=False,
            )
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail="OIDC provider metadata is unavailable",
            ) from exc

        if document.get("issuer", "").rstrip("/") != issuer:
            raise HTTPException(
                status_code=503,
                detail="OIDC discovery issuer does not match configuration",
            )
        required = ("authorization_endpoint", "token_endpoint", "jwks_uri")
        if any(not document.get(key) for key in required):
            raise HTTPException(
                status_code=503,
                detail="OIDC provider metadata is incomplete",
            )
        return OIDCMetadata(
            issuer=issuer,
            authorization_endpoint=str(document["authorization_endpoint"]),
            token_endpoint=str(document["token_endpoint"]),
            jwks_uri=str(document["jwks_uri"]),
        )

    def _redirect_uri(self, request: Request) -> str:
        if settings.oidc_redirect_uri:
            return settings.oidc_redirect_uri
        scheme = get_request_scheme(request)
        host = get_request_host(request) or request.url.netloc
        return f"{scheme}://{host}/auth/oidc/callback"

    def _sign_state(self, db: Session, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        encoded = _base64url_encode(serialized)
        signature = hmac.new(
            _jwt_secret(db).encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded}.{_base64url_encode(signature)}"

    def _verify_state(self, db: Session, value: str) -> dict[str, Any]:
        try:
            encoded, provided_signature = value.split(".", 1)
            expected_signature = hmac.new(
                _jwt_secret(db).encode("utf-8"),
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(
                expected_signature, _base64url_decode(provided_signature)
            ):
                raise ValueError("state signature mismatch")
            payload = cast(dict[str, Any], json.loads(_base64url_decode(encoded)))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=401, detail="Invalid OIDC state") from exc
        if int(payload.get("exp", 0)) < int(time.time()):
            raise HTTPException(status_code=401, detail="OIDC state expired")
        return payload

    def start_login(
        self,
        db: Session,
        request: Request,
        next_url: str,
    ) -> OIDCLogin:
        issuer, client_id = self._required_config()
        metadata = self._get_metadata(issuer)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _base64url_encode(hashlib.sha256(verifier.encode()).digest())
        state_cookie = self._sign_state(
            db,
            {
                "state": state,
                "nonce": nonce,
                "verifier": verifier,
                "next": next_url,
                "exp": int(time.time()) + OIDC_STATE_TTL_SECONDS,
            },
        )
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": self._redirect_uri(request),
            "scope": settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return OIDCLogin(
            authorization_url=f"{metadata.authorization_endpoint}?{urlencode(params)}",
            state_cookie=state_cookie,
        )

    def _exchange_code(
        self,
        db: Session,
        metadata: OIDCMetadata,
        request: Request,
        code: str,
        verifier: str,
        client_id: str,
    ) -> str:
        client_secret = resolve_secret(settings.oidc_client_secret, db)
        if not client_secret:
            raise HTTPException(
                status_code=503,
                detail="OIDC client secret is not configured",
            )
        try:
            response = httpx.post(
                metadata.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_uri(request),
                    "code_verifier": verifier,
                },
                auth=(client_id, client_secret),
                timeout=settings.oidc_request_timeout,
                follow_redirects=False,
            )
            response.raise_for_status()
            id_token = response.json().get("id_token")
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                status_code=401,
                detail="OIDC authorization code exchange failed",
            ) from exc
        if not id_token:
            raise HTTPException(status_code=401, detail="OIDC ID token is missing")
        return str(id_token)

    def _validate_id_token(
        self,
        metadata: OIDCMetadata,
        token: str,
        client_id: str,
        nonce: str,
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            algorithm = str(header.get("alg", ""))
            key_id = header.get("kid")
            if algorithm not in _ALLOWED_ID_TOKEN_ALGORITHMS or not key_id:
                raise JWTError("unsupported ID token header")
            response = httpx.get(
                metadata.jwks_uri,
                timeout=settings.oidc_request_timeout,
                follow_redirects=False,
            )
            response.raise_for_status()
            keys = response.json().get("keys", [])
            signing_key = next(key for key in keys if key.get("kid") == key_id)
            claims = cast(
                dict[str, Any],
                jwt.decode(
                    token,
                    signing_key,
                    algorithms=[algorithm],
                    audience=client_id,
                    issuer=metadata.issuer,
                    options={
                        "require_exp": True,
                        "require_iat": True,
                        "require_sub": True,
                    },
                ),
            )
        except (httpx.HTTPError, ValueError, StopIteration, JWTError) as exc:
            raise HTTPException(
                status_code=401, detail="Invalid OIDC ID token"
            ) from exc
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise HTTPException(status_code=401, detail="Invalid OIDC nonce")
        return claims

    def complete_login(
        self,
        db: Session,
        request: Request,
        *,
        code: str,
        state: str,
        state_cookie: str | None,
    ) -> OIDCAuthentication:
        if not state_cookie:
            raise HTTPException(status_code=401, detail="OIDC state cookie is missing")
        saved = self._verify_state(db, state_cookie)
        if not hmac.compare_digest(str(saved.get("state", "")), state):
            raise HTTPException(status_code=401, detail="Invalid OIDC state")

        issuer, client_id = self._required_config()
        metadata = self._get_metadata(issuer)
        id_token = self._exchange_code(
            db,
            metadata,
            request,
            code,
            str(saved["verifier"]),
            client_id,
        )
        claims = self._validate_id_token(
            metadata,
            id_token,
            client_id,
            str(saved["nonce"]),
        )
        subject = str(claims["sub"])
        binding = db.scalar(
            select(FederatedIdentity).where(
                FederatedIdentity.issuer == issuer,
                FederatedIdentity.subject == subject,
                FederatedIdentity.is_active.is_(True),
            )
        )
        if not binding:
            raise HTTPException(
                status_code=403,
                detail="OIDC identity is not linked to an ERP user",
            )
        person = db.get(Person, binding.person_id)
        if not person or not person.is_active or person.status != PersonStatus.active:
            raise HTTPException(status_code=403, detail="ERP user is inactive")

        binding.last_authenticated_at = datetime.now(UTC)
        db.flush()
        return OIDCAuthentication(
            person_id=str(person.id),
            next_url=str(saved.get("next") or "/"),
        )

    def list_bindings(self, db: Session) -> list[FederatedIdentity]:
        issuer, _ = self._required_config(require_enabled=False)
        return list(
            db.scalars(
                select(FederatedIdentity)
                .where(FederatedIdentity.issuer == issuer)
                .order_by(FederatedIdentity.created_at.desc())
            ).all()
        )

    def bind_identity(
        self,
        db: Session,
        *,
        person_id: str,
        subject: str,
    ) -> FederatedIdentity:
        issuer, _ = self._required_config(require_enabled=False)
        person = db.get(Person, coerce_uuid(person_id))
        if not person:
            raise HTTPException(status_code=404, detail="ERP user not found")
        existing = db.scalar(
            select(FederatedIdentity).where(
                FederatedIdentity.issuer == issuer,
                FederatedIdentity.subject == subject.strip(),
            )
        )
        if existing and existing.person_id == person.id:
            existing.is_active = True
            db.flush()
            db.refresh(existing)
            return existing
        binding = FederatedIdentity(
            person_id=person.id,
            issuer=issuer,
            subject=subject.strip(),
            is_active=True,
        )
        db.add(binding)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="OIDC identity or ERP user is already bound for this issuer",
            ) from exc
        db.refresh(binding)
        return binding

    def disable_binding(self, db: Session, binding_id: str) -> FederatedIdentity:
        issuer, _ = self._required_config(require_enabled=False)
        binding = db.get(FederatedIdentity, coerce_uuid(binding_id))
        if not binding or binding.issuer != issuer:
            raise HTTPException(
                status_code=404, detail="OIDC identity binding not found"
            )
        binding.is_active = False
        db.flush()
        db.refresh(binding)
        return binding


oidc_client = OIDCClient()
