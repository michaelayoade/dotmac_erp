"""
Contacts Importer (Customers and Suppliers).

Imports customer and vendor data from Zoho Books CSV exports.
"""

import re
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer, CustomerType, RiskCategory
from app.models.finance.ap.supplier import Supplier, SupplierType
from app.models.finance.gl.account import Account

from .base import BaseImporter, FieldMapping, ImportConfig


class CustomerImporter(BaseImporter[Customer]):
    """
    Importer for customers from Zoho Books Contacts.csv export.

    CSV Format (Zoho Books Contacts.csv):
    - Display Name: Customer display name
    - Company Name: Company name (if applicable)
    - First Name, Last Name: Individual name
    - Phone: Phone number
    - Currency Code: Default currency
    - Status: Active/Inactive
    - Credit Limit: Credit limit amount
    - Billing Address, Billing City, etc.: Billing address fields
    - Shipping Address, etc.: Shipping address fields
    - Payment Terms: Payment term days
    """

    entity_name = "Customer"
    model_class = Customer

    def __init__(self, db: Session, config: ImportConfig, ar_control_account_id: UUID):
        super().__init__(db, config)
        self.ar_control_account_id = ar_control_account_id
        self._code_counter = 0

    def get_field_mappings(self) -> List[FieldMapping]:
        """Define field mappings from Zoho CSV to Customer model."""
        return [
            FieldMapping("Display Name", "display_name", required=True),
            FieldMapping("Company Name", "company_name", required=False),
            FieldMapping("First Name", "first_name", required=False),
            FieldMapping("Last Name", "last_name", required=False),
            FieldMapping("Phone", "phone", required=False),
            FieldMapping("Currency Code", "currency_code", required=False, default="NGN"),
            FieldMapping("Status", "is_active", required=False,
                         transformer=lambda v: v != "Inactive", default=True),
            FieldMapping("Credit Limit", "credit_limit", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Payment Terms", "payment_terms_days", required=False,
                         transformer=lambda v: int(v) if v and v.isdigit() else 30,
                         default=30),
            # Billing address fields
            FieldMapping("Billing Attention", "billing_attention", required=False),
            FieldMapping("Billing Address", "billing_street", required=False),
            FieldMapping("Billing Street2", "billing_street2", required=False),
            FieldMapping("Billing City", "billing_city", required=False),
            FieldMapping("Billing State", "billing_state", required=False),
            FieldMapping("Billing Country", "billing_country", required=False),
            FieldMapping("Billing Code", "billing_postal_code", required=False),
            FieldMapping("Billing Phone", "billing_phone", required=False),
            # Shipping address fields
            FieldMapping("Shipping Attention", "shipping_attention", required=False),
            FieldMapping("Shipping Address", "shipping_street", required=False),
            FieldMapping("Shipping Street2", "shipping_street2", required=False),
            FieldMapping("Shipping City", "shipping_city", required=False),
            FieldMapping("Shipping State", "shipping_state", required=False),
            FieldMapping("Shipping Country", "shipping_country", required=False),
            FieldMapping("Shipping Code", "shipping_postal_code", required=False),
            FieldMapping("Shipping Phone", "shipping_phone", required=False),
            # Contact info
            FieldMapping("Notes", "notes", required=False),
            FieldMapping("Website", "website", required=False),
            FieldMapping("Customer Sub Type", "customer_sub_type", required=False),
        ]

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        """Unique key is the display name."""
        return row.get("Display Name", "").strip()

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[Customer]:
        """Check if customer already exists by name."""
        name = self.get_unique_key(row)
        if not name:
            return None

        # Check by legal_name
        existing = self.db.execute(
            select(Customer).where(
                Customer.organization_id == self.config.organization_id,
                Customer.legal_name == name,
            )
        ).scalar_one_or_none()

        return existing

    def create_entity(self, row: Dict[str, Any]) -> Customer:
        """Create a new customer from transformed row data."""
        display_name = row.get("display_name", "").strip()
        company_name = row.get("company_name", "").strip()
        first_name = row.get("first_name", "").strip()
        last_name = row.get("last_name", "").strip()

        # Determine customer type
        if company_name:
            customer_type = CustomerType.COMPANY
            legal_name = company_name
            trading_name = display_name if display_name != company_name else None
        elif first_name or last_name:
            customer_type = CustomerType.INDIVIDUAL
            legal_name = display_name or f"{first_name} {last_name}".strip()
            trading_name = None
        else:
            customer_type = CustomerType.COMPANY
            legal_name = display_name
            trading_name = None

        # Generate customer code
        self._code_counter += 1
        customer_code = f"CUST{self._code_counter:05d}"

        # Build billing address JSONB
        billing_address = self._build_address(row, "billing")
        shipping_address = self._build_address(row, "shipping")

        # Build primary contact JSONB
        primary_contact = {
            "name": display_name,
            "phone": row.get("phone"),
            "email": row.get("email"),
        }
        primary_contact = {k: v for k, v in primary_contact.items() if v}

        customer = Customer(
            customer_id=uuid4(),
            organization_id=self.config.organization_id,
            customer_code=customer_code,
            customer_type=customer_type,
            legal_name=legal_name[:255],
            trading_name=trading_name[:255] if trading_name else None,
            credit_limit=row.get("credit_limit"),
            credit_terms_days=row.get("payment_terms_days", 30),
            currency_code=row.get("currency_code", "NGN") or "NGN",
            ar_control_account_id=self.ar_control_account_id,
            risk_category=RiskCategory.MEDIUM,
            is_related_party=False,
            billing_address=billing_address if billing_address else None,
            shipping_address=shipping_address if shipping_address else None,
            primary_contact=primary_contact if primary_contact else None,
            is_active=row.get("is_active", True),
            created_by_user_id=self.config.user_id,
        )

        return customer

    def _build_address(self, row: Dict[str, Any], prefix: str) -> Optional[Dict[str, Any]]:
        """Build address JSONB from row data."""
        address = {
            "attention": row.get(f"{prefix}_attention"),
            "street": row.get(f"{prefix}_street"),
            "street2": row.get(f"{prefix}_street2"),
            "city": row.get(f"{prefix}_city"),
            "state": row.get(f"{prefix}_state"),
            "country": row.get(f"{prefix}_country"),
            "postal_code": row.get(f"{prefix}_postal_code"),
            "phone": row.get(f"{prefix}_phone"),
        }
        # Remove None values
        address = {k: v for k, v in address.items() if v}
        return address if address else None


