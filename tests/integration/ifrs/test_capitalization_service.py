"""
Integration Tests for CapitalizationService.

Tests asset creation from AP invoice lines using real PostgreSQL database.
"""

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.fixed_assets.asset import Asset, AssetStatus
from app.services.fixed_assets.capitalization import CapitalizationService


class TestValidateCapitalizationThreshold:
    """Tests for validate_capitalization_threshold method."""

    def test_amount_above_threshold_passes(
        self, db: Session, org_id: uuid.UUID, asset_category
    ):
        """Amount above threshold should pass validation."""
        is_valid, message = CapitalizationService.validate_capitalization_threshold(
            db=db,
            organization_id=org_id,
            category_id=asset_category.category_id,
            amount=Decimal("5000.00"),  # Above 1000 threshold
        )

        assert is_valid is True
        assert "meets capitalization threshold" in message

    def test_amount_below_threshold_fails(
        self, db: Session, org_id: uuid.UUID, asset_category
    ):
        """Amount below threshold should fail validation."""
        is_valid, message = CapitalizationService.validate_capitalization_threshold(
            db=db,
            organization_id=org_id,
            category_id=asset_category.category_id,
            amount=Decimal("500.00"),  # Below 1000 threshold
        )

        assert is_valid is False
        assert "below capitalization threshold" in message

    def test_amount_equals_threshold_passes(
        self, db: Session, org_id: uuid.UUID, asset_category
    ):
        """Amount equal to threshold should pass validation."""
        is_valid, message = CapitalizationService.validate_capitalization_threshold(
            db=db,
            organization_id=org_id,
            category_id=asset_category.category_id,
            amount=Decimal("1000.00"),  # Equal to threshold
        )

        assert is_valid is True

    def test_category_not_found_fails(self, db: Session, org_id: uuid.UUID):
        """Non-existent category should fail validation."""
        is_valid, message = CapitalizationService.validate_capitalization_threshold(
            db=db,
            organization_id=org_id,
            category_id=uuid.uuid4(),  # Non-existent
            amount=Decimal("5000.00"),
        )

        assert is_valid is False
        assert "not found" in message

    def test_inactive_category_fails(
        self, db: Session, org_id: uuid.UUID, asset_category
    ):
        """Inactive category should fail validation."""
        # Deactivate the category
        asset_category.is_active = False
        db.flush()

        is_valid, message = CapitalizationService.validate_capitalization_threshold(
            db=db,
            organization_id=org_id,
            category_id=asset_category.category_id,
            amount=Decimal("5000.00"),
        )

        assert is_valid is False
        assert "not active" in message

    def test_wrong_organization_fails(self, db: Session, asset_category):
        """Category from different org should fail validation."""
        other_org_id = uuid.uuid4()

        is_valid, message = CapitalizationService.validate_capitalization_threshold(
            db=db,
            organization_id=other_org_id,  # Different org
            category_id=asset_category.category_id,
            amount=Decimal("5000.00"),
        )

        assert is_valid is False
        assert "not found" in message


