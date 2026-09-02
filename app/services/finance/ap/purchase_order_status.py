"""Purchase-order status — one owner for the lifecycle, one place that assigns it.

`PurchaseOrder.status` had three independent writers, and the third one could
undo the other two.

* `PurchaseOrderService` performed the lifecycle transitions — DRAFT ->
  PENDING_APPROVAL -> APPROVED, plus CANCELLED and CLOSED — each behind its own
  inline guard.
* `GoodsReceiptService._update_po_status` independently assigned
  PARTIALLY_RECEIVED / RECEIVED from the line quantities, bypassing
  `PurchaseOrderService` entirely.
* `WorkflowActionExecutor._action_update_field` could `setattr(po, "status", ...)`
  to ANY value, because `PURCHASE_ORDER` is in the automation entity registry and
  the action is generic.

The third one is the serious one, and it is **reachable from the admin UI with no
code change**: `ActionType.UPDATE_FIELD` is offered as "Update Field" and
`TriggerEvent.ON_STATUS_CHANGE` / `ON_APPROVAL` as "When Status Changes" /
"When Approved". `submit_for_approval` and `approve_po` fire exactly those events
for exactly this entity type — so a rule can rewrite the status on the very event
a guarded transition fires. A rule that sets `status = APPROVED` on
ON_STATUS_CHANGE takes a PO from DRAFT to APPROVED without ever entering
`approve_po`, which means `approved_by_user_id` and `approved_at` stay NULL and
**the Segregation of Duties check never runs**. That check is the whole control:
it is what stops the person who raised a purchase order from approving it.

So the status column now has one owner: this module. It holds the transition
table, the guards belong to the transitions rather than to whichever caller
happened to be written first, and `_assign` below is the ONLY place in the
codebase that assigns `PurchaseOrder.status`.
`tests/architecture/test_po_status_single_owner.py` fails the build if a second
assignment appears anywhere under `app/`.

## Two authorities, one column, declared rather than implied

The eventual ownership split is that procurement owns the PO lifecycle while ERP
AP stays authoritative for receipts, invoices and payments. That split is
expressed here as `StatusAuthority` on each transition, so it is already visible
in code:

* `PROCUREMENT_LIFECYCLE` — SUBMIT, APPROVE, CANCEL, CLOSE. Decisions about the
  commitment itself.
* `AP_RECEIPT_CONSEQUENCE` — RECORD_RECEIPT_PROGRESS. Not a decision at all: a
  consequence of goods receipt rows, which ERP AP owns.

**Placement note.** This module lives under `app/services/finance/ap/` rather
than `app/services/procurement/` on purpose. The dependency direction in this
codebase is strictly procurement -> finance; `app/services/finance/**` imports
nothing from `app/services/procurement/**` today. Since `goods_receipt.py` must
call the owner, putting the owner under `procurement/` would invert that edge,
and there is no import-linter here that would catch the inversion later. The
authority is named on every transition regardless of directory; moving the file
is a follow-on to moving the `PurchaseOrder` row itself, which is separately
blocked.

## What is deliberately NOT here

`POStatus.SUPERSEDED` has no writer anywhere in `app/`, and neither do the
amendment columns beside it. This module does not invent a transition for it —
an unreachable state with a fabricated transition reads as a supported feature.
It is listed in `_NO_TRANSITION_REACHES` so the omission is a recorded decision
rather than an oversight.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException

from app.models.finance.ap.purchase_order import POStatus, PurchaseOrder

logger = logging.getLogger(__name__)


class StatusAuthority(str, enum.Enum):
    """Who owns the decision a transition represents."""

    PROCUREMENT_LIFECYCLE = "procurement-lifecycle"
    AP_RECEIPT_CONSEQUENCE = "ap-receipt-consequence"


class POTransition(str, enum.Enum):
    """The complete set of ways a purchase order's status may change."""

    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    CANCEL = "CANCEL"
    CLOSE = "CLOSE"
    RECORD_RECEIPT_PROGRESS = "RECORD_RECEIPT_PROGRESS"


# States a goods receipt may be booked against. One definition: `goods_receipt.py`
# and `web/goods_receipt_web.py` each held their own copy of this pair.
RECEIVABLE_STATES: frozenset[POStatus] = frozenset(
    {POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED}
)

# States where receipt progress is meaningful. RECORD_RECEIPT_PROGRESS applies
# only within this set, so a reversal cannot resurrect a CANCELLED or CLOSED PO
# and a receipt cannot drag a DRAFT one forward.
_RECEIPT_TRACKING_STATES: frozenset[POStatus] = frozenset(
    {POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED, POStatus.RECEIVED}
)

# Recorded, not forgotten: no transition in this table reaches SUPERSEDED.
# Nothing in `app/` has ever written it, and the amendment columns it belongs to
# (`is_amendment`, `original_po_id`, `amendment_version`, `variation_id`) are
# DDL with no code either side. Inventing a transition would make a dead feature
# look supported. The architecture test asserts this set stays honest.
_NO_TRANSITION_REACHES: frozenset[POStatus] = frozenset({POStatus.SUPERSEDED})


@dataclass(frozen=True)
class _Spec:
    verb: str
    authority: StatusAuthority
    from_states: frozenset[POStatus]
    # `None` means the target is computed from the receipt rows rather than fixed.
    to_state: POStatus | None