class SupplierImporter(BaseImporter[Supplier]):
    """
    Importer for suppliers/vendors from Zoho Books Vendors.csv export.

    CSV Format (Zoho Books Vendors.csv):
    - Contact Name / Display Name: Vendor name
    - Company Name: Company name
    - Phone, MobilePhone: Contact numbers
    - Currency Code: Default currency
    - Status: Active/Inactive
    - Payment Terms: Payment term days
    - Billing/Shipping address fields
    """

    entity_name = "Supplier"
    model_class = Supplier

    def __init__(self, db: Session, config: ImportConfig, ap_control_account_id: UUID):
        super().__init__(db, config)
        self.ap_control_account_id = ap_control_account_id
        self._code_counter = 0

    def get_field_mappings(self) -> List[FieldMapping]:
        """Define field mappings from Zoho CSV to Supplier model."""
        return [
            FieldMapping("Display Name", "display_name", required=False),
            FieldMapping("Contact Name", "contact_name", required=False),
            FieldMapping("Company Name", "company_name", required=False),
            FieldMapping("First Name", "first_name", required=False),
            FieldMapping("Last Name", "last_name", required=False),
            FieldMapping("Phone", "phone", required=False),
            FieldMapping("MobilePhone", "mobile_phone", required=False),
            FieldMapping("EmailID", "email", required=False),
            FieldMapping("Currency Code", "currency_code", required=False, default="NGN"),
            FieldMapping("Status", "is_active", required=False,
                         transformer=lambda v: v != "Inactive", default=True),
            FieldMapping("Payment Terms", "payment_terms_days", required=False,
                         transformer=lambda v: int(v) if v and str(v).isdigit() else 30,
                         default=30),
            FieldMapping("Taxable", "taxable", required=False,
                         transformer=self.parse_boolean, default=False),
            FieldMapping("Tax Name", "tax_name", required=False),
            FieldMapping("Tax Percentage", "tax_percentage", required=False,
                         transformer=self.parse_decimal),
            # Billing address fields
            FieldMapping("Billing Attention", "billing_attention", required=False),
            FieldMapping("Billing Address", "billing_street", required=False),
            FieldMapping("Billing Street2", "billing_street2", required=False),
            FieldMapping("Billing City", "billing_city", required=False),
            FieldMapping("Billing State", "billing_state", required=False),
            FieldMapping("Billing Country", "billing_country", required=False),
            FieldMapping("Billing Code", "billing_postal_code", required=False),
            FieldMapping("Billing Phone", "billing_phone", required=False),
            # Remittance/Shipping address fields
            FieldMapping("Shipping Attention", "remittance_attention", required=False),
            FieldMapping("Shipping Address", "remittance_street", required=False),
            FieldMapping("Shipping Street2", "remittance_street2", required=False),
            FieldMapping("Shipping City", "remittance_city", required=False),
            FieldMapping("Shipping State", "remittance_state", required=False),
            FieldMapping("Shipping Country", "remittance_country", required=False),
            FieldMapping("Shipping Code", "remittance_postal_code", required=False),
            FieldMapping("Shipping Phone", "remittance_phone", required=False),
            # Other
            FieldMapping("Notes", "notes", required=False),
            FieldMapping("Website", "website", required=False),
        ]

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        """Unique key is the display name or contact name."""
        return (row.get("Display Name") or row.get("Contact Name") or "").strip()

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[Supplier]:
        """Check if supplier already exists by name."""
        name = self.get_unique_key(row)
        if not name:
            return None

        existing = self.db.execute(
            select(Supplier).where(
                Supplier.organization_id == self.config.organization_id,
                Supplier.legal_name == name,
            )
        ).scalar_one_or_none()

        return existing

    def create_entity(self, row: Dict[str, Any]) -> Supplier:
        """Create a new supplier from transformed row data."""
        display_name = (row.get("display_name") or row.get("contact_name") or "").strip()
        company_name = row.get("company_name", "").strip()

        # Determine supplier type
        if company_name:
            supplier_type = SupplierType.VENDOR
            legal_name = company_name
            trading_name = display_name if display_name != company_name else None
        else:
            supplier_type = SupplierType.VENDOR
            legal_name = display_name
            trading_name = None

        # Generate supplier code
        self._code_counter += 1
        supplier_code = f"SUPP{self._code_counter:05d}"

        # Build addresses
        billing_address = self._build_address(row, "billing")
        remittance_address = self._build_address(row, "remittance")

        # Build primary contact
        primary_contact = {
            "name": display_name,
            "phone": row.get("phone") or row.get("mobile_phone"),
            "email": row.get("email"),
        }
        primary_contact = {k: v for k, v in primary_contact.items() if v}

        # Determine if withholding tax applicable
        withholding_tax_applicable = bool(row.get("taxable"))

        supplier = Supplier(
            supplier_id=uuid4(),
            organization_id=self.config.organization_id,
            supplier_code=supplier_code,
            supplier_type=supplier_type,
            legal_name=legal_name[:255],
            trading_name=trading_name[:255] if trading_name else None,
            payment_terms_days=row.get("payment_terms_days", 30),
            currency_code=row.get("currency_code", "NGN") or "NGN",
            ap_control_account_id=self.ap_control_account_id,
            is_related_party=False,
            withholding_tax_applicable=withholding_tax_applicable,
            billing_address=billing_address if billing_address else None,
            remittance_address=remittance_address if remittance_address else None,
            primary_contact=primary_contact if primary_contact else None,
            is_active=row.get("is_active", True),
            created_by_user_id=self.config.user_id,
        )

        return supplier

    def _build_address(self, row: Dict[str, Any], prefix: str) -> Optional[Dict[str, Any]]:
        """Build address JSONB from row data."""
        address = {
            "attention": row.get(f"{prefix}_attention"),
            "street": row.get(f"{prefix}_street"),
            "street2": row.get(f"{prefix}_street2"),
            "city": row.get(f"{prefix}_city"),
            "state": row.get(f"{prefix}_state"),
            "country": row.get(f"{prefix}_country"),
            "postal_code": row.get(f"{prefix}_postal_code"),
            "phone": row.get(f"{prefix}_phone"),
        }
        address = {k: v for k, v in address.items() if v}
        return address if address else None


def get_ar_control_account(db: Session, organization_id: UUID) -> Optional[UUID]:
    """Find the AR control account for the organization."""
    account = db.execute(
        select(Account).where(
            Account.organization_id == organization_id,
            Account.subledger_type == "AR",
        )
    ).scalar_one_or_none()
    return account.account_id if account else None


def get_ap_control_account(db: Session, organization_id: UUID) -> Optional[UUID]:
    """Find the AP control account for the organization."""
    account = db.execute(
        select(Account).where(
            Account.organization_id == organization_id,
            Account.subledger_type == "AP",
        )
    ).scalar_one_or_none()
    return account.account_id if account else None
