"""
Splynx API Client.

Handles all HTTP communication with Splynx API for ISP billing data.
"""

import base64
import logging
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class SplynxError(Exception):
    """Splynx API error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SplynxAuthenticationError(SplynxError):
    """Authentication failed."""

    pass


class SplynxNotFoundError(SplynxError):
    """Resource not found."""

    pass


class SplynxRateLimitError(SplynxError):
    """Rate limit exceeded."""

    pass


@dataclass
class SplynxConfig:
    """Configuration for Splynx API."""

    api_url: str
    api_key: str
    api_secret: str
    timeout: float = 60.0
    max_retries: int = 3

    @classmethod
    def from_settings(cls) -> "SplynxConfig":
        """Create config from application settings."""
        return cls(
            api_url=settings.splynx_api_url,
            api_key=settings.splynx_api_key,
            api_secret=settings.splynx_api_secret,
            timeout=settings.splynx_request_timeout,
            max_retries=settings.splynx_max_retries,
        )

    def is_configured(self) -> bool:
        """Check if Splynx credentials are configured."""
        return bool(self.api_key and self.api_secret)

    @property
    def auth_header(self) -> str:
        """Generate Basic auth header value."""
        credentials = f"{self.api_key}:{self.api_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"


# =============================================================================
# Response Dataclasses
# =============================================================================


@dataclass
class SplynxCustomer:
    """Customer record from Splynx."""

    id: int
    login: str
    name: str
    email: str
    phone: str
    status: str  # active, inactive, blocked
    partner_id: int
    location_id: int
    street_1: str | None = None
    street_2: str | None = None
    city: str | None = None
    zip_code: str | None = None
    date_add: str | None = None
    company: str | None = None
    billing_type: str | None = None
    category: str | None = None


@dataclass
class SplynxInvoice:
    """Invoice record from Splynx."""

    id: int
    number: str
    customer_id: int
    date_created: str
    date_till: str  # Due date
    status: str  # paid, unpaid, partially_paid
    total: Decimal
    total_due: Decimal  # 'due' in API
    currency: str = "NGN"
    items: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None
    memo: str | None = None
    payment_id: int | None = None


@dataclass
class SplynxPaymentMethod:
    """Payment method from Splynx."""

    id: int
    name: str
    is_active: bool
    accounting_bank_account_id: int | None = None


@dataclass
class SplynxPayment:
    """Payment record from Splynx."""

    id: int
    customer_id: int
    customer_name: str
    invoice_id: int | None
    date: str
    amount: Decimal
    payment_type: int  # Payment method ID
    receipt_number: str | None = None
    comment: str | None = None
    reference: str | None = None  # field_1 often contains bank reference


@dataclass
class SplynxCreditNote:
    """Credit note record from Splynx."""

    id: int
    number: str
    customer_id: int
    customer_name: str
    date_created: str
    total: Decimal
    status: str
    note: str | None = None
    items: list[dict[str, Any]] = field(default_factory=list)


# =============================================================================
# API Client
# =============================================================================


class SplynxClient:
    """
    HTTP client for Splynx API.

    Handles authentication, pagination, and error handling.
    """

    API_VERSION = "2.0"

    def __init__(self, config: SplynxConfig | None = None):
        self.config = config or SplynxConfig.from_settings()
        self._client: httpx.Client | None = None

    def __enter__(self) -> "SplynxClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if not self.config.is_configured():
            raise SplynxError(
                "Splynx not configured. Set SPLYNX_API_KEY and SPLYNX_API_SECRET."
            )

        if self._client is None:
            self._client = httpx.Client(
                base_url=f"{self.config.api_url}/api/{self.API_VERSION}/admin",
                headers={
                    "Authorization": self.config.auth_header,
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.config.timeout),
            )
        return self._client

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Make an HTTP request with error handling and retries."""
        last_error: Exception | None = None

        for attempt in range(self.config.max_retries):
            try:
                response = self.client.request(
                    method=method,
                    url=endpoint,
                    params=params,
                    json=json,
                )

                if response.status_code == 401:
                    raise SplynxAuthenticationError(
                        "Authentication failed. Check API credentials.",
                        status_code=401,
                    )
                elif response.status_code == 404:
                    raise SplynxNotFoundError(
                        f"Resource not found: {endpoint}",
                        status_code=404,
                    )
                elif response.status_code == 429:
                    raise SplynxRateLimitError(
                        "Rate limit exceeded. Try again later.",
                        status_code=429,
                    )
                elif response.status_code >= 500:
                    # Server error - retry
                    raise SplynxError(
                        f"Server error: {response.status_code}",
                        status_code=response.status_code,
                    )

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    "Splynx request timeout (attempt %d/%d): %s",
                    attempt + 1,
                    self.config.max_retries,
                    endpoint,
                )
            except httpx.RequestError as e:
                last_error = e
                logger.warning(
                    "Splynx request error (attempt %d/%d): %s - %s",
                    attempt + 1,
                    self.config.max_retries,
                    endpoint,
                    str(e),
                )
            except SplynxRateLimitError:
                raise
            except SplynxError as e:
                if e.status_code and e.status_code >= 500:
                    last_error = e
                    logger.warning(
                        "Splynx server error (attempt %d/%d): %s",
                        attempt + 1,
                        self.config.max_retries,
                        e.message,
                    )
                else:
                    raise

        raise SplynxError(
            f"Request failed after {self.config.max_retries} attempts: {last_error}"
        )

    def _paginate(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        page_size: int = 100,
        page_delay: float = 0.0,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Paginate through API results.

        Splynx uses offset/limit pagination.

        Args:
            endpoint: API path to fetch
            params: Query parameters
            page_size: Number of records per page
            page_delay: Seconds to sleep between pages (rate-limit courtesy)
        """
        import time

        params = params or {}
        offset = 0
        is_first_page = True

        while True:
            if not is_first_page and page_delay > 0:
                time.sleep(page_delay)
            is_first_page = False

            params["offset"] = offset
            params["limit"] = page_size

            data = self._request("GET", endpoint, params=params)

            if not data:
                break

            # Handle both list response and dict with data key
            items = data if isinstance(data, list) else data.get("data", [])

            if not items:
                break

            yield from items

            if len(items) < page_size:
                break

            offset += page_size

    # =========================================================================
    # Customer Methods
    # =========================================================================

    def get_customers(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
    ) -> Generator[SplynxCustomer, None, None]:
        """
        Fetch all customers with optional filters.

        Args:
            date_from: Filter customers created after this date
            date_to: Filter customers created before this date
            status: Filter by status (active, inactive, blocked)
        """
        params: dict[str, Any] = {}

        if date_from:
            params["date_add_from"] = date_from.isoformat()
        if date_to:
            params["date_add_to"] = date_to.isoformat()
        if status:
            params["status"] = status

        logger.info("Fetching Splynx customers with params: %s", params)

        for item in self._paginate("/customers/customer", params=params):
            yield SplynxCustomer(
                id=int(item.get("id", 0)),
                login=item.get("login", ""),
                name=item.get("name", ""),
                email=item.get("email", ""),
                phone=item.get("phone", ""),
                status=item.get("status", ""),
                partner_id=int(item.get("partner_id", 0)),
                location_id=int(item.get("location_id", 0)),
                street_1=item.get("street_1"),
                street_2=item.get("street_2"),
                city=item.get("city"),
                zip_code=item.get("zip_code"),
                date_add=item.get("date_add"),
                company=item.get("company"),
                billing_type=item.get("billing_type"),
                category=item.get("category"),
            )

    def get_customer(self, customer_id: int) -> SplynxCustomer:
        """Fetch a single customer by ID."""
        data = self._request("GET", f"/customers/customer/{customer_id}")
        return SplynxCustomer(
            id=int(data.get("id", 0)),
            login=data.get("login", ""),
            name=data.get("name", ""),
            email=data.get("email", ""),
            phone=data.get("phone", ""),
            status=data.get("status", ""),
            partner_id=int(data.get("partner_id", 0)),
            location_id=int(data.get("location_id", 0)),
            street_1=data.get("street_1"),
            street_2=data.get("street_2"),
            city=data.get("city"),
            zip_code=data.get("zip_code"),
            date_add=data.get("date_add"),
            company=data.get("company"),
            billing_type=data.get("billing_type"),
            category=data.get("category"),
        )

    # =========================================================================
    # Invoice Methods
    # =========================================================================

    def get_invoices(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
        customer_id: int | None = None,
    ) -> Generator[SplynxInvoice, None, None]:
        """
        Fetch all invoices with optional filters.

        Args:
            date_from: Filter invoices created after this date
            date_to: Filter invoices created before this date
            status: Filter by status (paid, unpaid, partially_paid)
            customer_id: Filter by customer ID
        """
        params: dict[str, Any] = {}

        if date_from:
            params["date_from"] = date_from.isoformat()
        if date_to:
            params["date_to"] = date_to.isoformat()
        if status:
            params["status"] = status
        if customer_id:
            params["customer_id"] = customer_id

        logger.info("Fetching Splynx invoices with params: %s", params)

        for item in self._paginate("/finance/invoices", params=params):
            yield SplynxInvoice(
                id=int(item.get("id", 0)),
                number=item.get("number", ""),
                customer_id=int(item.get("customer_id", 0)),
                date_created=item.get("date_created", ""),
                date_till=item.get("date_till", item.get("date_created", "")),
                status=item.get("status", ""),
                total=Decimal(str(item.get("total", 0))),
                total_due=Decimal(str(item.get("due", 0))),
                currency="NGN",  # Splynx doesn't return currency, default to NGN
                items=item.get("items", []),
                note=item.get("note"),
                memo=item.get("memo"),
                payment_id=int(item["payment_id"]) if item.get("payment_id") else None,
            )

    def get_invoice(self, invoice_id: int) -> SplynxInvoice:
        """Fetch a single invoice by ID."""
        data = self._request("GET", f"/finance/invoices/{invoice_id}")
        return SplynxInvoice(
            id=int(data.get("id", 0)),
            number=data.get("number", ""),
            customer_id=int(data.get("customer_id", 0)),
            date_created=data.get("date_created", ""),
            date_till=data.get("date_till", data.get("date_created", "")),
            status=data.get("status", ""),
            total=Decimal(str(data.get("total", 0))),
            total_due=Decimal(str(data.get("due", 0))),
            currency="NGN",
            items=data.get("items", []),
            note=data.get("note"),
            memo=data.get("memo"),
            payment_id=int(data["payment_id"]) if data.get("payment_id") else None,
        )

    # =========================================================================
    # Payment Methods
    # =========================================================================

    def get_payment_methods(self) -> list[SplynxPaymentMethod]:
        """
        Fetch all payment methods from Splynx.

        Returns:
            List of payment methods with their bank account mappings.
        """
        data = self._request("GET", "/finance/payment-methods")

        methods = []
        for item in data:
            methods.append(
                SplynxPaymentMethod(
                    id=int(item.get("id", 0)),
                    name=item.get("name", ""),
                    is_active=item.get("is_active") == "1",
                    accounting_bank_account_id=(
                        int(item["accounting_bank_accounts_id"])
                        if item.get("accounting_bank_accounts_id")
                        else None
                    ),
                )
            )

        logger.info("Fetched %d payment methods from Splynx", len(methods))
        return methods

    def get_payments(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        customer_id: int | None = None,
    ) -> Generator[SplynxPayment, None, None]:
        """
        Fetch all payments with optional filters.

        Args:
            date_from: Filter payments after this date
            date_to: Filter payments before this date
            customer_id: Filter by customer ID
        """
        params: dict[str, Any] = {}

        if date_from:
            params["date_from"] = date_from.isoformat()
        if date_to:
            params["date_to"] = date_to.isoformat()
        if customer_id:
            params["customer_id"] = customer_id

        logger.info("Fetching Splynx payments with params: %s", params)

        for item in self._paginate("/finance/payments", params=params):
            yield SplynxPayment(
                id=int(item.get("id", 0)),
                customer_id=int(item.get("customer_id", 0)),
                customer_name=item.get("customer_name", ""),
                invoice_id=int(item["invoice_id"]) if item.get("invoice_id") else None,
                date=item.get("date", ""),
                amount=Decimal(str(item.get("amount", 0))),
                payment_type=int(item.get("payment_type", 0)),
                receipt_number=item.get("receipt_number"),
                comment=item.get("comment"),
                reference=item.get("field_1")
                or item.get("field_2"),  # Bank ref often in field_1/2
            )

    # =========================================================================
    # Credit Note Methods
    # =========================================================================

    def get_credit_notes(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        customer_id: int | None = None,
    ) -> Generator[SplynxCreditNote, None, None]:
        """
        Fetch all credit notes with optional filters.

        Args:
            date_from: Filter credit notes after this date
            date_to: Filter credit notes before this date
            customer_id: Filter by customer ID
        """
        params: dict[str, Any] = {}

        if date_from:
            params["date_from"] = date_from.isoformat()
        if date_to:
            params["date_to"] = date_to.isoformat()
        if customer_id:
            params["customer_id"] = customer_id

        logger.info("Fetching Splynx credit notes with params: %s", params)

        for item in self._paginate("/finance/credit-notes", params=params):
            yield SplynxCreditNote(
                id=int(item.get("id", 0)),
                number=item.get("number", ""),
                customer_id=int(item.get("customer_id", 0)),
                customer_name=item.get("customer_name", ""),
                date_created=item.get("date_created", ""),
                total=Decimal(str(item.get("total", 0))),
                status=item.get("status", ""),
                note=item.get("note"),
                items=item.get("items", []),
            )

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def test_connection(self) -> bool:
        """Test API connection and credentials."""
        try:
            # Try to fetch first page of customers
            self._request("GET", "/customers/customer", params={"limit": 1})
            return True
        except SplynxError as e:
            logger.error("Splynx connection test failed: %s", e.message)
            return False
