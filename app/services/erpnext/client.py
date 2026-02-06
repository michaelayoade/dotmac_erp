"""
ERPNext API Client.

Client for fetching and writing data to ERPNext via Frappe REST API.
Supports bidirectional sync for migration scenarios.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, cast
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

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
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
                    return self._parse_json(response)

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
                    error_data = self._parse_json(response)
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

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any]:
        """Parse JSON response into a dict payload."""
        try:
            data = response.json()
        except Exception:
            return {"message": response.text}
        if isinstance(data, dict):
            return data
        return {"message": data}

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
        return cast(dict[str, Any], result.get("data", {}))

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
        return cast(list[dict[str, Any]], result.get("data", []))

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
            if from_date:
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
                "qty_after_transaction",
                "valuation_rate",
                "stock_value_difference",
                "voucher_type",
                "voucher_no",
                "batch_no",
                "serial_no",
                "stock_uom",
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

    # --------------------------
    # Write operations (for bidirectional sync)
    # --------------------------

    def create_document(
        self,
        doctype: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Create a new document in ERPNext.

        Args:
            doctype: DocType name (e.g., 'Employee', 'Expense Claim')
            data: Document data fields

        Returns:
            Created document data including 'name'

        Raises:
            ERPNextError: On validation or permission error
        """
        payload = {"doctype": doctype, **data}
        result = self._request(
            "POST",
            f"/api/resource/{doctype}",
            json=payload,
        )
        return cast(dict[str, Any], result.get("data", {}))

    def update_document(
        self,
        doctype: str,
        name: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update an existing document in ERPNext.

        Args:
            doctype: DocType name
            name: Document name/ID
            data: Fields to update

        Returns:
            Updated document data

        Raises:
            ERPNextError: On validation or permission error
        """
        result = self._request(
            "PUT",
            f"/api/resource/{doctype}/{name}",
            json=data,
        )
        return cast(dict[str, Any], result.get("data", {}))

    def delete_document(
        self,
        doctype: str,
        name: str,
    ) -> bool:
        """
        Delete a document from ERPNext.

        Note: Many ERPNext documents cannot be deleted if they have
        linked transactions. Use cancel_document for such cases.

        Args:
            doctype: DocType name
            name: Document name/ID

        Returns:
            True if deleted successfully

        Raises:
            ERPNextError: On error (e.g., linked documents exist)
        """
        self._request("DELETE", f"/api/resource/{doctype}/{name}")
        return True

    def submit_document(
        self,
        doctype: str,
        name: str,
    ) -> dict[str, Any]:
        """
        Submit a document (change docstatus from Draft to Submitted).

        For workflow documents like Sales Invoice, Purchase Invoice,
        Journal Entry, Expense Claim, etc.

        Args:
            doctype: DocType name
            name: Document name/ID

        Returns:
            Updated document data

        Raises:
            ERPNextError: On validation or workflow error
        """
        result = self._request(
            "POST",
            "/api/method/frappe.client.submit",
            json={"doc": {"doctype": doctype, "name": name}},
        )
        return cast(dict[str, Any], result.get("message", {}))

    def cancel_document(
        self,
        doctype: str,
        name: str,
    ) -> dict[str, Any]:
        """
        Cancel a submitted document (change docstatus to Cancelled).

        Creates reversal entries where applicable.

        Args:
            doctype: DocType name
            name: Document name/ID

        Returns:
            Cancelled document data

        Raises:
            ERPNextError: On validation or workflow error
        """
        result = self._request(
            "POST",
            "/api/method/frappe.client.cancel",
            json={"doctype": doctype, "name": name},
        )
        return cast(dict[str, Any], result.get("message", {}))

    def run_method(
        self,
        doctype: str,
        name: str,
        method: str,
        args: Optional[dict[str, Any]] = None,
    ) -> Any:
        """
        Run a whitelisted method on a document.

        Useful for workflow transitions, custom actions, etc.

        Args:
            doctype: DocType name
            name: Document name/ID
            method: Method name to call
            args: Optional method arguments

        Returns:
            Method result

        Raises:
            ERPNextError: On error
        """
        payload: dict[str, Any] = {"doctype": doctype, "name": name, "method": method}
        if args:
            payload["args"] = args

        result = self._request(
            "POST",
            "/api/method/frappe.client.run_doc_method",
            json=payload,
        )
        return result.get("message")

    # --------------------------
    # HR-specific convenience methods
    # --------------------------

    def get_departments(self, company: Optional[str] = None):
        """
        Get departments.

        Args:
            company: Company name (defaults to config.company)

        Yields:
            Department documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company

        yield from self.get_all_documents(
            doctype="Department",
            filters=filters,
            fields=[
                "name",
                "department_name",
                "parent_department",
                "company",
                "is_group",
                "disabled",
                "modified",
            ],
            order_by="lft asc",  # Tree order
        )

    def get_designations(self):
        """
        Get designations (job titles).

        Yields:
            Designation documents
        """
        yield from self.get_all_documents(
            doctype="Designation",
            fields=[
                "name",
                "designation_name",
                "modified",
            ],
            order_by="designation_name asc",
        )

    def get_employment_types(self):
        """
        Get employment types (full-time, part-time, etc.).

        Yields:
            Employment Type documents
        """
        yield from self.get_all_documents(
            doctype="Employment Type",
            fields=[
                "name",
                "modified",
            ],
            order_by="name asc",
        )

    def get_employee_grades(self):
        """
        Get employee grades (salary bands).

        Yields:
            Employee Grade documents
        """
        yield from self.get_all_documents(
            doctype="Employee Grade",
            fields=[
                "name",
                "default_base_pay",
                "modified",
            ],
            order_by="name asc",
        )

    def get_employees(
        self,
        company: Optional[str] = None,
        include_inactive: bool = False,
    ):
        """
        Get employees.

        Args:
            company: Company name (defaults to config.company)
            include_inactive: Include left/inactive employees

        Yields:
            Employee documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company
        if not include_inactive:
            filters["status"] = "Active"

        yield from self.get_all_documents(
            doctype="Employee",
            filters=filters,
            fields=[
                "name",
                "employee_name",
                "first_name",
                "middle_name",
                "last_name",
                "gender",
                "date_of_birth",
                "date_of_joining",
                "relieving_date",
                "status",
                "department",
                "designation",
                "employment_type",
                "grade",
                "reports_to",
                "user_id",
                "cell_number",
                "personal_email",
                "company_email",
                "prefered_email",
                "bank_name",
                "bank_ac_no",
                "branch",  # Bank branch
                "custom_name_on_account",  # Account holder name
                "company",
                "modified",
                # Extended fields for ERPNext sync (some fields may be restricted)
                "current_address",
                "permanent_address",
                "marital_status",
                "blood_group",
                "passport_number",
                "valid_upto",  # ERPNext name for passport_valid_upto
                # "ht",  # Restricted: ERPNext name for current_accommodation_type
                "ctc",
                "salary_mode",
            ],
            order_by="employee_name asc",
        )

    def get_leave_types(self):
        """
        Get leave types.

        Yields:
            Leave Type documents
        """
        yield from self.get_all_documents(
            doctype="Leave Type",
            fields=[
                "name",
                "leave_type_name",
                "max_leaves_allowed",
                "max_continuous_days_allowed",
                "is_carry_forward",
                "is_earned_leave",
                "is_compensatory",
                "is_lwp",
                "allow_negative",
                "include_holiday",
                "modified",
            ],
            order_by="leave_type_name asc",
        )

    def get_leave_allocations(
        self,
        company: Optional[str] = None,
        fiscal_year: Optional[str] = None,
    ):
        """
        Get leave allocations.

        Args:
            company: Company name (defaults to config.company)
            fiscal_year: Fiscal year name (optional)

        Yields:
            Leave Allocation documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {"docstatus": 1}  # Only submitted

        if company:
            filters["company"] = company
        if fiscal_year:
            filters["fiscal_year"] = fiscal_year

        yield from self.get_all_documents(
            doctype="Leave Allocation",
            filters=filters,
            fields=[
                "name",
                "employee",
                "employee_name",
                "leave_type",
                "from_date",
                "to_date",
                "new_leaves_allocated",
                "carry_forward",
                "carry_forwarded_leaves",
                "total_leaves_allocated",
                "company",
                "modified",
            ],
            order_by="from_date desc",
        )

    def get_leave_applications(
        self,
        company: Optional[str] = None,
        from_date: Optional[datetime] = None,
    ):
        """
        Get leave applications.

        Args:
            company: Company name (defaults to config.company)
            from_date: Filter applications from this date

        Yields:
            Leave Application documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company
        if from_date:
            filters["from_date"] = [">=", from_date.strftime("%Y-%m-%d")]

        yield from self.get_all_documents(
            doctype="Leave Application",
            filters=filters,
            fields=[
                "name",
                "employee",
                "employee_name",
                "leave_type",
                "from_date",
                "to_date",
                "total_leave_days",
                "half_day",
                "half_day_date",
                "status",
                "docstatus",
                "description",
                "leave_approver",
                "company",
                "modified",
            ],
            order_by="from_date desc",
        )

    def get_shift_types(self):
        """
        Get shift types.

        Yields:
            Shift Type documents
        """
        yield from self.get_all_documents(
            doctype="Shift Type",
            fields=[
                "name",
                "start_time",
                "end_time",
                "enable_auto_attendance",
                "early_exit_grace_period",
                "late_entry_grace_period",
                "working_hours_threshold_for_half_day",
                "working_hours_threshold_for_absent",
                "modified",
            ],
            order_by="name asc",
        )

    def get_attendance(
        self,
        company: Optional[str] = None,
        from_date: Optional[datetime] = None,
    ):
        """
        Get attendance records.

        Args:
            company: Company name (defaults to config.company)
            from_date: Filter attendance from this date

        Yields:
            Attendance documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {"docstatus": 1}  # Only submitted

        if company:
            filters["company"] = company
        if from_date:
            filters["attendance_date"] = [">=", from_date.strftime("%Y-%m-%d")]

        yield from self.get_all_documents(
            doctype="Attendance",
            filters=filters,
            fields=[
                "name",
                "employee",
                "employee_name",
                "attendance_date",
                "status",
                "shift",
                "in_time",
                "out_time",
                "working_hours",
                "early_exit",
                "late_entry",
                "leave_type",
                "leave_application",
                "company",
                "modified",
            ],
            order_by="attendance_date desc",
        )

    def get_expense_claim_types(self):
        """
        Get expense claim types (categories).

        Yields:
            Expense Claim Type documents
        """
        yield from self.get_all_documents(
            doctype="Expense Claim Type",
            fields=[
                "name",
                "expense_type",
                "description",
                "modified",
            ],
            order_by="expense_type asc",
        )

    def get_expense_claims(
        self,
        company: Optional[str] = None,
        from_date: Optional[datetime] = None,
    ):
        """
        Get expense claims with their items.

        Args:
            company: Company name (defaults to config.company)
            from_date: Filter claims posted from this date

        Yields:
            Expense Claim documents (with nested expenses)
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company
        if from_date:
            filters["posting_date"] = [">=", from_date.strftime("%Y-%m-%d")]

        for claim in self.get_all_documents(
            doctype="Expense Claim",
            filters=filters,
            fields=[
                "name",
                "employee",
                "employee_name",
                "expense_approver",
                "posting_date",
                "approval_status",
                "status",
                "docstatus",
                "total_claimed_amount",
                "total_sanctioned_amount",
                "total_amount_reimbursed",
                "remark",
                "company",
                "cost_center",
                "project",
                "modified",
            ],
            order_by="posting_date desc",
        ):
            # Fetch expense items for each claim (gracefully handle permission errors)
            try:
                claim["expenses"] = self.list_documents(
                    doctype="Expense Claim Detail",
                    filters={"parent": claim["name"]},
                    fields=[
                        "name",
                        "expense_date",
                        "expense_type",
                        "description",
                        "amount",
                        "sanctioned_amount",
                        "cost_center",
                        "modified",
                    ],
                )
            except ERPNextError as e:
                # Permission denied or other error - continue without items
                import logging

                logging.getLogger(__name__).warning(
                    "Could not fetch expense items for %s: %s", claim["name"], e.message
                )
                claim["expenses"] = []
            yield claim

    def get_projects(
        self,
        company: Optional[str] = None,
        include_completed: bool = False,
    ):
        """
        Get projects.

        Args:
            company: Company name (defaults to config.company)
            include_completed: Include completed/cancelled projects

        Yields:
            Project documents
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company
        if not include_completed:
            filters["status"] = ["not in", ["Completed", "Cancelled"]]

        yield from self.get_all_documents(
            doctype="Project",
            filters=filters,
            fields=[
                "name",
                "project_name",
                "status",
                "is_active",
                "expected_start_date",
                "expected_end_date",
                "actual_start_date",
                "actual_end_date",
                "estimated_costing",
                "total_costing_amount",
                "percent_complete",
                "company",
                "cost_center",
                "customer",  # Customer link for client projects
                "modified",
            ],
            order_by="project_name asc",
        )

    def get_issues(
        self,
        from_date: Optional[datetime] = None,
        include_closed: bool = False,
    ):
        """
        Get helpdesk issues/tickets.

        Args:
            from_date: Filter issues from this date
            include_closed: Include closed issues

        Yields:
            Issue documents (HD Ticket in newer ERPNext)
        """
        filters: dict[str, Any] = {}

        if from_date:
            filters["opening_date"] = [">=", from_date.strftime("%Y-%m-%d")]
        if not include_closed:
            filters["status"] = ["not in", ["Closed", "Resolved"]]

        # Try HD Ticket first (ERPNext v14+), fallback to Issue
        try:
            yield from self.get_all_documents(
                doctype="HD Ticket",
                filters=filters,
                fields=[
                    "name",
                    "subject",
                    "description",
                    "status",
                    "priority",
                    "raised_by",
                    "owner",
                    "opening_date",
                    "resolution_date",
                    "resolution_details",
                    "customer",  # Customer link for support tickets
                    "modified",
                ],
                order_by="opening_date desc",
            )
        except ERPNextError as e:
            if e.status_code == 404:
                # Fall back to Issue DocType
                yield from self.get_all_documents(
                    doctype="Issue",
                    filters=filters,
                    fields=[
                        "name",
                        "subject",
                        "description",
                        "status",
                        "priority",
                        "raised_by",
                        "owner",
                        "opening_date",
                        "resolution_date",
                        "resolution_details",
                        "project",
                        "customer",  # Customer link for support tickets
                        "modified",
                    ],
                    order_by="opening_date desc",
                )
            else:
                raise

    def get_material_requests(
        self,
        company: Optional[str] = None,
        from_date: Optional[datetime] = None,
        include_cancelled: bool = False,
    ):
        """
        Get material requests with their items.

        Material Requests are inventory requisitions that can be linked to
        projects, support tickets, and tasks.

        Args:
            company: Company name (defaults to config.company)
            from_date: Filter requests from this date
            include_cancelled: Include cancelled requests

        Yields:
            Material Request documents (with nested items)
        """
        company = company or self.config.company
        filters: dict[str, Any] = {}

        if company:
            filters["company"] = company
        if from_date:
            filters["transaction_date"] = [">=", from_date.strftime("%Y-%m-%d")]
        if not include_cancelled:
            filters["status"] = ["not in", ["Cancelled", "Stopped"]]

        for request in self.get_all_documents(
            doctype="Material Request",
            filters=filters,
            # Note: Some fields like requested_by, reason may be restricted
            # Only fetch fields known to be permitted in ERPNext API
            fields=[
                "name",
                "material_request_type",
                "status",
                "transaction_date",
                "schedule_date",
                "set_warehouse",
                "company",
                "modified",
            ],
            order_by="transaction_date desc",
        ):
            # Fetch items for each request
            try:
                request["items"] = self.list_documents(
                    doctype="Material Request Item",
                    filters={"parent": request["name"]},
                    fields=[
                        "name",
                        "item_code",
                        "item_name",
                        "warehouse",
                        "qty",
                        "ordered_qty",
                        "stock_uom",
                        "schedule_date",
                        "project",
                        "modified",
                    ],
                )
            except ERPNextError as e:
                # Permission denied or other error - continue without items
                logger.warning(
                    "Could not fetch items for Material Request %s: %s",
                    request["name"],
                    e.message,
                )
                request["items"] = []
            yield request
