"""
CustomerService - Customer master data management.

Manages customer records, credit limits, and risk assessment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ifrs.ar.customer import Customer, CustomerType, RiskCategory
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class CustomerInput:
    """Input for creating/updating a customer."""

    customer_code: str
    customer_type: CustomerType
    legal_name: str
    ar_control_account_id: UUID
    trading_name: Optional[str] = None
    tax_identification_number: Optional[str] = None
    registration_number: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    credit_terms_days: int = 30
    credit_hold: bool = False
    payment_terms_id: Optional[UUID] = None
    currency_code: str = settings.default_functional_currency_code
    price_list_id: Optional[UUID] = None
    default_revenue_account_id: Optional[UUID] = None
    sales_rep_user_id: Optional[UUID] = None
    customer_group_id: Optional[UUID] = None
    risk_category: RiskCategory = RiskCategory.MEDIUM
    is_related_party: bool = False
    related_party_type: Optional[str] = None
    related_party_relationship: Optional[str] = None
    billing_address: Optional[dict[str, Any]] = None
    shipping_address: Optional[dict[str, Any]] = None
    primary_contact: Optional[dict[str, Any]] = None
    bank_details: Optional[dict[str, Any]] = None
    is_active: bool = True


class CustomerService(ListResponseMixin):
    """
    Service for customer master data management.

    Handles creation, updates, credit management, and queries.
    """

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

        # Check for duplicate customer code
        existing = (
            db.query(Customer)
            .filter(
                and_(
                    Customer.organization_id == org_id,
                    Customer.customer_code == input.customer_code,
                )
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Customer code '{input.customer_code}' already exists",
            )

        customer = Customer(
            organization_id=org_id,
            customer_code=input.customer_code,
            customer_type=input.customer_type,
            legal_name=input.legal_name,
            trading_name=input.trading_name,
            tax_identification_number=input.tax_identification_number,
            registration_number=input.registration_number,
            credit_limit=input.credit_limit,
            credit_terms_days=input.credit_terms_days,
            credit_hold=input.credit_hold,
            payment_terms_id=input.payment_terms_id,
            currency_code=input.currency_code,
            price_list_id=input.price_list_id,
            ar_control_account_id=input.ar_control_account_id,
            default_revenue_account_id=input.default_revenue_account_id,
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

        customer = db.get(Customer, cust_id)
        if not customer or customer.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Check for duplicate customer code
        if customer.customer_code != input.customer_code:
            existing = (
                db.query(Customer)
                .filter(
                    and_(
                        Customer.organization_id == org_id,
                        Customer.customer_code == input.customer_code,
                        Customer.customer_id != cust_id,
                    )
                )
                .first()
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Customer code '{input.customer_code}' already exists",
                )

        # Update fields
        customer.customer_code = input.customer_code
        customer.customer_type = input.customer_type
        customer.legal_name = input.legal_name
        customer.trading_name = input.trading_name
        customer.tax_identification_number = input.tax_identification_number
        customer.registration_number = input.registration_number
        customer.credit_limit = input.credit_limit
        customer.credit_terms_days = input.credit_terms_days
        customer.credit_hold = input.credit_hold
        customer.payment_terms_id = input.payment_terms_id
        customer.currency_code = input.currency_code
        customer.price_list_id = input.price_list_id
        customer.ar_control_account_id = input.ar_control_account_id
        customer.default_revenue_account_id = input.default_revenue_account_id
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
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        customer = db.get(Customer, cust_id)
        if not customer or customer.organization_id != org_id:
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
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        customer = db.get(Customer, cust_id)
        if not customer or customer.organization_id != org_id:
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
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        customer = db.get(Customer, cust_id)
        if not customer or customer.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer.is_active = False
        db.commit()
        db.refresh(customer)

        return customer

    @staticmethod
    def activate_customer(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
    ) -> Customer:
        """Reactivate a customer."""
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        customer = db.get(Customer, cust_id)
        if not customer or customer.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer.is_active = True
        db.commit()
        db.refresh(customer)

        return customer

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
        from app.models.ifrs.ar.invoice import Invoice, InvoiceStatus

        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        customer = db.get(Customer, cust_id)
        if not customer or customer.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Customer not found")

        if customer.credit_limit is None:
            # No credit limit = unlimited
            return (True, Decimal("0"), Decimal("999999999"))

        # Get outstanding balance
        outstanding_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        invoices = (
            db.query(Invoice)
            .filter(
                and_(
                    Invoice.customer_id == cust_id,
                    Invoice.status.in_(outstanding_statuses),
                )
            )
            .all()
        )

        current_balance = sum(inv.balance_due for inv in invoices)
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
        org_id = coerce_uuid(organization_id)
        customer = db.get(Customer, coerce_uuid(customer_id))
        if not customer or customer.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Customer not found")
        return customer

    @staticmethod
    def get_by_code(
        db: Session,
        organization_id: UUID,
        customer_code: str,
    ) -> Optional[Customer]:
        """Get a customer by code."""
        org_id = coerce_uuid(organization_id)

        return (
            db.query(Customer)
            .filter(
                and_(
                    Customer.organization_id == org_id,
                    Customer.customer_code == customer_code,
                )
            )
            .first()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str,
        customer_type: Optional[CustomerType] = None,
        risk_category: Optional[RiskCategory] = None,
        is_active: Optional[bool] = None,
        is_related_party: Optional[bool] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Customer]:
        """List customers with optional filters."""
        if not organization_id:
            raise HTTPException(status_code=400, detail="organization_id is required")

        org_id = coerce_uuid(organization_id)
        query = db.query(Customer).filter(Customer.organization_id == org_id)

        if customer_type:
            query = query.filter(Customer.customer_type == customer_type)

        if risk_category:
            query = query.filter(Customer.risk_category == risk_category)

        if is_active is not None:
            query = query.filter(Customer.is_active == is_active)

        if is_related_party is not None:
            query = query.filter(Customer.is_related_party == is_related_party)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Customer.customer_code.ilike(search_pattern))
                | (Customer.legal_name.ilike(search_pattern))
                | (Customer.trading_name.ilike(search_pattern))
                | (Customer.tax_identification_number.ilike(search_pattern))
            )

        query = query.order_by(Customer.legal_name)
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def get_customer_summary(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
    ) -> dict[str, Any]:
        """Get customer summary with balance information."""
        from app.models.ifrs.ar.invoice import Invoice, InvoiceStatus

        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        customer = db.get(Customer, cust_id)
        if not customer or customer.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Get outstanding invoices
        outstanding_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        invoices = (
            db.query(Invoice)
            .filter(
                and_(
                    Invoice.customer_id == cust_id,
                    Invoice.status.in_(outstanding_statuses),
                )
            )
            .all()
        )

        total_outstanding = sum(inv.balance_due for inv in invoices)
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


# Module-level singleton instance
customer_service = CustomerService()
