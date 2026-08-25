"""Refund is a decision with an owner, not a side effect eleven places take.

Refund had **no owner at all**. There was no `Refund` model, no `refund` table
and no `/refund` route; `docs/SOT_RELATIONSHIP_MAP.md` named no refund,
credit-note or reversal domain, and `grep -n "refund\\|reversal\\|credit"` over
`app/services/sot_relationships.py` returned nothing. What existed instead was
a refund-shaped side effect stamped onto five unrelated aggregates by eleven
writers, none of which could see the others:

* `payments/payment_service.py::process_transfer_reversal` and the webhook
  handler that delegates to it — which handled `transfer.reversed` but **not**
  `charge.refund` or `refund.processed`, so a refund Paystack had actually paid
  out matched no branch and was logged as "Unhandled event type" while the
  webhook row was marked PROCESSED;
* the AR credit-note lifecycle across `ar/invoice.py`, `ar_posting_saga.py`,
  `ar_inventory_integration.py` and a second orchestration surface in
  `ar/web/credit_note_web.py`;
* `ar/customer_payment.py::void_payment` and `::mark_bounced`, two
  near-identical bodies;
* `gl/reversal.py::ReversalService.create_reversal`, which owns the GL
  *mechanism* with fifteen direct callers and no notion of *why*;
* `dotmac_sub/sync/_payments.py`, assigning `PaymentStatus.REVERSED` because
  *Sub* refunded, and `dotmac_sub/sync/_credit_notes.py`, stamping
  `InvoiceStatus.VOID`;
* and two byte-identical, caller-less, test-less `record_refund` twins writing
  `CashAdvance.amount_refunded`.

ADR-0008 named two owners at one boundary — customer money in
(`CustomerPaymentService.refund_payment`) versus company money out
(`PaymentService`) — and this test is what stops the eleven regrowing.

## What this detects, precisely

Three acts, across `app/`, `scripts/` and `tools/`:

1. **Stamping a refunded status** — assigning `PaymentStatus.REVERSED` or
   `PaymentIntentStatus.REVERSED` to an attribute named `status`.
2. **Writing `amount_refunded`** — assignment or augmented assignment to that
   attribute. This is the cash-advance defect: two uncalled writers of one
   column, each also deciding `FULLY_SETTLED`.
3. **An unmarked reversal** — a `ReversalService.create_reversal` call site
   with no explicit `reason=`, or (in a module that decides a refund) no
   explicit `idempotency_key=`. A reversal nobody labelled is a refund
   indistinguishable in the ledger from an FX revaluation or a data-health
   correction.

## What it deliberately does NOT detect

Assignment of a *name* rather than a literal member. The AR owner
parameterises its terminal status on purpose — `refund_payment` is one
behaviour with three reasons (refund / void / bounce), so it writes
`payment.status = outcome_status`, a Name. A static scan cannot follow that,
and pretending otherwise would be worse than saying so here.

That is why `test_both_owners_remain_visible_to_the_detector` exists in the
shape it does: the AR owner's visibility is proved by the enum still being
*mentioned* in it, and the payments owner's by its literal stamp still being
*detected*. A renamed enum, a moved module or a mis-globbed root then fails
the build rather than silently passing over nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: enum name -> the member that means "this money came back".
REFUND_STATUS_MEMBER = {
    "PaymentStatus": "REVERSED",
    "PaymentIntentStatus": "REVERSED",
}

REFUND_AMOUNT_COLUMN = "amount_refunded"
STATUS_COLUMN = "status"

#: The two owners ADR-0008 names. Customer money in, company money out.
OWNERS = (
    "app/services/finance/ar/customer_payment.py",
    "app/services/finance/payments/payment_service.py",
)

#: Modules that DECIDE a refund and therefore reach the ledger on a path a
#: retry can re-enter. Every reversal they create must be replay-safe and
#: labelled, which an explicit `idempotency_key` is. Scoped to these rather
#: than to all eighteen `create_reversal` call sites on purpose: payroll, the
#: AP/AR sagas, FX revaluation and the data-health task have their own replay
#: semantics, and ADR-0008 declines to settle them inside a refund slice.
REFUND_DECIDERS = (
    "app/services/finance/ar/customer_payment.py",
    "app/services/dotmac_sub/sync/_payments.py",
    "app/services/dotmac_sub/sync/_invoices.py",
)

SCANNED_ROOTS = ("app", "scripts", "tools")
SKIPPED_PARTS = frozenset({"__pycache__", "archive"})


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _is_refund_status(node: ast.expr) -> bool:
    """True for `PaymentStatus.REVERSED` / `PaymentIntentStatus.REVERSED`."""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in REFUND_STATUS_MEMBER
        and node.attr == REFUND_STATUS_MEMBER[node.value.id]
    )


def stamps_refund_status(path: Path) -> list[int]:
    """Lines where this file decides that money came back."""
    tree = _parse(path)
    if tree is None:
        return []
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not _is_refund_status(node.value):
            continue
        if any(
            isinstance(target, ast.Attribute) and target.attr == STATUS_COLUMN
            for target in node.targets
        ):
            hits.append(node.lineno)
    return sorted(hits)


def mentions_refund_status(path: Path) -> list[int]:
    """Lines where a refunded-status member appears at all.

    Broader than :func:`stamps_refund_status` and used only for the
    owner-visibility half of the sensitivity proof — see this module's
    docstring for why the AR owner needs the broader form.
    """
    tree = _parse(path)
    if tree is None:
        return []
    return sorted(node.lineno for node in ast.walk(tree) if _is_refund_status(node))


def writes_amount_refunded(path: Path) -> list[int]:
    """Lines where this file moves a cash advance's refunded total."""
    tree = _parse(path)
    if tree is None:
        return []
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign):
            targets: list[ast.expr] = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        if any(
            isinstance(target, ast.Attribute) and target.attr == REFUND_AMOUNT_COLUMN
            for target in targets
        ):
            hits.append(node.lineno)
    return sorted(hits)


