"""
Tests for CRM Purchase Order Sync.

Tests the create_purchase_order flow including:
- Idempotency via CRMSyncMapping
- Fallback idempotency via correlation_id
- Supplier resolution (erpnext_id, supplier_code, not found)
- Project linkage via CRM project mapping
- PO line creation
- CRMSyncMapping creation
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.sync.dotmac_crm_sync import CRMEntityType, CRMSyncMapping
from app.schemas.sync.dotmac_crm import (
    CRMPurchaseOrderItemPayload,
    CRMPurchaseOrderPayload,
)
from app.services.sync.dotmac_crm_sync_service import DotMacCRMSyncService


@pytest.fixture
def mock_db() -> MagicMock:
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def service(mock_db: MagicMock) -> DotMacCRMSyncService:
    """Create a service instance with mocked db."""
    return DotMacCRMSyncService(mock_db)


@pytest.fixture
def org_id() -> uuid.UUID:
    """Sample organization ID."""
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def person_id() -> uuid.UUID:
    """Sample person ID for the API key user."""
    return uuid.UUID("00000000-0000-0000-0000-000000000099")


@pytest.fixture
def sample_payload() -> CRMPurchaseOrderPayload:
    """Minimal valid PO payload."""
    return CRMPurchaseOrderPayload(
        omni_work_order_id="wo-abc-123",
        vendor_erp_id="SUP-0001",
        vendor_name="Acme Fiber Supplies",
        vendor_code="ACME",
        title="Fiber installation — Site 42",
        currency="NGN",
        subtotal=Decimal("50000"),
        tax_total=Decimal("3750"),
        total=Decimal("53750"),
        items=[
            CRMPurchaseOrderItemPayload(
                item_type="material",
                description="Single-mode fiber cable 12-core",
                quantity=Decimal("500"),
                unit_price=Decimal("80"),
                amount=Decimal("40000"),
                cable_type="SM",
                fiber_count=12,
            ),
            CRMPurchaseOrderItemPayload(
                item_type="labor",
                description="Splicing — 24 joints",
                quantity=Decimal("24"),
                unit_price=Decimal("416.67"),
                amount=Decimal("10000"),
                splice_count=24,
            ),
        ],
    )


class TestCreatePurchaseOrderIdempotency:
    """Test idempotency via CRMSyncMapping and correlation_id."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_returns_existing_po_when_mapping_exists(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        sample_payload: CRMPurchaseOrderPayload,
    ) -> None:
        """If CRMSyncMapping already exists for this work order, return existing PO."""
        from app.models.finance.ap.purchase_order import POStatus

        # Existing mapping
        existing_mapping = MagicMock()
        existing_mapping.local_entity_id = uuid.uuid4()

        # Existing PO
        existing_po = MagicMock()
        existing_po.po_id = existing_mapping.local_entity_id
        existing_po.po_number = "PO-2026-00001"
        existing_po.status = POStatus.DRAFT

        # _get_mapping returns existing mapping
        mock_db.scalar.return_value = existing_mapping
        mock_db.get.return_value = existing_po

        result = service.create_purchase_order(org_id, sample_payload, person_id)

        assert result.purchase_order_id == "PO-2026-00001"
        assert result.po_id == existing_po.po_id
        assert result.status == "draft"
        assert result.omni_work_order_id == "wo-abc-123"
        # Should not create any new records
        mock_db.add.assert_not_called()

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_returns_existing_po_via_correlation_id_fallback(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        sample_payload: CRMPurchaseOrderPayload,
    ) -> None:
        """If mapping doesn't exist but PO has matching correlation_id, return it."""
        from app.models.finance.ap.purchase_order import POStatus

        existing_po = MagicMock()
        existing_po.po_id = uuid.uuid4()
        existing_po.po_number = "PO-2026-00002"
        existing_po.status = POStatus.DRAFT

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No CRMSyncMapping
            return existing_po  # correlation_id lookup finds PO

        mock_db.scalar.side_effect = scalar_side_effect
        mock_db.get.return_value = None  # Not used in fallback path

        result = service.create_purchase_order(org_id, sample_payload, person_id)

        assert result.purchase_order_id == "PO-2026-00002"
        assert result.omni_work_order_id == "wo-abc-123"
        # Should create the missing mapping and commit it
        assert mock_db.add.call_count == 1
        added = mock_db.add.call_args[0][0]
        assert isinstance(added, CRMSyncMapping)
        assert added.crm_entity_type == CRMEntityType.PURCHASE_ORDER
        assert added.crm_id == "wo-abc-123"
        # Verify the re-created mapping was committed
        mock_db.commit.assert_called_once()


