"""
Nextcloud Talk API Client.

Handles sending notification messages to users via Nextcloud Talk conversations.
Uses the OCS (Open Collaboration Services) REST API.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.metrics import categorize_http_status, observe_integration_request
from app.services.domain_settings import AMBIENT, _Ambient

logger = logging.getLogger(__name__)

# OCS API endpoints (Nextcloud Talk / Spreed)
_ROOM_API = "/ocs/v2.php/apps/spreed/api/v4/room"
_CHAT_API = "/ocs/v2.php/apps/spreed/api/v1/chat"
_PROVISIONING_API = "/ocs/v1.php/cloud"
_PROVISIONING_SUCCESS_STATUS_CODE = 100
_PROVISIONING_NOT_FOUND_STATUS_CODES = frozenset({998})


class NextcloudError(Exception):
    """Nextcloud API error."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        ocs_status_code: int | None = None,
        response_data: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.ocs_status_code = ocs_status_code
        self.response_data = response_data


@dataclass(frozen=True)
class NextcloudConfig:
    """Connection config for Nextcloud server."""

    server_url: str
    username: str
    password: str  # App password recommended
    timeout: float = 30.0

    @classmethod
    def from_db(
        cls,
        db: Session,
        *,
        organization_id: "UUID | None | _Ambient" = AMBIENT,
    ) -> "NextcloudConfig":
        """Load Nextcloud config from domain settings."""
        from app.services.settings_spec import resolve_value

        server_url = resolve_value(
            db,
            SettingDomain.notifications,
            "nextcloud_server_url",
            organization_id=organization_id,
        )
        if not server_url:
            raise ValueError(
                "Nextcloud is not configured (nextcloud_server_url missing)"
            )

        username = (
            resolve_value(
                db,
                SettingDomain.notifications,
                "nextcloud_username",
                organization_id=organization_id,
            )
            or ""
        )
        password = (
            resolve_value(
                db,
                SettingDomain.notifications,
                "nextcloud_password",
                organization_id=organization_id,
            )
            or ""
        )
        raw_timeout = resolve_value(
            db,
            SettingDomain.notifications,
            "nextcloud_request_timeout",
            organization_id=organization_id,
        )

        return cls(
            server_url=str(server_url).rstrip("/"),
            username=str(username),
            password=str(password),
            timeout=float(str(raw_timeout)) if raw_timeout else 30.0,
        )


@dataclass(frozen=True)
class NextcloudProvisioningConfig:
    """Dedicated, least-privilege employee provisioning configuration."""

    enabled: bool
    server_url: str
    username: str
    app_password: str | None
    group: str
    quota: str
    timeout: float

    @classmethod
    def from_settings(cls) -> "NextcloudProvisioningConfig":
        from app.config import settings

        return cls(
            enabled=settings.nextcloud_provisioning_enabled,
            server_url=settings.nextcloud_provisioning_server_url,
            username=settings.nextcloud_provisioning_username,
            app_password=settings.nextcloud_provisioning_app_password,
            group=settings.nextcloud_provisioning_group,
            quota=settings.nextcloud_provisioning_quota,
            timeout=settings.nextcloud_provisioning_timeout,
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.server_url and self.username and self.app_password and self.group
        )


def is_configured(
    db: Session,
    *,
    organization_id: "UUID | None | _Ambient" = AMBIENT,
) -> bool:
    """Return True if Nextcloud Talk integration is configured."""
    from app.services.settings_spec import resolve_value

    server_url = resolve_value(
        db,
        SettingDomain.notifications,
        "nextcloud_server_url",
        organization_id=organization_id,
    )
    username = resolve_value(
        db,
        SettingDomain.notifications,
        "nextcloud_username",
        organization_id=organization_id,
    )
    password = resolve_value(
        db,
        SettingDomain.notifications,
        "nextcloud_password",
        organization_id=organization_id,
    )
    return bool(server_url and username and password)


