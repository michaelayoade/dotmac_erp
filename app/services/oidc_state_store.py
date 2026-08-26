"""Atomic PostgreSQL ceremony storage for the shared OIDC adapter."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from dotmac_auth_oidc import LoginState
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.auth import OIDCLoginState


class PostgresOIDCStateStore:
    def __init__(
        self,
        db: Session,
        *,
        organization_id: UUID,
        provider_binding: str,
    ) -> None:
        self._db = db
        self._organization_id = organization_id
        self._provider_binding = provider_binding

    @staticmethod
    def _hash(state_id: str) -> str:
        return hashlib.sha256(state_id.encode("utf-8")).hexdigest()

    def put(self, state: LoginState, *, ttl_seconds: int) -> None:
        self._db.execute(
            delete(OIDCLoginState).where(
                OIDCLoginState.organization_id == self._organization_id,
                OIDCLoginState.expires_at <= datetime.now(UTC),
            )
        )
        self._db.add(
            OIDCLoginState(
                organization_id=self._organization_id,
                state_hash=self._hash(state.state_id),
                code_verifier=state.code_verifier,
                nonce=state.nonce,
                redirect_uri=state.redirect_uri,
                return_to=state.return_to,
                issued_at=state.issued_at,
                provider_binding=self._provider_binding,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            )
        )
        self._db.flush()

    def take(self, state_id: str) -> LoginState | None:
        row = self._db.execute(
            delete(OIDCLoginState)
            .where(
                OIDCLoginState.organization_id == self._organization_id,
                OIDCLoginState.state_hash == self._hash(state_id),
                OIDCLoginState.provider_binding == self._provider_binding,
                OIDCLoginState.expires_at > datetime.now(UTC),
            )
            .returning(
                OIDCLoginState.nonce,
                OIDCLoginState.code_verifier,
                OIDCLoginState.redirect_uri,
                OIDCLoginState.issued_at,
                OIDCLoginState.return_to,
            )
        ).one_or_none()
        if row is None:
            return None
        return LoginState(
            state_id=state_id,
            nonce=row.nonce,
            code_verifier=row.code_verifier,
            redirect_uri=row.redirect_uri,
            issued_at=row.issued_at,
            return_to=row.return_to,
        )


__all__ = ["PostgresOIDCStateStore"]
