"""
Fixtures for Inventory Services Tests.

These tests use mock objects to avoid PostgreSQL-specific dependencies
while still testing the service logic.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.inventory.inventory_transaction import TransactionType

# ============ Mock Enums ============
from app.models.inventory.item import CostingMethod, ItemType

MockItemType = ItemType
MockCostingMethod = CostingMethod
MockTransactionType = TransactionType


# ============ Mock Model Classes ============


class MockItemCategory:
    """Mock ItemCategory model."""

    def __init__(
        self,
        category_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        category_code: str = "CAT-001",
        category_name: str = "General Inventory",
        description: str | None = None,
        parent_category_id: uuid.UUID | None = None,
        inventory_account_id: uuid.UUID = None,
        cogs_account_id: uuid.UUID = None,
        revenue_account_id: uuid.UUID = None,
        inventory_adjustment_account_id: uuid.UUID = None,
        purchase_variance_account_id: uuid.UUID | None = None,
        is_active: bool = True,
        created_at: datetime = None,
        updated_at: datetime = None,
    ):
        self.category_id = category_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.category_code = category_code
        self.category_name = category_name
        self.description = description
        self.parent_category_id = parent_category_id
        self.inventory_account_id = inventory_account_id or uuid.uuid4()
        self.cogs_account_id = cogs_account_id or uuid.uuid4()
        self.revenue_account_id = revenue_account_id or uuid.uuid4()
        self.inventory_adjustment_account_id = (
            inventory_adjustment_account_id or uuid.uuid4()
        )
        self.purchase_variance_account_id = purchase_variance_account_id
        self.is_active = is_active
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at


class MockItem:
    """Mock Item model."""

    def __init__(
        self,
        item_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        item_code: str = "ITEM-001",
        item_name: str = "Test Item",
        description: str | None = None,
        item_type: ItemType = ItemType.INVENTORY,
        category_id: uuid.UUID = None,
        base_uom: str = "EACH",
        purchase_uom: str | None = "EACH",
        sales_uom: str | None = "EACH",
        costing_method: CostingMethod = CostingMethod.WEIGHTED_AVERAGE,
        standard_cost: Decimal | None = None,
        last_purchase_cost: Decimal | None = None,
        average_cost: Decimal | None = None,
        currency_code: str = "USD",
        list_price: Decimal | None = None,
        track_inventory: bool = True,
        track_lots: bool = False,
        track_serial_numbers: bool = False,
        reorder_point: Decimal | None = None,
        reorder_quantity: Decimal | None = None,
        minimum_stock: Decimal | None = None,
        maximum_stock: Decimal | None = None,
        lead_time_days: int | None = None,
        weight: Decimal | None = None,
        weight_uom: str | None = None,
        volume: Decimal | None = None,
        volume_uom: str | None = None,
        barcode: str | None = None,
        manufacturer_part_number: str | None = None,
        tax_code_id: uuid.UUID | None = None,
        is_taxable: bool = True,
        inventory_account_id: uuid.UUID | None = None,
        cogs_account_id: uuid.UUID | None = None,
        revenue_account_id: uuid.UUID | None = None,
        default_supplier_id: uuid.UUID | None = None,
        is_active: bool = True,
        is_purchaseable: bool = True,
        is_saleable: bool = True,
        created_at: datetime = None,
        updated_at: datetime = None,
    ):
        self.item_id = item_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.item_code = item_code
        self.item_name = item_name
        self.description = description
        self.item_type = item_type
        self.category_id = category_id or uuid.uuid4()
        self.base_uom = base_uom
        self.purchase_uom = purchase_uom
        self.sales_uom = sales_uom
        self.costing_method = costing_method
        self.standard_cost = standard_cost
        self.last_purchase_cost = last_purchase_cost
        self.average_cost = average_cost
        self.currency_code = currency_code
        self.list_price = list_price
        self.track_inventory = track_inventory
        self.track_lots = track_lots
        self.track_serial_numbers = track_serial_numbers
        self.reorder_point = reorder_point
        self.reorder_quantity = reorder_quantity
        self.minimum_stock = minimum_stock
        self.maximum_stock = maximum_stock
        self.lead_time_days = lead_time_days
        self.weight = weight
        self.weight_uom = weight_uom
        self.volume = volume
        self.volume_uom = volume_uom
        self.barcode = barcode
        self.manufacturer_part_number = manufacturer_part_number
        self.tax_code_id = tax_code_id
        self.is_taxable = is_taxable
        self.inventory_account_id = inventory_account_id
        self.cogs_account_id = cogs_account_id
        self.revenue_account_id = revenue_account_id
        self.default_supplier_id = default_supplier_id
        self.is_active = is_active
        self.is_purchaseable = is_purchaseable
        self.is_saleable = is_saleable
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at


class MockWarehouse:
    """Mock Warehouse model."""

    def __init__(
        self,
        warehouse_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        warehouse_code: str = "WH-001",
        warehouse_name: str = "Main Warehouse",
        description: str | None = None,
        location_id: uuid.UUID | None = None,
        address: dict | None = None,
        contact_name: str | None = None,
        contact_phone: str | None = None,
        contact_email: str | None = None,
        is_receiving: bool = True,
        is_shipping: bool = True,
        is_consignment: bool = False,
        is_transit: bool = False,
        cost_center_id: uuid.UUID | None = None,
        is_active: bool = True,
        created_at: datetime = None,
        updated_at: datetime = None,
    ):
        self.warehouse_id = warehouse_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.warehouse_code = warehouse_code
        self.warehouse_name = warehouse_name
        self.description = description
        self.location_id = location_id
        self.address = address
        self.contact_name = contact_name
        self.contact_phone = contact_phone
        self.contact_email = contact_email
        self.is_receiving = is_receiving
        self.is_shipping = is_shipping
        self.is_consignment = is_consignment
        self.is_transit = is_transit
        self.cost_center_id = cost_center_id
        self.is_active = is_active
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at


class MockInventoryTransaction:
    """Mock InventoryTransaction model."""

    def __init__(
        self,
        transaction_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        transaction_type: TransactionType = TransactionType.RECEIPT,
        transaction_date: datetime = None,
        fiscal_period_id: uuid.UUID = None,
        item_id: uuid.UUID = None,
        warehouse_id: uuid.UUID = None,
        location_id: uuid.UUID | None = None,
        lot_id: uuid.UUID | None = None,
        to_warehouse_id: uuid.UUID | None = None,
        to_location_id: uuid.UUID | None = None,
        quantity: Decimal = Decimal("10.00"),
        uom: str = "EACH",
        unit_cost: Decimal = Decimal("100.00"),
        total_cost: Decimal = Decimal("1000.00"),
        cost_variance: Decimal = Decimal("0"),
        currency_code: str = "USD",
        reference: str | None = None,
        source_document_type: str | None = None,
        source_document_id: uuid.UUID | None = None,
        source_document_line_id: uuid.UUID | None = None,
        reason_code: str | None = None,
        quantity_before: Decimal = Decimal("0"),
        quantity_after: Decimal = Decimal("0"),
        journal_entry_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        created_at: datetime = None,
    ):
        self.transaction_id = transaction_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.transaction_type = transaction_type
        self.transaction_date = transaction_date or datetime.now(UTC)
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.item_id = item_id or uuid.uuid4()
        self.warehouse_id = warehouse_id or uuid.uuid4()
        self.location_id = location_id
        self.lot_id = lot_id
        self.to_warehouse_id = to_warehouse_id
        self.to_location_id = to_location_id
        self.quantity = quantity
        self.uom = uom
        self.unit_cost = unit_cost
        self.total_cost = total_cost
        self.cost_variance = cost_variance
        self.currency_code = currency_code
        self.reference = reference
        self.source_document_type = source_document_type
        self.source_document_id = source_document_id
        self.source_document_line_id = source_document_line_id
        self.reason_code = reason_code
        self.quantity_before = quantity_before
        self.quantity_after = quantity_after
        self.journal_entry_id = journal_entry_id
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(UTC)


class MockInventoryLot:
    """Mock InventoryLot model."""

    def __init__(
        self,
        lot_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        item_id: uuid.UUID = None,
        warehouse_id: uuid.UUID | None = None,
        lot_number: str = "LOT-001",
        expiry_date: date | None = None,
        manufacture_date: date | None = None,
        received_date: date = None,
        supplier_id: uuid.UUID | None = None,
        supplier_lot_number: str | None = None,
        purchase_order_id: uuid.UUID | None = None,
        unit_cost: Decimal = Decimal("10.00"),
        initial_quantity: Decimal = Decimal("100.00"),
        quantity_on_hand: Decimal = Decimal("100.00"),
        quantity_allocated: Decimal = Decimal("0"),
        quantity_available: Decimal | None = None,
        allocation_reference: str | None = None,
        is_active: bool = True,
        is_quarantined: bool = False,
        quarantine_reason: str | None = None,
        certificate_of_analysis: str | None = None,
        qc_status: str | None = None,
        created_at: datetime = None,
        updated_at: datetime | None = None,
    ):
        self.lot_id = lot_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.item_id = item_id or uuid.uuid4()
        self.warehouse_id = warehouse_id
        self.lot_number = lot_number
        self.expiry_date = expiry_date
        self.manufacture_date = manufacture_date
        self.received_date = received_date or date.today()
        self.supplier_id = supplier_id
        self.supplier_lot_number = supplier_lot_number
        self.purchase_order_id = purchase_order_id
        self.unit_cost = unit_cost
        self.initial_quantity = initial_quantity
        self.quantity_on_hand = quantity_on_hand
        self.quantity_allocated = quantity_allocated
        self.quantity_available = (
            quantity_available
            if quantity_available is not None
            else quantity_on_hand - quantity_allocated
        )
        self.allocation_reference = allocation_reference
        self.is_active = is_active
        self.is_quarantined = is_quarantined
        self.quarantine_reason = quarantine_reason
        self.certificate_of_analysis = certificate_of_analysis
        self.qc_status = qc_status
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at


class MockInventoryValuation:
    """Mock InventoryValuation model."""

    def __init__(
        self,
        valuation_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        item_id: uuid.UUID = None,
        warehouse_id: uuid.UUID = None,
        valuation_date: date = None,
        quantity_on_hand: Decimal = Decimal("100.00"),
        unit_cost: Decimal = Decimal("10.00"),
        total_value: Decimal = Decimal("1000.00"),
        total_cost: Decimal = Decimal("1000.00"),
        carrying_amount: Decimal = Decimal("1000.00"),
        write_down_amount: Decimal = Decimal("0"),
        currency_code: str = "USD",
        created_at: datetime = None,
    ):
        self.valuation_id = valuation_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.item_id = item_id or uuid.uuid4()
        self.warehouse_id = warehouse_id or uuid.uuid4()
        self.valuation_date = valuation_date or date.today()
        self.quantity_on_hand = quantity_on_hand
        self.unit_cost = unit_cost
        self.total_value = total_value
        self.total_cost = total_cost
        self.carrying_amount = carrying_amount
        self.write_down_amount = write_down_amount
        self.currency_code = currency_code
        self.created_at = created_at or datetime.now(UTC)


# ============ Fixtures ============


@pytest.fixture
def organization_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    """Generate a test user ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    session = MagicMock()
    session.scalars = MagicMock(
        return_value=MagicMock(
            first=MagicMock(return_value=None), all=MagicMock(return_value=[])
        )
    )
    session.scalar = MagicMock(return_value=None)
    session.execute = MagicMock()
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.get = MagicMock(return_value=None)
    return session


@pytest.fixture
def mock_item_category(organization_id) -> MockItemCategory:
    """Create a mock item category."""
    return MockItemCategory(organization_id=organization_id)


@pytest.fixture
def mock_item(organization_id, mock_item_category) -> MockItem:
    """Create a mock item."""
    return MockItem(
        organization_id=organization_id,
        category_id=mock_item_category.category_id,
    )


@pytest.fixture
def mock_warehouse(organization_id) -> MockWarehouse:
    """Create a mock warehouse."""
    return MockWarehouse(organization_id=organization_id)


@pytest.fixture
def mock_inventory_transaction(
    organization_id, mock_item, mock_warehouse
) -> MockInventoryTransaction:
    """Create a mock inventory transaction."""
    return MockInventoryTransaction(
        organization_id=organization_id,
        item_id=mock_item.item_id,
        warehouse_id=mock_warehouse.warehouse_id,
    )


@pytest.fixture
def mock_inventory_lot(organization_id, mock_item) -> MockInventoryLot:
    """Create a mock inventory lot."""
    return MockInventoryLot(
        organization_id=organization_id,
        item_id=mock_item.item_id,
    )
