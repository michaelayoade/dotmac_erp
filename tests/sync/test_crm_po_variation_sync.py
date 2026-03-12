"""
Tests for CRM Purchase Order Variation/Amendment Sync.

Tests the create_purchase_order_variation flow including:
- Idempotency via variation_id
- Baseline PO lookup and superseding
- Amendment PO creation with proper linkage
- Terminal state rejection (CANCELLED/CLOSED)
- Sync mapping update with variation metadata
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.finance.ap.purchase_order import POStatus
from app.schemas.sync.dotmac_crm import (
    CRMPurchaseOrderItemPayload,
    CRMPurchaseOrderVariationPayload,
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
def baseline_po_id() -> uuid.UUID:
    """Baseline PO ID."""
    return uuid.UUID("00000000-0000-0000-0000-000000000010")


@pytest.fixture
def variation_payload() -> CRMPurchaseOrderVariationPayload:
    """Minimal valid variation payload."""
    return CRMPurchaseOrderVariationPayload(
        omni_work_order_id="wo-abc-123",
        variation_id="var-001-xyz",
        variation_version=2,
        amendment_reason="Scope increase — additional 200m fiber",
        vendor_erp_id="SUP-0001",
        vendor_name="Acme Fiber Supplies",
        vendor_code="ACME",
        title="Fiber installation — Site 42 (amended)",
        currency="NGN",
        subtotal=Decimal("70000"),
        tax_total=Decimal("5250"),
        total=Decimal("75250"),
        items=[
            CRMPurchaseOrderItemPayload(
                item_type="material",
                description="Single-mode fiber cable 12-core (extended)",
                quantity=Decimal("700"),
                unit_price=Decimal("80"),
                amount=Decimal("56000"),
                cable_type="SM",
                fiber_count=12,
            ),
            CRMPurchaseOrderItemPayload(
                item_type="labor",
                description="Splicing — 36 joints",
                quantity=Decimal("36"),
                unit_price=Decimal("388.89"),
                amount=Decimal("14000"),
                splice_count=36,
            ),
        ],
    )


class TestVariationIdempotency:
    """Test idempotency via variation_id."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_returns_existing_variation_po(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
        baseline_po_id: uuid.UUID,
    ) -> None:
        """If a PO with this variation_id already exists, return it."""
        existing_po = MagicMock()
        existing_po.po_id = uuid.uuid4()
        existing_po.po_number = "PO-2026-00002"
        existing_po.status = POStatus.DRAFT
        existing_po.amendment_version = 2
        existing_po.original_po_id = baseline_po_id
        existing_po.variation_id = "var-001-xyz"

        # First scalar call: variation_id lookup returns existing
        mock_db.scalar.return_value = existing_po

        result = service.create_purchase_order_variation(
            org_id, variation_payload, person_id
        )

        assert result.purchase_order_id == "PO-2026-00002"
        assert result.is_amendment is True
        assert result.variation_id == "var-001-xyz"
        assert result.amendment_version == 2
        assert result.superseded_po_id == baseline_po_id
        # No new records created
        mock_db.add.assert_not_called()


