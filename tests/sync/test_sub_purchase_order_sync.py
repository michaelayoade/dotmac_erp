from __future__ import annotations

from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy.exc import IntegrityError

from app.models.finance.ap.purchase_order import POStatus
from app.schemas.sync.dotmac_sub import (
    SubPurchaseOrderItemPayload,
    SubPurchaseOrderPayload,
)
from app.services.sync.dotmac_sub_sync_service import DotmacSubSyncService
from app.services.sync.sub.errors import SubReplayConflictError


@pytest.fixture
def org_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def person_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000099")


@pytest.fixture
def payload() -> SubPurchaseOrderPayload:
    return SubPurchaseOrderPayload(
        source_work_order_id="wo-abc-123",
        source_quote_id="quote-123",
        source_project_id="project-123",
        vendor_erp_id="SUP-0001",
        vendor_name="Acme Fiber Supplies",
        vendor_code="ACME",
        title="Fiber installation — Site 42",
        currency="NGN",
        subtotal=Decimal("50000.00"),
        tax_total=Decimal("3750.00"),
        total=Decimal("53750.00"),
        items=[
            SubPurchaseOrderItemPayload(
                item_type="material",
                description="Single-mode fiber cable 12-core",
                quantity=Decimal("500"),
                unit_price=Decimal("80.00"),
                amount=Decimal("40000.00"),
            ),
            SubPurchaseOrderItemPayload(
                item_type="labor",
                description="Splicing — 25 joints",
                quantity=Decimal("25"),
                unit_price=Decimal("400.00"),
                amount=Decimal("10000.00"),
            ),
        ],
    )


def test_retry_returns_purchase_order_with_matching_correlation(
    org_id: UUID, person_id: UUID, payload: SubPurchaseOrderPayload
) -> None:
    db = MagicMock()
    existing = MagicMock(
        po_id=uuid4(),
        po_number="PO-2026-00001",
        status=POStatus.DRAFT,
        source_fingerprint=fingerprint_of(payload),
    )
    db.scalar.return_value = existing

    result = DotmacSubSyncService(db).create_purchase_order(org_id, payload, person_id)

    assert result.po_id == existing.po_id
    assert result.source_work_order_id == payload.source_work_order_id
    assert result.status == "draft"
    db.add.assert_not_called()


def test_changed_purchase_order_replay_is_rejected(
    org_id: UUID, person_id: UUID, payload: SubPurchaseOrderPayload
) -> None:
    existing = MagicMock(
        po_id=uuid4(),
        po_number="PO-2026-00001",
        status=POStatus.DRAFT,
        source_fingerprint=fingerprint_of(payload),
    )
    db = MagicMock()
    db.scalar.return_value = existing
    changed = payload.model_copy(update={"title": "Changed immutable title"})

    with pytest.raises(SubReplayConflictError, match="different immutable payload"):
        DotmacSubSyncService(db).create_purchase_order(org_id, changed, person_id)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("line", "line 1 amount"),
        ("subtotal", "subtotal"),
        ("total", "total"),
    ],
)
def test_purchase_order_money_mismatch_fails_before_any_database_work(
    org_id: UUID,
    person_id: UUID,
    payload: SubPurchaseOrderPayload,
    mutation: str,
    message: str,
) -> None:
    changed = payload.model_copy(deep=True)
    if mutation == "line":
        changed.items[0].amount = Decimal("39999.99")
    elif mutation == "subtotal":
        changed.subtotal = Decimal("49999.99")
    else:
        changed.total = Decimal("53749.99")
    db = MagicMock()

    with pytest.raises(ValueError, match=message):
        DotmacSubSyncService(db).create_purchase_order(org_id, changed, person_id)

    db.scalar.assert_not_called()
    db.begin_nested.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_not_called()


