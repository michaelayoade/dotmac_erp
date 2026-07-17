"""
dotmac_sub API Client.

HTTP client for the dotmac_sub subscriber-management system at
``selfcare.dotmac.io``. Replaces the legacy Splynx ISP-billing feed.

- **Auth**: scoped dotmac_sub API key sent as ``X-Api-Key``. dotmac_sub's
  ``Authorization: Bearer`` path only accepts session-bound login JWTs, so a
  long-lived service key MUST go in the ``X-Api-Key`` header.
- **API**: REST under ``/api/v1`` with ``?limit=&offset=`` pagination wrapped as
  ``{"items": [...], "count", "limit", "offset"}``.
- **Domain**: ``Reseller -> Subscriber -> BillingAccount`` with
  invoices/payments/credit-notes keyed on the *billing account* (``account_id``).
- **Allocations are inline** on invoices and payments — no ledger re-fetch.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
from dotmac_integration import (
    IntegrationHttpClient,
    ReachabilityCircuit,
    exponential_backoff,
)

from app.config import settings
from app.metrics import observe_integration_request
from app.observability import get_request_id

logger = logging.getLogger(__name__)

# Reachability-circuit cooldown (seconds); <= 0 disables the breaker.
_CIRCUIT_COOLDOWN_ENV = "DOTMAC_SUB_CIRCUIT_SECONDS"
_CIRCUIT_COOLDOWN_DEFAULT = 30.0


def _circuit_cooldown_seconds() -> float:
    raw = os.getenv(_CIRCUIT_COOLDOWN_ENV, "")
    if not raw:
        return _CIRCUIT_COOLDOWN_DEFAULT
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "Invalid %s=%r; using default %.0fs",
            _CIRCUIT_COOLDOWN_ENV,
            raw,
            _CIRCUIT_COOLDOWN_DEFAULT,
        )
        return _CIRCUIT_COOLDOWN_DEFAULT


def _request_id_provider() -> str | None:
    """Propagate ERP's x-request-id contextvar onto outbound sub calls."""
    request_id = get_request_id()
    return request_id or None


class DotmacSubError(Exception):
    """dotmac_sub API error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class DotmacSubAuthenticationError(DotmacSubError):
    """Authentication failed (bad or missing bearer token)."""


class DotmacSubNotFoundError(DotmacSubError):
    """Resource not found."""


class DotmacSubRateLimitError(DotmacSubError):
    """Rate limit exceeded.

    ``retry_after`` (seconds, already capped at the client's
    ``_RETRY_AFTER_CAP``) carries a parsed ``Retry-After`` header; the retry
    engine honours it over exponential backoff when set.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code)
        self.retry_after = retry_after


class _TransientServerError(DotmacSubError):
    """Internal: a 5xx response, distinguishable so the engine retries it.

    Never escapes ``DotmacSubClient._request`` — exhausted retries are
    re-wrapped as a plain :class:`DotmacSubError`, exactly like the old loop.
    """


