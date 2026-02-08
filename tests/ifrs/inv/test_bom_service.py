"""
Tests for BOMService.

Tests Bill of Materials management and assembly/disassembly operations.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.inventory.bom import BOMType
from app.services.inventory.bom import (
    AssemblyInput,
    AssemblyResult,
    BOMComponentInput,
    BOMInput,
    BOMService,
)
from tests.ifrs.inv.conftest import MockItem

# ============ Mock Classes ============


class MockBOM:
    """Mock BillOfMaterials model."""

    def __init__(
        self,
        bom_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        bom_code: str = "BOM-001",
        bom_name: str = "Test BOM",
        description: str = None,
        item_id: uuid.UUID = None,
        bom_type: BOMType = BOMType.ASSEMBLY,
        output_quantity: Decimal = Decimal("1"),
        output_uom: str = "EACH",
        version: int = 1,
        is_default: bool = True,
        is_active: bool = True,
    ):
        self.bom_id = bom_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.bom_code = bom_code
        self.bom_name = bom_name
        self.description = description
        self.item_id = item_id or uuid.uuid4()
        self.bom_type = bom_type
        self.output_quantity = output_quantity
        self.output_uom = output_uom
        self.version = version
        self.is_default = is_default
        self.is_active = is_active


class MockBOMComponent:
    """Mock BOMComponent model."""

    def __init__(
        self,
        component_id: uuid.UUID = None,
        bom_id: uuid.UUID = None,
        component_item_id: uuid.UUID = None,
        quantity: Decimal = Decimal("2"),
        uom: str = "EACH",
        scrap_percent: Decimal = Decimal("0"),
        line_number: int = 1,
        warehouse_id: uuid.UUID = None,
        is_active: bool = True,
    ):
        self.component_id = component_id or uuid.uuid4()
        self.bom_id = bom_id or uuid.uuid4()
        self.component_item_id = component_item_id or uuid.uuid4()
        self.quantity = quantity
        self.uom = uom
        self.scrap_percent = scrap_percent
        self.line_number = line_number
        self.warehouse_id = warehouse_id
        self.is_active = is_active


# ============ Fixtures ============


@pytest.fixture
def org_id():
    """Generate test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id():
    """Generate test user ID."""
    return uuid.uuid4()


@pytest.fixture
def bom_id():
    """Generate test BOM ID."""
    return uuid.uuid4()


@pytest.fixture
def item_id():
    """Generate test item ID (finished good)."""
    return uuid.uuid4()


@pytest.fixture
def component_item_id():
    """Generate test component item ID."""
    return uuid.uuid4()


@pytest.fixture
def warehouse_id():
    """Generate test warehouse ID."""
    return uuid.uuid4()


