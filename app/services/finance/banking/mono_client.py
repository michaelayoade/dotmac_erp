"""
Mono API Client.

Handles all HTTP communication with the Mono Connect API for bank account
linking and transaction retrieval.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

import httpx

from app.metrics import categorize_http_status, observe_integration_request

logger = logging.getLogger(__name__)

MONO_BASE_URL = "https://api.withmono.com"


class MonoError(Exception):
    """Mono API error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class MonoConfig:
    """Configuration for Mono API."""

    secret_key: str = ""
    public_key: str = ""
    webhook_secret: str = ""


@dataclass
class MonoTransaction:
    """A single transaction record from Mono."""

    id: str
    narration: str
    amount: int  # in minor currency units (kobo for NGN)
    type: str  # "debit" or "credit"
    balance: int | None  # running balance in minor units, may be null
    date: str  # ISO 8601 timestamp
    category: str | None = None

    @property
    def amount_major(self) -> Decimal:
        """Amount in major currency units (Naira)."""
        return Decimal(self.amount) / Decimal("100")

    @property
    def balance_major(self) -> Decimal | None:
        """Balance in major currency units (Naira)."""
        if self.balance is None:
            return None
        return Decimal(self.balance) / Decimal("100")


@dataclass
class MonoAccountIdentity:
    """Account holder identity from Mono."""

    full_name: str
    email: str | None = None
    phone: str | None = None
    bvn: str | None = None
    account_number: str | None = None
    institution_name: str | None = None


@dataclass
class MonoAccountInfo:
    """Current account info from Mono, including authoritative balance."""

    id: str
    name: str
    account_number: str
    currency: str
    balance: int  # minor units (kobo for NGN)
    type: str | None = None
    institution_name: str | None = None
    bank_code: str | None = None
    # Last data-request metadata from Mono's indexer. ``data_request_id`` is
    # Mono's internal reference for the most recent refresh job against this
    # account — the value support@mono.co asks for when debugging failed
    # syncs. ``data_status`` and ``retrieved_data`` describe that job's
    # outcome. These are pulled from ``data.meta`` on ``GET /v2/accounts/{id}``.
    data_request_id: str | None = None
    data_status: str | None = None
    retrieved_data: list[str] | None = None

    @property
    def balance_major(self) -> Decimal:
        """Balance in major currency units (Naira)."""
        return Decimal(self.balance) / Decimal("100")


@dataclass
class MonoDataRefreshResult:
    """Result from triggering a Mono real-time data refresh."""

    has_new_data: bool
    job_id: str | None
    job_status: str | None  # "finished", "processing", or "failed"


@dataclass
class MonoExchangeResult:
    """Result from exchanging a Mono Connect widget code."""

    account_id: str


@dataclass
class MonoTransactionPage:
    """A page of transactions from Mono."""

    transactions: list[MonoTransaction] = field(default_factory=list)
    total: int = 0
    page: int = 1
    has_next: bool = False
    next_url: str | None = None