@dataclass
class DotmacSubConfig:
    """Configuration for the dotmac_sub API.

    Auth is a **scoped dotmac_sub API key** (``api_token``), sent as
    ``X-Api-Key``. Staff-credential login has been retired for security
    (audit S1) — the client no longer logs in with a username + password;
    ``api_token`` must be a dotmac_sub API key with read scopes for the
    synced domains.
    """

    api_url: str
    api_token: str = ""
    timeout: float = 60.0
    max_retries: int = 3

    @classmethod
    def from_settings(cls) -> DotmacSubConfig:
        """Create config from application settings (env fallback / bootstrap)."""
        return cls(
            api_url=settings.dotmac_sub_api_url,
            api_token=settings.dotmac_sub_api_token,
            timeout=settings.dotmac_sub_request_timeout,
            max_retries=settings.dotmac_sub_max_retries,
        )

    @classmethod
    def for_org(cls, db: Any, organization_id: Any) -> DotmacSubConfig:
        """Resolve config for an org, preferring UI-managed IntegrationConfig.

        Looks up the org's ``DOTMAC_SUB`` row in ``integration_config`` (with
        decrypted, possibly OpenBao-backed credentials) and falls back to the
        env-based settings for any field not configured there.
        """
        from app.models.sync import IntegrationType
        from app.services.integration_config import IntegrationConfigService

        env = cls.from_settings()
        try:
            creds = IntegrationConfigService(db).get_decrypted_credentials(
                organization_id, IntegrationType.DOTMAC_SUB
            )
        except Exception:  # noqa: BLE001 — never let config lookup break sync
            logger.warning(
                "Could not load dotmac_sub IntegrationConfig for org %s; "
                "falling back to env settings",
                organization_id,
                exc_info=True,
            )
            creds = None

        if not creds:
            return env

        # IntegrationConfig column mapping for dotmac_sub:
        #   base_url   -> api_url
        #   api_key    -> dotmac_sub API key (encrypted)
        #   api_secret -> webhook secret (used by the inbound webhook route)
        return cls(
            api_url=creds.get("base_url") or env.api_url,
            api_token=creds.get("api_key") or env.api_token,
            timeout=env.timeout,
            max_retries=env.max_retries,
        )

    def is_configured(self) -> bool:
        """Configured when we have a base URL and an API key."""
        return bool(self.api_url and self.api_token)

    @property
    def auth_headers(self) -> dict[str, str]:
        """Auth headers for a dotmac_sub request (scoped API key)."""
        return {"X-Api-Key": self.api_token}


@dataclass
class ResellerRecord:
    """Reseller (parent account) from dotmac_sub."""

    id: str
    name: str
    code: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    is_active: bool = True
    updated_at: str | None = None


@dataclass
class SubscriberRecord:
    """Subscriber (end customer) from dotmac_sub."""

    id: str
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    company_name: str | None = None
    legal_name: str | None = None
    email: str | None = None
    phone: str | None = None
    status: str | None = None
    category: str | None = None
    is_active: bool = True
    reseller_id: str | None = None
    tax_id: str | None = None
    subscriber_number: str | None = None
    account_number: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def full_name(self) -> str:
        if self.display_name:
            return self.display_name
        if self.company_name:
            return self.company_name
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or self.legal_name or self.account_number or self.id


@dataclass
class BillingAccountRecord:
    """Billing account from dotmac_sub (reseller-scoped; invoices key on this)."""

    id: str
    reseller_id: str
    name: str
    currency: str
    status: str
    balance: Decimal = Decimal("0")
    is_active: bool = True
    subscriber_id: str | None = None
    updated_at: str | None = None


@dataclass
class InvoiceLineRecord:
    """Invoice line item."""

    id: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    tax_rate_id: str | None = None
    tax_application: str = "exclusive"


@dataclass
class AllocationRecord:
    """Payment-to-invoice allocation (inline on invoices and payments)."""

    id: str
    payment_id: str
    invoice_id: str
    amount: Decimal


@dataclass
class InvoiceRecord:
    """Invoice from dotmac_sub."""

    id: str
    account_id: str
    invoice_number: str | None
    status: str
    currency: str
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    balance_due: Decimal
    issued_at: str | None = None
    due_at: str | None = None
    paid_at: str | None = None
    memo: str | None = None
    is_proforma: bool = False
    # Server-tracked last-modified instant (ISO8601). Drives the incremental
    # sync watermark so we only pull the delta each cycle.
    updated_at: str | None = None
    lines: list[InvoiceLineRecord] = field(default_factory=list)
    allocations: list[AllocationRecord] = field(default_factory=list)