class TestSupplierResolution:
    """Test _resolve_supplier with different lookup strategies."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_resolve_by_erpnext_id(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        mock_db: MagicMock,
    ) -> None:
        """Should find supplier by erpnext_id first."""
        mock_supplier = MagicMock()
        mock_supplier.supplier_id = uuid.uuid4()
        mock_db.scalar.return_value = mock_supplier

        result = service._resolve_supplier(org_id, "SUP-0001", "ACME")

        assert result == mock_supplier
        # Only one DB call needed (erpnext_id lookup succeeded)
        assert mock_db.scalar.call_count == 1

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_resolve_fallback_to_supplier_code(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        mock_db: MagicMock,
    ) -> None:
        """Should fall back to supplier_code when erpnext_id not found."""
        mock_supplier = MagicMock()
        mock_supplier.supplier_id = uuid.uuid4()

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # erpnext_id not found
            return mock_supplier  # supplier_code found

        mock_db.scalar.side_effect = scalar_side_effect

        result = service._resolve_supplier(org_id, "NONEXISTENT", "ACME")

        assert result == mock_supplier
        assert mock_db.scalar.call_count == 2

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_raises_value_error_when_supplier_not_found(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        mock_db: MagicMock,
    ) -> None:
        """Should raise ValueError when neither lookup finds a supplier."""
        mock_db.scalar.return_value = None

        with pytest.raises(ValueError, match="Supplier not found"):
            service._resolve_supplier(org_id, "GHOST", "PHANTOM")

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_raises_when_both_identifiers_none(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        mock_db: MagicMock,
    ) -> None:
        """Should raise ValueError when no identifiers provided."""
        with pytest.raises(ValueError, match="Supplier not found"):
            service._resolve_supplier(org_id, None, None)


class TestCreatePurchaseOrderHappyPath:
    """Test the full create flow when no existing PO."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_creates_po_with_lines_and_mapping(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        sample_payload: CRMPurchaseOrderPayload,
    ) -> None:
        """Full happy path: creates PO, lines, and CRMSyncMapping."""
        mock_supplier = MagicMock()
        mock_supplier.supplier_id = uuid.uuid4()
        mock_supplier.supplier_code = "ACME"

        # Mock PO returned by PurchaseOrderService.create_po
        mock_po = MagicMock()
        mock_po.po_id = uuid.uuid4()
        mock_po.po_number = "PO-2026-00042"
        mock_po.status = MagicMock()
        mock_po.status.value = "DRAFT"

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No CRMSyncMapping
            if call_count == 2:
                return None  # No PO by correlation_id
            if call_count == 3:
                return mock_supplier  # Supplier lookup by erpnext_id
            return None  # Remaining lookups (project, person)

        mock_db.scalar.side_effect = scalar_side_effect

        with patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=mock_po,
        ) as mock_create_po:
            result = service.create_purchase_order(org_id, sample_payload, person_id)

        assert result.purchase_order_id == "PO-2026-00042"
        assert result.po_id == mock_po.po_id
        assert result.status == "draft"
        assert result.omni_work_order_id == "wo-abc-123"

        # Verify create_po was called with correct arguments
        mock_create_po.assert_called_once()
        args = mock_create_po.call_args
        assert args[0][0] == mock_db  # db
        assert args[0][1] == org_id  # organization_id
        po_input = args[0][2]
        assert po_input.supplier_id == mock_supplier.supplier_id
        assert po_input.currency_code == "NGN"
        assert po_input.correlation_id == "crm-wo:wo-abc-123"
        assert len(po_input.lines) == 2
        assert po_input.lines[0].description == "Single-mode fiber cable 12-core"
        assert po_input.lines[0].quantity_ordered == Decimal("500")
        assert po_input.lines[0].unit_price == Decimal("80")
        assert po_input.lines[1].description == "Splicing — 24 joints"

        # Verify tax distribution: total tax_total=3750 distributed by line amount ratio
        # Line 0: amount=40000, Line 1: amount=10000 → ratio 80:20
        total_tax = sum(line.tax_amount for line in po_input.lines)
        assert total_tax == Decimal("3750")
        assert po_input.lines[0].tax_amount == Decimal("3000.00")  # 40000/50000 * 3750
        assert po_input.lines[1].tax_amount == Decimal("750")  # remainder

        # Verify CRMSyncMapping was created
        mapping_add_calls = [
            c for c in mock_db.add.call_args_list if isinstance(c[0][0], CRMSyncMapping)
        ]
        assert len(mapping_add_calls) == 1
        mapping = mapping_add_calls[0][0][0]
        assert mapping.crm_entity_type == CRMEntityType.PURCHASE_ORDER
        assert mapping.crm_id == "wo-abc-123"
        assert mapping.local_entity_type == "purchase_order"
        assert mapping.local_entity_id == mock_po.po_id
        assert mapping.display_code == "PO-2026-00042"
        assert mapping.customer_name == "Acme Fiber Supplies"

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_uses_approver_person_id_when_email_resolved(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        sample_payload: CRMPurchaseOrderPayload,
    ) -> None:
        """When approved_by_email resolves, use that person as creator."""
        sample_payload.approved_by_email = "approver@example.com"

        mock_supplier = MagicMock()
        mock_supplier.supplier_id = uuid.uuid4()

        approver_person_id = uuid.uuid4()
        mock_po = MagicMock()
        mock_po.po_id = uuid.uuid4()
        mock_po.po_number = "PO-2026-00050"
        mock_po.status = MagicMock()
        mock_po.status.value = "DRAFT"

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No mapping
            if call_count == 2:
                return None  # No correlation_id PO
            if call_count == 3:
                return mock_supplier  # Supplier by erpnext_id
            if call_count == 4:
                return None  # Project mapping
            if call_count == 5:
                return approver_person_id  # Person ID from email
            return None

        mock_db.scalar.side_effect = scalar_side_effect

        with patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=mock_po,
        ) as mock_create_po:
            service.create_purchase_order(org_id, sample_payload, person_id)

        # created_by_user_id should be the resolved approver, not the API key person
        actual_creator = mock_create_po.call_args[0][3]
        assert actual_creator == approver_person_id

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_falls_back_to_api_key_person_when_email_not_resolved(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        sample_payload: CRMPurchaseOrderPayload,
    ) -> None:
        """When approved_by_email is None, use the API key's person_id."""
        sample_payload.approved_by_email = None

        mock_supplier = MagicMock()
        mock_supplier.supplier_id = uuid.uuid4()

        mock_po = MagicMock()
        mock_po.po_id = uuid.uuid4()
        mock_po.po_number = "PO-2026-00051"
        mock_po.status = MagicMock()
        mock_po.status.value = "DRAFT"

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No mapping
            if call_count == 2:
                return None  # No correlation_id PO
            if call_count == 3:
                return mock_supplier  # Supplier by erpnext_id
            return None  # project + person lookups

        mock_db.scalar.side_effect = scalar_side_effect

        with patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=mock_po,
        ) as mock_create_po:
            service.create_purchase_order(org_id, sample_payload, person_id)

        # created_by_user_id should be the API key person (fallback)
        actual_creator = mock_create_po.call_args[0][3]
        assert actual_creator == person_id


