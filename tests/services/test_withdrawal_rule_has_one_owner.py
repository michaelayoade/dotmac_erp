"""Whether approval can be withdrawn is decided in exactly one place.

The withdrawal feature shipped with the rule written TWICE: `withdraw_approval`
enforced four conditions, and `claim_detail_response` re-derived the same four
to decide whether to show the button. Same status check, same approver check,
the same five-field financial-activity tuple, the same PaymentIntent query.

Two copies of an authorization decision fail in both directions and neither is
loud: the button appears for an action that is then refused, or it is hidden
for one that was allowed. Nobody files the second kind — the user concludes
they lack permission and stops.

`ExpenseService.withdrawal_refusal` is now the one owner, and these hold it
there.
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "app" / "services" / "expense" / "web_claims.py"
SERVICE = REPO_ROOT / "app" / "services" / "expense" / "service_claims.py"

# The tell-tales of the duplicated rule, each naming the field that gave it
# away. If any reappears in the web layer, the copy is back.
_OWNED_BY_THE_SERVICE = (
    "reimbursement_journal_id",  # the financial-activity tuple
    "PaymentIntentStatus.PROCESSING",  # the in-flight payment query
)


def test_the_web_layer_does_not_re_derive_the_rule() -> None:
    source = WEB.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    leaked = [marker for marker in _OWNED_BY_THE_SERVICE if marker in executable]
    assert leaked == [], (
        "The withdrawal eligibility rule is being re-derived in web_claims.py. "
        "Ask `ExpenseService.withdrawal_refusal` instead — it is the owner, and "
        "a second copy is how a button gets shown for an action that is then "
        f"refused. Found: {leaked}"
    )


def test_the_web_layer_asks_the_owner() -> None:
    """Specificity: the test above passes trivially if the page simply stopped
    offering withdrawal. It must still ask."""
    assert "withdrawal_refusal(" in WEB.read_text(encoding="utf-8")


def test_the_service_still_enforces_it() -> None:
    """And the owner must be consulted on the WRITE path too, not just for
    display — a rule only the UI checks is not enforced at all."""
    source = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    enforcing = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "withdraw_approval"
    ]
    assert enforcing, "withdraw_approval is gone"
    calls = {
        getattr(inner.func, "attr", None)
        for inner in ast.walk(enforcing[0])
        if isinstance(inner, ast.Call)
    }
    assert "withdrawal_refusal" in calls, (
        "withdraw_approval no longer consults the shared rule — the display "
        "and the enforcement can now disagree."
    )


def test_no_nil_uuid_sentinel_crosses_the_boundary() -> None:
    """`UUID(int=0)` was passed as the approver when an admin had no employee
    record. A magic value only means something if the receiver recognises it,
    and the service compared it against a real `approver_id` — so it read as a
    genuine approver who simply never matched. `None` says the same thing and
    is handled explicitly."""
    assert "UUID(int=0)" not in WEB.read_text(encoding="utf-8")
