"""
Mobile push notification service (FCM HTTP v1).

Device registration + delivery for the DotMac Frontline mobile app.
Follows the external-integration rules: empty config default,
``is_configured()`` gate, and push failures never break the main flow —
the in-app/polling channel remains the baseline.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.notification import DeviceToken

logger = logging.getLogger(__name__)

_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class PushService:
    """Registers device tokens and delivers FCM pushes."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @staticmethod
    def is_configured() -> bool:
        """True when an FCM service-account JSON is configured."""
        return bool(settings.fcm_service_account_json)

    @staticmethod
    def _load_credentials():
        """Build google-auth credentials from the configured service account.

        Accepts either a filesystem path or the JSON content itself.
        """
        from google.oauth2 import service_account

        raw = settings.fcm_service_account_json
        if raw.strip().startswith("{"):
            info = json.loads(raw)
            return service_account.Credentials.from_service_account_info(
                info, scopes=[_FCM_SCOPE]
            )
        return service_account.Credentials.from_service_account_file(
            raw, scopes=[_FCM_SCOPE]
        )

    # ------------------------------------------------------------------
    # Device registration
    # ------------------------------------------------------------------

    def register_device(
        self,
        org_id: UUID,
        person_id: UUID,
        *,
        token: str,
        platform: str,
    ) -> DeviceToken:
        """Upsert a device token (tokens are globally unique per FCM).

        Re-registering an existing token reactivates it and reassigns it to
        the current person — FCM tokens follow the app install, so after a
        device handover the new login owns the token.
        """
        device = self.db.scalar(select(DeviceToken).where(DeviceToken.token == token))
        now = datetime.utcnow()
        if device:
            device.organization_id = org_id
            device.person_id = person_id
            device.platform = platform
            device.last_seen_at = now
            device.revoked_at = None
        else:
            device = DeviceToken(
                organization_id=org_id,
                person_id=person_id,
                token=token,
                platform=platform,
                last_seen_at=now,
            )
            self.db.add(device)
        self.db.flush()
        logger.info("Registered device token for person %s (%s)", person_id, platform)
        return device

    def unregister_device(self, person_id: UUID, *, token: str) -> bool:
        """Soft-revoke a token owned by the caller. Idempotent."""
        device = self.db.scalar(
            select(DeviceToken).where(
                DeviceToken.token == token,
                DeviceToken.person_id == person_id,
            )
        )
        if not device:
            return False
        if device.revoked_at is None:
            device.revoked_at = datetime.utcnow()
            self.db.flush()
        return True

    def active_tokens_for_person(self, person_id: UUID) -> list[DeviceToken]:
        return list(
            self.db.scalars(
                select(DeviceToken).where(
                    DeviceToken.person_id == person_id,
                    DeviceToken.revoked_at.is_(None),
                )
            ).all()
        )

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def send_to_person(
        self,
        person_id: UUID,
        *,
        title: str,
        body: str,
        data: dict[str, str] | None = None,
    ) -> int:
        """Send a push to every active device of a person.

        Returns the number of successful sends. Invalid/expired tokens are
        soft-revoked so they drop out of future fan-outs. Never raises for
        delivery failures — push is an enhancement, not the system of record.
        """
        if not self.is_configured():
            return 0
        devices = self.active_tokens_for_person(person_id)
        if not devices:
            return 0

        try:
            credentials = self._load_credentials()
            import google.auth.transport.requests

            credentials.refresh(google.auth.transport.requests.Request())
            project_id = credentials.project_id
            access_token = credentials.token
        except Exception:
            logger.exception("FCM credential refresh failed")
            return 0

        url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
        sent = 0
        for device in devices:
            payload = {
                "message": {
                    "token": device.token,
                    "notification": {"title": title, "body": body},
                    **({"data": data} if data else {}),
                }
            }
            try:
                resp = httpx.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    sent += 1
                elif resp.status_code in (400, 404):
                    # UNREGISTERED / invalid token — stop sending to it.
                    device.revoked_at = datetime.utcnow()
                    logger.info(
                        "Revoked dead device token for person %s (%s)",
                        person_id,
                        resp.status_code,
                    )
                else:
                    logger.warning(
                        "FCM send failed (%s) for person %s",
                        resp.status_code,
                        person_id,
                    )
            except httpx.HTTPError:
                logger.exception("FCM send error for person %s", person_id)
        if devices:
            self.db.flush()
        return sent
