"""
SupplierService - Supplier master data management.

Manages vendor/supplier records, validation, and lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import UUID

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.ap.supplier import Supplier, SupplierType
from app.models.finance.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceStatus
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin
from app.services.finance.common import (
    validate_unique_code,
    get_org_scoped_entity,
    toggle_entity_status,
    apply_search_filter,
)


@dataclass
class SupplierInput:
    """
    Input for creating/updating a supplier.

    Uses template-friendly field names for seamless API/web integration.
    Service layer handles mapping to model fields internally.
    """

    # Template-friendly names (what API/templates send)
    supplier_code: str
    supplier_type: SupplierType
    supplier_name: str  # Maps to model: legal_name
    default_payable_account_id: Optional[UUID] = None  # Maps to model: ap_control_account_id
    trading_name: Optional[str] = None
    tax_id: Optional[str] = None  # Maps to model: tax_identification_number
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
    # Additional template fields (optional - for richer UI forms)
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_method: Optional[str] = None


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

        # Validate unique supplier code
        validate_unique_code(
            db=db,
            model_class=Supplier,
            org_id=org_id,
            code_value=input.supplier_code,
            code_field_name="supplier_code",
            entity_name="Supplier",
        )

        # Map template-friendly names to model field names
        supplier = Supplier(
            organization_id=org_id,
            supplier_code=input.supplier_code,
            supplier_type=input.supplier_type,
            legal_name=input.supplier_name,  # template: supplier_name → model: legal_name
            trading_name=input.trading_name,
            tax_identification_number=input.tax_id,  # template: tax_id → model: tax_identification_number
            registration_number=input.registration_number,
            payment_terms_days=input.payment_terms_days,
            currency_code=input.currency_code,
            default_expense_account_id=input.default_expense_account_id,
            ap_control_account_id=input.default_payable_account_id,  # template: default_payable_account_id → model: ap_control_account_id
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

        supplier = get_org_scoped_entity(
            db=db,
            model_class=Supplier,
            entity_id=sup_id,
            org_id=org_id,
            entity_name="Supplier",
        )
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found")

        # Validate unique supplier code (if changed)
        if supplier.supplier_code != input.supplier_code:
            validate_unique_code(
                db=db,
                model_class=Supplier,
                org_id=org_id,
                code_value=input.supplier_code,
                code_field_name="supplier_code",
                entity_name="Supplier",
                exclude_id=sup_id,
            )

        # Update fields - map template-friendly names to model field names
        supplier.supplier_code = input.supplier_code
        supplier.supplier_type = input.supplier_type
        supplier.legal_name = input.supplier_name  # template: supplier_name → model: legal_name
        supplier.trading_name = input.trading_name
        supplier.tax_identification_number = input.tax_id  # template: tax_id → model: tax_identification_number
        supplier.registration_number = input.registration_number
        supplier.payment_terms_days = input.payment_terms_days
        supplier.currency_code = input.currency_code
        supplier.default_expense_account_id = input.default_expense_account_id
        if input.default_payable_account_id is not None:
            supplier.ap_control_account_id = input.default_payable_account_id  # template: default_payable_account_id → model: ap_control_account_id
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
    def partial_update_supplier(
        db: Session,
        organization_id: UUID,
        supplier_id: UUID,
        update_data: dict,
    ) -> Supplier:
        """
        Partial update a supplier - only update provided fields.

        Args:
            db: Database session
            organization_id: Organization scope
            supplier_id: Supplier to update
            update_data: Dictionary of fields to update (None values are ignored)

        Returns:
            Updated Supplier

        Raises:
            HTTPException(404): If supplier not found
        """
        org_id = coerce_uuid(organization_id)
        sup_id = coerce_uuid(supplier_id)

        supplier = get_org_scoped_entity(
            db=db,
            model_class=Supplier,
            entity_id=sup_id,
            org_id=org_id,
            entity_name="Supplier",
        )
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found")

        # Template field name → Model field name mapping
        field_mapping = {
            "supplier_name": "legal_name",
            "tax_id": "tax_identification_number",
            "default_payable_account_id": "ap_control_account_id",
        }

        # Fields that pass through unchanged (same name in template and model)
        direct_fields = [
            "trading_name",
            "registration_number",
            "payment_terms_days",
            "currency_code",
            "default_expense_account_id",
            "supplier_group_id",
            "is_related_party",
            "related_party_relationship",
            "withholding_tax_applicable",
            "withholding_tax_code_id",
            "billing_address",
            "remittance_address",
            "primary_contact",
            "bank_details",
        ]

        # Update mapped fields (template name → model name)
        for template_field, model_field in field_mapping.items():
            if template_field in update_data and update_data[template_field] is not None:
                setattr(supplier, model_field, update_data[template_field])

        # Update direct fields (same name in both)
        for field in direct_fields:
            if field in update_data and update_data[field] is not None:
                setattr(supplier, field, update_data[field])

        # Handle is_active separately - requires activate/deactivate logic
        if "is_active" in update_data and update_data["is_active"] is not None:
            if update_data["is_active"] != supplier.is_active:
                if update_data["is_active"]:
                    # Activating - no checks needed
                    supplier.is_active = True
                else:
                    # Deactivating - check for outstanding balance
                    SupplierService._check_no_outstanding_balance(db, supplier)
                    supplier.is_active = False

        db.commit()
        db.refresh(supplier)

        return supplier

    @staticmethod
    def _check_no_outstanding_balance(db: Session, supplier: Supplier) -> None:
        """Pre-check for deactivation: ensure no outstanding balance."""
        outstanding_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        # Note: balance_due is a @property, so we compute it inline for SQL
        outstanding_balance = (
            db.query(func.coalesce(func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid), Decimal("0")))
            .filter(
                and_(
                    SupplierInvoice.supplier_id == supplier.supplier_id,
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
        return toggle_entity_status(
            db=db,
            model_class=Supplier,
            entity_id=supplier_id,
            org_id=organization_id,
            is_active=False,
            entity_name="Supplier",
            pre_check=SupplierService._check_no_outstanding_balance,
        )

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
        return toggle_entity_status(
            db=db,
            model_class=Supplier,
            entity_id=supplier_id,
            org_id=organization_id,
            is_active=True,
            entity_name="Supplier",
        )

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
        supplier = get_org_scoped_entity(
            db=db,
            model_class=Supplier,
            entity_id=supplier_id,
            org_id=organization_id,
            entity_name="Supplier",
        )
        if not supplier:
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
    ) -> List[Supplier]:
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

        query = apply_search_filter(
            query,
            search,
            [
                Supplier.supplier_code,
                Supplier.legal_name,
                Supplier.trading_name,
                Supplier.tax_identification_number,
            ],
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
        sup_id = coerce_uuid(supplier_id)

        supplier = get_org_scoped_entity(
            db=db,
            model_class=Supplier,
            entity_id=supplier_id,
            org_id=organization_id,
            entity_name="Supplier",
        )
        if not supplier:
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

        total_outstanding = sum((inv.balance_due for inv in invoices), Decimal("0"))
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
