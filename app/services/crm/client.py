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

import httpx

from app.config import settings

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


class CRMRateLimitError(CRMError):
    """Rate limit exceeded."""

    pass


@dataclass
class CRMConfig:
    """CRM connection configuration."""

    url: str
    api_token: str | None = None
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0

    @classmethod
    def from_settings(cls) -> "CRMConfig":
        """Create config from application settings."""
        return cls(
            url=settings.crm_api_url,
            api_token=settings.crm_api_token,
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

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            if self.config.api_token:
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
        """
        Make HTTP request with retry logic.

        Args:
            method: HTTP method
            path: API path (relative to base URL)
            params: Query parameters
            json: JSON body for POST/PATCH

        Returns:
            Response JSON data

        Raises:
            CRMError: On API error
        """
        last_error: Exception | None = None

        for attempt in range(self.config.max_retries):
            try:
                response = self.client.request(
                    method=method,
                    url=path,
                    params=params,
                    json=json,
                )

                if response.status_code == 401:
                    raise CRMAuthenticationError(
                        "Authentication failed - check CRM_API_TOKEN",
                        status_code=401,
                    )
                if response.status_code == 404:
                    raise CRMNotFoundError(
                        f"Resource not found: {path}",
                        status_code=404,
                    )
                if response.status_code == 429:
                    # Rate limited - wait and retry
                    retry_after = int(response.headers.get("Retry-After", 5))
                    logger.warning(
                        "Rate limited by CRM API, waiting %d seconds",
                        retry_after,
                    )
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                return cast(dict[str, Any], response.json())

            except httpx.HTTPStatusError as e:
                last_error = CRMError(
                    f"HTTP {e.response.status_code}: {e.response.text}",
                    status_code=e.response.status_code,
                )
                if e.response.status_code >= 500:
                    # Server error - retry
                    logger.warning(
                        "CRM API server error (attempt %d/%d): %s",
                        attempt + 1,
                        self.config.max_retries,
                        str(e),
                    )
                    time.sleep(self.config.retry_delay * (attempt + 1))
                    continue
                raise last_error

            except httpx.RequestError as e:
                last_error = CRMError(f"Request failed: {str(e)}")
                logger.warning(
                    "CRM API request failed (attempt %d/%d): %s",
                    attempt + 1,
                    self.config.max_retries,
                    str(e),
                )
                time.sleep(self.config.retry_delay * (attempt + 1))

        if last_error:
            raise last_error
        raise CRMError("Max retries exceeded")

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