class MonoClient:
    """
    HTTP client for Mono Connect API.

    Handles token exchange, transaction retrieval, and webhook verification.
    """

    def __init__(self, config: MonoConfig, timeout: float = 30.0):
        self.config = config
        self.timeout = timeout
        self._client: httpx.Client | None = None

    def __enter__(self) -> MonoClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=MONO_BASE_URL,
                headers={
                    "mono-sec-key": self.config.secret_key,
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        return self._client

    def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to the Mono API."""
        started_at = time.perf_counter()
        metric_status = "unknown"
        try:
            response = self._get_client().request(
                method=method,
                url=path,
                params=params,
                json=json,
            )
            metric_status = categorize_http_status(response.status_code)
            if response.status_code >= 400:
                try:
                    body = response.json() if response.content else {}
                except ValueError:
                    body = {}
                msg = body.get("message", response.text[:200])
                raise MonoError(
                    f"Mono API error: {msg}",
                    status_code=response.status_code,
                )
            return cast(dict[str, Any], response.json())
        except MonoError:
            observe_integration_request(
                "mono",
                operation,
                metric_status,
                max(time.perf_counter() - started_at, 0.0),
            )
            raise
        except httpx.RequestError as exc:
            observe_integration_request(
                "mono",
                operation,
                "request_error",
                max(time.perf_counter() - started_at, 0.0),
            )
            raise MonoError(f"Mono request failed: {exc}") from exc
        finally:
            if metric_status == "success":
                observe_integration_request(
                    "mono",
                    operation,
                    metric_status,
                    max(time.perf_counter() - started_at, 0.0),
                )

    # ------------------------------------------------------------------
    # Account linking
    # ------------------------------------------------------------------

    def exchange_token(self, code: str) -> MonoExchangeResult:
        """
        Exchange a Mono Connect widget authorization code for an account ID.

        The code expires after 10 minutes. The returned account_id is permanent
        unless unlinked via the API.

        Args:
            code: Authorization code from Mono Connect widget onSuccess callback.

        Returns:
            MonoExchangeResult with the permanent account_id.
        """
        response = self._request(
            "POST",
            "/v2/accounts/auth",
            operation="exchange_token",
            json={"code": code},
        )
        data = response.get("data") or {}
        account_id = data.get("id") or ""
        if not account_id:
            raise MonoError("No account ID returned from Mono")
        logger.info("Mono token exchanged successfully, account_id=%s", account_id)
        return MonoExchangeResult(account_id=account_id)

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    def get_transactions(
        self,
        account_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
        narration: str | None = None,
        type: str | None = None,
        limit: int = 100,
        paginate: bool = True,
    ) -> MonoTransactionPage:
        """
        Fetch transactions for a linked account.

        Args:
            account_id: Mono account ID from exchange_token.
            start: Start date in DD-MM-YYYY format.
            end: End date in DD-MM-YYYY format.
            narration: Filter by narration text.
            type: Filter by "debit" or "credit".
            limit: Number of transactions per page.
            paginate: Whether to paginate results.

        Returns:
            MonoTransactionPage with transactions and pagination info.
        """
        params: dict[str, Any] = {"limit": limit, "paginate": paginate}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if narration:
            params["narration"] = narration
        if type:
            params["type"] = type

        response = self._request(
            "GET",
            f"/v2/accounts/{account_id}/transactions",
            operation="get_transactions",
            params=params,
        )

        data = response.get("data") or []
        meta = response.get("meta") or {}

        transactions = [
            MonoTransaction(
                id=txn["id"],
                narration=txn.get("narration", ""),
                amount=txn.get("amount", 0),
                type=txn.get("type", "debit"),
                balance=txn.get("balance"),
                date=txn.get("date", ""),
                category=txn.get("category"),
            )
            for txn in data
        ]

        return MonoTransactionPage(
            transactions=transactions,
            total=meta.get("total", len(transactions)),
            page=meta.get("page", 1),
            has_next=meta.get("next") is not None,
            next_url=meta.get("next"),
        )

    def get_all_transactions(
        self,
        account_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
        limit: int = 100,
        max_pages: int = 200,
    ) -> list[MonoTransaction]:
        """
        Fetch all transactions for a date range, handling pagination.

        Args:
            account_id: Mono account ID.
            start: Start date in DD-MM-YYYY format.
            end: End date in DD-MM-YYYY format.
            limit: Page size.
            max_pages: Safety cap to prevent infinite pagination loops.

        Returns:
            Complete list of MonoTransaction objects.
        """
        all_transactions: list[MonoTransaction] = []
        params: dict[str, Any] = {"limit": limit, "paginate": True}
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        path = f"/v2/accounts/{account_id}/transactions"

        page_count = 0
        while True:
            page_count += 1
            if page_count > max_pages:
                raise MonoError(
                    f"Mono pagination overflow after {max_pages} pages",
                )

            response = self._request(
                "GET",
                path,
                operation="get_transactions",
                params=params,
            )

            data = response.get("data") or []
            meta = response.get("meta") or {}

            for txn in data:
                all_transactions.append(
                    MonoTransaction(
                        id=txn["id"],
                        narration=txn.get("narration", ""),
                        amount=txn.get("amount", 0),
                        type=txn.get("type", "debit"),
                        balance=txn.get("balance"),
                        date=txn.get("date", ""),
                        category=txn.get("category"),
                    )
                )

            next_url = meta.get("next")
            if not next_url or not data:
                break

            # For subsequent pages, use the next URL directly
            # Clear params since the next URL contains them
            path = next_url.replace(MONO_BASE_URL, "")
            params = {}

        return all_transactions

    # ------------------------------------------------------------------
    # Real-time data refresh
    # ------------------------------------------------------------------

    def trigger_data_refresh(self, account_id: str) -> MonoDataRefreshResult:
        """Trigger a real-time data refresh for a linked account.

        Sends ``x-realtime: true`` on the transactions endpoint, which tells
        Mono's indexer to do a fresh pull from the upstream bank instead of
        serving cached data.  The response headers carry job-tracking
        metadata; when the refresh completes Mono fires an
        ``account_updated`` webhook that the existing handler picks up.

        Rate-limited to one call per account every 5 minutes on Mono's side.
        No charge when ``x-has-new-data`` is ``false``.

        Args:
            account_id: Mono account ID of the linked account.

        Returns:
            MonoDataRefreshResult with job tracking fields.
        """
        started_at = time.perf_counter()
        metric_status = "unknown"
        try:
            response = self._get_client().request(
                method="GET",
                url=f"/v2/accounts/{account_id}/transactions",
                params={"limit": 1, "paginate": False},
                headers={"x-realtime": "true"},
            )
            metric_status = categorize_http_status(response.status_code)
            if response.status_code >= 400:
                try:
                    body = response.json() if response.content else {}
                except ValueError:
                    body = {}
                msg = body.get("message", response.text[:200])
                raise MonoError(
                    f"Mono API error: {msg}",
                    status_code=response.status_code,
                )

            has_new_data = response.headers.get("x-has-new-data", "").lower() == "true"
            job_id = response.headers.get("x-job-id") or None
            job_status = response.headers.get("x-job-status") or None

            logger.info(
                "Mono data refresh triggered for account_id=%s: "
                "has_new_data=%s job_id=%s job_status=%s",
                account_id,
                has_new_data,
                job_id,
                job_status,
            )
            return MonoDataRefreshResult(
                has_new_data=has_new_data,
                job_id=job_id,
                job_status=job_status,
            )
        except MonoError:
            observe_integration_request(
                "mono",
                "trigger_data_refresh",
                metric_status,
                max(time.perf_counter() - started_at, 0.0),
            )
            raise
        except httpx.RequestError as exc:
            observe_integration_request(
                "mono",
                "trigger_data_refresh",
                "request_error",
                max(time.perf_counter() - started_at, 0.0),
            )
            raise MonoError(f"Mono request failed: {exc}") from exc
        finally:
            if metric_status == "success":
                observe_integration_request(
                    "mono",
                    "trigger_data_refresh",
                    metric_status,
                    max(time.perf_counter() - started_at, 0.0),
                )

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def get_account_info(self, account_id: str) -> MonoAccountInfo:
        """
        Fetch current account info for a linked account.

        Returns Mono's authoritative current balance, which should always be
        treated as the source of truth for ``last_statement_balance`` —
        running balances on individual transactions reflect a moment in time
        and may be behind.

        Args:
            account_id: Mono account ID from exchange_token.

        Returns:
            MonoAccountInfo with current balance, institution, and account
            identification.
        """
        response = self._request(
            "GET",
            f"/v2/accounts/{account_id}",
            operation="get_account",
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise MonoError("Mono account response missing data")

        account_obj = data.get("account")
        if account_obj is None and "balance" in data:
            account_obj = data
        if not isinstance(account_obj, dict):
            raise MonoError("Mono account response missing account details")

        if "balance" not in account_obj or account_obj.get("balance") is None:
            raise MonoError("Mono account response missing balance")
        try:
            balance = int(account_obj["balance"])
        except (TypeError, ValueError) as exc:
            raise MonoError("Mono account response has invalid balance") from exc

        institution = account_obj.get("institution") or {}
        if not isinstance(institution, dict):
            institution = {}

        # Mono returns indexer metadata under data.meta on GET /v2/accounts/{id}.
        # This is the authoritative source for data_request_id — the account
        # webhooks sometimes omit it (observed on relinks and late follow-ups),
        # whereas this endpoint always has the latest value.
        response_meta = data.get("meta") if isinstance(data, dict) else None
        if not isinstance(response_meta, dict):
            response_meta = {}
        retrieved_data_raw = response_meta.get("retrieved_data")
        retrieved_data = (
            [str(item) for item in retrieved_data_raw]
            if isinstance(retrieved_data_raw, list)
            else None
        )

        return MonoAccountInfo(
            id=account_obj.get("id") or account_obj.get("_id") or account_id,
            name=account_obj.get("name", ""),
            account_number=account_obj.get("account_number")
            or account_obj.get("accountNumber", ""),
            currency=account_obj.get("currency", ""),
            balance=balance,
            type=account_obj.get("type"),
            institution_name=institution.get("name"),
            bank_code=institution.get("bank_code") or institution.get("bankCode"),
            data_request_id=response_meta.get("data_request_id"),
            data_status=response_meta.get("data_status"),
            retrieved_data=retrieved_data,
        )

    def get_account_identity(self, account_id: str) -> MonoAccountIdentity:
        """
        Get identity information for a linked account.

        Args:
            account_id: Mono account ID.

        Returns:
            MonoAccountIdentity with account holder details.
        """
        response = self._request(
            "GET",
            f"/v2/accounts/{account_id}/identity",
            operation="get_identity",
        )
        data = response.get("data") or {}
        identity_meta = data.get("meta") or {}
        return MonoAccountIdentity(
            full_name=data.get("full_name", ""),
            email=data.get("email"),
            phone=data.get("phone"),
            bvn=data.get("bvn"),
            account_number=data.get("account_number"),
            institution_name=identity_meta.get("institution_name"),
        )

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    def verify_webhook(self, header_secret: str) -> bool:
        """
        Verify a Mono webhook request by comparing the header secret.

        Mono uses a simple string comparison (not HMAC). The
        ``mono-webhook-secret`` header value must match the webhook secret
        configured in the Mono dashboard.

        Args:
            header_secret: Value of the ``mono-webhook-secret`` request header.

        Returns:
            True if the secret matches.
        """
        import hmac

        # Fail closed when either side is empty. compare_digest("", "") returns
        # True, so without this guard a misconfigured deployment (or any future
        # caller that forgets to check ``is_configured()`` first) would accept
        # any webhook whose header is also empty.
        if not header_secret or not self.config.webhook_secret:
            return False

        return hmac.compare_digest(header_secret, self.config.webhook_secret)
