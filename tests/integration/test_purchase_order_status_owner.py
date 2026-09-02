"""PostgreSQL proof that the purchase-order status lifecycle has one owner.

`PurchaseOrder.status` had three writers. `PurchaseOrderService` did the
lifecycle transitions behind inline guards, `GoodsReceiptService` assigned
RECEIVED / PARTIALLY_RECEIVED straight from the line quantities without passing
through any of them, and the generic workflow `update_field` action could
`setattr` the column to anything at all.

`app.services.finance.ap.purchase_order_status` now owns it. These tests run
against real PostgreSQL because the CANCEL interlock derives from receipt rows
with a real SQL aggregate, and because the two bugs below are only visible when
line quantities actually move.

The load-bearing cases:

* **the SoD bypass**, which is the reason the automation gate exists — a PO must
  not be able to reach APPROVED without `approve_po` having run;
* **the bricked purchase order** — reversing a PO's only receipt used to strand
  it at RECEIVED, where it could be neither cancelled nor received against;
* **the interlock moved onto the transition**, so a second cancel path cannot be
  written without it.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _make_po(db, org_id, supplier, user_id, number, status=None):
    from app.models.finance.ap.purchase_order import POStatus, PurchaseOrder

    po = PurchaseOrder(
        organization_id=org_id,
        supplier_id=supplier.supplier_id,
        po_number=number,
        po_date=date(2024, 1, 10),
        currency_code="USD",
        exchange_rate=Decimal("1.0"),
        subtotal=Decimal("1000.00"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("1000.00"),
        status=status or POStatus.DRAFT,
        created_by_user_id=user_id,
    )
    db.add(po)
    db.flush()
    return po


def _make_line(db, po, ordered="10", received="0", unit_price="100.00"):
    from app.models.finance.ap.purchase_order_line import PurchaseOrderLine

    line = PurchaseOrderLine(
        po_id=po.po_id,
        line_number=1,
        description="Line 1",
        quantity_ordered=Decimal(ordered),
        quantity_received=Decimal(received),
        unit_price=Decimal(unit_price),
        line_amount=Decimal(ordered) * Decimal(unit_price),
    )
    db.add(line)
    db.flush()
    return line


def test_a_purchase_order_is_created_in_draft(
    db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID
) -> None:
    """DRAFT is the construction default, not a transition target."""
    from app.models.finance.ap.purchase_order import POStatus

    po = _make_po(db, org_id, supplier, user_id, "PO-ST-1")
    assert po.status == POStatus.DRAFT


def test_the_lifecycle_runs_draft_to_pending_to_approved(
    db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID
) -> None:
    from app.models.finance.ap.purchase_order import POStatus
    from app.services.finance.ap.purchase_order import PurchaseOrderService

    po = _make_po(db, org_id, supplier, user_id, "PO-ST-2")

    PurchaseOrderService.submit_for_approval(db, org_id, po.po_id, user_id)
    assert po.status == POStatus.PENDING_APPROVAL

    approver = uuid.uuid4()
    PurchaseOrderService.approve_po(db, org_id, po.po_id, approver)
    assert po.status == POStatus.APPROVED
    assert po.approved_by_user_id == approver
    assert po.approved_at is not None


def test_the_creator_cannot_approve_their_own_purchase_order(
    db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID
) -> None:
    """The Segregation of Duties control, exercised end to end.

    This is the control the generic workflow `setattr` could skip: a rule that
    wrote `status = APPROVED` never entered `approve_po`, so this check never
    ran and `approved_by_user_id` stayed NULL. The gate asserted in
    `tests/architecture/test_po_status_single_owner.py` is what closes that
    path; this is the control it protects.
    """
    from app.models.finance.ap.purchase_order import POStatus
    from app.services.finance.ap.purchase_order import PurchaseOrderService

    po = _make_po(db, org_id, supplier, user_id, "PO-ST-3")
    PurchaseOrderService.submit_for_approval(db, org_id, po.po_id, user_id)

    with pytest.raises(HTTPException) as exc:
        PurchaseOrderService.approve_po(db, org_id, po.po_id, user_id)

    assert exc.value.status_code == 400
    assert "Segregation of Duties" in str(exc.value.detail)
    assert po.status == POStatus.PENDING_APPROVAL
    assert po.approved_by_user_id is None


def test_an_illegal_transition_is_refused_with_its_verb(
    db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID
) -> None:
    """Refusal wording is unchanged by the move into the owner."""
    from app.models.finance.ap.purchase_order import POStatus
    from app.services.finance.ap.purchase_order import PurchaseOrderService

    po = _make_po(db, org_id, supplier, user_id, "PO-ST-4", status=POStatus.APPROVED)

    with pytest.raises(HTTPException) as exc:
        PurchaseOrderService.submit_for_approval(db, org_id, po.po_id, user_id)
    assert "Cannot submit PO in APPROVED status" in str(exc.value.detail)

    with pytest.raises(HTTPException) as exc:
        PurchaseOrderService.approve_po(db, org_id, po.po_id, uuid.uuid4())
    assert "Cannot approve PO in APPROVED status" in str(exc.value.detail)

    assert po.status == POStatus.APPROVED


def test_the_cancel_interlock_belongs_to_the_transition(
    db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID
) -> None:
    """A PO with received goods cannot be cancelled, whichever path asks.

    The guard used to sit inline in `cancel_po`. It now sits on the CANCEL
    transition, so a second cancel path cannot be written without it — which is
    exactly how the interlock came to exist in only one of the two places that
    could cancel a PO last time.
    """
    from app.models.finance.ap.purchase_order import POStatus
    from app.services.finance.ap import purchase_order_status
    from app.services.finance.ap.purchase_order import PurchaseOrderService

    po = _make_po(db, org_id, supplier, user_id, "PO-ST-5", status=POStatus.APPROVED)
    _make_line(db, po, ordered="10", received="4")

    with pytest.raises(HTTPException) as exc:
        PurchaseOrderService.cancel_po(db, org_id, po.po_id)
    assert "received goods" in str(exc.value.detail).lower()
    assert po.status == POStatus.APPROVED

    # Same refusal when the transition is asked for directly.
    with pytest.raises(HTTPException):
        purchase_order_status.apply_transition(
            db, po, purchase_order_status.POTransition.CANCEL
        )
    assert po.status == POStatus.APPROVED


def test_a_clean_purchase_order_cancels(
    db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID
) -> None:
    from app.models.finance.ap.purchase_order import POStatus
    from app.services.finance.ap.purchase_order import PurchaseOrderService

    po = _make_po(db, org_id, supplier, user_id, "PO-ST-6", status=POStatus.APPROVED)
    _make_line(db, po, ordered="10", received="0")

    PurchaseOrderService.cancel_po(db, org_id, po.po_id)
    assert po.status == POStatus.CANCELLED


def test_reversing_the_only_receipt_unbricks_the_purchase_order(
    db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID
) -> None:
    """The regression that stranded purchase orders at RECEIVED.

    The replaced code only moved status forward: with nothing received it left
    the status alone. So a PO whose only receipt was rejected kept RECEIVED,
    and RECEIVED is neither cancellable nor receivable — the purchase order was
    stuck with no route out. This walks the whole way back and proves the PO is
    usable again on the other side.
    """
    from app.models.finance.ap.purchase_order import POStatus
    from app.services.finance.ap import purchase_order_status
    from app.services.finance.ap.purchase_order import PurchaseOrderService

    po = _make_po(db, org_id, supplier, user_id, "PO-ST-7", status=POStatus.APPROVED)
    line = _make_line(db, po, ordered="10", received="10")

    purchase_order_status.record_receipt_progress(db, po)
    assert po.status == POStatus.RECEIVED

    # The receipt is rejected and its quantities reversed.
    line.quantity_received = Decimal("0")
    db.flush()
    purchase_order_status.record_receipt_progress(db, po)

    assert po.status == POStatus.APPROVED
    assert purchase_order_status.can_receive_against(po.status)

    # And the PO is genuinely usable again, not merely relabelled.
    PurchaseOrderService.cancel_po(db, org_id, po.po_id)
    assert po.status == POStatus.CANCELLED


def test_receipt_progress_tracks_partial_and_full(
    db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID
) -> None:
    from app.models.finance.ap.purchase_order import POStatus
    from app.services.finance.ap import purchase_order_status

    po = _make_po(db, org_id, supplier, user_id, "PO-ST-8", status=POStatus.APPROVED)
    line = _make_line(db, po, ordered="10", received="4")

    purchase_order_status.record_receipt_progress(db, po)
    assert po.status == POStatus.PARTIALLY_RECEIVED

    line.quantity_received = Decimal("10")
    db.flush()
    purchase_order_status.record_receipt_progress(db, po)
    assert po.status == POStatus.RECEIVED


@pytest.mark.parametrize("terminal", ["CANCELLED", "CLOSED"])
def test_receipt_progress_leaves_a_terminal_purchase_order_alone(
    db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID, terminal: str
) -> None:
    """A receipt-driven recalculation must not resurrect a closed lifecycle.

    The replaced assignment looked only at quantities, never at where the PO
    was, so it would overwrite CANCELLED or CLOSED without hesitating.
    """
    from app.models.finance.ap.purchase_order import POStatus
    from app.services.finance.ap import purchase_order_status

    state = POStatus[terminal]
    po = _make_po(db, org_id, supplier, user_id, f"PO-ST-9-{terminal}", status=state)
    _make_line(db, po, ordered="10", received="10")

    assert purchase_order_status.record_receipt_progress(db, po) is None
    assert po.status == state


def test_goods_receipt_refuses_a_purchase_order_that_is_not_receivable(
    db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID
) -> None:
    """`can_receive_against` is the one definition of the receivable set."""
    from app.models.finance.ap.purchase_order import POStatus
    from app.services.finance.ap import purchase_order_status

    receivable = {POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED}
    for state in POStatus:
        assert purchase_order_status.can_receive_against(state) is (
            state in receivable
        ), state
