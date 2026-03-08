"""
Nextcloud Talk API Client.

Handles sending notification messages to users via Nextcloud Talk conversations.
Uses the OCS (Open Collaboration Services) REST API.
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain

logger = logging.getLogger(__name__)

# OCS API endpoints (Nextcloud Talk / Spreed)
_ROOM_API = "/ocs/v2.php/apps/spreed/api/v4/room"
_CHAT_API = "/ocs/v2.php/apps/spreed/api/v1/chat"


class NextcloudError(Exception):
    """Nextcloud API error."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_data: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_data = response_data


@dataclass(frozen=True)
class NextcloudConfig:
    """Connection config for Nextcloud server."""

    server_url: str
    username: str
    password: str  # App password recommended
    timeout: float = 30.0

    @classmethod
    def from_db(cls, db: Session) -> "NextcloudConfig":
        """Load Nextcloud config from domain settings."""
        from app.services.settings_spec import resolve_value

        server_url = resolve_value(db, SettingDomain.notifications, "nextcloud_server_url")
        if not server_url:
            raise ValueError("Nextcloud is not configured (nextcloud_server_url missing)")

        username = resolve_value(db, SettingDomain.notifications, "nextcloud_username") or ""
        password = resolve_value(db, SettingDomain.notifications, "nextcloud_password") or ""
        raw_timeout = resolve_value(
            db, SettingDomain.notifications, "nextcloud_request_timeout"
        )

        return cls(
            server_url=str(server_url).rstrip("/"),
            username=str(username),
            password=str(password),
            timeout=float(str(raw_timeout)) if raw_timeout else 30.0,
        )


def is_configured(db: Session) -> bool:
    """Return True if Nextcloud Talk integration is configured."""
    from app.services.settings_spec import resolve_value

    server_url = resolve_value(db, SettingDomain.notifications, "nextcloud_server_url")
    username = resolve_value(db, SettingDomain.notifications, "nextcloud_username")
    password = resolve_value(db, SettingDomain.notifications, "nextcloud_password")
    return bool(server_url and username and password)


class NextcloudTalkClient:
    """Client for sending messages via Nextcloud Talk."""

    def __init__(self, config: NextcloudConfig):
        self._base_url = config.server_url
        self._auth = (config.username, config.password)
        self._timeout = config.timeout
        self._headers = {
            "OCS-APIRequest": "true",
            "Accept": "application/json",
        }

    @classmethod
    def from_db(cls, db: Session) -> "NextcloudTalkClient":
        """Create a client using settings from the database."""
        return cls(NextcloudConfig.from_db(db))

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.request(
                method,
                url,
                auth=self._auth,
                headers=self._headers,
                json=json,
            )

        if resp.status_code >= 400:
            raise NextcloudError(
                f"Nextcloud API error: {resp.status_code}",
                status_code=resp.status_code,
                response_data=resp.json() if resp.content else None,
            )

        result: dict[str, Any] = resp.json()
        return result

    def get_or_create_conversation(self, nextcloud_user_id: str) -> str:
        """
        Get or create a 1-on-1 conversation with a Nextcloud user.

        Args:
            nextcloud_user_id: The Nextcloud user ID to create a conversation with.

        Returns:
            The conversation token (used to send messages).
        """
        data = self._request(
            "POST",
            _ROOM_API,
            json={
                "roomType": 1,  # ONE_TO_ONE
                "invite": nextcloud_user_id,
            },
        )
        token: str | None = data.get("ocs", {}).get("data", {}).get("token")
        if not token:
            raise NextcloudError(
                f"No conversation token returned for user {nextcloud_user_id}",
                response_data=data,
            )
        return token

    def send_message(self, conversation_token: str, message: str) -> dict[str, Any]:
        """
        Send a message to a Nextcloud Talk conversation.

        Args:
            conversation_token: The conversation token.
            message: The message text to send.

        Returns:
            The API response data.
        """
        data = self._request(
            "POST",
            f"{_CHAT_API}/{conversation_token}",
            json={"message": message},
        )
        result: dict[str, Any] = data.get("ocs", {}).get("data", {})
        return result

    def send_to_user(self, nextcloud_user_id: str, message: str) -> dict[str, Any]:
        """
        Send a notification message to a user via Talk.

        Creates/finds the 1-on-1 conversation and sends the message.

        Args:
            nextcloud_user_id: Target Nextcloud user ID.
            message: The notification message text.

        Returns:
            The sent message data from the API.
        """
        token = self.get_or_create_conversation(nextcloud_user_id)
        return self.send_message(token, message)