@pytest.fixture
def fiscal_period_id():
    """Generate test fiscal period ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_finished_item(org_id, item_id):
    """Create a mock finished goods item."""
    return MockItem(
        item_id=item_id,
        organization_id=org_id,
        item_code="PROD-001",
        item_name="Finished Product",
        base_uom="EACH",
        average_cost=Decimal("50.00"),
        currency_code="USD",
    )


@pytest.fixture
def mock_component_item(org_id, component_item_id):
    """Create a mock component item."""
    return MockItem(
        item_id=component_item_id,
        organization_id=org_id,
        item_code="COMP-001",
        item_name="Component Part",
        base_uom="EACH",
        average_cost=Decimal("10.00"),
        currency_code="USD",
    )


@pytest.fixture
def mock_bom(org_id, bom_id, item_id):
    """Create a mock BOM."""
    return MockBOM(
        bom_id=bom_id,
        organization_id=org_id,
        bom_code="BOM-PROD",
        bom_name="Product Assembly BOM",
        item_id=item_id,
        bom_type=BOMType.ASSEMBLY,
        output_quantity=Decimal("1"),
        output_uom="EACH",
        is_active=True,
    )


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    session = MagicMock()
    session.query = MagicMock(return_value=session)
    session.filter = MagicMock(return_value=session)
    session.first = MagicMock(return_value=None)
    session.all = MagicMock(return_value=[])
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.get = MagicMock(return_value=None)
    session.order_by = MagicMock(return_value=session)
    session.limit = MagicMock(return_value=session)
    session.offset = MagicMock(return_value=session)
    session.update = MagicMock()
    return session


# ============ Tests for create_bom ============


class TestCreateBOM:
    """Tests for create_bom method."""

    def test_raises_error_on_duplicate_code(self, mock_db, org_id, item_id):
        """Should raise HTTPException when BOM code already exists."""
        existing = MockBOM(organization_id=org_id, bom_code="BOM-001")
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        input = BOMInput(
            bom_code="BOM-001",
            bom_name="New BOM",
            item_id=item_id,
            output_quantity=Decimal("1"),
            output_uom="EACH",
        )

        with pytest.raises(HTTPException) as exc:
            BOMService.create_bom(mock_db, org_id, input)

        assert exc.value.status_code == 400
        assert "already exists" in str(exc.value.detail)

    def test_raises_error_when_item_not_found(self, mock_db, org_id, item_id):
        """Should raise HTTPException when output item not found."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.get.return_value = None

        input = BOMInput(
            bom_code="BOM-NEW",
            bom_name="New BOM",
            item_id=item_id,
            output_quantity=Decimal("1"),
            output_uom="EACH",
        )

        with pytest.raises(HTTPException) as exc:
            BOMService.create_bom(mock_db, org_id, input)

        assert exc.value.status_code == 404
        assert "Item not found" in str(exc.value.detail)

    def test_creates_bom_successfully(self, mock_db, org_id, mock_finished_item):
        """Should create BOM when inputs are valid."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.get.return_value = mock_finished_item

        input = BOMInput(
            bom_code="BOM-NEW",
            bom_name="New Assembly BOM",
            item_id=mock_finished_item.item_id,
            output_quantity=Decimal("1"),
            output_uom="EACH",
            bom_type=BOMType.ASSEMBLY,
            description="Test BOM description",
        )

        BOMService.create_bom(mock_db, org_id, input)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called()

    def test_clears_other_defaults_when_setting_default(
        self, mock_db, org_id, mock_finished_item
    ):
        """Should clear other default BOMs for same item when setting as default."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.get.return_value = mock_finished_item

        input = BOMInput(
            bom_code="BOM-DEFAULT",
            bom_name="Default BOM",
            item_id=mock_finished_item.item_id,
            output_quantity=Decimal("1"),
            output_uom="EACH",
            is_default=True,
        )

        BOMService.create_bom(mock_db, org_id, input)

        # Should have called update to clear other defaults
        mock_db.query.return_value.filter.return_value.update.assert_called()

    def test_creates_kit_type_bom(self, mock_db, org_id, mock_finished_item):
        """Should create BOM with KIT type."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.get.return_value = mock_finished_item

        input = BOMInput(
            bom_code="BOM-KIT",
            bom_name="Kit BOM",
            item_id=mock_finished_item.item_id,
            output_quantity=Decimal("1"),
            output_uom="EACH",
            bom_type=BOMType.KIT,
        )

        BOMService.create_bom(mock_db, org_id, input)

        mock_db.add.assert_called_once()


# ============ Tests for add_component ============


class TestAddComponent:
    """Tests for add_component method."""

    def test_raises_error_when_bom_not_found(
        self, mock_db, org_id, bom_id, component_item_id
    ):
        """Should raise HTTPException when BOM not found."""
        mock_db.get.return_value = None

        input = BOMComponentInput(
            component_item_id=component_item_id,
            quantity=Decimal("2"),
            uom="EACH",
        )

        with pytest.raises(HTTPException) as exc:
            BOMService.add_component(mock_db, org_id, bom_id, input)

        assert exc.value.status_code == 404
        assert "BOM not found" in str(exc.value.detail)

    def test_raises_error_when_component_not_found(
        self, mock_db, org_id, mock_bom, component_item_id
    ):
        """Should raise HTTPException when component item not found."""
        mock_db.get.side_effect = lambda model, id: (
            mock_bom if id == mock_bom.bom_id else None
        )

        input = BOMComponentInput(
            component_item_id=component_item_id,
            quantity=Decimal("2"),
            uom="EACH",
        )

        with pytest.raises(HTTPException) as exc:
            BOMService.add_component(mock_db, org_id, mock_bom.bom_id, input)

        assert exc.value.status_code == 404
        assert "Component item not found" in str(exc.value.detail)

    def test_raises_error_on_circular_reference(
        self, mock_db, org_id, mock_bom, mock_finished_item
    ):
        """Should raise HTTPException when adding output item as component."""
        mock_db.get.side_effect = lambda model, id: (
            mock_bom if id == mock_bom.bom_id else mock_finished_item
        )

        # Try to add the output item as its own component
        input = BOMComponentInput(
            component_item_id=mock_bom.item_id,  # Same as BOM output
            quantity=Decimal("2"),
            uom="EACH",
        )

        with pytest.raises(HTTPException) as exc:
            BOMService.add_component(mock_db, org_id, mock_bom.bom_id, input)

        assert exc.value.status_code == 400
        assert "own component" in str(exc.value.detail).lower()

    def test_adds_component_successfully(
        self, mock_db, org_id, mock_bom, mock_component_item
    ):
        """Should add component when inputs are valid."""
        mock_db.get.side_effect = lambda model, id: (
            mock_bom if id == mock_bom.bom_id else mock_component_item
        )

        input = BOMComponentInput(
            component_item_id=mock_component_item.item_id,
            quantity=Decimal("3"),
            uom="EACH",
            scrap_percent=Decimal("5.0"),
            line_number=1,
        )

        BOMService.add_component(mock_db, org_id, mock_bom.bom_id, input)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called()

    def test_adds_component_with_warehouse(
        self, mock_db, org_id, mock_bom, mock_component_item, warehouse_id
    ):
        """Should add component with specific warehouse."""
        mock_db.get.side_effect = lambda model, id: (
            mock_bom if id == mock_bom.bom_id else mock_component_item
        )

        input = BOMComponentInput(
            component_item_id=mock_component_item.item_id,
            quantity=Decimal("2"),
            uom="EACH",
            warehouse_id=warehouse_id,
        )

        BOMService.add_component(mock_db, org_id, mock_bom.bom_id, input)

        mock_db.add.assert_called_once()


# ============ Tests for process_assembly ============


class TestProcessAssembly:
    """Tests for process_assembly method."""

    def test_raises_error_when_bom_not_found(
        self, mock_db, org_id, user_id, bom_id, warehouse_id, fiscal_period_id
    ):
        """Should raise HTTPException when BOM not found."""
        mock_db.get.return_value = None

        input = AssemblyInput(
            bom_id=bom_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("10"),
            fiscal_period_id=fiscal_period_id,
            transaction_date=datetime.now(UTC),
        )

        with pytest.raises(HTTPException) as exc:
            BOMService.process_assembly(mock_db, org_id, input, user_id)

        assert exc.value.status_code == 404

    def test_raises_error_when_bom_inactive(
        self, mock_db, org_id, user_id, mock_bom, warehouse_id, fiscal_period_id
    ):
        """Should raise HTTPException when BOM is not active."""
        mock_bom.is_active = False
        mock_db.get.return_value = mock_bom

        input = AssemblyInput(
            bom_id=mock_bom.bom_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("10"),
            fiscal_period_id=fiscal_period_id,
            transaction_date=datetime.now(UTC),
        )

        with pytest.raises(HTTPException) as exc:
            BOMService.process_assembly(mock_db, org_id, input, user_id)

        assert exc.value.status_code == 400
        assert "not active" in str(exc.value.detail).lower()

    def test_raises_error_when_no_components(
        self, mock_db, org_id, user_id, mock_bom, warehouse_id, fiscal_period_id
    ):
        """Should raise HTTPException when BOM has no components."""
        mock_db.get.return_value = mock_bom
        mock_db.query.return_value.filter.return_value.all.return_value = []

        input = AssemblyInput(
            bom_id=mock_bom.bom_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("10"),
            fiscal_period_id=fiscal_period_id,
            transaction_date=datetime.now(UTC),
        )

        with pytest.raises(HTTPException) as exc:
            BOMService.process_assembly(mock_db, org_id, input, user_id)

        assert exc.value.status_code == 400
        assert "no components" in str(exc.value.detail).lower()

    def test_raises_error_when_insufficient_components(
        self,
        mock_db,
        org_id,
        user_id,
        mock_bom,
        mock_component_item,
        warehouse_id,
        fiscal_period_id,
    ):
        """Should raise HTTPException when insufficient component inventory."""
        component = MockBOMComponent(
            bom_id=mock_bom.bom_id,
            component_item_id=mock_component_item.item_id,
            quantity=Decimal("5"),
        )
        mock_db.get.side_effect = lambda model, id: (
            mock_bom if id == mock_bom.bom_id else mock_component_item
        )
        mock_db.query.return_value.filter.return_value.all.return_value = [component]

        input = AssemblyInput(
            bom_id=mock_bom.bom_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("100"),  # Requires 500 components
            fiscal_period_id=fiscal_period_id,
            transaction_date=datetime.now(UTC),
        )

        with patch(
            "app.services.inventory.balance.InventoryBalanceService.get_available"
        ) as mock_get_available:
            mock_get_available.return_value = Decimal("100")  # Only 100 available

            with pytest.raises(HTTPException) as exc:
                BOMService.process_assembly(mock_db, org_id, input, user_id)

            assert exc.value.status_code == 400
            assert "Insufficient" in str(exc.value.detail)

    def test_creates_assembly_transactions(
        self,
        mock_db,
        org_id,
        user_id,
        mock_bom,
        mock_finished_item,
        mock_component_item,
        warehouse_id,
        fiscal_period_id,
    ):
        """Should create issue and receipt transactions for assembly."""
        component = MockBOMComponent(
            bom_id=mock_bom.bom_id,
            component_item_id=mock_component_item.item_id,
            quantity=Decimal("2"),
            uom="EACH",
        )
        mock_db.get.side_effect = lambda model, id: (
            mock_bom
            if id == mock_bom.bom_id
            else mock_finished_item
            if id == mock_bom.item_id
            else mock_component_item
        )
        mock_db.query.return_value.filter.return_value.all.return_value = [component]

        input = AssemblyInput(
            bom_id=mock_bom.bom_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("10"),
            fiscal_period_id=fiscal_period_id,
            transaction_date=datetime.now(UTC),
        )

        with patch(
            "app.services.inventory.balance.InventoryBalanceService.get_available"
        ) as mock_get_available:
            mock_get_available.return_value = Decimal("100")

            with patch(
                "app.services.inventory.transaction.InventoryTransactionService.create_issue"
            ) as mock_issue:
                with patch(
                    "app.services.inventory.transaction.InventoryTransactionService.create_receipt"
                ) as mock_receipt:
                    mock_issue_txn = MagicMock()
                    mock_issue_txn.transaction_id = uuid.uuid4()
                    mock_receipt_txn = MagicMock()
                    mock_receipt_txn.transaction_id = uuid.uuid4()
                    mock_issue.return_value = mock_issue_txn
                    mock_receipt.return_value = mock_receipt_txn

                    result = BOMService.process_assembly(
                        mock_db, org_id, input, user_id
                    )

                    assert isinstance(result, AssemblyResult)
                    assert result.output_quantity == Decimal("10")
                    mock_issue.assert_called_once()  # Component issue
                    mock_receipt.assert_called_once()  # Finished good receipt

    def test_calculates_cost_correctly(
        self,
        mock_db,
        org_id,
        user_id,
        mock_bom,
        mock_finished_item,
        mock_component_item,
        warehouse_id,
        fiscal_period_id,
    ):
        """Should calculate assembly cost from component costs."""
        component = MockBOMComponent(
            bom_id=mock_bom.bom_id,
            component_item_id=mock_component_item.item_id,
            quantity=Decimal("2"),
            uom="EACH",
        )
        mock_component_item.average_cost = Decimal("15.00")
        mock_db.get.side_effect = lambda model, id: (
            mock_bom
            if id == mock_bom.bom_id
            else mock_finished_item
            if id == mock_bom.item_id
            else mock_component_item
        )
        mock_db.query.return_value.filter.return_value.all.return_value = [component]

        input = AssemblyInput(
            bom_id=mock_bom.bom_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("5"),  # Produce 5 finished goods
            fiscal_period_id=fiscal_period_id,
            transaction_date=datetime.now(UTC),
        )

        with patch(
            "app.services.inventory.balance.InventoryBalanceService.get_available"
        ) as mock_get_available:
            mock_get_available.return_value = Decimal("100")

            with patch(
                "app.services.inventory.transaction.InventoryTransactionService.create_issue"
            ) as mock_issue:
                with patch(
                    "app.services.inventory.transaction.InventoryTransactionService.create_receipt"
                ) as mock_receipt:
                    mock_issue.return_value = MagicMock(transaction_id=uuid.uuid4())
                    mock_receipt.return_value = MagicMock(transaction_id=uuid.uuid4())

                    result = BOMService.process_assembly(
                        mock_db, org_id, input, user_id
                    )

                    # 2 components * 5 units * $15 = $150 total, $30 per unit
                    assert result.total_component_cost == Decimal("150.000000")
                    assert result.unit_cost == Decimal("30.000000")

    def test_applies_scrap_factor(
        self,
        mock_db,
        org_id,
        user_id,
        mock_bom,
        mock_finished_item,
        mock_component_item,
        warehouse_id,
        fiscal_period_id,
    ):
        """Should apply scrap percentage to component consumption."""
        component = MockBOMComponent(
            bom_id=mock_bom.bom_id,
            component_item_id=mock_component_item.item_id,
            quantity=Decimal("10"),
            scrap_percent=Decimal("10"),  # 10% scrap
            uom="EACH",
        )
        mock_db.get.side_effect = lambda model, id: (
            mock_bom
            if id == mock_bom.bom_id
            else mock_finished_item
            if id == mock_bom.item_id
            else mock_component_item
        )
        mock_db.query.return_value.filter.return_value.all.return_value = [component]

        input = AssemblyInput(
            bom_id=mock_bom.bom_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("1"),
            fiscal_period_id=fiscal_period_id,
            transaction_date=datetime.now(UTC),
        )

        with patch(
            "app.services.inventory.balance.InventoryBalanceService.get_available"
        ) as mock_get_available:
            mock_get_available.return_value = Decimal("100")

            with patch(
                "app.services.inventory.transaction.InventoryTransactionService.create_issue"
            ) as mock_issue:
                with patch(
                    "app.services.inventory.transaction.InventoryTransactionService.create_receipt"
                ) as mock_receipt:
                    mock_issue.return_value = MagicMock(transaction_id=uuid.uuid4())
                    mock_receipt.return_value = MagicMock(transaction_id=uuid.uuid4())

                    BOMService.process_assembly(mock_db, org_id, input, user_id)

                    # 10 * 1.10 = 11 components should be consumed
                    issue_call = mock_issue.call_args
                    assert issue_call[1]["input"].quantity == Decimal("11.000000")


# ============ Tests for process_disassembly ============


class TestProcessDisassembly:
    """Tests for process_disassembly method."""

    def test_raises_error_when_bom_not_found(
        self, mock_db, org_id, user_id, bom_id, warehouse_id, fiscal_period_id
    ):
        """Should raise HTTPException when BOM not found."""
        mock_db.get.return_value = None

        input = AssemblyInput(
            bom_id=bom_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("10"),
            fiscal_period_id=fiscal_period_id,
            transaction_date=datetime.now(UTC),
        )

        with pytest.raises(HTTPException) as exc:
            BOMService.process_disassembly(mock_db, org_id, input, user_id)

        assert exc.value.status_code == 404

    def test_raises_error_when_insufficient_finished_goods(
        self, mock_db, org_id, user_id, mock_bom, warehouse_id, fiscal_period_id
    ):
        """Should raise HTTPException when insufficient finished goods."""
        mock_db.get.return_value = mock_bom

        input = AssemblyInput(
            bom_id=mock_bom.bom_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("100"),
            fiscal_period_id=fiscal_period_id,
            transaction_date=datetime.now(UTC),
        )

        with patch(
            "app.services.inventory.balance.InventoryBalanceService.get_available"
        ) as mock_get_available:
            mock_get_available.return_value = Decimal("10")  # Only 10 available

            with pytest.raises(HTTPException) as exc:
                BOMService.process_disassembly(mock_db, org_id, input, user_id)

            assert exc.value.status_code == 400
            assert "Insufficient" in str(exc.value.detail)

    def test_creates_disassembly_transactions(
        self,
        mock_db,
        org_id,
        user_id,
        mock_bom,
        mock_finished_item,
        mock_component_item,
        warehouse_id,
        fiscal_period_id,
    ):
        """Should create issue and receipt transactions for disassembly."""
        component = MockBOMComponent(
            bom_id=mock_bom.bom_id,
            component_item_id=mock_component_item.item_id,
            quantity=Decimal("2"),
            uom="EACH",
        )
        mock_finished_item.average_cost = Decimal("30.00")
        mock_db.get.side_effect = lambda model, id: (
            mock_bom
            if id == mock_bom.bom_id
            else mock_finished_item
            if id == mock_bom.item_id
            else mock_component_item
        )
        mock_db.query.return_value.filter.return_value.all.return_value = [component]

        input = AssemblyInput(
            bom_id=mock_bom.bom_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("5"),  # Disassemble 5 units
            fiscal_period_id=fiscal_period_id,
            transaction_date=datetime.now(UTC),
        )

        with patch(
            "app.services.inventory.balance.InventoryBalanceService.get_available"
        ) as mock_get_available:
            mock_get_available.return_value = Decimal("100")

            with patch(
                "app.services.inventory.transaction.InventoryTransactionService.create_issue"
            ) as mock_issue:
                with patch(
                    "app.services.inventory.transaction.InventoryTransactionService.create_receipt"
                ) as mock_receipt:
                    mock_issue.return_value = MagicMock(transaction_id=uuid.uuid4())
                    mock_receipt.return_value = MagicMock(transaction_id=uuid.uuid4())

                    result = BOMService.process_disassembly(
                        mock_db, org_id, input, user_id
                    )

                    assert isinstance(result, AssemblyResult)
                    mock_issue.assert_called_once()  # Finished good issue
                    mock_receipt.assert_called_once()  # Component receipt


# ============ Tests for get ============


class TestGetBOM:
    """Tests for get method."""

    def test_raises_error_when_not_found(self, mock_db):
        """Should raise HTTPException when BOM not found."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            BOMService.get(mock_db, str(uuid.uuid4()))

        assert exc.value.status_code == 404

    def test_returns_bom(self, mock_db, mock_bom):
        """Should return BOM when found."""
        mock_db.get.return_value = mock_bom

        result = BOMService.get(mock_db, str(mock_bom.bom_id))

        assert result == mock_bom


