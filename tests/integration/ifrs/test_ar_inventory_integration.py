"""
Integration Tests for ARInventoryIntegration Service.

Tests inventory validation, costing, and COGS recording using real PostgreSQL database.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.ifrs.ar.invoice_line import InvoiceLine
from app.models.ifrs.inv.item import CostingMethod
from app.services.ifrs.ar.ar_inventory_integration import (
    ARInventoryIntegration,
    CostingResult,
)


class TestValidateInventoryAvailability:
    """Tests for validate_inventory_availability method."""

    def test_passes_when_sufficient_inventory(
        self,
        db: Session,
        org_id: uuid.UUID,
        ar_invoice,
        inventory_item,
        warehouse,
        initial_inventory_transaction,
        revenue_account,
    ):
        """Should pass when enough inventory is available."""
        # Create invoice line for 50 units (100 available)
        line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=1,
            item_id=inventory_item.item_id,
            revenue_account_id=revenue_account.account_id,
            warehouse_id=warehouse.warehouse_id,
            description="Test Item",
            quantity=Decimal("50"),
            unit_price=Decimal("20.00"),
            line_amount=Decimal("1000.00"),
        )
        db.add(line)
        db.flush()

        is_valid, errors = ARInventoryIntegration.validate_inventory_availability(
            db=db,
            organization_id=org_id,
            lines=[line],
        )

        assert is_valid is True
        assert len(errors) == 0

    def test_fails_when_insufficient_inventory(
        self,
        db: Session,
        org_id: uuid.UUID,
        ar_invoice,
        inventory_item,
        warehouse,
        initial_inventory_transaction,  # 100 available
        revenue_account,
    ):
        """Should fail when requested quantity exceeds available."""
        # Create invoice line for 150 units (only 100 available)
        line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=1,
            item_id=inventory_item.item_id,
            revenue_account_id=revenue_account.account_id,
            warehouse_id=warehouse.warehouse_id,
            description="Test Item",
            quantity=Decimal("150"),  # More than available
            unit_price=Decimal("20.00"),
            line_amount=Decimal("3000.00"),
        )
        db.add(line)
        db.flush()

        is_valid, errors = ARInventoryIntegration.validate_inventory_availability(
            db=db,
            organization_id=org_id,
            lines=[line],
        )

        assert is_valid is False
        assert len(errors) == 1
        assert "Insufficient inventory" in errors[0]
        assert "available: 100" in errors[0]
        assert "required: 150" in errors[0]

    def test_fails_when_warehouse_not_specified(
        self,
        db: Session,
        org_id: uuid.UUID,
        ar_invoice,
        inventory_item,
        initial_inventory_transaction,
        revenue_account,
    ):
        """Should fail when inventory item has no warehouse specified."""
        line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=1,
            item_id=inventory_item.item_id,
            revenue_account_id=revenue_account.account_id,
            warehouse_id=None,  # Missing warehouse
            description="Test Item",
            quantity=Decimal("10"),
            unit_price=Decimal("20.00"),
            line_amount=Decimal("200.00"),
        )
        db.add(line)
        db.flush()

        is_valid, errors = ARInventoryIntegration.validate_inventory_availability(
            db=db,
            organization_id=org_id,
            lines=[line],
        )

        assert is_valid is False
        assert len(errors) == 1
        assert "Warehouse required" in errors[0]

    def test_skips_non_inventory_lines(
        self,
        db: Session,
        org_id: uuid.UUID,
        ar_invoice,
        revenue_account,
    ):
        """Should skip lines without item_id (service lines)."""
        line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=1,
            item_id=None,  # Service line
            revenue_account_id=revenue_account.account_id,
            description="Consulting Service",
            quantity=Decimal("5"),
            unit_price=Decimal("100.00"),
            line_amount=Decimal("500.00"),
        )
        db.add(line)
        db.flush()

        is_valid, errors = ARInventoryIntegration.validate_inventory_availability(
            db=db,
            organization_id=org_id,
            lines=[line],
        )

        assert is_valid is True
        assert len(errors) == 0

    def test_skips_non_tracked_items(
        self,
        db: Session,
        org_id: uuid.UUID,
        ar_invoice,
        inventory_item,
        warehouse,
        revenue_account,
    ):
        """Should skip items with track_inventory=False."""
        # Disable inventory tracking
        inventory_item.track_inventory = False
        db.flush()

        line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=1,
            item_id=inventory_item.item_id,
            revenue_account_id=revenue_account.account_id,
            warehouse_id=warehouse.warehouse_id,
            description="Non-tracked Item",
            quantity=Decimal("1000"),  # Any quantity OK
            unit_price=Decimal("20.00"),
            line_amount=Decimal("20000.00"),
        )
        db.add(line)
        db.flush()

        is_valid, errors = ARInventoryIntegration.validate_inventory_availability(
            db=db,
            organization_id=org_id,
            lines=[line],
        )

        assert is_valid is True
        assert len(errors) == 0


class TestGetItemCost:
    """Tests for get_item_cost method."""

    def test_standard_cost_method(
        self,
        db: Session,
        org_id: uuid.UUID,
        inventory_item,
        warehouse,
    ):
        """Should use standard_cost for STANDARD_COST costing method."""
        inventory_item.costing_method = CostingMethod.STANDARD_COST
        inventory_item.standard_cost = Decimal("15.00")
        db.flush()

        result = ARInventoryIntegration.get_item_cost(
            db=db,
            organization_id=org_id,
            item=inventory_item,
            quantity=Decimal("10"),
            warehouse_id=warehouse.warehouse_id,
        )

        assert result.unit_cost == Decimal("15.00")
        assert result.total_cost == Decimal("150.00")

    def test_weighted_average_cost_method(
        self,
        db: Session,
        org_id: uuid.UUID,
        inventory_item,
        warehouse,
    ):
        """Should use average_cost for WEIGHTED_AVERAGE costing method."""
        inventory_item.costing_method = CostingMethod.WEIGHTED_AVERAGE
        inventory_item.average_cost = Decimal("12.50")
        db.flush()

        result = ARInventoryIntegration.get_item_cost(
            db=db,
            organization_id=org_id,
            item=inventory_item,
            quantity=Decimal("8"),
            warehouse_id=warehouse.warehouse_id,
        )

        assert result.unit_cost == Decimal("12.50")
        assert result.total_cost == Decimal("100.00")

    def test_fifo_cost_method_single_lot(
        self,
        db: Session,
        org_id: uuid.UUID,
        inventory_item,
        warehouse,
        inventory_lot,
    ):
        """Should calculate FIFO cost from lots."""
        inventory_item.costing_method = CostingMethod.FIFO
        db.flush()

        result = ARInventoryIntegration.get_item_cost(
            db=db,
            organization_id=org_id,
            item=inventory_item,
            quantity=Decimal("25"),
            warehouse_id=warehouse.warehouse_id,
        )

        # Lot has unit_cost of 10.00
        assert result.unit_cost == Decimal("10.000000")
        assert result.total_cost == Decimal("250.00")
        assert result.lot_id == inventory_lot.lot_id

    def test_fifo_cost_method_multiple_lots(
        self,
        db: Session,
        org_id: uuid.UUID,
        inventory_item,
        warehouse,
    ):
        """Should consume from multiple lots in FIFO order."""
        from app.models.ifrs.inv.inventory_lot import InventoryLot

        inventory_item.costing_method = CostingMethod.FIFO
        db.flush()

        # Create first lot (older, cheaper)
        lot1 = InventoryLot(
            organization_id=org_id,
            item_id=inventory_item.item_id,
            warehouse_id=warehouse.warehouse_id,
            lot_number="LOT-A",
            received_date=date(2024, 1, 1),  # Older
            initial_quantity=Decimal("30"),
            quantity_on_hand=Decimal("30"),
            quantity_available=Decimal("30"),
            unit_cost=Decimal("8.00"),  # Cheaper
            is_active=True,
            is_quarantined=False,
        )

        # Create second lot (newer, more expensive)
        lot2 = InventoryLot(
            organization_id=org_id,
            item_id=inventory_item.item_id,
            warehouse_id=warehouse.warehouse_id,
            lot_number="LOT-B",
            received_date=date(2024, 1, 15),  # Newer
            initial_quantity=Decimal("50"),
            quantity_on_hand=Decimal("50"),
            quantity_available=Decimal("50"),
            unit_cost=Decimal("12.00"),  # More expensive
            is_active=True,
            is_quarantined=False,
        )

        db.add(lot1)
        db.add(lot2)
        db.flush()

        # Request 50 units: 30 from lot1 @ 8.00, 20 from lot2 @ 12.00
        # Total: (30 * 8) + (20 * 12) = 240 + 240 = 480
        result = ARInventoryIntegration.get_item_cost(
            db=db,
            organization_id=org_id,
            item=inventory_item,
            quantity=Decimal("50"),
            warehouse_id=warehouse.warehouse_id,
        )

        assert result.total_cost == Decimal("480.00")
        assert result.lot_id == lot1.lot_id  # First lot consumed

    def test_specific_identification_with_lot(
        self,
        db: Session,
        org_id: uuid.UUID,
        inventory_item,
        warehouse,
        inventory_lot,
    ):
        """Should use specific lot cost for SPECIFIC_IDENTIFICATION method."""
        inventory_item.costing_method = CostingMethod.SPECIFIC_IDENTIFICATION
        inventory_lot.unit_cost = Decimal("18.50")
        db.flush()

        result = ARInventoryIntegration.get_item_cost(
            db=db,
            organization_id=org_id,
            item=inventory_item,
            quantity=Decimal("5"),
            warehouse_id=warehouse.warehouse_id,
            lot_id=inventory_lot.lot_id,
        )

        assert result.unit_cost == Decimal("18.50")
        assert result.total_cost == Decimal("92.50")
        assert result.lot_id == inventory_lot.lot_id


class TestProcessInvoiceInventory:
    """Tests for process_invoice_inventory method."""

    def test_creates_sale_transaction_for_standard_invoice(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        ar_invoice,
        inventory_item,
        warehouse,
        initial_inventory_transaction,
        fiscal_period,
        revenue_account,
        inv_transaction_sequence,
    ):
        """Should create SALE transaction and COGS entries for standard invoice."""
        line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=1,
            item_id=inventory_item.item_id,
            revenue_account_id=revenue_account.account_id,
            warehouse_id=warehouse.warehouse_id,
            description="Test Sale",
            quantity=Decimal("10"),
            unit_price=Decimal("20.00"),
            line_amount=Decimal("200.00"),
        )
        db.add(line)
        db.flush()

        result = ARInventoryIntegration.process_invoice_inventory(
            db=db,
            organization_id=org_id,
            invoice=ar_invoice,
            lines=[line],
            fiscal_period_id=fiscal_period.fiscal_period_id,
            user_id=user_id,
            is_credit_note=False,
        )

        assert result.success is True
        assert len(result.transaction_ids) == 1
        assert len(result.errors) == 0

        # Should have COGS journal lines (2: Dr COGS, Cr Inventory)
        assert len(result.cogs_journal_lines) == 2

        # Check COGS entry (debit)
        cogs_line = result.cogs_journal_lines[0]
        assert cogs_line.debit_amount == Decimal("100.00")  # 10 * 10.00 avg cost
        assert cogs_line.credit_amount == Decimal("0")

        # Check Inventory entry (credit)
        inv_line = result.cogs_journal_lines[1]
        assert inv_line.debit_amount == Decimal("0")
        assert inv_line.credit_amount == Decimal("100.00")

        # Total COGS should be positive for sales
        assert result.total_cogs == Decimal("100.00")

    def test_creates_return_transaction_for_credit_note(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        ar_invoice,
        inventory_item,
        warehouse,
        initial_inventory_transaction,
        fiscal_period,
        revenue_account,
        inv_transaction_sequence,
    ):
        """Should create RETURN transaction and reverse COGS for credit note."""
        line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=1,
            item_id=inventory_item.item_id,
            revenue_account_id=revenue_account.account_id,
            warehouse_id=warehouse.warehouse_id,
            description="Return",
            quantity=Decimal("5"),
            unit_price=Decimal("20.00"),
            line_amount=Decimal("100.00"),
        )
        db.add(line)
        db.flush()

        result = ARInventoryIntegration.process_invoice_inventory(
            db=db,
            organization_id=org_id,
            invoice=ar_invoice,
            lines=[line],
            fiscal_period_id=fiscal_period.fiscal_period_id,
            user_id=user_id,
            is_credit_note=True,  # Credit note
        )

        assert result.success is True
        assert len(result.transaction_ids) == 1

        # Check reverse COGS entries (Dr Inventory, Cr COGS)
        assert len(result.cogs_journal_lines) == 2

        # Inventory debit (return to stock)
        inv_line = result.cogs_journal_lines[0]
        assert inv_line.debit_amount == Decimal("50.00")  # 5 * 10.00
        assert inv_line.credit_amount == Decimal("0")

        # COGS credit (reverse expense)
        cogs_line = result.cogs_journal_lines[1]
        assert cogs_line.debit_amount == Decimal("0")
        assert cogs_line.credit_amount == Decimal("50.00")

        # Total COGS should be negative for returns
        assert result.total_cogs == Decimal("-50.00")

    def test_skips_service_lines(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        ar_invoice,
        fiscal_period,
        revenue_account,
    ):
        """Should skip lines without item_id."""
        line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=1,
            item_id=None,  # Service line
            revenue_account_id=revenue_account.account_id,
            description="Consulting",
            quantity=Decimal("10"),
            unit_price=Decimal("100.00"),
            line_amount=Decimal("1000.00"),
        )
        db.add(line)
        db.flush()

        result = ARInventoryIntegration.process_invoice_inventory(
            db=db,
            organization_id=org_id,
            invoice=ar_invoice,
            lines=[line],
            fiscal_period_id=fiscal_period.fiscal_period_id,
            user_id=user_id,
            is_credit_note=False,
        )

        assert result.success is True
        assert len(result.transaction_ids) == 0  # No inventory transactions
        assert len(result.cogs_journal_lines) == 0  # No COGS
        assert result.total_cogs == Decimal("0")

    def test_updates_line_with_transaction_id(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        ar_invoice,
        inventory_item,
        warehouse,
        initial_inventory_transaction,
        fiscal_period,
        revenue_account,
        inv_transaction_sequence,
    ):
        """Should update invoice line with inventory_transaction_id."""
        line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=1,
            item_id=inventory_item.item_id,
            revenue_account_id=revenue_account.account_id,
            warehouse_id=warehouse.warehouse_id,
            description="Test",
            quantity=Decimal("10"),
            unit_price=Decimal("20.00"),
            line_amount=Decimal("200.00"),
        )
        db.add(line)
        db.flush()

        assert line.inventory_transaction_id is None  # Before

        result = ARInventoryIntegration.process_invoice_inventory(
            db=db,
            organization_id=org_id,
            invoice=ar_invoice,
            lines=[line],
            fiscal_period_id=fiscal_period.fiscal_period_id,
            user_id=user_id,
            is_credit_note=False,
        )

        assert result.success is True
        assert line.inventory_transaction_id is not None  # After
        assert line.inventory_transaction_id == result.transaction_ids[0]

    def test_handles_mixed_inventory_and_service_lines(
        self,
        db: Session,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        ar_invoice,
        inventory_item,
        warehouse,
        initial_inventory_transaction,
        fiscal_period,
        revenue_account,
        inv_transaction_sequence,
    ):
        """Should process inventory lines and skip service lines."""
        # Inventory line
        inv_line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=1,
            item_id=inventory_item.item_id,
            revenue_account_id=revenue_account.account_id,
            warehouse_id=warehouse.warehouse_id,
            description="Product",
            quantity=Decimal("5"),
            unit_price=Decimal("30.00"),
            line_amount=Decimal("150.00"),
        )

        # Service line
        svc_line = InvoiceLine(
            invoice_id=ar_invoice.invoice_id,
            line_number=2,
            item_id=None,
            revenue_account_id=revenue_account.account_id,
            description="Installation",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            line_amount=Decimal("100.00"),
        )

        db.add(inv_line)
        db.add(svc_line)
        db.flush()

        result = ARInventoryIntegration.process_invoice_inventory(
            db=db,
            organization_id=org_id,
            invoice=ar_invoice,
            lines=[inv_line, svc_line],
            fiscal_period_id=fiscal_period.fiscal_period_id,
            user_id=user_id,
            is_credit_note=False,
        )

        assert result.success is True
        assert len(result.transaction_ids) == 1  # Only inventory line
        assert inv_line.inventory_transaction_id is not None
        # Service line should not have transaction
        assert svc_line.inventory_transaction_id is None