@dataclass
class PaymentRecord:
    """Payment from dotmac_sub."""

    id: str
    account_id: str | None
    billing_account_id: str | None
    amount: Decimal
    currency: str
    status: str
    # Total refunded so far (gross `amount` is unchanged). Net cash = amount -
    # refunded_amount. Defaults to 0 when dotmac_sub hasn't deployed the field
    # yet, so the two apps deploy in any order.
    refunded_amount: Decimal = Decimal("0")
    gross_amount: Decimal | None = None
    net_amount: Decimal | None = None
    wht_amount: Decimal = Decimal("0")
    wht_rate: Decimal | None = None
    wht_status: str | None = None
    wht_record_id: str | None = None
    wht_certificate_reference: str | None = None
    wht_resolved_at: str | None = None
    paid_at: str | None = None
    external_id: str | None = None
    memo: str | None = None
    payment_method_id: str | None = None
    payment_channel_id: str | None = None
    # Server-tracked last-modified instant (ISO8601); see InvoiceRecord.
    updated_at: str | None = None
    allocations: list[AllocationRecord] = field(default_factory=list)

    @property
    def effective_account_id(self) -> str | None:
        return self.billing_account_id or self.account_id


@dataclass
class CreditNoteLineRecord:
    """Credit note line item."""

    id: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    tax_rate_id: str | None = None
    tax_application: str = "exclusive"


@dataclass
class CreditNoteRecord:
    """Credit note from dotmac_sub."""

    id: str
    account_id: str
    invoice_id: str | None
    credit_number: str | None
    status: str
    currency: str
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    applied_total: Decimal = Decimal("0")
    memo: str | None = None
    issued_at: str | None = None
    # Server-tracked last-modified instant (ISO8601); see InvoiceRecord.
    updated_at: str | None = None
    lines: list[CreditNoteLineRecord] = field(default_factory=list)


@dataclass
class TaxRateRecord:
    """Tax rate from dotmac_sub."""

    id: str
    name: str
    rate: Decimal
    code: str | None = None
    is_active: bool = True


def _dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        logger.warning("Could not parse decimal: %r", value)
        return Decimal(default)


def _watermark_params(
    account_id: str | None,
    status: str | None,
    updated_since: str | None,
) -> dict[str, Any]:
    """Build filters for a deterministic sync feed.

    Sync endpoints always order by ``updated_at, id``; clients cannot override
    that ordering because doing so would make paging nondeterministic.
    """
    params: dict[str, Any] = {}
    if account_id:
        params["account_id"] = account_id
    if status:
        params["status"] = status
    if updated_since:
        params["updated_since"] = updated_since
    return params


def _allocations(items: list[dict[str, Any]] | None) -> list[AllocationRecord]:
    out: list[AllocationRecord] = []
    for a in items or []:
        out.append(
            AllocationRecord(
                id=str(a.get("id", "")),
                payment_id=str(a.get("payment_id", "")),
                invoice_id=str(a.get("invoice_id", "")),
                amount=_dec(a.get("amount")),
            )
        )
    return out