# ============ Tests for get_default_for_item ============


class TestGetDefaultForItem:
    """Tests for get_default_for_item method."""

    def test_returns_none_when_no_default(self, mock_db, org_id, item_id):
        """Should return None when no default BOM exists."""
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = BOMService.get_default_for_item(mock_db, org_id, item_id)

        assert result is None

    def test_returns_default_bom(self, mock_db, org_id, mock_bom):
        """Should return default BOM for item."""
        mock_bom.is_default = True
        mock_bom.is_active = True
        mock_db.query.return_value.filter.return_value.first.return_value = mock_bom

        result = BOMService.get_default_for_item(mock_db, org_id, mock_bom.item_id)

        assert result == mock_bom


# ============ Tests for list ============


class TestListBOMs:
    """Tests for list method."""

    def test_returns_all_when_no_filters(self, mock_db, mock_bom):
        """Should return all BOMs when no filters applied."""
        mock_db.query.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = [
            mock_bom
        ]

        result = BOMService.list(mock_db)

        assert len(result) == 1

    def test_filters_by_organization(self, mock_db, org_id):
        """Should filter by organization_id."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = []

        BOMService.list(mock_db, organization_id=str(org_id))

        mock_db.query.return_value.filter.assert_called()

    def test_filters_by_item(self, mock_db, item_id):
        """Should filter by item_id."""
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = []

        BOMService.list(mock_db, item_id=str(item_id))

        assert mock_db.query.return_value.filter.called

    def test_filters_by_type(self, mock_db):
        """Should filter by bom_type."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = []

        BOMService.list(mock_db, bom_type=BOMType.KIT)

        mock_db.query.return_value.filter.assert_called()

    def test_filters_by_active_status(self, mock_db):
        """Should filter by is_active."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = []

        BOMService.list(mock_db, is_active=True)

        mock_db.query.return_value.filter.assert_called()


# ============ Tests for list_components ============


class TestListComponents:
    """Tests for list_components method."""

    def test_returns_components_for_bom(self, mock_db, bom_id):
        """Should return components for specified BOM."""
        comp1 = MockBOMComponent(bom_id=bom_id, line_number=1)
        comp2 = MockBOMComponent(bom_id=bom_id, line_number=2)
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            comp1,
            comp2,
        ]

        result = BOMService.list_components(mock_db, str(bom_id))

        assert len(result) == 2


# ============ Tests for module-level instance ============


class TestModuleInstance:
    """Tests for module-level singleton instance."""

    def test_singleton_instance_exists(self):
        """Should have module-level bom_service instance."""
        from app.services.inventory.bom import bom_service

        assert bom_service is not None
        assert isinstance(bom_service, BOMService)
