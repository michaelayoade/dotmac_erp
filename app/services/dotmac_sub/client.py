"""
dotmac_sub API Client.

HTTP client for the dotmac_sub subscriber-management system at
``selfcare.dotmac.io``. Replaces the legacy Splynx ISP-billing feed.

- **Auth**: static bearer token (``Authorization: Bearer <token>``).
- **API**: REST under ``/api/v1`` with ``?limit=&offset=`` pagination wrapped as
  ``{"items": [...], "count", "limit", "offset"}``.
- **Domain**: ``Reseller -> Subscriber -> BillingAccount`` with
  invoices/payments/credit-notes keyed on the *billing account* (``account_id``).
- **Allocations are inline** on invoices and payments — no ledger re-fetch.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings
from app.metrics import categorize_http_status, observe_integration_request

logger = logging.getLogger(__name__)


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
    """Rate limit exceeded."""


@dataclass
class DotmacSubConfig:
    """Configuration for the dotmac_sub API.

    Two auth modes (api_token takes precedence):
    - ``api_token`` — a static bearer token (e.g. a service JWT), if provided.
    - ``username`` + ``password`` — staff credentials; the client performs a
      session login + ``/api/v1/auth/refresh`` to obtain a short-lived JWT and
      auto-refreshes on 401. This is the "passwordless" mode from the operator's
      view: the password is entered once (encrypted at rest), never per-sync.
    """

    api_url: str
    api_token: str = ""
    username: str = ""
    password: str = ""
    timeout: float = 60.0
    max_retries: int = 3

    @classmethod
    def from_settings(cls) -> DotmacSubConfig:
        """Create config from application settings (env fallback / bootstrap)."""
        return cls(
            api_url=settings.dotmac_sub_api_url,
            api_token=settings.dotmac_sub_api_token,
            username=getattr(settings, "dotmac_sub_username", "") or "",
            password=getattr(settings, "dotmac_sub_password", "") or "",
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
        #   company    -> staff username (not secret)
        #   api_key    -> staff password OR a static bearer token (encrypted)
        #   api_secret -> webhook secret (used by the inbound webhook route)
        # If ``company`` (username) is set we treat api_key as the password;
        # otherwise api_key is a static bearer token.
        username = creds.get("company") or env.username
        secret = creds.get("api_key") or ""
        if username:
            return cls(
                api_url=creds.get("base_url") or env.api_url,
                api_token=env.api_token,
                username=username,
                password=secret or env.password,
                timeout=env.timeout,
                max_retries=env.max_retries,
            )
        return cls(
            api_url=creds.get("base_url") or env.api_url,
            api_token=secret or env.api_token,
            username=env.username,
            password=env.password,
            timeout=env.timeout,
            max_retries=env.max_retries,
        )

    def is_configured(self) -> bool:
        """Check if dotmac_sub credentials are configured (token or login)."""
        if not self.api_url:
            return False
        return bool(self.api_token or (self.username and self.password))

    @property
    def auth_header(self) -> str:
        """Generate the Bearer auth header value."""
        return f"Bearer {self.api_token}"


@dataclass
class ResellerRecord:
    """Reseller (parent account) from dotmac_sub."""

    id: str
    name: str
    code: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    is_active: bool = True


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


@dataclass
class InvoiceLineRecord:
    """Invoice line item."""

    id: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    tax_rate_id: str | None = None


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
    paid_at: str | None = None
    external_id: str | None = None
    memo: str | None = None
    payment_method_id: str | None = None
    payment_channel_id: str | None = None
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
    lines: list[CreditNoteLineRecord] = field(default_factory=list)


@dataclass
class TaxRateRecord:
    """Tax rate from dotmac_sub."""

    id: str
    name: str
    rate: Decimal


def _first_match(text: str, *patterns: str) -> str | None:
    """Return the first regex group-1 match across ``patterns`` (or None)."""
    import re

    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def _dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        logger.warning("Could not parse decimal: %r", value)
        return Decimal(default)


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

    def __init__(self, config: DotmacSubConfig | None = None) -> None:
        self.config = config or DotmacSubConfig.from_settings()
        self._client: httpx.Client | None = None
        self._bearer: str | None = None

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

    def _bearer_token(self) -> str:
        """Return a bearer token, logging in with credentials if necessary."""
        if self.config.api_token:
            return self.config.api_token
        if self._bearer:
            return self._bearer
        self._bearer = self._login_for_jwt()
        return self._bearer

    def _login_for_jwt(self) -> str:
        """Staff session login + /api/v1/auth/refresh → short-lived JWT.

        dotmac_sub staff accounts authenticate by session (the bearer middleware
        only accepts JWTs / subscriber API keys), so we log in at ``/auth/login``
        (CSRF-protected form), then exchange the session for an access token.
        """
        base = self.config.api_url.rstrip("/")
        with httpx.Client(timeout=httpx.Timeout(self.config.timeout)) as sess:
            page = sess.get(f"{base}/auth/login")
            html = page.text
            form_csrf = _first_match(
                html,
                r'name="_csrf_token"[^>]*value="([^"]+)"',
                r'value="([^"]+)"[^>]*name="_csrf_token"',
            )
            meta_csrf = _first_match(
                html,
                r'name="csrf-token"[^>]*content="([^"]+)"',
                r'content="([^"]+)"[^>]*name="csrf-token"',
            )
            login = sess.post(
                f"{base}/auth/login",
                data={
                    "username": self.config.username,
                    "password": self.config.password,
                    "_csrf_token": form_csrf or "",
                    "remember": "true",
                },
                follow_redirects=False,
            )
            if login.status_code not in (200, 302, 303):
                raise DotmacSubAuthenticationError(
                    f"dotmac_sub staff login failed (HTTP {login.status_code})",
                    status_code=login.status_code,
                )
            headers = {"X-CSRF-Token": meta_csrf} if meta_csrf else {}
            refresh = sess.post(
                f"{base}{self.API_PREFIX}/auth/refresh", json={}, headers=headers
            )
            token = (
                refresh.json().get("access_token")
                if refresh.status_code == 200
                else None
            )
            if not token:
                raise DotmacSubAuthenticationError(
                    "dotmac_sub token refresh returned no access_token",
                    status_code=refresh.status_code,
                )
            return str(token)

    def _backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff delay (seconds) for retry ``attempt`` (0-indexed)."""
        delay = self._RETRY_BACKOFF_BASE * (2.0**attempt)
        return float(min(delay, self._RETRY_BACKOFF_CAP))

    def _retry_after_seconds(self, header_value: str | None, attempt: int) -> float:
        """Seconds to wait after a 429, honouring ``Retry-After`` when present.

        Only the integer-seconds form of ``Retry-After`` is parsed (the common
        case); anything else falls back to exponential backoff.
        """
        if header_value:
            try:
                return min(float(int(header_value)), self._RETRY_AFTER_CAP)
            except (ValueError, TypeError):
                pass
        return self._backoff_seconds(attempt)

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        last_error: Exception | None = None
        started_at = time.perf_counter()
        metric_status = "unknown"

        for attempt in range(self.config.max_retries):
            try:
                headers = {"Authorization": f"Bearer {self._bearer_token()}"}
                response = self.client.request(
                    method=method,
                    url=endpoint,
                    params=params,
                    json=json,
                    headers=headers,
                )
                if response.status_code in (401, 403):
                    metric_status = "auth_error"
                    # In credential (login) mode a 401 may mean the JWT expired:
                    # drop the cached token and retry once with a fresh login.
                    if (
                        not self.config.api_token
                        and attempt < self.config.max_retries - 1
                    ):
                        self._bearer = None
                        last_error = DotmacSubAuthenticationError(
                            "JWT expired; re-authenticating",
                            status_code=response.status_code,
                        )
                        continue
                    raise DotmacSubAuthenticationError(
                        "Authentication failed for dotmac_sub.",
                        status_code=response.status_code,
                    )
                elif response.status_code == 404:
                    metric_status = "not_found"
                    raise DotmacSubNotFoundError(
                        f"Resource not found: {endpoint}", status_code=404
                    )
                elif response.status_code == 429:
                    metric_status = "rate_limited"
                    if attempt < self.config.max_retries - 1:
                        delay = self._retry_after_seconds(
                            response.headers.get("Retry-After"), attempt
                        )
                        logger.warning(
                            "dotmac_sub rate limited (attempt %d/%d); "
                            "retrying in %.1fs: %s",
                            attempt + 1,
                            self.config.max_retries,
                            delay,
                            endpoint,
                        )
                        last_error = DotmacSubRateLimitError(
                            "Rate limit exceeded.", status_code=429
                        )
                        time.sleep(delay)
                        continue
                    raise DotmacSubRateLimitError(
                        "Rate limit exceeded. Try again later.", status_code=429
                    )
                elif response.status_code >= 500:
                    metric_status = "server_error"
                    raise DotmacSubError(
                        f"Server error: {response.status_code}",
                        status_code=response.status_code,
                    )

                response.raise_for_status()
                metric_status = categorize_http_status(response.status_code)
                return response.json()

            except httpx.TimeoutException as e:
                metric_status = "timeout"
                last_error = e
                logger.warning(
                    "dotmac_sub request timeout (attempt %d/%d): %s",
                    attempt + 1,
                    self.config.max_retries,
                    endpoint,
                )
                if attempt < self.config.max_retries - 1:
                    time.sleep(self._backoff_seconds(attempt))
            except httpx.RequestError as e:
                metric_status = "request_error"
                last_error = e
                logger.warning(
                    "dotmac_sub request error (attempt %d/%d): %s - %s",
                    attempt + 1,
                    self.config.max_retries,
                    endpoint,
                    str(e),
                )
                if attempt < self.config.max_retries - 1:
                    time.sleep(self._backoff_seconds(attempt))
            except DotmacSubRateLimitError:
                raise
            except DotmacSubError as e:
                metric_status = (
                    categorize_http_status(e.status_code)
                    if e.status_code is not None
                    else "request_error"
                )
                if e.status_code and e.status_code >= 500:
                    last_error = e
                    logger.warning(
                        "dotmac_sub server error (attempt %d/%d): %s",
                        attempt + 1,
                        self.config.max_retries,
                        e.message,
                    )
                    if attempt < self.config.max_retries - 1:
                        time.sleep(self._backoff_seconds(attempt))
                else:
                    raise
            finally:
                if attempt == self.config.max_retries - 1 or metric_status in {
                    "success",
                    "auth_error",
                    "not_found",
                    "rate_limited",
                    "client_error",
                }:
                    observe_integration_request(
                        "dotmac_sub",
                        f"{method.upper()} {endpoint}",
                        metric_status,
                        max(time.perf_counter() - started_at, 0.0),
                    )

        raise DotmacSubError(
            f"Request failed after {self.config.max_retries} attempts: {last_error}"
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

    def get_resellers(self) -> Generator[ResellerRecord, None, None]:
        logger.info("Fetching dotmac_sub resellers")
        for item in self._paginate("/resellers"):
            yield ResellerRecord(
                id=str(item.get("id", "")),
                name=item.get("name", ""),
                code=item.get("code"),
                contact_email=item.get("contact_email"),
                contact_phone=item.get("contact_phone"),
                is_active=bool(item.get("is_active", True)),
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
        self, subscriber_type: str | None = None
    ) -> Generator[SubscriberRecord, None, None]:
        params: dict[str, Any] = {}
        if subscriber_type:
            params["subscriber_type"] = subscriber_type
        logger.info("Fetching dotmac_sub subscribers with params: %s", params)
        for item in self._paginate("/subscribers", params=params):
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
        )

    def get_billing_accounts(
        self, reseller_id: str | None = None
    ) -> Generator[BillingAccountRecord, None, None]:
        params: dict[str, Any] = {}
        if reseller_id:
            params["reseller_id"] = reseller_id
        logger.info("Fetching dotmac_sub billing accounts with params: %s", params)
        for item in self._paginate("/billing-accounts", params=params):
            yield self._parse_billing_account(item)

    def get_billing_account(self, billing_account_id: str) -> BillingAccountRecord:
        """Fetch a single billing account by id (used to resolve its reseller)."""
        return self._parse_billing_account(
            self._request("GET", f"/billing-accounts/{billing_account_id}")
        )

    def get_subscriptions(
        self, account_id: str | None = None
    ) -> Generator[dict[str, Any], None, None]:
        params: dict[str, Any] = {}
        if account_id:
            params["account_id"] = account_id
        yield from self._paginate("/subscriptions", params=params)

    def _parse_invoice(self, item: dict[str, Any]) -> InvoiceRecord:
        lines = [
            InvoiceLineRecord(
                id=str(line.get("id", "")),
                description=line.get("description", ""),
                quantity=_dec(line.get("quantity"), "1"),
                unit_price=_dec(line.get("unit_price")),
                amount=_dec(line.get("amount")),
                tax_rate_id=line.get("tax_rate_id"),
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
            lines=lines,
            allocations=_allocations(item.get("payment_allocations")),
        )

    def get_invoices(
        self, account_id: str | None = None, status: str | None = None
    ) -> Generator[InvoiceRecord, None, None]:
        params: dict[str, Any] = {}
        if account_id:
            params["account_id"] = account_id
        if status:
            params["status"] = status
        logger.info("Fetching dotmac_sub invoices with params: %s", params)
        for item in self._paginate("/invoices", params=params):
            yield self._parse_invoice(item)

    def get_invoice(self, invoice_id: str) -> InvoiceRecord:
        return self._parse_invoice(self._request("GET", f"/invoices/{invoice_id}"))

    def _parse_payment(self, item: dict[str, Any]) -> PaymentRecord:
        return PaymentRecord(
            id=str(item.get("id", "")),
            account_id=item.get("account_id"),
            billing_account_id=item.get("billing_account_id"),
            amount=_dec(item.get("amount")),
            currency=item.get("currency", settings.default_functional_currency_code),
            status=item.get("status", ""),
            paid_at=item.get("paid_at"),
            external_id=item.get("external_id"),
            memo=item.get("memo"),
            payment_method_id=item.get("payment_method_id"),
            payment_channel_id=item.get("payment_channel_id"),
            allocations=_allocations(item.get("allocations")),
        )

    def get_payments(
        self, account_id: str | None = None, status: str | None = None
    ) -> Generator[PaymentRecord, None, None]:
        params: dict[str, Any] = {}
        if account_id:
            params["account_id"] = account_id
        if status:
            params["status"] = status
        logger.info("Fetching dotmac_sub payments with params: %s", params)
        for item in self._paginate("/payments", params=params):
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
            lines=lines,
        )

    def get_credit_notes(
        self, account_id: str | None = None, status: str | None = None
    ) -> Generator[CreditNoteRecord, None, None]:
        params: dict[str, Any] = {}
        if account_id:
            params["account_id"] = account_id
        if status:
            params["status"] = status
        logger.info("Fetching dotmac_sub credit notes with params: %s", params)
        for item in self._paginate("/credit-notes", params=params):
            yield self._parse_credit_note(item)

    def get_tax_rates(self) -> list[TaxRateRecord]:
        rates: list[TaxRateRecord] = []
        for item in self._paginate("/tax-rates"):
            rates.append(
                TaxRateRecord(
                    id=str(item.get("id", "")),
                    name=item.get("name", ""),
                    rate=_dec(item.get("rate")),
                )
            )
        return rates

    def test_connection(self) -> bool:
        try:
            self._request("GET", "/subscribers", params={"limit": 1})
            return True
        except DotmacSubError as e:
            logger.error("dotmac_sub connection test failed: %s", e.message)
            return False