def test_create_purchase_order_uses_canonical_correlation_and_tax_distribution(
    org_id: UUID, person_id: UUID, payload: SubPurchaseOrderPayload
) -> None:
    db = MagicMock()
    db.scalar.return_value = None
    supplier = MagicMock(supplier_id=uuid4(), supplier_code="ACME")
    project_id = uuid4()
    created = MagicMock(po_id=uuid4(), po_number="PO-2026-00042", status=POStatus.DRAFT)
    service = DotmacSubSyncService(db)

    with (
        patch.object(service, "_resolve_supplier", return_value=supplier),
        patch.object(service, "_resolve_project_id", return_value=project_id),
        patch.object(service, "_resolve_person_id_by_email", return_value=None),
        patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=created,
        ) as create_po,
    ):
        result = service.create_purchase_order(org_id, payload, person_id)

    po_input = create_po.call_args.args[2]
    assert po_input.correlation_id == "sub-wo:wo-abc-123"
    assert po_input.currency_code == "NGN"
    assert [line.project_id for line in po_input.lines] == [project_id, project_id]
    assert [line.tax_amount for line in po_input.lines] == [
        Decimal("3000.00"),
        Decimal("750.00"),
    ]
    assert create_po.call_args.args[3] == person_id
    assert created.source_fingerprint == fingerprint_of(payload)
    assert result.po_id == created.po_id
    assert result.source_work_order_id == "wo-abc-123"


def test_resolved_approver_is_the_purchase_order_creator(
    org_id: UUID, person_id: UUID, payload: SubPurchaseOrderPayload
) -> None:
    payload.approved_by_email = "approver@example.com"
    db = MagicMock()
    db.scalar.return_value = None
    service = DotmacSubSyncService(db)
    approver_id = uuid4()
    supplier = MagicMock(supplier_id=uuid4(), supplier_code="ACME")
    created = MagicMock(po_id=uuid4(), po_number="PO-1", status=POStatus.DRAFT)

    with (
        patch.object(service, "_resolve_supplier", return_value=supplier),
        patch.object(service, "_resolve_project_id", return_value=None),
        patch.object(service, "_resolve_person_id_by_email", return_value=approver_id),
        patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=created,
        ) as create_po,
    ):
        service.create_purchase_order(org_id, payload, person_id)

    assert create_po.call_args.args[3] == approver_id


@pytest.mark.parametrize("winner_matches", [True, False], ids=["matching", "changed"])
def test_concurrent_purchase_order_create_validates_the_winner(
    org_id: UUID,
    person_id: UUID,
    payload: SubPurchaseOrderPayload,
    winner_matches: bool,
) -> None:
    winner = MagicMock(
        po_id=uuid4(),
        po_number="PO-2026-00099",
        status=POStatus.DRAFT,
        source_fingerprint=fingerprint_of(payload) if winner_matches else "0" * 64,
    )
    db = MagicMock()
    db.scalar.side_effect = [None, winner]
    service = DotmacSubSyncService(db)
    supplier = MagicMock(supplier_id=uuid4(), supplier_code="ACME")

    outcome = (
        nullcontext()
        if winner_matches
        else pytest.raises(SubReplayConflictError, match="different immutable payload")
    )
    with (
        patch.object(service, "_resolve_supplier", return_value=supplier),
        patch.object(service, "_resolve_project_id", return_value=None),
        patch.object(service, "_resolve_person_id_by_email", return_value=None),
        patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            side_effect=IntegrityError("insert", {}, Exception("duplicate")),
        ),
        outcome,
    ):
        result = service.create_purchase_order(org_id, payload, person_id)

    if winner_matches:
        assert result.po_id == winner.po_id
        assert result.purchase_order_id == winner.po_number
    db.begin_nested.assert_called_once()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_supplier_resolution_fails_closed_when_no_identifier_matches(
    org_id: UUID,
) -> None:
    service = DotmacSubSyncService(MagicMock())
    service.db.scalar.return_value = None

    with pytest.raises(ValueError, match="Supplier not found"):
        service._resolve_supplier(org_id, "GHOST", "PHANTOM")