class TestCreateAssetsFromInvoice:
    """Tests for create_assets_from_invoice method."""

    def test_creates_draft_asset_for_capitalizable_line(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        supplier,
        supplier_invoice,
        asset_category,
        expense_account,
        fa_asset_sequence,  # Required for asset code generation
    ):
        """Should create DRAFT asset for line with capitalize_flag=True."""
        # Create capitalizable invoice line
        line = SupplierInvoiceLine(
            invoice_id=supplier_invoice.invoice_id,
            line_number=1,
            expense_account_id=expense_account.account_id,
            description="Office Computer Dell XPS",
            quantity=Decimal("1"),
            unit_price=Decimal("2500.00"),
            line_amount=Decimal("2500.00"),
            capitalize_flag=True,
            asset_category_id=asset_category.category_id,
        )
        db.add(line)
        db.flush()

        result = CapitalizationService.create_assets_from_invoice(
            db=db,
            organization_id=org_id,
            invoice=supplier_invoice,
            lines=[line],
            supplier=supplier,
            user_id=user_id,
        )

        assert result.success is True
        assert len(result.asset_ids) == 1
        assert "Successfully created 1 draft asset" in result.message
        assert len(result.errors) == 0

        # Verify asset was created with correct attributes
        asset = db.get(Asset, result.asset_ids[0])
        assert asset is not None
        assert asset.status == AssetStatus.DRAFT
        assert asset.acquisition_cost == Decimal("2500.00")
        assert asset.category_id == asset_category.category_id
        assert asset.supplier_id == supplier.supplier_id
        assert asset.source_type == "SUPPLIER_INVOICE"
        assert asset.source_document_id == supplier_invoice.invoice_id

        # Verify line was updated with created asset
        assert line.created_asset_id == asset.asset_id

    def test_creates_multiple_assets_for_multiple_lines(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        supplier,
        supplier_invoice,
        asset_category,
        expense_account,
        fa_asset_sequence,  # Required for asset code generation
    ):
        """Should create multiple assets for multiple capitalizable lines."""
        lines = []
        for i in range(3):
            line = SupplierInvoiceLine(
                invoice_id=supplier_invoice.invoice_id,
                line_number=i + 1,
                expense_account_id=expense_account.account_id,
                description=f"Equipment Item {i + 1}",
                quantity=Decimal("1"),
                unit_price=Decimal("1500.00"),
                line_amount=Decimal("1500.00"),
                capitalize_flag=True,
                asset_category_id=asset_category.category_id,
            )
            db.add(line)
            lines.append(line)
        db.flush()

        result = CapitalizationService.create_assets_from_invoice(
            db=db,
            organization_id=org_id,
            invoice=supplier_invoice,
            lines=lines,
            supplier=supplier,
            user_id=user_id,
        )

        assert result.success is True
        assert len(result.asset_ids) == 3
        assert "Successfully created 3 draft asset" in result.message

    def test_skips_non_capitalizable_lines(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        supplier,
        supplier_invoice,
        expense_account,
    ):
        """Should skip lines without capitalize_flag."""
        line = SupplierInvoiceLine(
            invoice_id=supplier_invoice.invoice_id,
            line_number=1,
            expense_account_id=expense_account.account_id,
            description="Office Supplies",
            quantity=Decimal("1"),
            unit_price=Decimal("500.00"),
            line_amount=Decimal("500.00"),
            capitalize_flag=False,  # Not capitalizable
        )
        db.add(line)
        db.flush()

        result = CapitalizationService.create_assets_from_invoice(
            db=db,
            organization_id=org_id,
            invoice=supplier_invoice,
            lines=[line],
            supplier=supplier,
            user_id=user_id,
        )

        assert result.success is True
        assert len(result.asset_ids) == 0
        assert "No capitalizable lines found" in result.message

    def test_errors_when_capitalize_flag_but_no_category(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        supplier,
        supplier_invoice,
        expense_account,
    ):
        """Should report error when capitalize_flag set but no asset_category_id."""
        line = SupplierInvoiceLine(
            invoice_id=supplier_invoice.invoice_id,
            line_number=1,
            expense_account_id=expense_account.account_id,
            description="Equipment without category",
            quantity=Decimal("1"),
            unit_price=Decimal("2000.00"),
            line_amount=Decimal("2000.00"),
            capitalize_flag=True,
            asset_category_id=None,  # Missing category
        )
        db.add(line)
        db.flush()

        result = CapitalizationService.create_assets_from_invoice(
            db=db,
            organization_id=org_id,
            invoice=supplier_invoice,
            lines=[line],
            supplier=supplier,
            user_id=user_id,
        )

        assert result.success is False
        assert len(result.asset_ids) == 0
        assert len(result.errors) == 1
        assert "no asset_category_id" in result.errors[0]

    def test_errors_when_amount_below_threshold(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        supplier,
        supplier_invoice,
        asset_category,
        expense_account,
    ):
        """Should report error when amount is below capitalization threshold."""
        line = SupplierInvoiceLine(
            invoice_id=supplier_invoice.invoice_id,
            line_number=1,
            expense_account_id=expense_account.account_id,
            description="Small equipment",
            quantity=Decimal("1"),
            unit_price=Decimal("500.00"),  # Below 1000 threshold
            line_amount=Decimal("500.00"),
            capitalize_flag=True,
            asset_category_id=asset_category.category_id,
        )
        db.add(line)
        db.flush()

        result = CapitalizationService.create_assets_from_invoice(
            db=db,
            organization_id=org_id,
            invoice=supplier_invoice,
            lines=[line],
            supplier=supplier,
            user_id=user_id,
        )

        assert result.success is False
        assert len(result.asset_ids) == 0
        assert len(result.errors) == 1
        assert "below capitalization threshold" in result.errors[0]

    def test_partial_success_with_mixed_lines(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        supplier,
        supplier_invoice,
        asset_category,
        expense_account,
        fa_asset_sequence,  # Required for asset code generation
    ):
        """Should create assets for valid lines and report errors for invalid ones."""
        # Valid line
        valid_line = SupplierInvoiceLine(
            invoice_id=supplier_invoice.invoice_id,
            line_number=1,
            expense_account_id=expense_account.account_id,
            description="Valid Equipment",
            quantity=Decimal("1"),
            unit_price=Decimal("2000.00"),
            line_amount=Decimal("2000.00"),
            capitalize_flag=True,
            asset_category_id=asset_category.category_id,
        )

        # Invalid line (below threshold)
        invalid_line = SupplierInvoiceLine(
            invoice_id=supplier_invoice.invoice_id,
            line_number=2,
            expense_account_id=expense_account.account_id,
            description="Too cheap equipment",
            quantity=Decimal("1"),
            unit_price=Decimal("500.00"),
            line_amount=Decimal("500.00"),
            capitalize_flag=True,
            asset_category_id=asset_category.category_id,
        )

        db.add(valid_line)
        db.add(invalid_line)
        db.flush()

        result = CapitalizationService.create_assets_from_invoice(
            db=db,
            organization_id=org_id,
            invoice=supplier_invoice,
            lines=[valid_line, invalid_line],
            supplier=supplier,
            user_id=user_id,
        )

        assert result.success is True  # Partial success
        assert len(result.asset_ids) == 1
        assert len(result.errors) == 1
        assert "1 asset(s) with 1 error(s)" in result.message


