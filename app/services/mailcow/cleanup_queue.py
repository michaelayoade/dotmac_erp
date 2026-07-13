"""Client for the private Mailcow SOGo cleanup request receiver."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx


class SogoCleanupQueueClientError(RuntimeError):
    """Raised when the cleanup receiver rejects or cannot queue a request."""


class SogoCleanupQueueClient:
    def __init__(
        self,
        *,
        url: str,
        token: str,
        timeout: float = 20.0,
    ) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout

    def enqueue(self, email: str) -> bool:
        payload = {
            "email": email,
            "event": "employee_offboarding",
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                self.url,
                headers={"Authorization": f"Bearer {self.token}"},
                json=payload,
            )
            response.raise_for_status()

        result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise SogoCleanupQueueClientError(
                "Mailcow cleanup receiver returned an invalid response"
            )
        return result.get("queued") is True