class TestVariationBaselineLookup:
    """Test baseline PO resolution."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_raises_when_no_baseline_mapping(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
    ) -> None:
        """Should raise ValueError when no baseline PO mapping exists."""
        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return None  # No variation PO, no baseline mapping

        mock_db.scalar.side_effect = scalar_side_effect

        with pytest.raises(ValueError, match="No baseline PO found"):
            service.create_purchase_order_variation(
                org_id, variation_payload, person_id
            )

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_raises_when_baseline_cancelled(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
        baseline_po_id: uuid.UUID,
    ) -> None:
        """Should raise ValueError when baseline PO is CANCELLED."""
        baseline_mapping = MagicMock()
        baseline_mapping.local_entity_id = baseline_po_id

        baseline_po = MagicMock()
        baseline_po.po_id = baseline_po_id
        baseline_po.po_number = "PO-2026-00001"
        baseline_po.status = POStatus.CANCELLED

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No existing variation PO
            return baseline_mapping  # Baseline mapping found

        mock_db.scalar.side_effect = scalar_side_effect
        mock_db.get.return_value = baseline_po

        with pytest.raises(ValueError, match="Cannot amend PO.*CANCELLED"):
            service.create_purchase_order_variation(
                org_id, variation_payload, person_id
            )

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_raises_when_baseline_closed(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
        baseline_po_id: uuid.UUID,
    ) -> None:
        """Should raise ValueError when baseline PO is CLOSED."""
        baseline_mapping = MagicMock()
        baseline_mapping.local_entity_id = baseline_po_id

        baseline_po = MagicMock()
        baseline_po.po_id = baseline_po_id
        baseline_po.po_number = "PO-2026-00001"
        baseline_po.status = POStatus.CLOSED

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return baseline_mapping

        mock_db.scalar.side_effect = scalar_side_effect
        mock_db.get.return_value = baseline_po

        with pytest.raises(ValueError, match="Cannot amend PO.*CLOSED"):
            service.create_purchase_order_variation(
                org_id, variation_payload, person_id
            )

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_raises_when_baseline_superseded(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
        baseline_po_id: uuid.UUID,
    ) -> None:
        """Should raise ValueError when baseline PO is already SUPERSEDED."""
        baseline_mapping = MagicMock()
        baseline_mapping.local_entity_id = baseline_po_id

        baseline_po = MagicMock()
        baseline_po.po_id = baseline_po_id
        baseline_po.po_number = "PO-2026-00001"
        baseline_po.status = POStatus.SUPERSEDED

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return baseline_mapping

        mock_db.scalar.side_effect = scalar_side_effect
        mock_db.get.return_value = baseline_po

        with pytest.raises(ValueError, match="Cannot amend PO.*SUPERSEDED"):
            service.create_purchase_order_variation(
                org_id, variation_payload, person_id
            )


class TestVariationAccountingSafety:
    """Test that received/invoiced POs cannot be superseded."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_raises_when_baseline_has_received_quantity(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
        baseline_po_id: uuid.UUID,
    ) -> None:
        """Should block amendment when baseline PO has received quantities."""
        baseline_mapping = MagicMock()
        baseline_mapping.local_entity_id = baseline_po_id

        baseline_po = MagicMock()
        baseline_po.po_id = baseline_po_id
        baseline_po.po_number = "PO-2026-00001"
        baseline_po.status = POStatus.APPROVED
        baseline_po.amount_received = Decimal("10000")
        baseline_po.amount_invoiced = Decimal("0")

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return baseline_mapping

        mock_db.scalar.side_effect = scalar_side_effect
        mock_db.get.return_value = baseline_po

        with pytest.raises(ValueError, match="Cannot supersede PO.*amount_received"):
            service.create_purchase_order_variation(
                org_id, variation_payload, person_id
            )

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_raises_when_baseline_has_invoiced_quantity(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
        baseline_po_id: uuid.UUID,
    ) -> None:
        """Should block amendment when baseline PO has invoiced quantities."""
        baseline_mapping = MagicMock()
        baseline_mapping.local_entity_id = baseline_po_id

        baseline_po = MagicMock()
        baseline_po.po_id = baseline_po_id
        baseline_po.po_number = "PO-2026-00001"
        baseline_po.status = POStatus.APPROVED
        baseline_po.amount_received = Decimal("0")
        baseline_po.amount_invoiced = Decimal("5000")

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return baseline_mapping

        mock_db.scalar.side_effect = scalar_side_effect
        mock_db.get.return_value = baseline_po

        with pytest.raises(ValueError, match="Cannot supersede PO.*amount_invoiced"):
            service.create_purchase_order_variation(
                org_id, variation_payload, person_id
            )

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_allows_amendment_when_zero_received_invoiced(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
        baseline_po_id: uuid.UUID,
    ) -> None:
        """Should allow amendment when PO has zero received/invoiced."""
        baseline_mapping = MagicMock()
        baseline_mapping.local_entity_id = baseline_po_id
        baseline_mapping.crm_data = {"total": "53750"}

        baseline_po = MagicMock()
        baseline_po.po_id = baseline_po_id
        baseline_po.po_number = "PO-2026-00001"
        baseline_po.status = POStatus.APPROVED
        baseline_po.amount_received = Decimal("0")
        baseline_po.amount_invoiced = Decimal("0")

        new_po = MagicMock()
        new_po.po_id = uuid.uuid4()
        new_po.po_number = "PO-2026-00002"
        new_po.status = POStatus.DRAFT

        mock_supplier = MagicMock()
        mock_supplier.supplier_id = uuid.uuid4()

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No existing variation PO
            if call_count == 2:
                return baseline_mapping
            if call_count == 3:
                return None  # Line-level activity check (no lines with qty)
            if call_count == 4:
                return mock_supplier
            return None

        mock_db.scalar.side_effect = scalar_side_effect
        mock_db.get.return_value = baseline_po

        with patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=new_po,
        ):
            result = service.create_purchase_order_variation(
                org_id, variation_payload, person_id
            )

        assert result.is_amendment is True
        assert result.purchase_order_id == "PO-2026-00002"
        assert baseline_po.status == POStatus.SUPERSEDED


