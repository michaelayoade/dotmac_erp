"""
CapitalizationService - Asset creation from AP invoices.

Handles the automatic creation of fixed assets from supplier invoices
when lines are marked for capitalization.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_category import AssetCategory
from app.services.common import coerce_uuid
from app.services.fixed_assets.asset import AssetService, AssetInput


@dataclass
class CapitalizationResult:
    """Result of a capitalization operation."""

    success: bool
    asset_ids: list[UUID]
    message: str
    errors: list[str]


class CapitalizationService:
    """
    Service for creating fixed assets from AP invoice lines.

    Implements the AP → FA integration for capitalizable purchases.
    Assets are created in DRAFT status for user review and activation.
    """

    @staticmethod
    def validate_capitalization_threshold(
        db: Session,
        organization_id: UUID,
        category_id: UUID,
        amount: Decimal,
    ) -> tuple[bool, str]:
        """
        Validate that an amount meets the capitalization threshold for a category.

        Args:
            db: Database session
            organization_id: Organization scope
            category_id: Asset category to check
            amount: Amount to validate

        Returns:
            Tuple of (is_valid, message)
        """
        org_id = coerce_uuid(organization_id)
        cat_id = coerce_uuid(category_id)

        category = db.get(AssetCategory, cat_id)
        if not category or category.organization_id != org_id:
            return False, "Asset category not found"

        if not category.is_active:
            return False, "Asset category is not active"

        threshold = category.capitalization_threshold or Decimal("0")
        if amount < threshold:
            return False, f"Amount {amount} is below capitalization threshold {threshold}"

        return True, "Amount meets capitalization threshold"

    @staticmethod
    def create_assets_from_invoice(
        db: Session,
        organization_id: UUID,
        invoice: SupplierInvoice,
        lines: list[SupplierInvoiceLine],
        supplier: Supplier,
        user_id: UUID,
    ) -> CapitalizationResult:
        """
        Create draft fixed assets from invoice lines marked for capitalization.

        For each line with capitalize_flag=True and asset_category_id set:
        - Validates the amount meets the category's capitalization threshold
        - Creates a DRAFT asset with source document linkage
        - Updates the invoice line with the created_asset_id

        Args:
            db: Database session
            organization_id: Organization scope
            invoice: The supplier invoice being posted
            lines: Invoice lines to process
            supplier: Supplier for asset supplier_id
            user_id: User creating the assets

        Returns:
            CapitalizationResult with created asset IDs and any errors
        """
        org_id = coerce_uuid(organization_id)
        uid = coerce_uuid(user_id)

        created_asset_ids: list[UUID] = []
        errors: list[str] = []

        for line in lines:
            # Skip lines not marked for capitalization
            if not line.capitalize_flag:
                continue

            # Skip lines without asset category
            if not line.asset_category_id:
                errors.append(
                    f"Line {line.line_number}: capitalize_flag set but no asset_category_id"
                )
                continue

            # Get the amount (line amount without tax for asset cost)
            # Tax is typically not capitalized for depreciable assets
            amount = abs(line.line_amount)

            # Validate capitalization threshold
            is_valid, message = CapitalizationService.validate_capitalization_threshold(
                db=db,
                organization_id=org_id,
                category_id=line.asset_category_id,
                amount=amount,
            )

            if not is_valid:
                errors.append(f"Line {line.line_number}: {message}")
                continue

            # Get category for defaults
            category = db.get(AssetCategory, line.asset_category_id)
            if not category:
                errors.append(f"Line {line.line_number}: Category not found")
                continue

            # Create asset input
            asset_input = AssetInput(
                asset_name=CapitalizationService._generate_asset_name(
                    line.description, supplier.legal_name
                ),
                category_id=line.asset_category_id,
                acquisition_date=invoice.invoice_date,
                acquisition_cost=amount,
                currency_code=invoice.currency_code,
                description=f"From Invoice {invoice.invoice_number}: {line.description}",
                source_type="SUPPLIER_INVOICE",
                source_document_id=invoice.invoice_id,
                supplier_id=supplier.supplier_id,
                invoice_reference=invoice.supplier_invoice_number or invoice.invoice_number,
                exchange_rate=invoice.exchange_rate or Decimal("1.0"),
            )

            try:
                # Create the asset in DRAFT status
                asset = AssetService.create_asset(
                    db=db,
                    organization_id=org_id,
                    input=asset_input,
                    created_by_user_id=uid,
                )

                # Link asset back to invoice line
                line.created_asset_id = asset.asset_id
                created_asset_ids.append(asset.asset_id)

            except Exception as e:
                errors.append(f"Line {line.line_number}: Failed to create asset - {str(e)}")

        # Determine overall result
        if created_asset_ids:
            if errors:
                return CapitalizationResult(
                    success=True,
                    asset_ids=created_asset_ids,
                    message=f"Created {len(created_asset_ids)} asset(s) with {len(errors)} error(s)",
                    errors=errors,
                )
            else:
                return CapitalizationResult(
                    success=True,
                    asset_ids=created_asset_ids,
                    message=f"Successfully created {len(created_asset_ids)} draft asset(s)",
                    errors=[],
                )
        else:
            if errors:
                return CapitalizationResult(
                    success=False,
                    asset_ids=[],
                    message="No assets created due to validation errors",
                    errors=errors,
                )
            else:
                return CapitalizationResult(
                    success=True,
                    asset_ids=[],
                    message="No capitalizable lines found",
                    errors=[],
                )

    @staticmethod
    def _generate_asset_name(description: str, supplier_name: str) -> str:
        """
        Generate a suitable asset name from invoice line description.

        Args:
            description: Invoice line description
            supplier_name: Supplier legal name

        Returns:
            Asset name (max 200 chars)
        """
        # Clean up description
        name = description.strip()

        # Truncate if necessary (asset_name max is 200)
        if len(name) > 180:
            name = name[:177] + "..."

        return name

    @staticmethod
    def get_capitalizable_lines(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
    ) -> list[SupplierInvoiceLine]:
        """
        Get all lines from an invoice that are marked for capitalization.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to check

        Returns:
            List of capitalizable invoice lines
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        # Verify invoice belongs to org
        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return []

        return (
            db.query(SupplierInvoiceLine)
            .filter(
                SupplierInvoiceLine.invoice_id == inv_id,
                SupplierInvoiceLine.capitalize_flag == True,
            )
            .order_by(SupplierInvoiceLine.line_number)
            .all()
        )

    @staticmethod
    def get_assets_for_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
    ) -> list[Asset]:
        """
        Get all assets created from an invoice.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to check

        Returns:
            List of assets linked to this invoice
        """
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        return (
            db.query(Asset)
            .filter(
                Asset.organization_id == org_id,
                Asset.source_type == "SUPPLIER_INVOICE",
                Asset.source_document_id == inv_id,
            )
            .order_by(Asset.asset_number)
            .all()
        )


# Module-level singleton instance
capitalization_service = CapitalizationService()