class TestGetCapitalizableLines:
    """Tests for get_capitalizable_lines method."""

    def test_returns_capitalizable_lines_only(
        self,
        db: Session,
        org_id: uuid.UUID,
        supplier_invoice,
        asset_category,
        expense_account,
    ):
        """Should return only lines with capitalize_flag=True."""
        # Capitalizable line
        cap_line = SupplierInvoiceLine(
            invoice_id=supplier_invoice.invoice_id,
            line_number=1,
            expense_account_id=expense_account.account_id,
            description="Equipment",
            quantity=Decimal("1"),
            unit_price=Decimal("2000.00"),
            line_amount=Decimal("2000.00"),
            capitalize_flag=True,
            asset_category_id=asset_category.category_id,
        )

        # Non-capitalizable line
        regular_line = SupplierInvoiceLine(
            invoice_id=supplier_invoice.invoice_id,
            line_number=2,
            expense_account_id=expense_account.account_id,
            description="Supplies",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            line_amount=Decimal("100.00"),
            capitalize_flag=False,
        )

        db.add(cap_line)
        db.add(regular_line)
        db.flush()

        lines = CapitalizationService.get_capitalizable_lines(
            db=db,
            organization_id=org_id,
            invoice_id=supplier_invoice.invoice_id,
        )

        assert len(lines) == 1
        assert lines[0].line_id == cap_line.line_id

    def test_returns_empty_for_wrong_org(
        self,
        db: Session,
        supplier_invoice,
        asset_category,
        expense_account,
    ):
        """Should return empty list for invoice from different org."""
        cap_line = SupplierInvoiceLine(
            invoice_id=supplier_invoice.invoice_id,
            line_number=1,
            expense_account_id=expense_account.account_id,
            description="Equipment",
            quantity=Decimal("1"),
            unit_price=Decimal("2000.00"),
            line_amount=Decimal("2000.00"),
            capitalize_flag=True,
            asset_category_id=asset_category.category_id,
        )
        db.add(cap_line)
        db.flush()

        lines = CapitalizationService.get_capitalizable_lines(
            db=db,
            organization_id=uuid.uuid4(),  # Different org
            invoice_id=supplier_invoice.invoice_id,
        )

        assert len(lines) == 0


class TestGetAssetsForInvoice:
    """Tests for get_assets_for_invoice method."""

    def test_returns_assets_linked_to_invoice(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        supplier,
        supplier_invoice,
        asset_category,
        expense_account,
        fa_asset_sequence,  # Required for asset code generation
    ):
        """Should return all assets created from an invoice."""
        # Create capitalizable lines
        lines = []
        for i in range(2):
            line = SupplierInvoiceLine(
                invoice_id=supplier_invoice.invoice_id,
                line_number=i + 1,
                expense_account_id=expense_account.account_id,
                description=f"Equipment {i + 1}",
                quantity=Decimal("1"),
                unit_price=Decimal("1500.00"),
                line_amount=Decimal("1500.00"),
                capitalize_flag=True,
                asset_category_id=asset_category.category_id,
            )
            db.add(line)
            lines.append(line)
        db.flush()

        # Create assets from invoice
        CapitalizationService.create_assets_from_invoice(
            db=db,
            organization_id=org_id,
            invoice=supplier_invoice,
            lines=lines,
            supplier=supplier,
            user_id=user_id,
        )

        # Retrieve assets
        assets = CapitalizationService.get_assets_for_invoice(
            db=db,
            organization_id=org_id,
            invoice_id=supplier_invoice.invoice_id,
        )

        assert len(assets) == 2
        for asset in assets:
            assert asset.source_type == "SUPPLIER_INVOICE"
            assert asset.source_document_id == supplier_invoice.invoice_id

    def test_returns_empty_for_invoice_without_assets(
        self,
        db: Session,
        org_id: uuid.UUID,
        supplier_invoice,
    ):
        """Should return empty list for invoice without assets."""
        assets = CapitalizationService.get_assets_for_invoice(
            db=db,
            organization_id=org_id,
            invoice_id=supplier_invoice.invoice_id,
        )

        assert len(assets) == 0


class TestGenerateAssetName:
    """Tests for _generate_asset_name static method."""

    def test_short_description_unchanged(self):
        """Short descriptions should be returned unchanged."""
        name = CapitalizationService._generate_asset_name(
            description="Dell XPS 15 Laptop",
            supplier_name="Dell Inc.",
        )
        assert name == "Dell XPS 15 Laptop"

    def test_long_description_truncated(self):
        """Long descriptions should be truncated to 180 chars with ellipsis."""
        long_desc = "A" * 200
        name = CapitalizationService._generate_asset_name(
            description=long_desc,
            supplier_name="Supplier",
        )
        assert len(name) == 180
        assert name.endswith("...")

    def test_whitespace_trimmed(self):
        """Leading/trailing whitespace should be trimmed."""
        name = CapitalizationService._generate_asset_name(
            description="  Computer  ",
            supplier_name="Supplier",
        )
        assert name == "Computer"
