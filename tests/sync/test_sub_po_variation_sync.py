from __future__ import annotations

from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy.exc import IntegrityError

from app.models.finance.ap.purchase_order import POStatus
from app.schemas.sync.dotmac_sub import (
    SubPurchaseOrderItemPayload,
    SubPurchaseOrderVariationPayload,
)
from app.services.sync.dotmac_sub_sync_service import DotmacSubSyncService
from app.services.sync.sub.errors import SubReplayConflictError


@pytest.fixture
def payload() -> SubPurchaseOrderVariationPayload:
    return SubPurchaseOrderVariationPayload(
        source_work_order_id="wo-abc-123",
        source_quote_id="quote-456",
        source_project_id="project-789",
        variation_id="var-001-xyz",
        variation_version=2,
        amendment_reason="Scope increase — additional 200m fiber",
        vendor_erp_id="SUP-0001",
        vendor_name="Acme Fiber Supplies",
        vendor_code="ACME",
        title="Fiber installation — Site 42 (amended)",
        currency="NGN",
        subtotal=Decimal("70000.00"),
        tax_total=Decimal("5250.00"),
        total=Decimal("75250.00"),
        items=[
            SubPurchaseOrderItemPayload(
                item_type="material",
                description="Single-mode fiber cable 12-core (extended)",
                quantity=Decimal("700"),
                unit_price=Decimal("80.00"),
                amount=Decimal("56000.00"),
            ),
            SubPurchaseOrderItemPayload(
                item_type="labor",
                description="Splicing — 35 joints",
                quantity=Decimal("35"),
                unit_price=Decimal("400.00"),
                amount=Decimal("14000.00"),
            ),
        ],
    )


def test_variation_retry_returns_existing_amendment(
    payload: SubPurchaseOrderVariationPayload,
) -> None:
    db = MagicMock()
    original_id = uuid4()
    existing = MagicMock(
        po_id=uuid4(),
        po_number="PO-2026-00002",
        status=POStatus.DRAFT,
        amendment_version=2,
        original_po_id=original_id,
        source_fingerprint=fingerprint_of(payload),
    )
    db.scalar.return_value = existing

    result = DotmacSubSyncService(db).create_purchase_order_variation(
        uuid4(), payload, uuid4()
    )

    assert result.po_id == existing.po_id
    assert result.source_work_order_id == payload.source_work_order_id
    assert result.is_amendment is True
    assert result.variation_id == payload.variation_id
    assert result.superseded_po_id == original_id


def test_changed_variation_replay_is_rejected(
    payload: SubPurchaseOrderVariationPayload,
) -> None:
    existing = MagicMock(
        po_id=uuid4(),
        po_number="PO-2026-00002",
        status=POStatus.DRAFT,
        amendment_version=2,
        original_po_id=uuid4(),
        source_fingerprint=fingerprint_of(payload),
    )
    db = MagicMock()
    db.scalar.return_value = existing
    changed = payload.model_copy(
        update={"amendment_reason": "Changed immutable amendment reason"}
    )

    with pytest.raises(SubReplayConflictError, match="different immutable payload"):
        DotmacSubSyncService(db).create_purchase_order_variation(
            uuid4(), changed, uuid4()
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("line", "line 1 amount"),
        ("subtotal", "subtotal"),
        ("total", "total"),
    ],
)
def test_variation_money_mismatch_fails_before_any_database_work(
    payload: SubPurchaseOrderVariationPayload,
    mutation: str,
    message: str,
) -> None:
    changed = payload.model_copy(deep=True)
    if mutation == "line":
        changed.items[0].amount = Decimal("55999.99")
    elif mutation == "subtotal":
        changed.subtotal = Decimal("69999.99")
    else:
        changed.total = Decimal("75249.99")
    db = MagicMock()

    with pytest.raises(ValueError, match=message):
        DotmacSubSyncService(db).create_purchase_order_variation(
            uuid4(), changed, uuid4()
        )

    db.scalar.assert_not_called()
    db.begin_nested.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_not_called()


def test_variation_requires_the_preceding_correlation(
    payload: SubPurchaseOrderVariationPayload,
) -> None:
    db = MagicMock()
    db.scalar.side_effect = [None, None]

    with pytest.raises(ValueError, match="No baseline PO found"):
        DotmacSubSyncService(db).create_purchase_order_variation(
            uuid4(), payload, uuid4()
        )


