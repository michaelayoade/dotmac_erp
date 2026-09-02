"""One owner for `PurchaseOrder.status`.

The column had three independent writers, and the third could undo the other two.

* `PurchaseOrderService` performed DRAFT -> PENDING_APPROVAL -> APPROVED, plus
  CANCELLED and CLOSED, each behind its own inline guard.
* `GoodsReceiptService._update_po_status` assigned PARTIALLY_RECEIVED / RECEIVED
  straight from the line quantities, bypassing `PurchaseOrderService` and every
  guard it holds.
* `WorkflowActionExecutor._action_update_field` could `setattr(po, "status", ...)`
  to any value at all.

The third is the one that matters, and it needed no code to exploit:
`ActionType.UPDATE_FIELD` is offered in the admin UI as "Update Field" and
`TriggerEvent.ON_STATUS_CHANGE` / `ON_APPROVAL` as "When Status Changes" / "When
Approved", while `submit_for_approval` and `approve_po` fire precisely those
events for precisely this entity type. An operator-built rule could therefore
move a PO to APPROVED without ever entering `approve_po` — leaving
`approved_by_user_id` NULL and **never running the Segregation of Duties check**
that stops the raiser of a purchase order approving their own.

`app.services.finance.ap.purchase_order_status` is now the owner, and its
`_assign` is the only place the column is written.

## Why the detector is shaped the way it is

There is no type information in an AST walk, so "is this receiver a
PurchaseOrder?" cannot be answered directly — and `ap/goods_receipt.py` alone
contains five legitimate `receipt.status = ReceiptStatus...` assignments for a
DIFFERENT entity's lifecycle. A rule that flagged every `.status =` would fire on
those and be switched off within the week.

So a write is flagged when EITHER half of it identifies a purchase order:

* the value is a `POStatus` member — you cannot assign one of those to anything
  else and mean it; or
* the receiver is named `po` or `purchase_order` — the shape a variable-valued
  write takes.

That is a heuristic, and stating its limit is part of the contract: a write
through a differently-named variable carrying a non-`POStatus` value is NOT
caught here. That path is the generic `setattr` one, and it is closed by the
protected-field gate asserted below rather than by this scan. The two together
cover the region; neither is claimed to cover it alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP = REPO_ROOT / "app"

OWNER_REL = "app/services/finance/ap/purchase_order_status.py"

_PO_RECEIVER_NAMES = {"po", "purchase_order"}


def _python_files() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _is_po_status_value(value: ast.expr) -> bool:
    """`POStatus.SOMETHING` on the right-hand side."""
    return (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "POStatus"
    )


def _is_po_receiver(target: ast.Attribute) -> bool:
    """`po.status` / `purchase_order.status`, including `self.po.status`."""
    recv = target.value
    if isinstance(recv, ast.Name):
        return recv.id in _PO_RECEIVER_NAMES
    if isinstance(recv, ast.Attribute):
        return recv.attr in _PO_RECEIVER_NAMES
    return False


def _status_writes(source: str) -> list[int]:
    """Line numbers where a purchase order's status is assigned."""
    tree = ast.parse(source)
    hits: list[int] = []

    def record(target: ast.expr, value: ast.expr | None, lineno: int) -> None:
        if isinstance(target, ast.Attribute) and target.attr == "status":
            by_value = value is not None and _is_po_status_value(value)
            if by_value or _is_po_receiver(target):
                hits.append(lineno)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                record(elt, None, lineno)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                record(target, node.value, node.lineno)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            record(node.target, node.value, node.lineno)

    return hits


def test_only_the_owner_assigns_purchase_order_status() -> None:
    offenders: dict[str, list[int]] = {}
    for path in _python_files():
        rel = _rel(path)
        if rel == OWNER_REL:
            continue
        hits = _status_writes(path.read_text(encoding="utf-8"))
        if hits:
            offenders[rel] = hits

    assert not offenders, (
        "A purchase order's status is being assigned outside its owner:\n"
        + "\n".join(f"  {rel}: lines {hits}" for rel, hits in sorted(offenders.items()))
        + f"\n\nThe status lifecycle has one owner, {OWNER_REL}. Add or use a "
        "transition there — the guards belong to the transition, so a caller "
        "that assigns the column directly is a caller that skipped them."
    )


def test_the_owner_assigns_in_exactly_one_place() -> None:
    """Inside the owner, the write is funnelled through `_assign`.

    A transition table whose branches each assign the column is still one file
    with several writers, and the next branch added would not have to route
    through anything.
    """
    source = (REPO_ROOT / OWNER_REL).read_text(encoding="utf-8")
    hits = _status_writes(source)
    assert len(hits) == 1, (
        f"{OWNER_REL} assigns the status at lines {hits}; it must do so exactly "
        "once, inside `_assign`."
    )

    tree = ast.parse(source)
    assigning_functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and _status_writes(ast.unparse(node))
    ]
    assert assigning_functions == ["_assign"], (
        f"Functions assigning status in {OWNER_REL}: {assigning_functions}. "
        "Only `_assign` may."
    )


def test_the_automation_engine_may_not_write_status() -> None:
    """The generic `setattr` action is the path the AST scan cannot see."""
    from app.services.finance.automation.entity_registry import (
        field_authority_owner,
        protected_fields,
    )

    assert "status" in protected_fields("PURCHASE_ORDER"), (
        "'status' is not in the PURCHASE_ORDER protected-field map. "
        "`_action_update_field` setattrs any named field on any registered "
        "entity, so an ungated `status` lets a workflow rule move a PO to "
        "APPROVED without entering `approve_po` — skipping the Segregation of "
        "Duties check entirely."
    )
    owner = field_authority_owner("PURCHASE_ORDER", "status")
    assert owner and "purchase_order_status" in owner, (
        "The protected-field entry for 'status' must name its owner, so the "
        "refusal tells an operator where the transition belongs."
    )


