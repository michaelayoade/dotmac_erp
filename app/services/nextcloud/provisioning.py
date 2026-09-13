"""Nextcloud OCS user provisioning for ERP-owned employee identities."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings
from app.metrics import categorize_http_status, observe_integration_request

_PROVISIONING_API = "/ocs/v1.php/cloud"
_SUCCESS_STATUS_CODE = 100
_NOT_FOUND_STATUS_CODES = frozenset({998})


class NextcloudProvisioningError(RuntimeError):
    """A transport, HTTP, or OCS-level provisioning failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        ocs_status_code: int | None = None,
        response_data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.ocs_status_code = ocs_status_code
        self.response_data = response_data


class NextcloudIdentityConflictError(ValueError):
    """The desired Nextcloud identity exists without an ERP binding."""


@dataclass(frozen=True)
class NextcloudProvisioningConfig:
    """Dedicated, least-privilege Nextcloud provisioning configuration."""

    enabled: bool
    server_url: str
    username: str
    app_password: str | None
    group: str
    quota: str
    timeout: float

    @classmethod
    def from_settings(cls) -> NextcloudProvisioningConfig:
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


class NextcloudProvisioningClient:
    """Client for the Nextcloud OCS Provisioning API."""

    def __init__(self, config: NextcloudProvisioningConfig) -> None:
        if not config.configured:
            raise ValueError("Nextcloud provisioning API is not configured")
        self._base_url = config.server_url.rstrip("/")
        self._auth = (config.username, config.app_password or "")
        self._timeout = config.timeout
        self._headers = {
            "OCS-APIRequest": "true",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str | list[str]] | None = None,
    ) -> dict[str, Any]:
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
                "nextcloud_provisioning",
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
            _SUCCESS_STATUS_CODE,
        }
        observe_integration_request(
            "nextcloud_provisioning",
            endpoint,
            metric_status if not failed else "error",
            max(time.perf_counter() - started_at, 0.0),
        )
        if failed:
            message = (
                str(meta.get("message") or "").strip() if isinstance(meta, dict) else ""
            )
            raise NextcloudProvisioningError(
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
            response = self._request("GET", f"/users/{encoded_user_id}")
        except NextcloudProvisioningError as exc:
            if exc.status_code == 404 or exc.ocs_status_code in _NOT_FOUND_STATUS_CODES:
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
            "password": "",
            "email": email,
            "displayName": display_name,
            "groups[]": [group],
        }
        if quota:
            payload["quota"] = quota
        self._request("POST", "/users", data=payload)

    def add_user_to_group(self, user_id: str, group: str) -> None:
        encoded_user_id = quote(user_id, safe="")
        self._request(
            "POST",
            f"/users/{encoded_user_id}/groups",
            data={"groupid": group},
        )

    def enable_user(self, user_id: str) -> None:
        encoded_user_id = quote(user_id, safe="")
        self._request("PUT", f"/users/{encoded_user_id}/enable")

    def disable_user(self, user_id: str) -> None:
        encoded_user_id = quote(user_id, safe="")
        self._request("PUT", f"/users/{encoded_user_id}/disable")
