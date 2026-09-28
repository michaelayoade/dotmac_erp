"""Fail-closed Traccar client boundary.

Step 1 defines configuration, authentication, timeout and error semantics but
does not activate an outbound provider transport. Repository governance keeps
provider connectors outside ERP; a later approved adapter can implement the
``TraccarTransport`` protocol without allowing routes, templates or services to
depend on transport details.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from app.config import settings
from app.services.secrets import resolve_secret

logger = logging.getLogger(__name__)


class TraccarClientError(RuntimeError):
    """Base error for the Traccar integration boundary."""


class TraccarConfigurationError(TraccarClientError):
    """Raised when required server-side configuration is missing or unsafe."""


class TraccarAuthenticationError(TraccarClientError):
    """Raised when Traccar rejects the configured service credentials."""


class TraccarUnavailableError(TraccarClientError):
    """Raised for timeout, network, or server-availability failures."""


class TraccarRequestError(TraccarClientError):
    """Raised for a non-success response that is not an availability failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class TraccarConfig:
    """Server-only Traccar configuration."""

    base_url: str
    username: str
    password: str
    connect_timeout: float = 5.0
    read_timeout: float = 15.0

    @classmethod
    def from_settings(cls) -> TraccarConfig:
        """Resolve credentials, including supported OpenBao references."""
        return cls(
            base_url=settings.traccar_base_url,
            username=resolve_secret(settings.traccar_username) or "",
            password=resolve_secret(settings.traccar_password) or "",
            connect_timeout=settings.traccar_connect_timeout,
            read_timeout=settings.traccar_read_timeout,
        )

    def is_configured(self) -> bool:
        """Return whether all required service settings are present."""
        return bool(self.base_url and self.username and self.password)

    def validate(self) -> None:
        """Reject incomplete or credential-bearing URLs before any call."""
        if not self.is_configured():
            raise TraccarConfigurationError("Traccar is not configured")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise TraccarConfigurationError("TRACCAR_BASE_URL is invalid")
        if parsed.username or parsed.password:
            raise TraccarConfigurationError(
                "TRACCAR_BASE_URL must not contain credentials"
            )
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            raise TraccarConfigurationError("Traccar timeouts must be positive")


@dataclass(frozen=True)
class TraccarTransportResponse:
    """Minimal response contract returned by an approved transport adapter."""

    status_code: int
    payload: Any = None


class TraccarTransport(Protocol):
    """Transport port implemented outside this Step 1 change."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        connect_timeout: float,
        read_timeout: float,
        json: dict[str, Any] | None = None,
    ) -> TraccarTransportResponse: ...


class TraccarClient:
    """Transport-neutral Traccar client used only by FleetTrackingService."""

    def __init__(
        self,
        config: TraccarConfig | None = None,
        *,
        transport: TraccarTransport | None = None,
    ) -> None:
        self.config = config or TraccarConfig.from_settings()
        self._transport = transport

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an authenticated request through an approved adapter."""
        self.config.validate()
        if self._transport is None:
            raise TraccarUnavailableError(
                "Traccar transport is not enabled in ERP Step 1"
            )

        credentials = f"{self.config.username}:{self.config.password}".encode()
        authorization = base64.b64encode(credentials).decode("ascii")
        url = urljoin(f"{self.config.base_url.rstrip('/')}/", path.lstrip("/"))
        logger.debug("Calling Traccar method=%s path=%s", method.upper(), path)

        try:
            response = self._transport.request(
                method=method.upper(),
                url=url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Basic {authorization}",
                },
                connect_timeout=self.config.connect_timeout,
                read_timeout=self.config.read_timeout,
                json=payload,
            )
        except (TimeoutError, ConnectionError, OSError) as exc:
            logger.warning("Traccar service is unavailable: %s", type(exc).__name__)
            raise TraccarUnavailableError("Traccar service is unavailable") from exc

        if response.status_code in {401, 403}:
            raise TraccarAuthenticationError(
                "Traccar rejected the configured service credentials"
            )
        if response.status_code >= 500:
            raise TraccarUnavailableError("Traccar service is unavailable")
        if response.status_code >= 400:
            raise TraccarRequestError(
                "Traccar request failed",
                status_code=response.status_code,
            )
        return response.payload
