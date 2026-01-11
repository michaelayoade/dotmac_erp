"""
SupplierService - Supplier master data management.

Manages vendor/supplier records, validation, and lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ifrs.ap.supplier import Supplier, SupplierType
from app.models.ifrs.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceStatus
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class SupplierInput:
    """Input for creating/updating a supplier."""

    supplier_code: str
    supplier_type: SupplierType
    legal_name: str
    ap_control_account_id: UUID
    trading_name: Optional[str] = None
    tax_identification_number: Optional[str] = None
    registration_number: Optional[str] = None
    payment_terms_days: int = 30
    currency_code: str = settings.default_functional_currency_code
    default_expense_account_id: Optional[UUID] = None
    supplier_group_id: Optional[UUID] = None
    is_related_party: bool = False
    related_party_relationship: Optional[str] = None
    withholding_tax_applicable: bool = False
    withholding_tax_code_id: Optional[UUID] = None
    billing_address: Optional[dict[str, Any]] = None
    remittance_address: Optional[dict[str, Any]] = None
    primary_contact: Optional[dict[str, Any]] = None
    bank_details: Optional[dict[str, Any]] = None


class SupplierService(ListResponseMixin):
    """
    Service for supplier master data management.

    Handles creation, updates, validation, and queries for supplier records.
    """

    @staticmethod
    def create_supplier(
        db: Session,
        organization_id: UUID,
        input: SupplierInput,
    ) -> Supplier:
        """
        Create a new supplier.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Supplier input data

        Returns:
            Created Supplier

        Raises:
            HTTPException(400): If supplier code already exists
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate supplier code
        existing = (
            db.query(Supplier)
            .filter(
                and_(
                    Supplier.organization_id == org_id,
                    Supplier.supplier_code == input.supplier_code,
                )
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Supplier code '{input.supplier_code}' already exists",
            )

        supplier = Supplier(
            organization_id=org_id,
            supplier_code=input.supplier_code,
            supplier_type=input.supplier_type,
            legal_name=input.legal_name,
            trading_name=input.trading_name,
            tax_identification_number=input.tax_identification_number,
            registration_number=input.registration_number,
            payment_terms_days=input.payment_terms_days,
            currency_code=input.currency_code,
            default_expense_account_id=input.default_expense_account_id,
            ap_control_account_id=input.ap_control_account_id,
            supplier_group_id=input.supplier_group_id,
            is_related_party=input.is_related_party,
            related_party_relationship=input.related_party_relationship,
            withholding_tax_applicable=input.withholding_tax_applicable,
            withholding_tax_code_id=input.withholding_tax_code_id,
            billing_address=input.billing_address,
            remittance_address=input.remittance_address,
            primary_contact=input.primary_contact,
            bank_details=input.bank_details,
            is_active=True,
        )

        db.add(supplier)
        db.commit()
        db.refresh(supplier)

        return supplier

    @staticmethod
    def update_supplier(
        db: Session,
        organization_id: UUID,
        supplier_id: UUID,
        input: SupplierInput,
    ) -> Supplier:
        """
        Update an existing supplier.

        Args:
            db: Database session
            organization_id: Organization scope
            supplier_id: Supplier to update
            input: Updated supplier data

        Returns:
            Updated Supplier

        Raises:
            HTTPException(404): If supplier not found
            HTTPException(400): If supplier code conflicts with another supplier
        """
        org_id = coerce_uuid(organization_id)
        sup_id = coerce_uuid(supplier_id)

        supplier = db.get(Supplier, sup_id)
        if not supplier or supplier.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Supplier not found")

        # Check for duplicate supplier code (if changed)
        if supplier.supplier_code != input.supplier_code:
            existing = (
                db.query(Supplier)
                .filter(
                    and_(
                        Supplier.organization_id == org_id,
                        Supplier.supplier_code == input.supplier_code,
                        Supplier.supplier_id != sup_id,
                    )
                )
                .first()
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Supplier code '{input.supplier_code}' already exists",
                )

        # Update fields
        supplier.supplier_code = input.supplier_code
        supplier.supplier_type = input.supplier_type
        supplier.legal_name = input.legal_name
        supplier.trading_name = input.trading_name
        supplier.tax_identification_number = input.tax_identification_number
        supplier.registration_number = input.registration_number
        supplier.payment_terms_days = input.payment_terms_days
        supplier.currency_code = input.currency_code
        supplier.default_expense_account_id = input.default_expense_account_id
        supplier.ap_control_account_id = input.ap_control_account_id
        supplier.supplier_group_id = input.supplier_group_id
        supplier.is_related_party = input.is_related_party
        supplier.related_party_relationship = input.related_party_relationship
        supplier.withholding_tax_applicable = input.withholding_tax_applicable
        supplier.withholding_tax_code_id = input.withholding_tax_code_id
        supplier.billing_address = input.billing_address
        supplier.remittance_address = input.remittance_address
        supplier.primary_contact = input.primary_contact
        supplier.bank_details = input.bank_details

        db.commit()
        db.refresh(supplier)

        return supplier

    @staticmethod
    def deactivate_supplier(
        db: Session,
        organization_id: UUID,
        supplier_id: UUID,
    ) -> Supplier:
        """
        Deactivate a supplier (soft delete).

        Args:
            db: Database session
            organization_id: Organization scope
            supplier_id: Supplier to deactivate

        Returns:
            Updated Supplier

        Raises:
            HTTPException(404): If supplier not found
            HTTPException(400): If supplier has outstanding balance
        """
        org_id = coerce_uuid(organization_id)
        sup_id = coerce_uuid(supplier_id)

        supplier = db.get(Supplier, sup_id)
        if not supplier or supplier.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Supplier not found")

        # Check for outstanding invoices before deactivation
        outstanding_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        outstanding_balance = (
            db.query(func.coalesce(func.sum(SupplierInvoice.balance_due), Decimal("0")))
            .filter(
                and_(
                    SupplierInvoice.supplier_id == sup_id,
                    SupplierInvoice.status.in_(outstanding_statuses),
                )
            )
            .scalar()
        )

        if outstanding_balance > Decimal("0"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot deactivate supplier with outstanding balance of {outstanding_balance}",
            )

        supplier.is_active = False
        db.commit()
        db.refresh(supplier)

        return supplier

    @staticmethod
    def activate_supplier(
        db: Session,
        organization_id: UUID,
        supplier_id: UUID,
    ) -> Supplier:
        """
        Reactivate a deactivated supplier.

        Args:
            db: Database session
            organization_id: Organization scope
            supplier_id: Supplier to activate

        Returns:
            Updated Supplier

        Raises:
            HTTPException(404): If supplier not found
        """
        org_id = coerce_uuid(organization_id)
        sup_id = coerce_uuid(supplier_id)

        supplier = db.get(Supplier, sup_id)
        if not supplier or supplier.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Supplier not found")

        supplier.is_active = True
        db.commit()
        db.refresh(supplier)

        return supplier

    @staticmethod
    def get(
        db: Session,
        organization_id: UUID,
        supplier_id: str,
    ) -> Supplier:
        """
        Get a supplier by ID.

        Args:
            db: Database session
            supplier_id: Supplier ID

        Returns:
            Supplier

        Raises:
            HTTPException(404): If not found
        """
        org_id = coerce_uuid(organization_id)
        supplier = db.get(Supplier, coerce_uuid(supplier_id))
        if not supplier or supplier.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Supplier not found")
        return supplier

    @staticmethod
    def get_by_code(
        db: Session,
        organization_id: UUID,
        supplier_code: str,
    ) -> Optional[Supplier]:
        """
        Get a supplier by code.

        Args:
            db: Database session
            organization_id: Organization scope
            supplier_code: Supplier code

        Returns:
            Supplier or None
        """
        org_id = coerce_uuid(organization_id)

        return (
            db.query(Supplier)
            .filter(
                and_(
                    Supplier.organization_id == org_id,
                    Supplier.supplier_code == supplier_code,
                )
            )
            .first()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str,
        supplier_type: Optional[SupplierType] = None,
        is_active: Optional[bool] = None,
        is_related_party: Optional[bool] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Supplier]:
        """
        List suppliers with optional filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            supplier_type: Filter by supplier type
            is_active: Filter by active status
            is_related_party: Filter by related party flag
            search: Search in code, name, tax ID
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of Supplier objects
        """
        if not organization_id:
            raise HTTPException(status_code=400, detail="organization_id is required")

        org_id = coerce_uuid(organization_id)
        query = db.query(Supplier).filter(Supplier.organization_id == org_id)

        if supplier_type:
            query = query.filter(Supplier.supplier_type == supplier_type)

        if is_active is not None:
            query = query.filter(Supplier.is_active == is_active)

        if is_related_party is not None:
            query = query.filter(Supplier.is_related_party == is_related_party)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Supplier.supplier_code.ilike(search_pattern))
                | (Supplier.legal_name.ilike(search_pattern))
                | (Supplier.trading_name.ilike(search_pattern))
                | (Supplier.tax_identification_number.ilike(search_pattern))
            )

        query = query.order_by(Supplier.legal_name)
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def get_supplier_summary(
        db: Session,
        organization_id: UUID,
        supplier_id: UUID,
    ) -> dict[str, Any]:
        """
        Get supplier summary with balance information.

        Args:
            db: Database session
            organization_id: Organization scope
            supplier_id: Supplier ID

        Returns:
            Dictionary with supplier summary data
        """
        org_id = coerce_uuid(organization_id)
        sup_id = coerce_uuid(supplier_id)

        supplier = db.get(Supplier, sup_id)
        if not supplier or supplier.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Supplier not found")

        # Get outstanding invoices
        outstanding_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        invoices = (
            db.query(SupplierInvoice)
            .filter(
                and_(
                    SupplierInvoice.supplier_id == sup_id,
                    SupplierInvoice.status.in_(outstanding_statuses),
                )
            )
            .all()
        )

        total_outstanding = sum(inv.balance_due for inv in invoices)
        invoice_count = len(invoices)

        return {
            "supplier_id": supplier.supplier_id,
            "supplier_code": supplier.supplier_code,
            "legal_name": supplier.legal_name,
            "currency_code": supplier.currency_code,
            "is_active": supplier.is_active,
            "outstanding_balance": total_outstanding,
            "outstanding_invoice_count": invoice_count,
            "payment_terms_days": supplier.payment_terms_days,
            "is_related_party": supplier.is_related_party,
        }


# Module-level singleton instance
supplier_service = SupplierService()