class NextcloudTalkClient:
    """Existing Nextcloud connector for Talk and scoped user provisioning."""

    def __init__(
        self,
        config: NextcloudConfig | NextcloudProvisioningConfig,
    ):
        self._base_url = config.server_url
        password = (
            config.password
            if isinstance(config, NextcloudConfig)
            else config.app_password or ""
        )
        self._auth = (config.username, password)
        self._timeout = config.timeout
        self._headers = {
            "OCS-APIRequest": "true",
            "Accept": "application/json",
        }

    @classmethod
    def from_db(
        cls,
        db: Session,
        *,
        organization_id: "UUID | None | _Ambient" = AMBIENT,
    ) -> "NextcloudTalkClient":
        """Create a client using settings from the database."""
        return cls(NextcloudConfig.from_db(db, organization_id=organization_id))

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        started_at = time.perf_counter()
        metric_status = "unknown"
        with httpx.Client(timeout=self._timeout) as client:
            try:
                resp = client.request(
                    method,
                    url,
                    auth=self._auth,
                    headers=self._headers,
                    json=json,
                )
                metric_status = categorize_http_status(resp.status_code)
            except httpx.RequestError:
                observe_integration_request(
                    "nextcloud",
                    f"{method.upper()} {path}",
                    "request_error",
                    max(time.perf_counter() - started_at, 0.0),
                )
                raise

        if resp.status_code >= 400:
            observe_integration_request(
                "nextcloud",
                f"{method.upper()} {path}",
                metric_status,
                max(time.perf_counter() - started_at, 0.0),
            )
            raise NextcloudError(
                f"Nextcloud API error: {resp.status_code}",
                status_code=resp.status_code,
                response_data=resp.json() if resp.content else None,
            )

        result: dict[str, Any] = resp.json()
        observe_integration_request(
            "nextcloud",
            f"{method.upper()} {path}",
            metric_status,
            max(time.perf_counter() - started_at, 0.0),
        )
        return result

    def _provisioning_request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str | list[str]] | None = None,
    ) -> dict[str, Any]:
        """Call the OCS v1 user API and enforce its embedded status code."""
        url = f"{self._base_url}{_PROVISIONING_API}{path}"
        endpoint = f"{method.upper()} {_PROVISIONING_API}{path}"
        started_at = time.perf_counter()
        metric_status = "unknown"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.request(
                    method,
                    url,
                    auth=self._auth,
                    headers=self._headers,
                    params={"format": "json"},
                    data=data,
                )
            metric_status = categorize_http_status(response.status_code)
        except httpx.RequestError:
            observe_integration_request(
                "nextcloud",
                endpoint,
                "request_error",
                max(time.perf_counter() - started_at, 0.0),
            )
            raise

        response_data: dict[str, Any] | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                response_data = parsed
        except ValueError:
            response_data = None

        ocs = response_data.get("ocs", {}) if response_data else {}
        meta = ocs.get("meta", {}) if isinstance(ocs, dict) else {}
        raw_ocs_status = meta.get("statuscode") if isinstance(meta, dict) else None
        try:
            ocs_status = int(raw_ocs_status) if raw_ocs_status is not None else None
        except (TypeError, ValueError):
            ocs_status = None

        failed = response.status_code >= 400 or ocs_status not in {
            None,
            _PROVISIONING_SUCCESS_STATUS_CODE,
        }
        observe_integration_request(
            "nextcloud",
            endpoint,
            metric_status if not failed else "error",
            max(time.perf_counter() - started_at, 0.0),
        )
        if failed:
            message = (
                str(meta.get("message") or "").strip() if isinstance(meta, dict) else ""
            )
            raise NextcloudError(
                message or f"Nextcloud provisioning API error: {response.status_code}",
                status_code=response.status_code,
                ocs_status_code=ocs_status,
                response_data=response_data,
            )
        return response_data or {"ocs": {"meta": {}, "data": {}}}

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        """Return one Nextcloud user, or ``None`` when it does not exist."""
        encoded_user_id = quote(user_id, safe="")
        try:
            response = self._provisioning_request("GET", f"/users/{encoded_user_id}")
        except NextcloudError as exc:
            if (
                exc.status_code == 404
                or exc.ocs_status_code in _PROVISIONING_NOT_FOUND_STATUS_CODES
            ):
                return None
            raise
        data = response.get("ocs", {}).get("data", {})
        return data if isinstance(data, dict) else {}

    def create_user(
        self,
        user_id: str,
        *,
        email: str,
        display_name: str,
        group: str,
        quota: str = "",
    ) -> None:
        """Create a user and let Nextcloud email its password-setting link."""
        payload: dict[str, str | list[str]] = {
            "userid": user_id,
            "email": email,
            "displayName": display_name,
            "groups[]": [group],
        }
        if quota:
            payload["quota"] = quota
        self._provisioning_request("POST", "/users", data=payload)

    def add_user_to_group(self, user_id: str, group: str) -> None:
        encoded_user_id = quote(user_id, safe="")
        self._provisioning_request(
            "POST",
            f"/users/{encoded_user_id}/groups",
            data={"groupid": group},
        )

    def enable_user(self, user_id: str) -> None:
        encoded_user_id = quote(user_id, safe="")
        self._provisioning_request("PUT", f"/users/{encoded_user_id}/enable")

    def disable_user(self, user_id: str) -> None:
        encoded_user_id = quote(user_id, safe="")
        self._provisioning_request("PUT", f"/users/{encoded_user_id}/disable")

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