@pytest.mark.parametrize(
    "status", [POStatus.CANCELLED, POStatus.CLOSED, POStatus.SUPERSEDED]
)
def test_variation_refuses_terminal_baseline(
    payload: SubPurchaseOrderVariationPayload, status: POStatus
) -> None:
    baseline = MagicMock(po_number="PO-BASE", status=status)
    db = MagicMock()
    db.scalar.side_effect = [None, baseline]

    with pytest.raises(ValueError, match="Cannot amend PO"):
        DotmacSubSyncService(db).create_purchase_order_variation(
            uuid4(), payload, uuid4()
        )


def test_variation_refuses_baseline_with_accounting_activity(
    payload: SubPurchaseOrderVariationPayload,
) -> None:
    baseline = MagicMock(
        po_id=uuid4(),
        po_number="PO-BASE",
        status=POStatus.APPROVED,
        amount_received=Decimal("1"),
        amount_invoiced=Decimal("0"),
    )
    db = MagicMock()
    db.scalar.side_effect = [None, baseline, None]

    with pytest.raises(ValueError, match="received or invoiced quantities"):
        DotmacSubSyncService(db).create_purchase_order_variation(
            uuid4(), payload, uuid4()
        )


def test_variation_supersedes_baseline_and_uses_versioned_correlation(
    payload: SubPurchaseOrderVariationPayload,
) -> None:
    org_id = uuid4()
    actor_id = uuid4()
    baseline = MagicMock(
        po_id=uuid4(),
        po_number="PO-BASE",
        status=POStatus.APPROVED,
        amount_received=Decimal("0"),
        amount_invoiced=Decimal("0"),
    )
    created = MagicMock(po_id=uuid4(), po_number="PO-AMEND", status=POStatus.DRAFT)
    supplier = MagicMock(supplier_id=uuid4())
    db = MagicMock()
    db.scalar.side_effect = [None, baseline, None]
    service = DotmacSubSyncService(db)

    with (
        patch.object(service, "_resolve_supplier", return_value=supplier),
        patch.object(service, "_resolve_project_id", return_value=None),
        patch.object(service, "_resolve_person_id_by_email", return_value=None),
        patch(
            "app.services.finance.ap.purchase_order.PurchaseOrderService.create_po",
            return_value=created,
        ) as create_po,
    ):
        result = service.create_purchase_order_variation(org_id, payload, actor_id)

    po_input = create_po.call_args.args[2]
    assert po_input.correlation_id == "sub-wo:wo-abc-123:v2"
    assert baseline.status == POStatus.SUPERSEDED
    assert created.original_po_id == baseline.po_id
    assert created.variation_id == payload.variation_id
    assert created.source_fingerprint == fingerprint_of(payload)
    assert result.superseded_po_id == baseline.po_id
    assert result.source_work_order_id == payload.source_work_order_id
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.parametrize("winner_matches", [True, False], ids=["matching", "changed"])
def test_concurrent_variation_create_validates_the_winner(
    payload: SubPurchaseOrderVariationPayload,
    winner_matches: bool,
) -> None:
    org_id = uuid4()
    actor_id = uuid4()
    baseline = MagicMock(
        po_id=uuid4(),
        po_number="PO-BASE",
        status=POStatus.APPROVED,
        amount_received=Decimal("0"),
        amount_invoiced=Decimal("0"),
    )
    winner = MagicMock(
        po_id=uuid4(),
        po_number="PO-AMEND-WINNER",
        status=POStatus.DRAFT,
        amendment_version=2,
        original_po_id=baseline.po_id,
        source_fingerprint=fingerprint_of(payload) if winner_matches else "0" * 64,
    )
    supplier = MagicMock(supplier_id=uuid4())
    db = MagicMock()
    db.scalar.side_effect = [None, baseline, None, winner]
    service = DotmacSubSyncService(db)

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
        result = service.create_purchase_order_variation(org_id, payload, actor_id)

    if winner_matches:
        assert result.po_id == winner.po_id
        assert result.superseded_po_id == baseline.po_id
    assert baseline.status == POStatus.APPROVED
    db.begin_nested.assert_called_once()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