_TRANSITIONS: dict[POTransition, _Spec] = {
    POTransition.SUBMIT: _Spec(
        verb="submit",
        authority=StatusAuthority.PROCUREMENT_LIFECYCLE,
        from_states=frozenset({POStatus.DRAFT}),
        to_state=POStatus.PENDING_APPROVAL,
    ),
    POTransition.APPROVE: _Spec(
        verb="approve",
        authority=StatusAuthority.PROCUREMENT_LIFECYCLE,
        from_states=frozenset({POStatus.PENDING_APPROVAL}),
        to_state=POStatus.APPROVED,
    ),
    # Faithful to the behaviour being replaced: cancellation was refused only
    # from RECEIVED and CLOSED, so every other state is legal here — including
    # cancelling an already-CANCELLED PO. That is preserved rather than tightened,
    # because a cutover that also changes behaviour cannot be reviewed as a
    # cutover. See the report note.
    POTransition.CANCEL: _Spec(
        verb="cancel",
        authority=StatusAuthority.PROCUREMENT_LIFECYCLE,
        from_states=frozenset(POStatus) - {POStatus.RECEIVED, POStatus.CLOSED},
        to_state=POStatus.CANCELLED,
    ),
    POTransition.CLOSE: _Spec(
        verb="close",
        authority=StatusAuthority.PROCUREMENT_LIFECYCLE,
        from_states=frozenset(
            {POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED, POStatus.RECEIVED}
        ),
        to_state=POStatus.CLOSED,
    ),
    POTransition.RECORD_RECEIPT_PROGRESS: _Spec(
        verb="record receipt against",
        authority=StatusAuthority.AP_RECEIPT_CONSEQUENCE,
        from_states=_RECEIPT_TRACKING_STATES,
        to_state=None,
    ),
}


def can_receive_against(status: POStatus) -> bool:
    """Whether a goods receipt may be booked against a PO in this status."""
    return status in RECEIVABLE_STATES


def legal_from_states(transition: POTransition) -> frozenset[POStatus]:
    """The states a transition may be applied from."""
    return _TRANSITIONS[transition].from_states


def authority_for(transition: POTransition) -> StatusAuthority:
    """Which authority owns the decision this transition represents."""
    return _TRANSITIONS[transition].authority


def _assign(po: PurchaseOrder, new_status: POStatus) -> None:
    """The ONE place `PurchaseOrder.status` is assigned.

    Everything else routes through `apply_transition`. Keeping the assignment in
    a single named function is what makes the architecture guard a simple,
    unambiguous rule rather than a list of blessed call sites.
    """
    po.status = new_status


def _refuse(spec: _Spec, current: POStatus) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail=f"Cannot {spec.verb} PO in {current.value} status",
    )


def apply_transition(
    db,
    po: PurchaseOrder,
    transition: POTransition,
) -> POStatus:
    """Apply a status transition, or refuse it.

    Returns the resulting status. Refusals raise `HTTPException(400)` carrying the
    same wording the inline guards used, so callers and their tests are unchanged
    by the move.

    The guards live here rather than in the callers on purpose: a precondition
    attached to the transition cannot be forgotten by the next caller, which is
    exactly how the cancel interlock came to exist in only one of the two places
    that could cancel a PO.
    """
    spec = _TRANSITIONS[transition]
    current = po.status

    if current not in spec.from_states:
        raise _refuse(spec, current)

    if transition is POTransition.CANCEL:
        # Derived, never read off the row: `amount_received` is not a column.
        # This interlock belongs to the transition, not to `cancel_po`.
        from app.services.finance.ap.purchase_order_amounts import received_for

        if received_for(db, po.organization_id, po.po_id) > 0:
            raise HTTPException(
                status_code=400, detail="Cannot cancel PO with received goods"
            )

    if transition is POTransition.RECORD_RECEIPT_PROGRESS:
        target = _receipt_progress_target(po)
    elif spec.to_state is None:
        # Unreachable through the table above, and deliberately a raise rather
        # than an `assert`: asserts are stripped under `python -O`, so an
        # invariant guarded by one is not guarded in a production image.
        raise RuntimeError(f"Transition {transition.value} declares no target state")
    else:
        target = spec.to_state

    if target != current:
        _assign(po, target)
        logger.info(
            "PO %s status %s -> %s (%s, %s)",
            po.po_id,
            current.value,
            target.value,
            transition.value,
            spec.authority.value,
        )

    return target


def _receipt_progress_target(po: PurchaseOrder) -> POStatus:
    """Derive the status implied by the PO's line receipt quantities.

    `purchase_order_line.quantity_received` is written by the goods-receipt path
    and is the authoritative input; this reads it and nothing else.

    **This can move backwards, and that is a fix.** The code being replaced only
    ever moved status forward: with nothing received it left the status alone.
    So rejecting a PO's only receipt reversed the line quantities to zero and
    left the PO at RECEIVED forever — which then refused a cancel (RECEIVED is
    not cancellable) and refused any further receipt (RECEIVED is not
    receivable). Rejecting one receipt bricked the purchase order. Both call
    sites already say "Recalculate PO status"; now it actually recalculates.
    """
    total_ordered = Decimal("0")
    total_received = Decimal("0")

    for line in po.lines:
        total_ordered += line.quantity_ordered * line.unit_price
        total_received += line.quantity_received * line.unit_price

    if total_received <= 0:
        return POStatus.APPROVED
    if total_received >= total_ordered:
        return POStatus.RECEIVED
    return POStatus.PARTIALLY_RECEIVED


def record_receipt_progress(db, po: PurchaseOrder) -> POStatus | None:
    """Re-derive the PO's status from its receipt quantities, if applicable.

    Returns the resulting status, or `None` when the PO is in a state where
    receipt progress is not meaningful (DRAFT, PENDING_APPROVAL, CANCELLED,
    CLOSED). Those are silently left alone rather than refused: this is called
    from receipt bookkeeping that has already validated what it needs, and a
    CLOSED PO having its lines touched is not an error the receipt path can act
    on.
    """
    if po.status not in _RECEIPT_TRACKING_STATES:
        return None
    return apply_transition(db, po, POTransition.RECORD_RECEIPT_PROGRESS)
