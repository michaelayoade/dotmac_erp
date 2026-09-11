"""Small Mailcow API client for employee offboarding."""

from __future__ import annotations

import httpx


class MailcowClientError(RuntimeError):
    """Raised when Mailcow API operations fail."""


class MailcowClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:300] or "empty response"
        if isinstance(payload, dict):
            return str(payload.get("msg") or payload.get("message") or payload)
        return str(payload)[:300]

    @classmethod
    def _raise_for_http_error(cls, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MailcowClientError(
                f"Mailcow API returned HTTP {response.status_code}: "
                f"{cls._error_message(response)}"
            ) from exc

    @staticmethod
    def _raise_for_api_failure(result: object, operation: str) -> None:
        items = result if isinstance(result, list) else [result]
        failures = [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("type", "")).lower() not in {"success", "info"}
        ]
        if failures:
            raise MailcowClientError(f"Mailbox {operation} failed: {failures}")

    def get_mailbox(self, email: str) -> dict | None:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/get/mailbox/{email}",
                headers=self._headers(),
            )
            if response.status_code == 404:
                return None
            self._raise_for_http_error(response)
        payload = response.json()
        if isinstance(payload, list):
            return payload[0] if payload else None
        if isinstance(payload, dict) and payload:
            return payload
        return None

    def list_mailboxes(self, domain: str | None = None) -> list[dict]:
        endpoint = f"{self.base_url}/get/mailbox/all"
        if domain:
            endpoint = f"{endpoint}/{domain}"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(endpoint, headers=self._headers())
            self._raise_for_http_error(response)
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def create_mailbox(
        self,
        email: str,
        *,
        name: str,
        password: str,
        quota_mb: int,
        force_password_update: bool = True,
    ) -> None:
        local_part, separator, domain = email.strip().lower().partition("@")
        if not separator or not local_part or not domain:
            raise ValueError("A valid mailbox email address is required")
        payload = {
            "active": "1",
            "domain": domain,
            "local_part": local_part,
            "name": name,
            "password": password,
            "password2": password,
            "quota": quota_mb,
            "force_pw_update": "1" if force_password_update else "0",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/add/mailbox",
                headers=self._headers(),
                json=payload,
            )
            self._raise_for_http_error(response)
        try:
            result = response.json()
        except ValueError as exc:
            raise MailcowClientError("Mailbox creation returned invalid JSON") from exc
        self._raise_for_api_failure(result, "creation")

    def update_mailbox_password(
        self,
        email: str,
        password: str,
        *,
        active: bool = True,
    ) -> None:
        payload = {
            "items": [email],
            "attr": {
                "active": "1" if active else "0",
                "authsource": "mailcow",
                "password": password,
                "password2": password,
                "force_pw_update": "0",
            },
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/edit/mailbox",
                headers=self._headers(),
                json=payload,
            )
            self._raise_for_http_error(response)
        result = response.json()
        if isinstance(result, list):
            failures = [
                item
                for item in result
                if isinstance(item, dict)
                and str(item.get("type", "")).lower() not in {"success", "info"}
            ]
            if failures:
                raise MailcowClientError(f"Mailbox update failed: {failures}")