def reversal_call_sites(path: Path) -> list[tuple[int, frozenset[str]]]:
    """`(lineno, keyword names)` for every `...create_reversal(...)` call."""
    tree = _parse(path)
    if tree is None:
        return []
    sites: list[tuple[int, frozenset[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "create_reversal":
            continue
        sites.append((node.lineno, frozenset(kw.arg for kw in node.keywords if kw.arg)))
    return sorted(sites)


def scanned_files():
    for root in SCANNED_ROOTS:
        directory = REPO_ROOT / root
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            if SKIPPED_PARTS & set(path.parts):
                continue
            yield path


def test_only_the_two_owners_stamp_a_refunded_status() -> None:
    offenders: list[str] = []
    for path in scanned_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in OWNERS:
            continue
        offenders += [f"{relative}:{line}" for line in stamps_refund_status(path)]

    assert offenders == [], (
        "these sites decide that money came back:\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nA customer refund is CustomerPaymentService.refund_payment's"
        "\n(ADR-0008); an outbound payout reversal is PaymentService's."
        "\nA sync adapter, webhook or web handler that assigns a terminal"
        "\nstatus has taken the decision instead of requesting it."
    )


def test_nothing_outside_the_owners_writes_amount_refunded() -> None:
    """The cash-advance defect: two uncalled, untested, byte-identical writers.

    Both `record_refund` twins were deleted by ADR-0008 §7, so the honest
    expectation today is that NOBODY writes this column. The column, its
    migration and the two report queries that read it stay — deleting dead
    service code is not a destructive migration.
    """
    offenders: list[str] = []
    for path in scanned_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in OWNERS:
            continue
        offenders += [f"{relative}:{line}" for line in writes_amount_refunded(path)]

    assert offenders == [], (
        "these sites move a cash advance's refunded total:\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nTwo uncalled writers of this column is what ADR-0008 removed."
        "\nA cash-advance refund is company money coming back IN; it has no"
        "\nowner yet, and wiring it to the customer owner would create the"
        "\nsecond writer that ADR removed. Design the decision first."
    )


def test_every_reversal_states_why() -> None:
    """`ReversalService` owns HOW a journal is reversed and is never told WHY
    by anything but this string. A call site that omits it puts a reversal in
    the ledger that nothing can classify."""
    unmarked: list[str] = []
    for path in scanned_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        for lineno, keywords in reversal_call_sites(path):
            if "reason" not in keywords:
                unmarked.append(f"{relative}:{lineno}")

    assert unmarked == [], (
        "these reversals do not say why they exist:\n  "
        + "\n  ".join(sorted(unmarked))
        + "\n\nPass an explicit reason= (ADR-0008 §6). Every call site already"
        "\ndid when this ratchet was installed; it only shrinks."
    )


def test_refund_deciders_state_an_idempotency_key() -> None:
    unkeyed: list[str] = []
    for relative in REFUND_DECIDERS:
        path = REPO_ROOT / relative
        assert path.is_file(), f"refund decider {relative} has moved"
        for lineno, keywords in reversal_call_sites(path):
            if "idempotency_key" not in keywords:
                unkeyed.append(f"{relative}:{lineno}")

    assert unkeyed == [], (
        "these refund reversals are not replay-safe:\n  "
        + "\n  ".join(sorted(unkeyed))
        + "\n\nA refund decider is re-entered by a webhook redelivery or a sync"
        "\nre-run. Pass an explicit idempotency_key= that names the reason"
        "\n(refund-reversal / void-reversal / bounce-reversal), so the ledger"
        "\ncan tell a refund from an FX revaluation."
    )


# ---------------------------------------------------------------------------
# Sensitivity. A check that passes because it sees nothing is not a check.
# ---------------------------------------------------------------------------


def test_the_status_detector_fires(tmp_path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from app.models.finance.ar.customer_payment import PaymentStatus\n"
        "def f(payment):\n"
        "    payment.status = PaymentStatus.REVERSED\n"
    )
    assert stamps_refund_status(probe) == [3]


def test_the_status_detector_covers_the_intent_vocabulary_too(tmp_path) -> None:
    """Two owners, two vocabularies. AR's scan must not stand in for payments'."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f(intent):\n    intent.status = PaymentIntentStatus.REVERSED\n"
    )
    assert stamps_refund_status(probe) == [2]


def test_the_status_detector_ignores_unrelated_reversed_members(tmp_path) -> None:
    """`JournalStatus.REVERSED` and `DepreciationRunStatus.REVERSED` are other
    enums entirely; sweeping them in would assert a rule that does not apply."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f(journal, run):\n"
        "    journal.status = JournalStatus.REVERSED\n"
        "    run.status = DepreciationRunStatus.REVERSED\n"
    )
    assert stamps_refund_status(probe) == []


def test_the_amount_refunded_detector_fires_on_both_forms(tmp_path) -> None:
    """The deleted twins used `+=`; a rewrite could use `=`. Both are writes."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f(advance, other):\n"
        "    advance.amount_refunded += refund_amount\n"
        "    other.amount_refunded = refund_amount\n"
    )
    assert writes_amount_refunded(probe) == [2, 3]


def test_the_reversal_detector_fires_on_an_unlabelled_call(tmp_path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f(db, org, journal):\n"
        "    ReversalService.create_reversal(\n"
        "        db=db,\n"
        "        organization_id=org,\n"
        "        original_journal_id=journal,\n"
        "    )\n"
    )
    sites = reversal_call_sites(probe)
    assert len(sites) == 1
    assert "reason" not in sites[0][1]
    assert "idempotency_key" not in sites[0][1]


def test_both_owners_remain_visible_to_the_detector() -> None:
    """The other half of the proof: the rule above must pass because the call
    sites moved behind the owners, not because the pattern stopped matching.

    The two owners are proved differently on purpose. `PaymentService` stamps
    the column literally, so the strict detector must still see it. The AR
    owner parameterises its terminal status (`payment.status = outcome_status`
    — one behaviour, three reasons), so the strict detector correctly cannot
    see it; what must remain true there is that the vocabulary is still
    *mentioned* in that module. Rename the enum, move either file, or break
    the glob, and one of these fails.
    """
    ar_owner = REPO_ROOT / "app/services/finance/ar/customer_payment.py"
    payments_owner = REPO_ROOT / "app/services/finance/payments/payment_service.py"

    assert ar_owner.is_file(), "the customer refund owner has moved"
    assert payments_owner.is_file(), "the payout reversal owner has moved"

    assert stamps_refund_status(payments_owner), (
        "PaymentService no longer stamps PaymentIntentStatus.REVERSED — either "
        "the payout reversal moved out of its owner, or this detector has "
        "stopped matching anything and the rule above is passing over nothing."
    )
    assert mentions_refund_status(ar_owner), (
        "the customer refund owner no longer mentions PaymentStatus.REVERSED — "
        "either refund_payment moved, or the enum was renamed and the rule "
        "above is passing over nothing."
    )
    assert reversal_call_sites(ar_owner), (
        "the customer refund owner no longer reaches ReversalService — the "
        "reversal ratchets above would be scanning an empty set."
    )


@pytest.mark.parametrize("relative", OWNERS + REFUND_DECIDERS)
def test_every_named_path_still_exists(relative: str) -> None:
    """Failure messages name files; a rename must not leave them pointing at
    nothing."""
    assert (REPO_ROOT / relative).is_file(), f"{relative} is missing"


def test_the_refund_owner_exposes_its_entry_point() -> None:
    """One explicit entry point, and `void`/`bounce` as its callers — not three
    parallel bodies."""
    tree = _parse(REPO_ROOT / "app/services/finance/ar/customer_payment.py")
    assert tree is not None
    functions = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert "refund_payment" in functions
    assert {"void_payment", "mark_bounced"} <= functions