class TestCreatePurchaseOrderProjectLinkage:
    """Test that PO lines get project_id when CRM project is mapped."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_lines_get_project_id_when_omni_project_id_resolved(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        sample_payload: CRMPurchaseOrderPayload,
    ) -> None:
        """PO lines should have project_id when omni_project_id maps to local project."""
        sample_payload.omni_project_id = "crm-proj-789"

        mock_supplier = MagicMock()
        mock_supplier.supplier_id = uuid.uuid4()

        project_uuid = uuid.uuid4()
        project_mapping = MagicMock()
        project_mapping.local_entity_id = project_uuid

        mock_po = MagicMock()
        mock_po.po_id = uuid.uuid4()
        mock_po.po_number = "PO-2026-00060"
        mock_po.status = MagicMock()
        mock_po.status.value = "DRAFT"

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No PO mapping
            if call_count == 2:
                return None  # No correlation_id PO
            if call_count == 3:
                return mock_supplier  # Supplier
            if call_count == 4:
                return project_mapping  # Project CRMSyncMapping
            return None  # Person lookup

        mock_db.scalar.side_effect = scalar_side_effect

        with patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=mock_po,
        ) as mock_create_po:
            service.create_purchase_order(org_id, sample_payload, person_id)

        po_input = mock_create_po.call_args[0][2]
        for line in po_input.lines:
            assert line.project_id == project_uuid


class TestCreatePurchaseOrderErrors:
    """Test error handling in create_purchase_order."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_raises_value_error_when_supplier_not_found(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        sample_payload: CRMPurchaseOrderPayload,
    ) -> None:
        """Should propagate ValueError when supplier can't be resolved."""
        mock_db.scalar.return_value = None  # Nothing found anywhere
        mock_db.get.return_value = None

        with pytest.raises(ValueError, match="Supplier not found"):
            service.create_purchase_order(org_id, sample_payload, person_id)


class TestResolvePersonIdByEmail:
    """Test _resolve_person_id_by_email helper."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_returns_none_when_email_is_none(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
    ) -> None:
        """Should return None immediately when email is None."""
        result = service._resolve_person_id_by_email(org_id, None)
        assert result is None

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_returns_person_id_from_work_email(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        mock_db: MagicMock,
    ) -> None:
        """Should find person via Person.email (work email)."""
        expected_id = uuid.uuid4()
        mock_db.scalar.return_value = expected_id

        result = service._resolve_person_id_by_email(org_id, "John@Example.COM")

        assert result == expected_id
        # Only one DB call needed
        assert mock_db.scalar.call_count == 1

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_falls_back_to_personal_email(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        mock_db: MagicMock,
    ) -> None:
        """Should try personal_email when work email not found."""
        expected_id = uuid.uuid4()
        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # Work email not found
            return expected_id  # Personal email found

        mock_db.scalar.side_effect = scalar_side_effect

        result = service._resolve_person_id_by_email(org_id, "personal@example.com")

        assert result == expected_id
        assert mock_db.scalar.call_count == 2
