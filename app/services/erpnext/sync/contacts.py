"""
Contact Sync Service - ERPNext to DotMac ERP (Customers/Suppliers).
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier, SupplierType
from app.models.finance.ar.customer import Customer, CustomerType
from app.models.finance.gl.account import Account
from app.services.erpnext.mappings.contacts import CustomerMapping, SupplierMapping

from .base import BaseSyncService

logger = logging.getLogger(__name__)

# Default account codes (from Chart of Accounts)
DEFAULT_AP_ACCOUNT = "2000"  # Trade Payables
DEFAULT_AR_ACCOUNT = "1400"  # Trade Receivables


# Customer type mapping
# Valid values: INDIVIDUAL, COMPANY, GOVERNMENT, RELATED_PARTY
CUSTOMER_TYPE_MAP = {
    "COMPANY": "COMPANY",
    "INDIVIDUAL": "INDIVIDUAL",
    "Company": "COMPANY",
    "Individual": "INDIVIDUAL",
}

# Supplier type mapping
# Valid values: VENDOR, CONTRACTOR, SERVICE_PROVIDER, UTILITY, GOVERNMENT, RELATED_PARTY
# ERPNext uses Company/Individual - map Company to VENDOR and Individual to SERVICE_PROVIDER
SUPPLIER_TYPE_MAP = {
    "COMPANY": "VENDOR",
    "INDIVIDUAL": "SERVICE_PROVIDER",
    "Company": "VENDOR",
    "Individual": "SERVICE_PROVIDER",
    "VENDOR": "VENDOR",
    "CONTRACTOR": "CONTRACTOR",
    "SERVICE_PROVIDER": "SERVICE_PROVIDER",
}


class CustomerSyncService(BaseSyncService[Customer]):
    """Sync Customers from ERPNext."""

    source_doctype = "Customer"
    target_table = "ar.customer"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = CustomerMapping()
        self._customer_cache: dict[str, Customer] = {}
        self._ar_account_id: uuid.UUID | None = None

    def _get_ar_account_id(self) -> uuid.UUID:
        """Get the AR control account ID."""
        if self._ar_account_id:
            return self._ar_account_id

        account = self.db.execute(
            select(Account).where(
                Account.organization_id == self.organization_id,
                Account.account_code == DEFAULT_AR_ACCOUNT,
            )
        ).scalar_one_or_none()

        if account:
            self._ar_account_id = account.account_id
            return self._ar_account_id

        raise ValueError(f"AR control account {DEFAULT_AR_ACCOUNT} not found")

    def fetch_records(self, client: Any, since: datetime | None = None):
        """Fetch customers from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Customer",
                since=since,
            )
        else:
            yield from client.get_customers()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext customer to DotMac ERP format."""
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> Customer:
        """Create Customer entity."""
        data.pop("_source_name", None)
        data.pop("_source_modified", None)

        # Map customer type - stored as VARCHAR
        customer_type_value = CUSTOMER_TYPE_MAP.get(
            data.get("customer_type", ""), "COMPANY"
        )
        customer_type = CustomerType(customer_type_value)

        # Generate customer code from source name
        customer_code = (data.get("customer_code") or "CUST")[:30]
        legal_name = (data.get("legal_name") or customer_code)[:200]

        # Get required AR control account
        ar_account_id = self._get_ar_account_id()

        customer = Customer(
            organization_id=self.organization_id,
            customer_code=customer_code,
            legal_name=legal_name,
            trading_name=(data.get("trading_name") or legal_name)[:200],
            customer_type=customer_type,
            currency_code=(data.get("currency_code") or "NGN")[:3],
            tax_identification_number=str(data.get("tax_id") or "")[:50] or None,
            ar_control_account_id=ar_account_id,
            credit_terms_days=30,  # Default credit terms
            risk_category="MEDIUM",  # Default risk category
            is_related_party=False,
            is_wht_applicable=False,
            is_active=data.get("is_active", True),
            created_by_user_id=self.user_id,
        )
        return customer

    def update_entity(self, entity: Customer, data: dict[str, Any]) -> Customer:
        """Update existing Customer."""
        data.pop("_source_name", None)
        data.pop("_source_modified", None)

        entity.legal_name = (data.get("legal_name") or entity.legal_name)[:200]
        entity.trading_name = (data.get("trading_name") or entity.legal_name)[:200]
        customer_type_value = CUSTOMER_TYPE_MAP.get(
            data.get("customer_type", ""), entity.customer_type.value
        )
        entity.customer_type = CustomerType(customer_type_value)
        entity.currency_code = (data.get("currency_code") or "NGN")[:3]
        entity.tax_identification_number = str(data.get("tax_id") or "")[:50] or None
        entity.is_active = data.get("is_active", True)

        return entity

    def get_entity_id(self, entity: Customer) -> uuid.UUID:
        """Get customer ID."""
        return entity.customer_id

    def find_existing_entity(self, source_name: str) -> Customer | None:
        """Find existing customer by code or name."""
        if source_name in self._customer_cache:
            return self._customer_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            customer = self.db.get(Customer, sync_entity.target_id)
            if customer:
                self._customer_cache[source_name] = customer
                return customer

        # Try by code (truncated source_name)
        code = source_name[:30] if source_name else None
        if code:
            result = self.db.execute(
                select(Customer).where(
                    Customer.organization_id == self.organization_id,
                    Customer.customer_code == code,
                )
            ).scalar_one_or_none()

            if result:
                self._customer_cache[source_name] = result
                return result

        return None


class SupplierSyncService(BaseSyncService[Supplier]):
    """Sync Suppliers from ERPNext."""

    source_doctype = "Supplier"
    target_table = "ap.supplier"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = SupplierMapping()
        self._supplier_cache: dict[str, Supplier] = {}
        self._ap_account_id: uuid.UUID | None = None

    def _get_ap_account_id(self) -> uuid.UUID:
        """Get the AP control account ID."""
        if self._ap_account_id:
            return self._ap_account_id

        account = self.db.execute(
            select(Account).where(
                Account.organization_id == self.organization_id,
                Account.account_code == DEFAULT_AP_ACCOUNT,
            )
        ).scalar_one_or_none()

        if account:
            self._ap_account_id = account.account_id
            return self._ap_account_id

        raise ValueError(f"AP control account {DEFAULT_AP_ACCOUNT} not found")

    def fetch_records(self, client: Any, since: datetime | None = None):
        """Fetch suppliers from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Supplier",
                since=since,
            )
        else:
            yield from client.get_suppliers()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext supplier to DotMac ERP format."""
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> Supplier:
        """Create Supplier entity."""
        data.pop("_source_name", None)
        data.pop("_source_modified", None)

        # Map supplier type - stored as VARCHAR enum
        supplier_type_value = SUPPLIER_TYPE_MAP.get(
            data.get("supplier_type", ""), "VENDOR"
        )
        supplier_type = SupplierType(supplier_type_value)

        supplier_code = (data.get("supplier_code") or "SUPP")[:30]
        legal_name = (data.get("legal_name") or supplier_code)[:200]

        # Get required AP control account
        ap_account_id = self._get_ap_account_id()

        supplier = Supplier(
            organization_id=self.organization_id,
            supplier_code=supplier_code,
            legal_name=legal_name,
            trading_name=(data.get("trading_name") or legal_name)[:200],
            supplier_type=supplier_type,
            currency_code=(data.get("currency_code") or "NGN")[:3],
            tax_identification_number=str(data.get("tax_id") or "")[:50] or None,
            ap_control_account_id=ap_account_id,
            is_active=data.get("is_active", True),
            created_by_user_id=self.user_id,
        )
        return supplier

    def update_entity(self, entity: Supplier, data: dict[str, Any]) -> Supplier:
        """Update existing Supplier."""
        data.pop("_source_name", None)
        data.pop("_source_modified", None)

        entity.legal_name = (data.get("legal_name") or entity.legal_name)[:200]
        entity.trading_name = (data.get("trading_name") or entity.legal_name)[:200]
        supplier_type_value = SUPPLIER_TYPE_MAP.get(
            data.get("supplier_type", ""), entity.supplier_type.value
        )
        entity.supplier_type = SupplierType(supplier_type_value)
        entity.currency_code = (data.get("currency_code") or "NGN")[:3]
        entity.tax_identification_number = str(data.get("tax_id") or "")[:50] or None
        entity.is_active = data.get("is_active", True)

        return entity

    def get_entity_id(self, entity: Supplier) -> uuid.UUID:
        """Get supplier ID."""
        return entity.supplier_id

    def find_existing_entity(self, source_name: str) -> Supplier | None:
        """Find existing supplier by code or name."""
        if source_name in self._supplier_cache:
            return self._supplier_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            supplier = self.db.get(Supplier, sync_entity.target_id)
            if supplier:
                self._supplier_cache[source_name] = supplier
                return supplier

        code = source_name[:30] if source_name else None
        if code:
            result = self.db.execute(
                select(Supplier).where(
                    Supplier.organization_id == self.organization_id,
                    Supplier.supplier_code == code,
                )
            ).scalar_one_or_none()

            if result:
                self._supplier_cache[source_name] = result
                return result

        return None
