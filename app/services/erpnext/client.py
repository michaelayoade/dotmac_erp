"""
ERPNext API Client.

Read-only client for fetching data from ERPNext via Frappe REST API.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)


class ERPNextError(Exception):
    """ERPNext API error."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass
class ERPNextConfig:
    """ERPNext connection configuration."""

    url: str
    api_key: str
    api_secret: str
    company: Optional[str] = None
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0


class ERPNextClient:
    """
    ERPNext API client for fetching data.

    Uses Frappe REST API with API Key authentication.
    Read-only operations only.
    """

    def __init__(self, config: ERPNextConfig):
        self.config = config
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.url.rstrip("/"),
                timeout=self.config.timeout,
                headers={
                    "Authorization": f"token {self.config.api_key}:{self.config.api_secret}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "ERPNextClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _request(
        self, method: str, path: str, **kwargs
    ) -> dict[str, Any]:
        """
        Make HTTP request with retry logic.

        Args:
            method: HTTP method
            path: API path (relative to base URL)
            **kwargs: Additional request arguments

        Returns:
            Response JSON data

        Raises:
            ERPNextError: On API error
        """
        url = urljoin(self.config.url, path)
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                response = self.client.request(method, path, **kwargs)

                if response.status_code == 200:
                    return response.json()

                # Handle specific error codes
                if response.status_code == 401:
                    raise ERPNextError("Authentication failed", 401)
                if response.status_code == 403:
                    raise ERPNextError("Permission denied", 403)
                if response.status_code == 404:
                    raise ERPNextError("Resource not found", 404)

                # Retry on 5xx errors
                if response.status_code >= 500:
                    last_error = ERPNextError(
                        f"Server error: {response.status_code}", response.status_code
                    )
                    if attempt < self.config.max_retries - 1:
                        time.sleep(self.config.retry_delay * (attempt + 1))
                        continue
                    raise last_error

                # Other errors - don't retry
                error_msg = response.text
                try:
                    error_data = response.json()
                    if "message" in error_data:
                        error_msg = error_data["message"]
                    elif "_server_messages" in error_data:
                        error_msg = str(error_data["_server_messages"])
                except Exception:
                    pass
                raise ERPNextError(error_msg, response.status_code)

            except httpx.RequestError as e:
                last_error = ERPNextError(f"Connection error: {str(e)}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))
                    continue
                raise last_error

        # Should not reach here, but just in case
        raise last_error or ERPNextError("Unknown error")

    def test_connection(self) -> dict[str, Any]:
        """
        Test connection to ERPNext.

        Returns:
            User info if successful

        Raises:
            ERPNextError: On connection/auth failure
        """
        result = self._request("GET", "/api/method/frappe.auth.get_logged_user")
        return {"user": result.get("message")}

    def get_document(
        self,
        doctype: str,
        name: str,
        fields: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Get a single document by name.

        Args:
            doctype: DocType name (e.g., 'Item', 'Asset')
            name: Document name/ID
            fields: Optional list of fields to return

        Returns:
            Document data

        Raises:
            ERPNextError: On error
        """
        params = {}
        if fields:
            params["fields"] = str(fields)

        result = self._request("GET", f"/api/resource/{doctype}/{name}", params=params)
        return result.get("data", {})

    def list_documents(
        self,
        doctype: str,
        filters: Optional[dict[str, Any]] = None,
        fields: Optional[list[str]] = None,
        order_by: Optional[str] = None,
        limit_start: int = 0,
        limit_page_length: int = 100,
    ) -> list[dict[str, Any]]:
        """
        List documents with pagination.

        Args:
            doctype: DocType name
            filters: Filter conditions (e.g., {"disabled": 0})
            fields: Fields to return (default: all)
            order_by: Sort order (e.g., "modified desc")
            limit_start: Offset for pagination
            limit_page_length: Number of records per page

        Returns:
            List of documents

        Raises:
            ERPNextError: On error
        """
        params: dict[str, Any] = {
            "limit_start": limit_start,
            "limit_page_length": limit_page_length,
        }

        import json

        if filters:
            # Frappe expects filters as JSON array
            params["filters"] = json.dumps(filters)

        if fields:
            # Frappe expects fields as JSON array
            params["fields"] = json.dumps(fields)

        if order_by:
            params["order_by"] = order_by

        result = self._request("GET", f"/api/resource/{doctype}", params=params)
        return result.get("data", [])

    def get_count(
        self,
        doctype: str,
        filters: Optional[dict[str, Any]] = None,
    ) -> int:
        """
        Get count of documents matching filters.

        Args:
            doctype: DocType name
            filters: Filter conditions

        Returns:
            Count of matching documents
        """
        params: dict[str, Any] = {"doctype": doctype}
        if filters:
            import json
            params["filters"] = json.dumps(filters)

        result = self._request(
            "GET", "/api/method/frappe.client.get_count", params=params
        )
        return int(result.get("message", 0))

    def get_all_documents(
        self,
        doctype: str,
        filters: Optional[dict[str, Any]] = None,
        fields: Optional[list[str]] = None,
        order_by: Optional[str] = None,
        batch_size: int = 100,
    ):
        """
        Generator to fetch all documents with automatic pagination.

        Args:
            doctype: DocType name
            filters: Filter conditions
            fields: Fields to return
            order_by: Sort order
            batch_size: Records per batch

        Yields:
            Document dictionaries
        """
        offset = 0
        while True:
            batch = self.list_documents(
                doctype=doctype,
                filters=filters,
                fields=fields,
                order_by=order_by,
                limit_start=offset,
                limit_page_length=batch_size,
            )

            if not batch:
                break

            for doc in batch:
                yield doc

            if len(batch) < batch_size:
                break

            offset += batch_size

    def get_modified_since(
        self,
        doctype: str,
        since: datetime,
        filters: Optional[dict[str, Any]] = None,
        fields: Optional[list[str]] = None,
    ):
        """
        Get documents modified since a given timestamp.

        For incremental sync.

        Args:
            doctype: DocType name
            since: Timestamp to filter from
            filters: Additional filter conditions
            fields: Fields to return

        Yields:
            Document dictionaries
        """
        modified_filter = {"modified": [">=", since.isoformat()]}

        if filters:
            combined_filters = {**filters, **modified_filter}
        else:
            combined_filters = modified_filter

        yield from self.get_all_documents(
            doctype=doctype,
            filters=combined_filters,
            fields=fields,
            order_by="modified asc",
        )

    # --------------------------
    # DocType-specific methods
    # --------------------------

    def get_chart_of_accounts(
        self,
        company: Optional[str] = None,
        include_disabled: bool = False,
    ):
        """
        Get Chart of Accounts for a company.

        Args:
            company: Company name (defaults to config.company)
            include_disabled: Include disabled accounts

        Yields:
            Account documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company
        if not include_disabled:
            filters["disabled"] = 0

        yield from self.get_all_documents(
            doctype="Account",
            filters=filters,
            fields=[
                "name",
                "account_name",
                "account_number",
                "root_type",
                "account_type",
                "is_group",
                "parent_account",
                "account_currency",
                "disabled",
                "modified",
            ],
            order_by="lft asc",  # Tree order
        )

    def get_items(
        self,
        include_disabled: bool = False,
    ):
        """
        Get inventory items.

        Args:
            include_disabled: Include disabled items

        Yields:
            Item documents
        """
        filters: dict[str, Any] = {}
        if not include_disabled:
            filters["disabled"] = 0

        yield from self.get_all_documents(
            doctype="Item",
            filters=filters,
            fields=[
                "name",
                "item_code",
                "item_name",
                "description",
                "item_group",
                "stock_uom",
                "is_stock_item",
                "has_batch_no",
                "has_serial_no",
                "valuation_method",
                "standard_rate",
                "last_purchase_rate",
                "disabled",
                "is_purchase_item",
                "is_sales_item",
                "modified",
            ],
            order_by="item_code asc",
        )

    def get_item_groups(self):
        """
        Get item groups (categories).

        Yields:
            Item Group documents
        """
        yield from self.get_all_documents(
            doctype="Item Group",
            fields=[
                "name",
                "item_group_name",
                "parent_item_group",
                "is_group",
                "modified",
            ],
            order_by="lft asc",  # Tree order
        )

    def get_assets(
        self,
        company: Optional[str] = None,
    ):
        """
        Get fixed assets.

        Args:
            company: Company name (defaults to config.company)

        Yields:
            Asset documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company

        # Use minimal fields to avoid version-specific field errors
        yield from self.get_all_documents(
            doctype="Asset",
            filters=filters,
            fields=[
                "name",
                "asset_name",
                "item_code",
                "asset_category",
                "company",
                "location",
                "purchase_date",
                "available_for_use_date",
                "gross_purchase_amount",
                "depreciation_method",
                "total_number_of_depreciations",
                "frequency_of_depreciation",
                "opening_accumulated_depreciation",
                "status",
                "disposal_date",
                "value_after_depreciation",
                "modified",
            ],
            order_by="name asc",
        )

    def get_asset_categories(self):
        """
        Get asset categories.

        Yields:
            Asset Category documents
        """
        yield from self.get_all_documents(
            doctype="Asset Category",
            fields=[
                "name",
                "asset_category_name",
                "modified",
            ],
            order_by="name asc",
        )

    def get_warehouses(
        self,
        company: Optional[str] = None,
        include_disabled: bool = False,
    ):
        """
        Get warehouses.

        Args:
            company: Company name (defaults to config.company)
            include_disabled: Include disabled warehouses

        Yields:
            Warehouse documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company
        if not include_disabled:
            filters["disabled"] = 0

        yield from self.get_all_documents(
            doctype="Warehouse",
            filters=filters,
            fields=[
                "name",
                "warehouse_name",
                "is_group",
                "parent_warehouse",
                "disabled",
                "company",
                "modified",
            ],
            order_by="lft asc",  # Tree order
        )

    def get_customers(
        self,
        include_disabled: bool = False,
    ):
        """
        Get customers.

        Args:
            include_disabled: Include disabled customers

        Yields:
            Customer documents
        """
        filters: dict[str, Any] = {}
        if not include_disabled:
            filters["disabled"] = 0

        yield from self.get_all_documents(
            doctype="Customer",
            filters=filters,
            fields=[
                "name",
                "customer_name",
                "customer_type",
                "customer_group",
                "territory",
                "default_currency",
                "tax_id",
                "disabled",
                "modified",
            ],
            order_by="customer_name asc",
        )

    def get_suppliers(
        self,
        include_disabled: bool = False,
    ):
        """
        Get suppliers.

        Args:
            include_disabled: Include disabled suppliers

        Yields:
            Supplier documents
        """
        filters: dict[str, Any] = {}
        if not include_disabled:
            filters["disabled"] = 0

        yield from self.get_all_documents(
            doctype="Supplier",
            filters=filters,
            fields=[
                "name",
                "supplier_name",
                "supplier_type",
                "supplier_group",
                "country",
                "default_currency",
                "tax_id",
                "disabled",
                "modified",
            ],
            order_by="supplier_name asc",
        )

    def get_stock_ledger_entries(
        self,
        company: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ):
        """
        Get stock ledger entries (inventory transactions).

        Args:
            company: Company name
            from_date: Start date filter
            to_date: End date filter

        Yields:
            Stock Ledger Entry documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company
        if from_date:
            filters["posting_date"] = [">=", from_date.strftime("%Y-%m-%d")]
        if to_date:
            if "posting_date" in filters:
                filters["posting_date"] = [
                    "between",
                    [from_date.strftime("%Y-%m-%d"), to_date.strftime("%Y-%m-%d")],
                ]
            else:
                filters["posting_date"] = ["<=", to_date.strftime("%Y-%m-%d")]

        yield from self.get_all_documents(
            doctype="Stock Ledger Entry",
            filters=filters,
            fields=[
                "name",
                "item_code",
                "warehouse",
                "posting_date",
                "posting_time",
                "actual_qty",
                "valuation_rate",
                "stock_value_difference",
                "voucher_type",
                "voucher_no",
                "batch_no",
                "serial_no",
                "company",
                "modified",
            ],
            order_by="posting_date asc, posting_time asc",
        )

    def get_sales_invoices(
        self,
        company: Optional[str] = None,
        from_date: Optional[datetime] = None,
    ):
        """
        Get sales invoices.

        Args:
            company: Company name
            from_date: Start date filter

        Yields:
            Sales Invoice documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company
        if from_date:
            filters["posting_date"] = [">=", from_date.strftime("%Y-%m-%d")]

        yield from self.get_all_documents(
            doctype="Sales Invoice",
            filters=filters,
            fields=[
                "name",
                "customer",
                "posting_date",
                "due_date",
                "currency",
                "grand_total",
                "outstanding_amount",
                "status",
                "docstatus",
                "company",
                "modified",
            ],
            order_by="posting_date asc",
        )

    def get_purchase_invoices(
        self,
        company: Optional[str] = None,
        from_date: Optional[datetime] = None,
    ):
        """
        Get purchase invoices.

        Args:
            company: Company name
            from_date: Start date filter

        Yields:
            Purchase Invoice documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company
        if from_date:
            filters["posting_date"] = [">=", from_date.strftime("%Y-%m-%d")]

        yield from self.get_all_documents(
            doctype="Purchase Invoice",
            filters=filters,
            fields=[
                "name",
                "supplier",
                "posting_date",
                "due_date",
                "currency",
                "grand_total",
                "outstanding_amount",
                "status",
                "docstatus",
                "company",
                "modified",
            ],
            order_by="posting_date asc",
        )