class DotmacSubClient:
    """HTTP client for the dotmac_sub API (bearer auth, ListResponse paging)."""

    API_PREFIX = "/api/v1"

    # Resilience tuning.
    _RETRY_BACKOFF_BASE = 0.5  # seconds; exponential base for retry sleeps
    _RETRY_BACKOFF_CAP = 10.0  # seconds; max backoff between retries
    _RETRY_AFTER_CAP = 60.0  # seconds; max honoured Retry-After on a 429
    _MAX_PAGES = 100_000  # pagination safety bound (guards an API ignoring offset)
    _SYNC_PAGE_SIZE = 500
    _SYNC_PAGE_DELAY_SECONDS = 1.0

    def __init__(self, config: DotmacSubConfig | None = None) -> None:
        self.config = config or DotmacSubConfig.from_settings()
        self._client: httpx.Client | None = None
        # Shared retry/transport engine (dotmac-integration-client). Policy:
        #   - 429  -> DotmacSubRateLimitError with Retry-After honoured (capped)
        #   - 5xx  -> _TransientServerError, retried with jittered backoff
        #   - connect/timeout transport failures retried; trip the circuit
        #   - auth/404/other typed errors raise immediately
        self._engine = IntegrationHttpClient(
            client_factory=lambda: self.client,
            response_handler=self._handle_response,
            backoff=exponential_backoff(
                base=self._RETRY_BACKOFF_BASE, cap=self._RETRY_BACKOFF_CAP
            ),
            max_attempts=self.config.max_retries,
            rate_limit_exc=DotmacSubRateLimitError,
            retryable_excs=(_TransientServerError,),
            non_retryable_excs=(DotmacSubError,),
            loop_exhausted_factory=self._loop_exhausted_error,
            circuit=ReachabilityCircuit(cooldown_seconds=_circuit_cooldown_seconds()),
            auth_headers=lambda: {"X-Api-Key": self._api_key()},
            edge="dotmac_sub",
            request_id_provider=_request_id_provider,
        )

    def __enter__(self) -> DotmacSubClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @property
    def client(self) -> httpx.Client:
        if not self.config.is_configured():
            raise DotmacSubError(
                "dotmac_sub not configured. Set DOTMAC_SUB_API_URL and either "
                "DOTMAC_SUB_API_TOKEN or username+password."
            )
        if self._client is None:
            self._client = httpx.Client(
                base_url=f"{self.config.api_url.rstrip('/')}{self.API_PREFIX}",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(self.config.timeout),
            )
        return self._client

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    # ---- Authentication ----

    def _api_key(self) -> str:
        """Return the configured dotmac_sub API key.

        Staff-credential login (username+password session scrape) was retired for
        security (audit S1). If no API key is configured we fail loudly rather
        than fall back to staff creds.
        """
        if not self.config.api_token:
            raise DotmacSubAuthenticationError(
                "dotmac_sub api_token is not configured. Staff-credential login "
                "has been retired — set DOTMAC_SUB_API_TOKEN (or the integration's "
                "api_key) to a scoped dotmac_sub API key."
            )
        return self.config.api_token

    def _parse_retry_after(self, header_value: str | None) -> float | None:
        """Parse a 429 ``Retry-After`` header, capped at ``_RETRY_AFTER_CAP``.

        Only the integer-seconds form is parsed (the common case); anything
        else returns ``None`` so the engine falls back to exponential backoff.
        """
        if header_value:
            try:
                return min(float(int(header_value)), self._RETRY_AFTER_CAP)
            except (ValueError, TypeError):
                pass
        return None

    def _handle_response(self, response: httpx.Response, *, endpoint: str) -> Any:
        """Map a dotmac_sub response to a parsed body or a typed error.

        Retry classification keys off the exception type: 429 raises the
        rate-limit error (with parsed ``retry_after``), 5xx raises the
        transient subclass; auth/404 raise immediately.
        """
        status = response.status_code
        if status in (401, 403):
            raise DotmacSubAuthenticationError(
                "Authentication failed for dotmac_sub.", status_code=status
            )
        if status == 404:
            raise DotmacSubNotFoundError(
                f"Resource not found: {endpoint}", status_code=404
            )
        if status == 429:
            raise DotmacSubRateLimitError(
                "Rate limit exceeded.",
                status_code=429,
                retry_after=self._parse_retry_after(
                    response.headers.get("Retry-After")
                ),
            )
        if status >= 500:
            raise _TransientServerError(f"Server error: {status}", status_code=status)
        response.raise_for_status()
        return response.json()

    def _loop_exhausted_error(self, exc: BaseException, retries: int) -> DotmacSubError:
        """Wrap engine give-up cases (429 exhaustion, circuit open)."""
        if isinstance(exc, DotmacSubRateLimitError):
            return DotmacSubRateLimitError(
                "Rate limit exceeded. Try again later.", status_code=429
            )
        return DotmacSubError(f"dotmac_sub temporarily unavailable: {exc}")

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Issue one logical request via the shared integration engine.

        The engine (``dotmac-integration-client``) owns the retry loop,
        jittered exponential backoff, 429 Retry-After honouring, the
        reachability circuit, X-Api-Key auth injection, and x-request-id
        propagation. This wrapper owns the metrics contract: exactly one
        ``observe_integration_request`` per logical call with the legacy
        status vocabulary, duration measured across all attempts.

        Behavioural deltas vs the old hand-rolled loop (accepted):
        - Backoff sleeps now carry up-to-0.25s of jitter.
        - A short reachability circuit (``DOTMAC_SUB_CIRCUIT_SECONDS``,
          default 30s) fails fast after a transport failure.
        - ``x-request-id`` is propagated from ERP's request context.
        - Transport retries cover connect + timeout failures; other
          ``httpx.RequestError`` subclasses (read/write/protocol errors) now
          fail fast instead of retrying, but still surface as the same
          ``DotmacSubError`` with a ``request_error`` metric.
        - Retried 429s emit one ``rate_limited`` metric per call rather than
          one per attempt, and 429 exhaustion sleeps once more before raising.
        """
        started_at = time.perf_counter()
        metric_status: str | None = None
        try:
            result = self._engine.request(
                method,
                endpoint,
                params=params,
                json_data=json,
                handler_kwargs={"endpoint": endpoint},
            )
            metric_status = "success"
            return result
        except _TransientServerError as e:
            metric_status = "server_error"
            raise DotmacSubError(
                f"Request failed after {self.config.max_retries} attempts: {e}"
            ) from e
        except DotmacSubAuthenticationError as e:
            # A response-mapped 401/403 counts as auth_error; a locally raised
            # missing-api-key error (status None) never reached the wire and,
            # as before, emits no metric.
            if e.status_code is not None:
                metric_status = "auth_error"
            raise
        except DotmacSubNotFoundError:
            metric_status = "not_found"
            raise
        except DotmacSubRateLimitError:
            metric_status = "rate_limited"
            raise
        except httpx.TimeoutException as e:
            metric_status = "timeout"
            raise DotmacSubError(
                f"Request failed after {self.config.max_retries} attempts: {e}"
            ) from e
        except httpx.RequestError as e:
            metric_status = "request_error"
            raise DotmacSubError(
                f"Request failed after {self.config.max_retries} attempts: {e}"
            ) from e
        finally:
            if metric_status is not None:
                observe_integration_request(
                    "dotmac_sub",
                    f"{method.upper()} {endpoint}",
                    metric_status,
                    max(time.perf_counter() - started_at, 0.0),
                )

    def _paginate(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        page_size: int = 100,
        page_delay: float = 0.0,
    ) -> Generator[dict[str, Any], None, None]:
        params = dict(params or {})
        offset = 0
        is_first_page = True
        page_count = 0
        while True:
            page_count += 1
            if page_count > self._MAX_PAGES:
                logger.error(
                    "dotmac_sub pagination exceeded %d pages for %s; aborting "
                    "(the API may be ignoring offset)",
                    self._MAX_PAGES,
                    endpoint,
                )
                break
            if not is_first_page and page_delay > 0:
                time.sleep(page_delay)
            is_first_page = False
            params["offset"] = offset
            params["limit"] = page_size
            data = self._request("GET", endpoint, params=params)
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("items", [])
            else:
                break
            if not items:
                break
            yield from items
            if len(items) < page_size:
                break
            offset += page_size

    def _sync_paginate(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Read one bounded integration feed without bursting the source API."""
        yield from self._paginate(
            endpoint,
            params=params,
            page_size=self._SYNC_PAGE_SIZE,
            page_delay=self._SYNC_PAGE_DELAY_SECONDS,
        )

    # ---- Staff accounts (ERP staff sync) ----

    def create_staff_account(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        role: str = "staff",
        roles: list[str] | None = None,
        send_invite: bool = True,
    ) -> dict[str, Any]:
        """Create (idempotently) + invite a dotmac_sub staff account.

        Requires the API key to carry ``rbac:assign``. Returns the endpoint's
        ``{id, email, is_active, created, invited}`` payload.
        """
        result = self._request(
            "POST",
            "/staff-accounts",
            json={
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "role": role,
                "roles": roles,
                "send_invite": send_invite,
            },
        )
        return dict(result) if isinstance(result, dict) else {}

    def get_staff_account(self, email: str) -> dict[str, Any] | None:
        """Look up a staff account by email; None when absent."""
        try:
            result = self._request("GET", "/staff-accounts", params={"email": email})
        except DotmacSubNotFoundError:
            return None
        return dict(result) if isinstance(result, dict) else None

    def set_staff_account_active(
        self, account_id: str, *, is_active: bool
    ) -> dict[str, Any]:
        """Activate/deactivate a staff account (deactivation revokes sessions)."""
        action = "activate" if is_active else "deactivate"
        result = self._request("POST", f"/staff-accounts/{account_id}/{action}")
        return dict(result) if isinstance(result, dict) else {}

    def set_staff_account_roles(
        self, account_id: str, *, roles: list[str]
    ) -> dict[str, Any]:
        """Replace only the role grants managed by ERP HR."""
        result = self._request(
            "PUT",
            f"/staff-accounts/{account_id}/roles",
            json={"roles": roles},
        )
        return dict(result) if isinstance(result, dict) else {}

    def get_resellers(
        self, *, updated_since: str | None = None
    ) -> Generator[ResellerRecord, None, None]:
        logger.info("Fetching dotmac_sub resellers")
        params = {"updated_since": updated_since} if updated_since else None
        for item in self._sync_paginate("/resellers/sync", params=params):
            yield ResellerRecord(
                id=str(item.get("id", "")),
                name=item.get("name", ""),
                code=item.get("code"),
                contact_email=item.get("contact_email"),
                contact_phone=item.get("contact_phone"),
                is_active=bool(item.get("is_active", True)),
                updated_at=item.get("updated_at"),
            )

    def _parse_subscriber(self, item: dict[str, Any]) -> SubscriberRecord:
        return SubscriberRecord(
            id=str(item.get("id", "")),
            first_name=item.get("first_name"),
            last_name=item.get("last_name"),
            display_name=item.get("display_name"),
            company_name=item.get("company_name"),
            legal_name=item.get("legal_name"),
            email=item.get("email"),
            phone=item.get("phone"),
            status=item.get("status"),
            category=item.get("category"),
            is_active=bool(item.get("is_active", True)),
            reseller_id=item.get("reseller_id"),
            tax_id=item.get("tax_id"),
            subscriber_number=item.get("subscriber_number"),
            account_number=item.get("account_number"),
            address_line1=item.get("address_line1"),
            address_line2=item.get("address_line2"),
            city=item.get("city"),
            region=item.get("region"),
            postal_code=item.get("postal_code"),
            country_code=item.get("country_code"),
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
        )

    def get_subscribers(
        self,
        subscriber_type: str | None = None,
        *,
        updated_since: str | None = None,
    ) -> Generator[SubscriberRecord, None, None]:
        params: dict[str, Any] = {}
        if subscriber_type:
            params["subscriber_type"] = subscriber_type
        if updated_since:
            params["updated_since"] = updated_since
        logger.info("Fetching dotmac_sub subscribers with params: %s", params)
        for item in self._sync_paginate("/subscribers/sync", params=params):
            yield self._parse_subscriber(item)

    def get_subscriber(self, subscriber_id: str) -> SubscriberRecord:
        return self._parse_subscriber(
            self._request("GET", f"/subscribers/{subscriber_id}")
        )

    def _parse_billing_account(self, item: dict[str, Any]) -> BillingAccountRecord:
        return BillingAccountRecord(
            id=str(item.get("id", "")),
            reseller_id=str(item.get("reseller_id", "")),
            name=item.get("name", ""),
            currency=item.get("currency", settings.default_functional_currency_code),
            status=item.get("status", ""),
            balance=_dec(item.get("balance")),
            is_active=bool(item.get("is_active", True)),
            updated_at=item.get("updated_at"),
        )

    def get_billing_accounts(
        self, reseller_id: str | None = None
    ) -> Generator[BillingAccountRecord, None, None]:
        params: dict[str, Any] = {}
        if reseller_id:
            params["reseller_id"] = reseller_id
        logger.info("Fetching dotmac_sub billing accounts with params: %s", params)
        for item in self._sync_paginate("/billing-accounts/sync", params=params):
            yield self._parse_billing_account(item)

    def get_billing_account(self, billing_account_id: str) -> BillingAccountRecord:
        """Fetch a single billing account by id (used to resolve its reseller)."""
        return self._parse_billing_account(
            self._request("GET", f"/billing-accounts/{billing_account_id}")
        )

    def _parse_invoice(self, item: dict[str, Any]) -> InvoiceRecord:
        lines = [
            InvoiceLineRecord(
                id=str(line.get("id", "")),
                description=line.get("description", ""),
                quantity=_dec(line.get("quantity"), "1"),
                unit_price=_dec(line.get("unit_price")),
                amount=_dec(line.get("amount")),
                tax_rate_id=line.get("tax_rate_id"),
                tax_application=line.get("tax_application", "exclusive"),
            )
            for line in item.get("lines", [])
        ]
        return InvoiceRecord(
            id=str(item.get("id", "")),
            account_id=str(item.get("account_id", "")),
            invoice_number=item.get("invoice_number"),
            status=item.get("status", ""),
            currency=item.get("currency", settings.default_functional_currency_code),
            subtotal=_dec(item.get("subtotal")),
            tax_total=_dec(item.get("tax_total")),
            total=_dec(item.get("total")),
            balance_due=_dec(item.get("balance_due")),
            issued_at=item.get("issued_at"),
            due_at=item.get("due_at"),
            paid_at=item.get("paid_at"),
            memo=item.get("memo"),
            is_proforma=bool(item.get("is_proforma", False)),
            updated_at=item.get("updated_at"),
            lines=lines,
            allocations=_allocations(item.get("payment_allocations")),
        )

    def get_invoices(
        self,
        account_id: str | None = None,
        status: str | None = None,
        *,
        updated_since: str | None = None,
    ) -> Generator[InvoiceRecord, None, None]:
        params = _watermark_params(account_id, status, updated_since)
        logger.info("Fetching dotmac_sub invoices with params: %s", params)
        # Bulk AR pulls use Sub's sync-specific projection: it includes invoice
        # lines but omits payment allocations and UI/detail fields. A larger page
        # keeps the initial backfill efficient without making Sub hydrate the
        # expensive full InvoiceRead graph for every row.
        for item in self._sync_paginate("/invoices/sync", params=params):
            yield self._parse_invoice(item)

    def get_invoice(self, invoice_id: str) -> InvoiceRecord:
        return self._parse_invoice(self._request("GET", f"/invoices/{invoice_id}"))

    def _parse_payment(self, item: dict[str, Any]) -> PaymentRecord:
        return PaymentRecord(
            id=str(item.get("id", "")),
            account_id=item.get("account_id"),
            billing_account_id=item.get("billing_account_id"),
            amount=_dec(item.get("amount")),
            refunded_amount=_dec(item.get("refunded_amount")),
            gross_amount=_dec(item.get("gross_amount"))
            if item.get("gross_amount") is not None
            else None,
            net_amount=_dec(item.get("net_amount"))
            if item.get("net_amount") is not None
            else None,
            wht_amount=_dec(item.get("wht_amount")),
            wht_rate=_dec(item.get("wht_rate"))
            if item.get("wht_rate") is not None
            else None,
            wht_status=item.get("wht_status"),
            wht_record_id=item.get("wht_record_id"),
            wht_certificate_reference=item.get("wht_certificate_reference"),
            wht_resolved_at=item.get("wht_resolved_at"),
            currency=item.get("currency", settings.default_functional_currency_code),
            status=item.get("status", ""),
            paid_at=item.get("paid_at"),
            external_id=item.get("external_id"),
            memo=item.get("memo"),
            payment_method_id=item.get("payment_method_id"),
            payment_channel_id=item.get("payment_channel_id"),
            updated_at=item.get("updated_at"),
            allocations=_allocations(item.get("allocations")),
        )

    def get_payments(
        self,
        account_id: str | None = None,
        status: str | None = None,
        *,
        updated_since: str | None = None,
    ) -> Generator[PaymentRecord, None, None]:
        params = _watermark_params(account_id, status, updated_since)
        logger.info("Fetching dotmac_sub payments with params: %s", params)
        for item in self._sync_paginate("/payments/sync", params=params):
            yield self._parse_payment(item)

    def get_payment(self, payment_id: str) -> PaymentRecord:
        return self._parse_payment(self._request("GET", f"/payments/{payment_id}"))

    def _parse_credit_note(self, item: dict[str, Any]) -> CreditNoteRecord:
        lines = [
            CreditNoteLineRecord(
                id=str(line.get("id", "")),
                description=line.get("description", ""),
                quantity=_dec(line.get("quantity"), "1"),
                unit_price=_dec(line.get("unit_price")),
                amount=_dec(line.get("amount")),
                tax_rate_id=line.get("tax_rate_id"),
                tax_application=line.get("tax_application", "exclusive"),
            )
            for line in item.get("lines", [])
        ]
        return CreditNoteRecord(
            id=str(item.get("id", "")),
            account_id=str(item.get("account_id", "")),
            invoice_id=item.get("invoice_id"),
            credit_number=item.get("credit_number"),
            status=item.get("status", ""),
            currency=item.get("currency", settings.default_functional_currency_code),
            subtotal=_dec(item.get("subtotal")),
            tax_total=_dec(item.get("tax_total")),
            total=_dec(item.get("total")),
            applied_total=_dec(item.get("applied_total")),
            memo=item.get("memo"),
            issued_at=item.get("issued_at") or item.get("created_at"),
            updated_at=item.get("updated_at"),
            lines=lines,
        )

    def get_credit_notes(
        self,
        account_id: str | None = None,
        status: str | None = None,
        *,
        updated_since: str | None = None,
    ) -> Generator[CreditNoteRecord, None, None]:
        params = _watermark_params(account_id, status, updated_since)
        logger.info("Fetching dotmac_sub credit notes with params: %s", params)
        for item in self._sync_paginate("/credit-notes/sync", params=params):
            yield self._parse_credit_note(item)

    def get_tax_rates(self) -> list[TaxRateRecord]:
        rates: list[TaxRateRecord] = []
        for item in self._sync_paginate("/tax-rates/sync"):
            rates.append(
                TaxRateRecord(
                    id=str(item.get("id", "")),
                    name=item.get("name", ""),
                    rate=_dec(item.get("rate")),
                    code=item.get("code"),
                    is_active=bool(item.get("is_active", True)),
                )
            )
        return rates

    def get_payment_channels(self) -> Generator[dict[str, Any], None, None]:
        yield from self._sync_paginate("/payment-channels/sync")

    def test_connection(self) -> bool:
        try:
            self._request("GET", "/subscribers/sync", params={"limit": 1})
            return True
        except DotmacSubError as e:
            logger.error("dotmac_sub connection test failed: %s", e.message)
            return False
