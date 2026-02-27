"""SSO Token Validation Service.

Validates JWT tokens against the shared auth database for SSO clients.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


class SSOTokenValidator:
    """Validates JWT tokens against shared auth database.

    This service is used by SSO clients to validate access tokens
    against the shared auth database hosted by the SSO provider.
    """

    def __init__(self, auth_db: Session):
        """Initialize with auth database session.

        Args:
            auth_db: Database session connected to the shared auth database
        """
        self.auth_db = auth_db

    def validate_token(self, token: str) -> dict[str, Any] | None:
        """Validate access token and return payload if valid.

        Args:
            token: JWT access token to validate

        Returns:
            Token payload dict if valid, None if invalid
        """
        try:
            # Import here to avoid circular imports
            from app.services.auth_flow import _jwt_algorithm

            # Get SSO JWT secret or fall back to standard secret
            secret = self._get_jwt_secret()
            algorithm = _jwt_algorithm(self.auth_db)

            # Decode with SSO secret
            payload = _decode_jwt_with_secret(
                token,
                secret,
                algorithm,
                "access",
            )

            if not payload:
                return None

            # Validate session exists and is active
            session_id = payload.get("session_id")
            if not self._validate_session(session_id):
                logger.debug("Session validation failed for session_id: %s", session_id)
                return None

            return payload

        except Exception as e:
            logger.warning("Token validation failed: %s", e)
            return None

    def _get_jwt_secret(self) -> str:
        """Get the JWT secret for token validation.

        Returns SSO_JWT_SECRET if SSO is enabled, otherwise falls back
        to the standard JWT_SECRET.
        """
        import os

        from app.services.secrets import resolve_secret

        if settings.sso_enabled and settings.sso_jwt_secret:
            secret = resolve_secret(settings.sso_jwt_secret, self.auth_db)
            if secret:
                return secret

        # Fall back to standard JWT secret
        secret = os.getenv("JWT_SECRET")
        secret = resolve_secret(secret, self.auth_db)
        if not secret:
            raise ValueError("JWT secret not configured")
        return secret

    def _validate_session(self, session_id: str | None) -> bool:
        """Check if session is active in shared database.

        Args:
            session_id: Session UUID string to validate

        Returns:
            True if session is valid and active, False otherwise
        """
        if not session_id:
            return False

        try:
            session_uuid = coerce_uuid(session_id)
            session = self.auth_db.get(AuthSession, session_uuid)

            if not session:
                logger.debug("Session not found: %s", session_id)
                return False

            if session.status != SessionStatus.active:
                logger.debug(
                    "Session not active: %s (status=%s)", session_id, session.status
                )
                return False

            if session.revoked_at is not None:
                logger.debug("Session revoked: %s", session_id)
                return False

            now = datetime.now(UTC)
            expires_at = session.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)

            if expires_at < now:
                logger.debug("Session expired: %s", session_id)
                return False

            return True

        except Exception as e:
            logger.warning("Session validation error: %s", e)
            return False

    def update_session_activity(self, session_id: str) -> None:
        """Update last_seen_at for session activity tracking.

        This is called after successful token validation to track
        when the session was last used.

        Args:
            session_id: Session UUID string
        """
        try:
            session_uuid = coerce_uuid(session_id)
            session = self.auth_db.get(AuthSession, session_uuid)
            if session:
                session.last_seen_at = datetime.now(UTC)
                self.auth_db.commit()
        except Exception as e:
            logger.warning("Failed to update session activity: %s", e)
            self.auth_db.rollback()


def _decode_jwt_with_secret(
    token: str,
    secret: str,
    algorithm: str,
    expected_type: str,
) -> dict[str, Any] | None:
    """Decode and validate a JWT token with explicit secret.

    Args:
        token: JWT token to decode
        secret: JWT signing secret
        algorithm: JWT algorithm (e.g., "HS256")
        expected_type: Expected token type (e.g., "access")

    Returns:
        Decoded payload dict or None if invalid
    """
    import jwt

    # Whitelist of allowed algorithms
    allowed_algorithms = frozenset(
        {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512"}
    )

    if algorithm.lower() == "none" or algorithm not in allowed_algorithms:
        logger.warning("Invalid JWT algorithm: %s", algorithm)
        return None

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            options={
                "require": ["exp", "iat"],
                "verify_exp": True,
                "verify_iat": True,
            },
        )

        if payload.get("typ") != expected_type:
            logger.debug(
                "Token type mismatch: expected %s, got %s",
                expected_type,
                payload.get("typ"),
            )
            return None

        return payload

    except jwt.PyJWTError as e:
        logger.debug("JWT decode error: %s", e)
        return None
