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

    def get_mailbox(self, email: str) -> dict | None:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/get/mailbox/{email}",
                headers=self._headers(),
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
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
            response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

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
            response.raise_for_status()
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
