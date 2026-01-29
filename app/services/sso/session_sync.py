"""SSO Session Synchronization Service.

Manages session state across SSO apps for logout propagation
and session management.
"""

import logging
from datetime import datetime, timezone
from typing import List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import Session as AuthSession, SessionStatus
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


class SSOSessionSync:
    """Manages session state across SSO apps.

    This service handles:
    - Session revocation (logout from all apps)
    - Bulk session revocation (global logout for a user)
    - Session activity tracking across apps
    """

    def __init__(self, auth_db: Session):
        """Initialize with auth database session.

        Args:
            auth_db: Database session connected to the shared auth database
        """
        self.auth_db = auth_db

    def revoke_session(self, session_id: UUID | str) -> bool:
        """Revoke a session (logout from all apps).

        When a user logs out from any app, this revokes their session
        in the shared database, effectively logging them out from all apps.

        Args:
            session_id: Session UUID to revoke

        Returns:
            True if session was revoked, False if not found or already revoked
        """
        try:
            if isinstance(session_id, str):
                session_id = coerce_uuid(session_id)

            session = self.auth_db.get(AuthSession, session_id)
            if not session:
                logger.warning("Session not found for revocation: %s", session_id)
                return False

            if session.status == SessionStatus.revoked:
                logger.debug("Session already revoked: %s", session_id)
                return True

            session.status = SessionStatus.revoked
            session.revoked_at = datetime.now(timezone.utc)
            self.auth_db.commit()

            logger.info("SSO session revoked: %s", session_id)
            return True

        except Exception as e:
            logger.error("Failed to revoke session %s: %s", session_id, e)
            self.auth_db.rollback()
            return False

    def revoke_all_user_sessions(
        self,
        person_id: UUID | str,
        exclude_session_id: UUID | str | None = None,
    ) -> int:
        """Revoke all sessions for a user (global logout).

        This is used for:
        - Password changes (invalidate all existing sessions)
        - Security incidents (force re-authentication)
        - Account lockout

        Args:
            person_id: Person UUID whose sessions to revoke
            exclude_session_id: Optional session to keep (e.g., current session)

        Returns:
            Number of sessions revoked
        """
        try:
            if isinstance(person_id, str):
                person_id = coerce_uuid(person_id)

            stmt = (
                select(AuthSession)
                .where(AuthSession.person_id == person_id)
                .where(AuthSession.status == SessionStatus.active)
                .where(AuthSession.revoked_at.is_(None))
            )

            if exclude_session_id:
                if isinstance(exclude_session_id, str):
                    exclude_session_id = coerce_uuid(exclude_session_id)
                stmt = stmt.where(AuthSession.id != exclude_session_id)

            sessions = list(self.auth_db.scalars(stmt).all())

            if not sessions:
                return 0

            now = datetime.now(timezone.utc)
            for session in sessions:
                session.status = SessionStatus.revoked
                session.revoked_at = now

            self.auth_db.commit()

            logger.info(
                "Revoked %d sessions for user %s (excluded: %s)",
                len(sessions),
                person_id,
                exclude_session_id,
            )
            return len(sessions)

        except Exception as e:
            logger.error("Failed to revoke user sessions for %s: %s", person_id, e)
            self.auth_db.rollback()
            return 0

    def get_active_session_count(self, person_id: UUID | str) -> int:
        """Get count of active sessions for a user.

        Useful for displaying session information to users or
        enforcing concurrent session limits.

        Args:
            person_id: Person UUID

        Returns:
            Number of active sessions
        """
        try:
            if isinstance(person_id, str):
                person_id = coerce_uuid(person_id)

            from sqlalchemy import func

            stmt = (
                select(func.count(AuthSession.id))
                .where(AuthSession.person_id == person_id)
                .where(AuthSession.status == SessionStatus.active)
                .where(AuthSession.revoked_at.is_(None))
            )
            return self.auth_db.scalar(stmt) or 0

        except Exception as e:
            logger.error("Failed to count sessions for %s: %s", person_id, e)
            return 0

    def get_active_sessions(self, person_id: UUID | str) -> List[AuthSession]:
        """Get all active sessions for a user.

        Useful for displaying session list to users for management.

        Args:
            person_id: Person UUID

        Returns:
            List of active AuthSession objects
        """
        try:
            if isinstance(person_id, str):
                person_id = coerce_uuid(person_id)

            now = datetime.now(timezone.utc)
            stmt = (
                select(AuthSession)
                .where(AuthSession.person_id == person_id)
                .where(AuthSession.status == SessionStatus.active)
                .where(AuthSession.revoked_at.is_(None))
                .where(AuthSession.expires_at > now)
                .order_by(AuthSession.last_seen_at.desc().nullsfirst())
            )
            return list(self.auth_db.scalars(stmt).all())

        except Exception as e:
            logger.error("Failed to get sessions for %s: %s", person_id, e)
            return []

    def update_session_activity(
        self,
        session_id: UUID | str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> bool:
        """Update session activity timestamp and metadata.

        Called after successful authentication to track session usage.

        Args:
            session_id: Session UUID
            ip_address: Optional IP address to update
            user_agent: Optional user agent to update

        Returns:
            True if updated successfully
        """
        try:
            if isinstance(session_id, str):
                session_id = coerce_uuid(session_id)

            session = self.auth_db.get(AuthSession, session_id)
            if not session:
                return False

            session.last_seen_at = datetime.now(timezone.utc)
            if ip_address:
                session.ip_address = ip_address
            if user_agent:
                # Truncate user agent to avoid DB field overflow
                session.user_agent = user_agent[:512] if len(user_agent) > 512 else user_agent

            self.auth_db.commit()
            return True

        except Exception as e:
            logger.warning("Failed to update session activity: %s", e)
            self.auth_db.rollback()
            return False