def test_the_transition_table_covers_every_reachable_status() -> None:
    """Every status is reachable by a transition, or recorded as unreachable.

    Without this, adding a `POStatus` member and no transition leaves a state the
    owner cannot produce — and the next person writes the column directly to get
    to it, which is how the second writer appeared last time.
    """
    from app.models.finance.ap.purchase_order import POStatus
    from app.services.finance.ap import purchase_order_status as owner

    fixed_targets = {
        spec.to_state
        for spec in owner._TRANSITIONS.values()
        if spec.to_state is not None
    }
    # The receipt-progress transition computes its target from the line rows.
    computed_targets = {
        POStatus.APPROVED,
        POStatus.PARTIALLY_RECEIVED,
        POStatus.RECEIVED,
    }
    # DRAFT is the construction default, not a transition target.
    creation_only = {POStatus.DRAFT}

    reachable = fixed_targets | computed_targets | creation_only
    unaccounted = sorted(
        s.value for s in set(POStatus) - reachable - owner._NO_TRANSITION_REACHES
    )
    assert not unaccounted, (
        f"POStatus {unaccounted} cannot be reached by any transition and is not "
        f"recorded in `_NO_TRANSITION_REACHES` in {OWNER_REL}. Give it a "
        "transition or record why it has none."
    )

    overlap = sorted(s.value for s in reachable & owner._NO_TRANSITION_REACHES)
    assert not overlap, (
        f"{overlap} is both reachable and recorded as unreachable in {OWNER_REL}."
    )


def test_the_unreachable_states_really_have_no_writer() -> None:
    """`_NO_TRANSITION_REACHES` is a claim about the codebase; check it.

    An exemption list that drifts out of date is worse than none: it asserts a
    property that stopped holding.
    """
    from app.services.finance.ap import purchase_order_status as owner

    for status in owner._NO_TRANSITION_REACHES:
        token = f"POStatus.{status.name}"
        writers = [
            _rel(p)
            for p in _python_files()
            if token in p.read_text(encoding="utf-8")
            and _rel(p) not in {OWNER_REL, "app/models/finance/ap/purchase_order.py"}
        ]
        assert not writers, (
            f"{token} is recorded in `_NO_TRANSITION_REACHES` as having no "
            f"writer, but {writers} reference it. Either it gained a real "
            "transition — give it one — or the record is stale."
        )


def test_the_receivable_state_set_has_one_definition() -> None:
    """`[APPROVED, PARTIALLY_RECEIVED]` was copied into two files."""
    offenders = []
    for path in _python_files():
        rel = _rel(path)
        if rel == OWNER_REL:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "POStatus.APPROVED" in line and "POStatus.PARTIALLY_RECEIVED" in line:
                offenders.append(rel)
                break

    assert not offenders, (
        f"The receivable-state pair is written out in {sorted(set(offenders))}. "
        f"It has one definition, `RECEIVABLE_STATES` in {OWNER_REL} — reachable "
        "through `can_receive_against`."
    )


# ---------------------------------------------------------------------------
# Sensitivity. A scan that finds nothing passes whether or not it can find
# anything, and a gate that refuses everything passes the gate assertions for
# the wrong reason. Both directions are proved.
# ---------------------------------------------------------------------------


def test_the_status_write_detector_still_bites() -> None:
    """It flags every shape a returning writer would take."""
    offender = "\n".join(
        (
            "po.status = POStatus.APPROVED",  # both halves
            "purchase_order.status = something",  # receiver only
            "invoice.status = POStatus.RECEIVED",  # value only
            "self.po.status = POStatus.CLOSED",  # attribute receiver
        )
    )
    hits = _status_writes(offender)
    assert hits == [1, 2, 3, 4], (
        f"The detector found {hits} in a file with four deliberate writers. It "
        "has stopped detecting, which means the green result above means nothing."
    )


def test_the_status_write_detector_leaves_other_lifecycles_alone() -> None:
    """The other direction, using shapes that really exist in this codebase.

    `ap/goods_receipt.py` holds five `receipt.status = ReceiptStatus...` writes
    for the goods receipt's own lifecycle, and `sync/sub/procurement.py` writes
    `mr.status`. A detector that flagged those would be turned off, not fixed.
    """
    innocent = "\n".join(
        (
            "receipt.status = ReceiptStatus.ACCEPTED",
            "mr.status = target_status",
            "request.status = target_status",
            "invoice.status = SupplierInvoiceStatus.POSTED",
            "status = POStatus.APPROVED",  # a local, not a record
        )
    )
    assert _status_writes(innocent) == [], (
        "The detector flagged a neighbouring entity's own status write. Scoped "
        "this broadly it would fire on unrelated lifecycles and be disabled."
    )


@pytest.mark.parametrize(
    "field",
    ["status", "amount_received", "amount_invoiced"],
)
def test_the_protected_field_gate_covers_the_po_facts(field: str) -> None:
    from app.services.finance.automation.entity_registry import field_authority_owner

    assert field_authority_owner("PURCHASE_ORDER", field) is not None


def test_the_protected_field_gate_still_lets_ordinary_fields_through() -> None:
    from app.services.finance.automation.entity_registry import field_authority_owner

    assert field_authority_owner("PURCHASE_ORDER", "terms_and_conditions") is None
    assert field_authority_owner("BILL", "status") is None
