"""
CRM API Client.

Client for syncing data from crm.dotmac.io (omni-channel CRM).
Supports fetching tickets, projects, tasks, and field services.
"""

import logging
import time
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import os

import httpx
from dotmac_integration import IntegrationHttpClient, ReachabilityCircuit

from app.config import settings
from app.metrics import categorize_http_status, observe_integration_request

logger = logging.getLogger(__name__)


class CRMError(Exception):
    """CRM API error."""

    def __init__(self, message: str, status_code: int | None = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class CRMAuthenticationError(CRMError):
    """Authentication failed."""

    pass


class CRMNotFoundError(CRMError):
    """Resource not found."""

    pass


class _TransientServerError(CRMError):
    """Internal retry marker for 5xx; never escapes _request."""


class CRMRateLimitError(CRMError):
    """Rate limit exceeded."""

    retry_after: float | None = None

    pass


@dataclass
class CRMConfig:
    """CRM connection configuration."""

    url: str
    api_token: str | None = None
    api_key: str | None = None
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0

    @classmethod
    def from_settings(cls) -> "CRMConfig":
        """Create config from application settings."""
        from app.services.secrets import resolve_secret

        return cls(
            url=settings.crm_api_url,
            api_token=resolve_secret(settings.crm_api_token),
            api_key=resolve_secret(settings.crm_api_key),
            timeout=settings.crm_request_timeout,
            max_retries=settings.crm_max_retries,
        )


class CRMClient:
    """
    CRM API client for fetching data from crm.dotmac.io.

    Supports:
    - Tickets (with comments, SLA events)
    - Projects
    - Tasks (if available)
    - Field Services (if available)

    Uses pagination for large datasets and incremental sync support.
    """

    # API endpoints
    ENDPOINTS = {
        "tickets": "/tickets",
        "ticket_comments": "/ticket-comments",
        "ticket_sla_events": "/ticket-sla-events",
        "projects": "/projects",
        "notifications": "/notifications",
    }

    # Page size for list operations
    DEFAULT_PAGE_SIZE = 50
    MAX_PAGE_SIZE = 200

    def __init__(self, config: CRMConfig | None = None):
        self.config = config or CRMConfig.from_settings()
        self._client: httpx.Client | None = None
        self._engine_cache: IntegrationHttpClient | None = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            # Scoped service ApiKey preferred (CRM resolves X-API-Key through
            # its ApiKey-principal path with the linked person's RBAC); the
            # legacy static Bearer remains only until the key is provisioned.
            if self.config.api_key:
                headers["X-API-Key"] = self.config.api_key
            elif self.config.api_token:
                headers["Authorization"] = f"Bearer {self.config.api_token}"

            self._client = httpx.Client(
                base_url=self.config.url.rstrip("/"),
                timeout=self.config.timeout,
                headers=headers,
            )
        return self._client

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "CRMClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request via the shared integration engine.

        Behaviour preserved from the hand-rolled loop: 401/404 raise
        immediately with the old messages; 429 sleeps Retry-After (default 5s)
        and retries every attempt; 5xx retries on the original linear
        ``retry_delay * (attempt + 1)`` schedule (plus jitter); exhaustion
        raises the last CRMError; metrics emit once per logical call with the
        same status vocabulary. Deltas (same class as the erp->sub port):
        jitter added; a reachability circuit (env CRM_CIRCUIT_SECONDS, default
        30, <=0 disables) fails fast after transport-failure exhaustion;
        x-request-id is propagated; non-connect httpx.RequestErrors fail fast
        instead of retrying.
        """
        started_at = time.perf_counter()
        outcome = {"status": "unknown"}
        try:
            result = self._engine().request(
                method=method,
                path=path,
                params=params,
                json_data=json,
                handler_kwargs={"path": path, "outcome": outcome},
            )
            return cast(dict[str, Any], result)
        except _TransientServerError as exc:
            raise CRMError(exc.args[0], status_code=exc.status_code) from exc
        except CRMRateLimitError:
            outcome["status"] = "rate_limited"
            raise
        except httpx.RequestError as exc:
            outcome["status"] = "request_error"
            raise CRMError(f"Request failed: {exc}") from exc
        finally:
            observe_integration_request(
                "crm",
                f"{method.upper()} {path}",
                outcome["status"],
                max(time.perf_counter() - started_at, 0.0),
            )

    def _engine(self) -> IntegrationHttpClient:
        if self._engine_cache is None:
            retry_delay = self.config.retry_delay

            def _linear_jittered(attempt: int) -> float:
                import random

                return retry_delay * (attempt + 1) + random.uniform(0.0, 0.25)  # noqa: S311 — retry jitter, not crypto

            self._engine_cache = IntegrationHttpClient(
                client_factory=lambda: self.client,
                response_handler=self._handle_response,
                backoff=_linear_jittered,
                max_attempts=self.config.max_retries,
                rate_limit_exc=CRMRateLimitError,
                retryable_excs=(_TransientServerError,),
                non_retryable_excs=(CRMAuthenticationError, CRMNotFoundError, CRMError),
                loop_exhausted_factory=lambda exc, retries: (
                    exc
                    if isinstance(exc, CRMError)
                    else CRMError(f"Max retries exceeded: {exc}")
                ),
                circuit=ReachabilityCircuit(
                    cooldown_seconds=float(os.getenv("CRM_CIRCUIT_SECONDS", "30"))
                ),
                edge="crm",
                request_id_provider=_current_request_id,
            )
        return self._engine_cache

    def _handle_response(self, response, *, path: str, outcome: dict) -> Any:
        if response.status_code == 401:
            outcome["status"] = "auth_error"
            raise CRMAuthenticationError(
                "Authentication failed - check CRM_API_TOKEN", status_code=401
            )
        if response.status_code == 404:
            outcome["status"] = "not_found"
            raise CRMNotFoundError(f"Resource not found: {path}", status_code=404)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            exc = CRMRateLimitError("Rate limited by CRM API", status_code=429)
            try:
                exc.retry_after = float(int(retry_after)) if retry_after else 5.0
            except (TypeError, ValueError):
                exc.retry_after = 5.0
            raise exc
        if response.status_code >= 500:
            outcome["status"] = categorize_http_status(response.status_code)
            raise _TransientServerError(
                f"HTTP {response.status_code}: {response.text}",
                status_code=response.status_code,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            outcome["status"] = categorize_http_status(e.response.status_code)
            raise CRMError(
                f"HTTP {e.response.status_code}: {e.response.text}",
                status_code=e.response.status_code,
            ) from e
        outcome["status"] = categorize_http_status(response.status_code)
        return response.json()

    def _paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Paginate through list endpoint results.

        Args:
            path: API endpoint path
            params: Base query parameters
            page_size: Number of items per page

        Yields:
            Individual records from paginated response
        """
        params = params or {}
        params["limit"] = min(page_size, self.MAX_PAGE_SIZE)
        offset = 0

        while True:
            params["offset"] = offset
            response = self._request("GET", path, params=params)

            # Handle different response formats
            if isinstance(response, list):
                items = response
            elif isinstance(response, dict):
                items = response.get("items", response.get("data", []))
            else:
                break

            if not items:
                break

            yield from items

            if len(items) < page_size:
                break

            offset += len(items)

    # =========================================================================
    # Ticket Operations
    # =========================================================================

    def get_tickets(
        self,
        subscriber_id: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Fetch tickets from CRM.

        Args:
            subscriber_id: Filter by subscriber
            status: Filter by status
            since: Only fetch tickets modified since this time
            page_size: Number of items per page

        Yields:
            Ticket dictionaries
        """
        params: dict[str, Any] = {"order_by": "updated_at", "order_dir": "asc"}

        if subscriber_id:
            params["subscriber_id"] = subscriber_id
        if status:
            params["status"] = status
        if since:
            params["updated_since"] = since.isoformat()

        yield from self._paginate(self.ENDPOINTS["tickets"], params, page_size)

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Fetch a single ticket by ID."""
        return self._request("GET", f"{self.ENDPOINTS['tickets']}/{ticket_id}")

    def get_ticket_comments(
        self,
        ticket_id: str | None = None,
        since: datetime | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Fetch ticket comments.

        Args:
            ticket_id: Filter by ticket
            since: Only fetch comments created since this time
            page_size: Number of items per page

        Yields:
            Comment dictionaries
        """
        params: dict[str, Any] = {"order_by": "created_at", "order_dir": "asc"}

        if ticket_id:
            params["ticket_id"] = ticket_id
        if since:
            params["created_since"] = since.isoformat()

        yield from self._paginate(self.ENDPOINTS["ticket_comments"], params, page_size)

    def get_ticket_sla_events(
        self,
        ticket_id: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Generator[dict[str, Any], None, None]:
        """Fetch SLA events for tickets."""
        params: dict[str, Any] = {}
        if ticket_id:
            params["ticket_id"] = ticket_id

        yield from self._paginate(
            self.ENDPOINTS["ticket_sla_events"], params, page_size
        )

    # =========================================================================
    # Project Operations
    # =========================================================================

    def get_projects(
        self,
        subscriber_id: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Fetch projects from CRM.

        Args:
            subscriber_id: Filter by subscriber
            status: Filter by status
            since: Only fetch projects modified since this time
            page_size: Number of items per page

        Yields:
            Project dictionaries
        """
        params: dict[str, Any] = {"order_by": "updated_at", "order_dir": "asc"}

        if subscriber_id:
            params["subscriber_id"] = subscriber_id
        if status:
            params["status"] = status
        if since:
            params["updated_since"] = since.isoformat()

        yield from self._paginate(self.ENDPOINTS["projects"], params, page_size)

    def get_project(self, project_id: str) -> dict[str, Any]:
        """Fetch a single project by ID."""
        return self._request("GET", f"{self.ENDPOINTS['projects']}/{project_id}")

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def health_check(self) -> bool:
        """
        Check if CRM API is accessible.

        Returns:
            True if API is healthy, False otherwise
        """
        try:
            # Try to fetch first page of tickets with minimal data
            self._request("GET", self.ENDPOINTS["tickets"], params={"limit": 1})
            return True
        except CRMError as e:
            logger.error("CRM health check failed: %s", str(e))
            return False


def _current_request_id() -> str | None:
    """Propagate the inbound request id onto outbound CRM calls."""
    try:
        from app.observability import request_id_var

        return request_id_var.get() or None
    except Exception:  # pragma: no cover
        return None
