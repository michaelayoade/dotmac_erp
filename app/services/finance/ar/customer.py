"""
CustomerService - Customer master data management.

Manages customer records, credit limits, and risk assessment.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.ar.customer import Customer, CustomerType, RiskCategory
from app.services.common import coerce_uuid
from app.services.finance.ar.input_utils import (
    parse_decimal,
    resolve_currency_code,
)
from app.services.finance.common import (
    get_org_scoped_entity,
    parse_enum_safe,
    toggle_entity_status,
    validate_unique_code,
)
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class CustomerInput:
    """
    Input for creating/updating a customer.

    Uses template-friendly field names for seamless API/web integration.
    Service layer handles mapping to model fields internally.
    """

    # Template-friendly names (what API/templates send)
    customer_type: CustomerType
    customer_name: str  # Maps to model: legal_name
    customer_code: str | None = None
    default_receivable_account_id: UUID | None = (
        None  # Maps to model: ar_control_account_id
    )
    trading_name: str | None = None
    tax_id: str | None = None  # Maps to model: tax_identification_number
    registration_number: str | None = None
    vat_category: str | None = None
    default_tax_code_id: UUID | None = None
    credit_limit: Decimal | None = None
    payment_terms_days: int = 30  # Maps to model: credit_terms_days
    credit_hold: bool = False
    payment_terms_id: UUID | None = None
    currency_code: str = settings.default_functional_currency_code
    price_list_id: UUID | None = None
    default_revenue_account_id: UUID | None = None
    sales_rep_user_id: UUID | None = None
    customer_group_id: UUID | None = None
    risk_category: RiskCategory = RiskCategory.MEDIUM
    is_related_party: bool = False
    related_party_type: str | None = None
    related_party_relationship: str | None = None
    billing_address: dict[str, Any] | None = None
    shipping_address: dict[str, Any] | None = None
    primary_contact: dict[str, Any] | None = None
    bank_details: dict[str, Any] | None = None
    is_active: bool = True
    # Additional template fields (optional - for richer UI forms)
    email: str | None = None
    phone: str | None = None
    address: str | None = None


class CustomerService(ListResponseMixin):
    """
    Service for customer master data management.

    Handles creation, updates, credit management, and queries.
    """

    @staticmethod
    def _parse_customer_type(value: str | None) -> CustomerType:
        parsed = parse_enum_safe(CustomerType, value, CustomerType.COMPANY)
        return parsed or CustomerType.COMPANY

    @staticmethod
    def build_input_from_payload(
        db: Session,
        organization_id: UUID,
        payload: dict,
    ) -> CustomerInput:
        """Build CustomerInput from raw payload (strings or JSON)."""
        org_id = coerce_uuid(organization_id)

        credit_limit = None
        if payload.get("credit_limit") not in (None, ""):
            credit_limit = parse_decimal(payload.get("credit_limit"), "Credit limit")

        payment_terms_raw = payload.get("payment_terms_days", 30)
        try:
            payment_terms_days = int(payment_terms_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid payment terms days") from exc

        currency_code = resolve_currency_code(db, org_id, payload.get("currency_code"))

        return CustomerInput(
            customer_code=payload.get("customer_code", ""),
            customer_type=CustomerService._parse_customer_type(
                payload.get("customer_type")
            ),
            customer_name=payload.get("customer_name", ""),
            trading_name=payload.get("trading_name")
            or payload.get("customer_name", ""),
            tax_id=payload.get("tax_id"),
            currency_code=currency_code,
            payment_terms_days=payment_terms_days,
            credit_limit=credit_limit,
            credit_hold=payload.get("credit_hold") is not None,
            risk_category=RiskCategory.MEDIUM,
            default_receivable_account_id=(
                coerce_uuid(payload.get("default_receivable_account_id"))
                if payload.get("default_receivable_account_id")
                else UUID("00000000-0000-0000-0000-000000000001")
            ),
            default_revenue_account_id=(
                coerce_uuid(payload.get("default_revenue_account_id"))
                if payload.get("default_revenue_account_id")
                else None
            ),
            default_tax_code_id=(
                coerce_uuid(payload.get("default_tax_code_id"))
                if payload.get("default_tax_code_id")
                else None
            ),
            billing_address={
                "address": payload.get("billing_address", ""),
            }
            if payload.get("billing_address")
            else None,
            shipping_address={
                "address": payload.get("shipping_address", ""),
            }
            if payload.get("shipping_address")
            else None,
            primary_contact={
                "email": payload.get("email", ""),
                "phone": payload.get("phone", ""),
            }
            if payload.get("email") or payload.get("phone")
            else None,
            is_active=payload.get("is_active") is not None,
        )

    @staticmethod
    def create_customer(
        db: Session,
        organization_id: UUID,
        input: CustomerInput,
    ) -> Customer:
        """
        Create a new customer.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Customer input data

        Returns:
            Created Customer

        Raises:
            HTTPException(400): If customer code already exists
        """
        org_id = coerce_uuid(organization_id)

        customer_code = input.customer_code or ""
        if not customer_code.strip():
            customer_code = CustomerService._generate_customer_code(db, org_id)

        # Validate unique customer code
        validate_unique_code(
            db=db,
            model_class=Customer,
            org_id=org_id,
            code_value=customer_code,
            code_field_name="customer_code",
            entity_name="Customer",
        )

        # Map template-friendly names to model field names
        customer = Customer(
            organization_id=org_id,
            customer_code=customer_code,
            customer_type=input.customer_type,
            legal_name=input.customer_name,  # template: customer_name → model: legal_name
            trading_name=input.trading_name,
            tax_identification_number=input.tax_id,  # template: tax_id → model: tax_identification_number
            registration_number=input.registration_number,
            vat_category=input.vat_category,
            credit_limit=input.credit_limit,
            credit_terms_days=input.payment_terms_days,  # template: payment_terms_days → model: credit_terms_days
            credit_hold=input.credit_hold,
            payment_terms_id=input.payment_terms_id,
            currency_code=input.currency_code,
            price_list_id=input.price_list_id,
            ar_control_account_id=input.default_receivable_account_id,  # template: default_receivable_account_id → model: ar_control_account_id
            default_revenue_account_id=input.default_revenue_account_id,
            default_tax_code_id=input.default_tax_code_id,
            sales_rep_user_id=input.sales_rep_user_id,
            customer_group_id=input.customer_group_id,
            risk_category=input.risk_category,
            is_related_party=input.is_related_party,
            related_party_type=input.related_party_type,
            related_party_relationship=input.related_party_relationship,
            billing_address=input.billing_address,
            shipping_address=input.shipping_address,
            primary_contact=input.primary_contact,
            bank_details=input.bank_details,
            is_active=input.is_active,
        )

        db.add(customer)
        db.commit()
        db.refresh(customer)

        return customer

    @staticmethod
    def update_customer(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
        input: CustomerInput,
    ) -> Customer:
        """
        Update an existing customer.

        Args:
            db: Database session
            organization_id: Organization scope
            customer_id: Customer to update
            input: Updated customer data

        Returns:
            Updated Customer
        """
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)
        org_id = coerce_uuid(organization_id)

        customer = get_org_scoped_entity(
            db=db,
            model_class=Customer,
            entity_id=cust_id,
            org_id=org_id,
            entity_name="Customer",
        )
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        if not input.customer_code:
            input.customer_code = customer.customer_code

        # Validate unique customer code (if changed)
        if customer.customer_code != input.customer_code:
            validate_unique_code(
                db=db,
                model_class=Customer,
                org_id=org_id,
                code_value=input.customer_code,
                code_field_name="customer_code",
                entity_name="Customer",
                exclude_id=cust_id,
            )

        # Update fields - map template-friendly names to model field names
        customer.customer_code = input.customer_code
        customer.customer_type = input.customer_type
        customer.legal_name = (
            input.customer_name
        )  # template: customer_name → model: legal_name
        customer.trading_name = input.trading_name
        customer.tax_identification_number = (
            input.tax_id
        )  # template: tax_id → model: tax_identification_number
        customer.registration_number = input.registration_number
        if input.vat_category is not None:
            customer.vat_category = input.vat_category
        customer.credit_limit = input.credit_limit
        customer.credit_terms_days = (
            input.payment_terms_days
        )  # template: payment_terms_days → model: credit_terms_days
        customer.credit_hold = input.credit_hold
        customer.payment_terms_id = input.payment_terms_id
        customer.currency_code = input.currency_code
        customer.price_list_id = input.price_list_id
        if input.default_receivable_account_id is not None:
            customer.ar_control_account_id = (
                input.default_receivable_account_id
            )  # template: default_receivable_account_id → model: ar_control_account_id
        customer.default_revenue_account_id = input.default_revenue_account_id
        customer.default_tax_code_id = input.default_tax_code_id
        customer.sales_rep_user_id = input.sales_rep_user_id
        customer.customer_group_id = input.customer_group_id
        customer.risk_category = input.risk_category
        customer.is_related_party = input.is_related_party
        customer.related_party_type = input.related_party_type
        customer.related_party_relationship = input.related_party_relationship
        customer.billing_address = input.billing_address
        customer.shipping_address = input.shipping_address
        customer.primary_contact = input.primary_contact
        customer.bank_details = input.bank_details
        customer.is_active = input.is_active

        db.commit()
        db.refresh(customer)

        return customer

    @staticmethod
    def partial_update_customer(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
        update_data: dict,
    ) -> Customer:
        """
        Partial update a customer - only update provided fields.

        Args:
            db: Database session
            organization_id: Organization scope
            customer_id: Customer to update
            update_data: Dictionary of fields to update (None values are ignored)

        Returns:
            Updated Customer

        Raises:
            HTTPException(404): If customer not found
        """
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        customer = get_org_scoped_entity(
            db=db,
            model_class=Customer,
            entity_id=cust_id,
            org_id=org_id,
            entity_name="Customer",
        )
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Template field name → Model field name mapping
        field_mapping = {
            "customer_name": "legal_name",
            "tax_id": "tax_identification_number",
            "payment_terms_days": "credit_terms_days",
            "default_receivable_account_id": "ar_control_account_id",
            "vat_category": "vat_category",
        }

        # Fields that pass through unchanged (same name in template and model)
        direct_fields = [
            "trading_name",
            "registration_number",
            "credit_limit",
            "credit_hold",
            "payment_terms_id",
            "currency_code",
            "price_list_id",
            "default_revenue_account_id",
            "default_tax_code_id",
            "sales_rep_user_id",
            "customer_group_id",
            "risk_category",
            "is_related_party",
            "related_party_type",
            "related_party_relationship",
            "billing_address",
            "shipping_address",
            "primary_contact",
            "bank_details",
        ]

        # Update mapped fields (template name → model name)
        for template_field, model_field in field_mapping.items():
            if (
                template_field in update_data
                and update_data[template_field] is not None
            ):
                setattr(customer, model_field, update_data[template_field])

        # Update direct fields (same name in both)
        for field in direct_fields:
            if field in update_data and update_data[field] is not None:
                setattr(customer, field, update_data[field])

        # Handle is_active separately
        if "is_active" in update_data and update_data["is_active"] is not None:
            if update_data["is_active"] != customer.is_active:
                customer.is_active = update_data["is_active"]

        db.commit()
        db.refresh(customer)

        return customer

    @staticmethod
    def _generate_customer_code(db: Session, org_id: UUID) -> str:
        """Generate a unique customer code (CUST-00001).

        Delegates to SyncNumberingService for race-condition-safe generation
        via SELECT FOR UPDATE locking.
        """
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(db).generate_next_number(
            org_id, SequenceType.CUSTOMER
        )

    @staticmethod
    def update_credit_limit(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
        new_credit_limit: Decimal,
    ) -> Customer:
        """
        Update customer credit limit.

        Args:
            db: Database session
            organization_id: Organization scope
            customer_id: Customer to update
            new_credit_limit: New credit limit

        Returns:
            Updated Customer
        """
        customer = get_org_scoped_entity(
            db=db,
            model_class=Customer,
            entity_id=customer_id,
            org_id=organization_id,
            entity_name="Customer",
        )
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer.credit_limit = new_credit_limit

        db.commit()
        db.refresh(customer)

        return customer

    @staticmethod
    def update_risk_category(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
        new_risk_category: RiskCategory,
    ) -> Customer:
        """
        Update customer risk category (for IFRS 9 ECL).

        Args:
            db: Database session
            organization_id: Organization scope
            customer_id: Customer to update
            new_risk_category: New risk category

        Returns:
            Updated Customer
        """
        customer = get_org_scoped_entity(
            db=db,
            model_class=Customer,
            entity_id=customer_id,
            org_id=organization_id,
            entity_name="Customer",
        )
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer.risk_category = new_risk_category

        db.commit()
        db.refresh(customer)

        return customer

    @staticmethod
    def deactivate_customer(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
    ) -> Customer:
        """Deactivate a customer (soft delete)."""
        return toggle_entity_status(
            db=db,
            model_class=Customer,
            entity_id=customer_id,
            org_id=organization_id,
            is_active=False,
            entity_name="Customer",
        )

    @staticmethod
    def activate_customer(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
    ) -> Customer:
        """Reactivate a customer."""
        return toggle_entity_status(
            db=db,
            model_class=Customer,
            entity_id=customer_id,
            org_id=organization_id,
            is_active=True,
            entity_name="Customer",
        )

    @staticmethod
    def check_credit_limit(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
        requested_amount: Decimal,
    ) -> tuple[bool, Decimal, Decimal]:
        """
        Check if a transaction would exceed credit limit.

        Args:
            db: Database session
            organization_id: Organization scope
            customer_id: Customer to check
            requested_amount: Amount being requested

        Returns:
            Tuple of (is_within_limit, current_balance, available_credit)
        """
        from app.models.finance.ar.invoice import Invoice, InvoiceStatus

        cust_id = coerce_uuid(customer_id)
        org_id = coerce_uuid(organization_id)

        customer = get_org_scoped_entity(
            db=db,
            model_class=Customer,
            entity_id=customer_id,
            org_id=organization_id,
            entity_name="Customer",
        )
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        if customer.credit_limit is None:
            # No credit limit = unlimited
            return (True, Decimal("0"), Decimal("999999999"))

        # Get outstanding balance
        invoices = db.scalars(
            select(Invoice).where(
                and_(
                    Invoice.organization_id == org_id,
                    Invoice.customer_id == cust_id,
                    Invoice.status.in_(InvoiceStatus.outstanding()),
                )
            )
        )
        invoices = invoices.all()

        current_balance = sum((inv.balance_due for inv in invoices), Decimal("0"))
        available_credit = customer.credit_limit - current_balance
        is_within_limit = (current_balance + requested_amount) <= customer.credit_limit

        return (is_within_limit, current_balance, available_credit)

    @staticmethod
    def get(
        db: Session,
        organization_id: UUID,
        customer_id: str,
    ) -> Customer:
        """Get a customer by ID."""
        customer = get_org_scoped_entity(
            db=db,
            model_class=Customer,
            entity_id=customer_id,
            org_id=organization_id,
            entity_name="Customer",
        )
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        return customer

    @staticmethod
    def get_by_code(
        db: Session,
        organization_id: UUID,
        customer_code: str,
    ) -> Customer | None:
        """Get a customer by code."""
        org_id = coerce_uuid(organization_id)

        return db.scalar(
            select(Customer).where(
                and_(
                    Customer.organization_id == org_id,
                    Customer.customer_code == customer_code,
                )
            )
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str,
        customer_type: CustomerType | None = None,
        risk_category: RiskCategory | None = None,
        is_active: bool | None = None,
        is_related_party: bool | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[Customer]:
        """List customers with optional filters."""
        if not organization_id:
            raise HTTPException(status_code=400, detail="organization_id is required")

        org_id = coerce_uuid(organization_id)
        stmt = select(Customer).where(Customer.organization_id == org_id)

        if customer_type:
            stmt = stmt.where(Customer.customer_type == customer_type)

        if risk_category:
            stmt = stmt.where(Customer.risk_category == risk_category)

        if is_active is not None:
            stmt = stmt.where(Customer.is_active == is_active)

        if is_related_party is not None:
            stmt = stmt.where(Customer.is_related_party == is_related_party)

        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Customer.customer_code.ilike(pattern),
                    Customer.legal_name.ilike(pattern),
                    Customer.trading_name.ilike(pattern),
                    Customer.tax_identification_number.ilike(pattern),
                )
            )

        stmt = stmt.order_by(Customer.legal_name).limit(limit).offset(offset)
        return db.scalars(stmt).all()

    @staticmethod
    def get_customer_summary(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
    ) -> dict[str, Any]:
        """Get customer summary with balance information."""
        from app.models.finance.ar.invoice import Invoice, InvoiceStatus

        cust_id = coerce_uuid(customer_id)
        org_id = coerce_uuid(organization_id)

        customer = get_org_scoped_entity(
            db=db,
            model_class=Customer,
            entity_id=customer_id,
            org_id=organization_id,
            entity_name="Customer",
        )

        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Get outstanding invoices
        invoices = db.scalars(
            select(Invoice).where(
                and_(
                    Invoice.organization_id == org_id,
                    Invoice.customer_id == cust_id,
                    Invoice.status.in_(InvoiceStatus.outstanding()),
                )
            )
        )
        invoices = invoices.all()

        total_outstanding = sum((inv.balance_due for inv in invoices), Decimal("0"))
        invoice_count = len(invoices)

        return {
            "customer_id": customer.customer_id,
            "customer_code": customer.customer_code,
            "legal_name": customer.legal_name,
            "currency_code": customer.currency_code,
            "is_active": customer.is_active,
            "outstanding_balance": total_outstanding,
            "outstanding_invoice_count": invoice_count,
            "credit_limit": customer.credit_limit,
            "available_credit": (customer.credit_limit - total_outstanding)
            if customer.credit_limit
            else None,
            "credit_terms_days": customer.credit_terms_days,
            "risk_category": customer.risk_category.value,
            "is_related_party": customer.is_related_party,
        }

    @staticmethod
    def delete_customer(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
    ) -> None:
        """Delete a customer (only when no invoices/receipts)."""
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        customer = db.get(Customer, cust_id)
        if not customer or customer.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Customer not found")

        from app.models.finance.ar.customer_payment import CustomerPayment
        from app.models.finance.ar.invoice import Invoice

        invoice_count = (
            db.scalar(
                select(func.count(Invoice.invoice_id)).where(
                    Invoice.organization_id == org_id,
                    Invoice.customer_id == cust_id,
                )
            )
            or 0
        )
        if invoice_count > 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot delete customer with {invoice_count} invoice(s). "
                    "Deactivate instead."
                ),
            )

        payment_count = (
            db.scalar(
                select(func.count(CustomerPayment.payment_id)).where(
                    CustomerPayment.organization_id == org_id,
                    CustomerPayment.customer_id == cust_id,
                )
            )
            or 0
        )
        if payment_count > 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot delete customer with {payment_count} receipt(s). "
                    "Deactivate instead."
                ),
            )

        db.delete(customer)
        db.flush()
        db.commit()


# Module-level singleton instance
customer_service = CustomerService()