class TestVariationCreation:
    """Test the full variation creation flow."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_creates_amendment_and_supersedes_baseline(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
        baseline_po_id: uuid.UUID,
    ) -> None:
        """Full happy path: create amendment PO, supersede baseline, update mapping."""
        baseline_mapping = MagicMock()
        baseline_mapping.local_entity_id = baseline_po_id
        baseline_mapping.crm_data = {
            "omni_work_order_id": "wo-abc-123",
            "total": "53750",
        }

        baseline_po = MagicMock()
        baseline_po.po_id = baseline_po_id
        baseline_po.po_number = "PO-2026-00001"
        baseline_po.status = POStatus.APPROVED
        baseline_po.amount_received = Decimal("0")
        baseline_po.amount_invoiced = Decimal("0")

        new_po = MagicMock()
        new_po.po_id = uuid.uuid4()
        new_po.po_number = "PO-2026-00002"
        new_po.status = POStatus.DRAFT

        mock_supplier = MagicMock()
        mock_supplier.supplier_id = uuid.uuid4()

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No existing variation PO
            if call_count == 2:
                return baseline_mapping  # Baseline mapping found
            if call_count == 3:
                return None  # Line-level activity check (no lines with qty)
            if call_count == 4:
                return mock_supplier  # Supplier resolved
            return None  # No project, no approver

        mock_db.scalar.side_effect = scalar_side_effect
        mock_db.get.return_value = baseline_po

        with patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=new_po,
        ):
            result = service.create_purchase_order_variation(
                org_id, variation_payload, person_id
            )

        # Verify result
        assert result.purchase_order_id == "PO-2026-00002"
        assert result.is_amendment is True
        assert result.variation_id == "var-001-xyz"
        assert result.amendment_version == 2
        assert result.superseded_po_id == baseline_po_id
        assert result.status == "draft"

        # Verify baseline was superseded
        assert baseline_po.status == POStatus.SUPERSEDED

        # Verify amendment fields set on new PO
        assert new_po.is_amendment is True
        assert new_po.original_po_id == baseline_po_id
        assert new_po.amendment_version == 2
        assert new_po.amendment_reason == "Scope increase — additional 200m fiber"
        assert new_po.variation_id == "var-001-xyz"

        # Verify mapping updated
        assert baseline_mapping.local_entity_id == new_po.po_id
        assert baseline_mapping.display_code == "PO-2026-00002"
        assert baseline_mapping.crm_data["variation_id"] == "var-001-xyz"
        assert baseline_mapping.crm_data["variation_version"] == 2
        assert baseline_mapping.crm_data["superseded_po_id"] == str(baseline_po_id)

        # Verify commit
        mock_db.commit.assert_called_once()


class TestVariationPayloadValidation:
    """Test schema validation for variation payloads."""

    def test_requires_variation_id(self) -> None:
        """variation_id is required."""
        with pytest.raises(Exception):
            CRMPurchaseOrderVariationPayload(
                omni_work_order_id="wo-abc-123",
                # variation_id missing
                variation_version=2,
                amendment_reason="Test",
                title="Test",
                subtotal=Decimal("100"),
                total=Decimal("100"),
                items=[
                    CRMPurchaseOrderItemPayload(
                        item_type="material",
                        description="Test",
                        quantity=Decimal("1"),
                        unit_price=Decimal("100"),
                        amount=Decimal("100"),
                    )
                ],
            )

    def test_variation_version_must_be_gte_2(self) -> None:
        """variation_version must be >= 2 (baseline is version 1)."""
        with pytest.raises(Exception):
            CRMPurchaseOrderVariationPayload(
                omni_work_order_id="wo-abc-123",
                variation_id="var-001",
                variation_version=1,  # Must be >= 2
                amendment_reason="Test",
                title="Test",
                subtotal=Decimal("100"),
                total=Decimal("100"),
                items=[
                    CRMPurchaseOrderItemPayload(
                        item_type="material",
                        description="Test",
                        quantity=Decimal("1"),
                        unit_price=Decimal("100"),
                        amount=Decimal("100"),
                    )
                ],
            )

    def test_valid_variation_payload(self) -> None:
        """A valid variation payload should parse successfully."""
        payload = CRMPurchaseOrderVariationPayload(
            omni_work_order_id="wo-abc-123",
            variation_id="var-001",
            variation_version=2,
            amendment_reason="Price adjustment",
            title="Updated PO",
            subtotal=Decimal("100"),
            total=Decimal("100"),
            items=[
                CRMPurchaseOrderItemPayload(
                    item_type="material",
                    description="Test item",
                    quantity=Decimal("1"),
                    unit_price=Decimal("100"),
                    amount=Decimal("100"),
                )
            ],
        )
        assert payload.variation_id == "var-001"
        assert payload.variation_version == 2


class TestPOStatusSuperseded:
    """Test SUPERSEDED status enum value."""

    def test_superseded_status_exists(self) -> None:
        """POStatus should have a SUPERSEDED value."""
        assert POStatus.SUPERSEDED.value == "SUPERSEDED"

    def test_superseded_is_distinct(self) -> None:
        """SUPERSEDED should be distinct from CANCELLED."""
        assert POStatus.SUPERSEDED != POStatus.CANCELLED


class TestCRMPurchaseOrderResponseVariationFields:
    """Test the response schema includes variation fields."""

    def test_response_default_values(self) -> None:
        """Default response should indicate no amendment."""
        from app.schemas.sync.dotmac_crm import CRMPurchaseOrderResponse

        resp = CRMPurchaseOrderResponse(
            purchase_order_id="PO-001",
            po_id=uuid.uuid4(),
            status="draft",
            omni_work_order_id="wo-123",
        )
        assert resp.is_amendment is False
        assert resp.variation_id is None
        assert resp.amendment_version == 1
        assert resp.superseded_po_id is None

    def test_response_with_variation_fields(self) -> None:
        """Response with variation fields populates correctly."""
        from app.schemas.sync.dotmac_crm import CRMPurchaseOrderResponse

        superseded_id = uuid.uuid4()
        resp = CRMPurchaseOrderResponse(
            purchase_order_id="PO-002",
            po_id=uuid.uuid4(),
            status="draft",
            omni_work_order_id="wo-123",
            is_amendment=True,
            variation_id="var-xyz",
            amendment_version=3,
            superseded_po_id=superseded_id,
        )
        assert resp.is_amendment is True
        assert resp.variation_id == "var-xyz"
        assert resp.amendment_version == 3
        assert resp.superseded_po_id == superseded_id


class TestVariationIntegrityErrorHandling:
    """Test DB-level conflict handling when concurrent requests race."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_integrity_error_returns_existing_po(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
        baseline_po_id: uuid.UUID,
    ) -> None:
        """On IntegrityError (race), rollback and return the existing PO."""
        from types import SimpleNamespace

        from sqlalchemy.exc import IntegrityError

        baseline_mapping = MagicMock()
        baseline_mapping.local_entity_id = baseline_po_id
        baseline_mapping.crm_data = {"total": "53750"}

        baseline_po = MagicMock()
        baseline_po.po_id = baseline_po_id
        baseline_po.po_number = "PO-2026-00001"
        baseline_po.status = POStatus.APPROVED
        baseline_po.amount_received = Decimal("0")
        baseline_po.amount_invoiced = Decimal("0")

        new_po = MagicMock()
        new_po.po_id = uuid.uuid4()
        new_po.po_number = "PO-2026-00002"
        new_po.status = POStatus.DRAFT

        existing_winner = MagicMock()
        existing_winner.po_id = uuid.uuid4()
        existing_winner.po_number = "PO-2026-00003"
        existing_winner.status = POStatus.DRAFT
        existing_winner.amendment_version = 2
        existing_winner.original_po_id = baseline_po_id
        existing_winner.variation_id = "var-001-xyz"

        mock_supplier = MagicMock()
        mock_supplier.supplier_id = uuid.uuid4()

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No existing variation PO (initial check)
            if call_count == 2:
                return baseline_mapping
            if call_count == 3:
                return None  # Line-level activity check (no lines with qty)
            if call_count == 4:
                return mock_supplier
            return None  # No project/approver

        mock_db.scalar.side_effect = scalar_side_effect
        mock_db.get.return_value = baseline_po
        # Simulate IntegrityError on commit, then return existing on retry query
        mock_db.commit.side_effect = IntegrityError(
            "duplicate key",
            params={},
            orig=SimpleNamespace(
                diag=SimpleNamespace(constraint_name="uq_po_variation_id")
            ),
        )

        def do_rollback():
            # After rollback, reset scalar to return the existing winner
            mock_db.scalar.side_effect = None
            mock_db.scalar.return_value = existing_winner

        mock_db.rollback.side_effect = do_rollback

        with patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=new_po,
        ):
            result = service.create_purchase_order_variation(
                org_id, variation_payload, person_id
            )

        # Should have rolled back
        mock_db.rollback.assert_called_once()
        # Should return the existing winner's data
        assert result.purchase_order_id == "PO-2026-00003"
        assert result.is_amendment is True
        assert result.variation_id == "var-001-xyz"

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_non_variation_integrity_error_is_raised(
        self,
        mock_select: MagicMock,
        service: DotMacCRMSyncService,
        org_id: uuid.UUID,
        person_id: uuid.UUID,
        mock_db: MagicMock,
        variation_payload: CRMPurchaseOrderVariationPayload,
        baseline_po_id: uuid.UUID,
    ) -> None:
        """Non-variation IntegrityErrors must not be treated as idempotent wins."""
        from types import SimpleNamespace

        from sqlalchemy.exc import IntegrityError

        baseline_mapping = MagicMock()
        baseline_mapping.local_entity_id = baseline_po_id
        baseline_mapping.crm_data = {"total": "53750"}

        baseline_po = MagicMock()
        baseline_po.po_id = baseline_po_id
        baseline_po.po_number = "PO-2026-00001"
        baseline_po.status = POStatus.APPROVED
        baseline_po.amount_received = Decimal("0")
        baseline_po.amount_invoiced = Decimal("0")

        new_po = MagicMock()
        new_po.po_id = uuid.uuid4()
        new_po.po_number = "PO-2026-00002"
        new_po.status = POStatus.DRAFT

        mock_supplier = MagicMock()
        mock_supplier.supplier_id = uuid.uuid4()

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            if call_count == 2:
                return baseline_mapping
            if call_count == 3:
                return None
            if call_count == 4:
                return mock_supplier
            return None

        mock_db.scalar.side_effect = scalar_side_effect
        mock_db.get.return_value = baseline_po
        mock_db.commit.side_effect = IntegrityError(
            "other constraint",
            params={},
            orig=SimpleNamespace(diag=SimpleNamespace(constraint_name="uq_other")),
        )

        with patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=new_po,
        ):
            with pytest.raises(IntegrityError):
                service.create_purchase_order_variation(
                    org_id, variation_payload, person_id
                )

        mock_db.rollback.assert_called_once()
