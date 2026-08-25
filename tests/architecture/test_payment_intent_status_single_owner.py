"""One owner for ``PaymentIntent.status``.

``payments.payment_intent.status`` is the record of whether money left the
account. It had three live writers:

* ``app/services/finance/payments/payment_service.py`` — the owner, which
  reaches the column through initiation, webhook completion, failure, polling
  and reversal;
* ``app/tasks/expense.py::poll_stuck_expense_transfers`` — a scheduled worker
  that promoted PENDING to PROCESSING, stamped EXPIRED over rows it had
  selected in a *different* session, and marked FAILED when its own circuit
  breaker tripped. It had no tests at all;
* ``app/services/finance/payments/batch_transfer_service.py`` — dead code
  holding a live ``initiate_transfer`` with no separation of duties. Deleted.

The worker is now an adapter and the batch service is gone. This test is what
stops a third writer growing back: a scheduler that writes this column decides
on state it read minutes ago, and the way that fails is a settled transfer
reopened or an in-flight payout stamped EXPIRED.

## What this detects, precisely

Two acts, both in ``app/``, ``scripts/`` and ``tools/``:

1. **Stamping** — assigning ``PaymentIntentStatus.<MEMBER>`` to an attribute
   named ``status`` (``intent.status = PaymentIntentStatus.FAILED``).
2. **Birth** — passing ``status=PaymentIntentStatus.<MEMBER>`` to a
   ``PaymentIntent(...)`` constructor. An intent created directly with a
   status is the same decision taken a moment earlier.

## What it deliberately does NOT detect

Assignment of a *name* rather than a literal member (``intent.status = wanted``)
and dynamic lookup (``PaymentIntentStatus[name]``). A static scan cannot follow
those, and pretending otherwise would be worse than saying so here. What keeps
the check honest instead is ``test_the_owner_still_stamps_the_column``: the
owner's own writes must remain visible to this detector, so a rename of the
enum, the column or the module — or a glob that stops matching — fails the
build rather than silently passing over nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

ENUM = "PaymentIntentStatus"
MODEL = "PaymentIntent"
COLUMN = "status"

OWNER = "app/services/finance/payments/payment_service.py"
"""The one module that may decide a payment intent's status."""

SCANNED_ROOTS = ("app", "scripts", "tools")
SKIPPED_PARTS = frozenset({"__pycache__", "archive"})


def _is_owned_status(node: ast.expr) -> bool:
    """True for ``PaymentIntentStatus.<MEMBER>``."""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == ENUM
    )


def writes_payment_intent_status(path: Path) -> list[int]:
    """Line numbers where this file decides a payment intent's status."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    hits: list[int] = []
    for node in ast.walk(tree):
        # 1. stamping: `<anything>.status = PaymentIntentStatus.X`
        if isinstance(node, ast.Assign) and _is_owned_status(node.value):
            if any(
                isinstance(target, ast.Attribute) and target.attr == COLUMN
                for target in node.targets
            ):
                hits.append(node.lineno)
                continue
        # 2. birth: `PaymentIntent(..., status=PaymentIntentStatus.X, ...)`
        if isinstance(node, ast.Call):
            func = node.func
            named = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if named != MODEL:
                continue
            for keyword in node.keywords:
                if keyword.arg == COLUMN and _is_owned_status(keyword.value):
                    hits.append(node.lineno)
    return sorted(hits)


def scanned_files():
    for root in SCANNED_ROOTS:
        directory = REPO_ROOT / root
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            if SKIPPED_PARTS & set(path.parts):
                continue
            yield path


def test_only_payment_service_writes_payment_intent_status() -> None:
    offenders: list[str] = []
    for path in scanned_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == OWNER:
            continue
        for lineno in writes_payment_intent_status(path):
            offenders.append(f"{relative}:{lineno}")

    assert offenders == [], (
        "these sites decide a payment intent's status themselves:\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nAsk PaymentService instead. It owns every transition:"
        "\n  initiate_expense_transfer / process_successful_transfer /"
        "\n  mark_transfer_failed / process_transfer_reversal /"
        "\n  reconcile_stuck_transfer / expire_stale_pending_transfer."
        "\nA scheduled job that writes this column is deciding on state it read"
        "\nin another session; that is how a settled transfer gets reopened."
    )


def test_the_owner_still_stamps_the_column() -> None:
    """Sensitivity proof, liveness half.

    The test above must pass because there are no other writers, not because
    the detector stopped matching anything. The owner is the one file that is
    SUPPOSED to be full of these writes, so if the scan comes back empty there,
    the enum, the column, the module path or the glob has moved.
    """
    owner_writes = writes_payment_intent_status(REPO_ROOT / OWNER)
    assert len(owner_writes) >= 5, (
        f"{OWNER} should be the one file full of status writes, found "
        f"{len(owner_writes)} — the detector has probably stopped matching "
        "(renamed enum/column, moved owner, or a bad glob)."
    )


def test_the_detector_fires_on_a_planted_stamp(tmp_path: Path) -> None:
    """Sensitivity proof, detection half: plant the violation the worker had."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from app.models.finance.payments.payment_intent import PaymentIntentStatus\n"
        "def poll(intent):\n"
        "    intent.status = PaymentIntentStatus.PROCESSING\n"
    )
    assert writes_payment_intent_status(probe) == [3]

    # ...and remove it: the same file, same scan, now clean.
    probe.write_text(
        "from app.models.finance.payments.payment_intent import PaymentIntentStatus\n"
        "def poll(intent, svc, config):\n"
        "    return svc.reconcile_stuck_transfer(intent.intent_id, config)\n"
    )
    assert writes_payment_intent_status(probe) == []


def test_the_detector_fires_on_a_planted_construction(tmp_path: Path) -> None:
    """The deleted batch service decided a status at construction time."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def make(org_id):\n"
        "    return PaymentIntent(\n"
        "        organization_id=org_id,\n"
        "        status=PaymentIntentStatus.PROCESSING,\n"
        "    )\n"
    )
    assert writes_payment_intent_status(probe) == [2]


def test_the_detector_ignores_reads_and_other_vocabularies(tmp_path: Path) -> None:
    """Reading the column is what every adapter is allowed to do, and the
    webhook receiver's own WebhookStatus is a different decision entirely."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f(intent, webhook):\n"
        "    if intent.status == PaymentIntentStatus.COMPLETED:\n"
        "        return True\n"
        "    webhook.status = WebhookStatus.PROCESSED\n"
        "    resolved = PaymentIntentStatus.FAILED\n"
        "    return resolved\n"
    )
    assert writes_payment_intent_status(probe) == []


def test_the_owner_and_the_vocabulary_still_exist() -> None:
    """The failure message names a module and six methods; a rename must not
    leave it pointing at nothing."""
    import importlib

    assert (REPO_ROOT / OWNER).is_file(), f"the named owner {OWNER} is missing"

    module = importlib.import_module(OWNER.removesuffix(".py").replace("/", "."))
    for method in (
        "initiate_expense_transfer",
        "process_successful_transfer",
        "mark_transfer_failed",
        "process_transfer_reversal",
        "reconcile_stuck_transfer",
        "expire_stale_pending_transfer",
    ):
        assert callable(getattr(module.PaymentService, method)), method

    intent_model = importlib.import_module("app.models.finance.payments.payment_intent")
    assert hasattr(intent_model, ENUM)
    assert hasattr(intent_model.PaymentIntent, COLUMN)


@pytest.mark.parametrize("root", SCANNED_ROOTS)
def test_every_scanned_root_is_real(root: str) -> None:
    """A mis-globbed root would make the sweep pass over nothing."""
    assert (REPO_ROOT / root).is_dir(), (
        f"{root}/ does not exist — the sweep above is scanning a directory "
        "that is not there and would pass for the wrong reason."
    )


def test_the_deleted_batch_service_stays_deleted() -> None:
    """BatchTransferService was the third writer. It was dead code that still
    held a live `initiate_transfer` with no separation of duties and no
    permission check on approve_batch; its disposition (zero callers across
    app/, tests/, scripts/, tools/; not exported; zero tests) is recorded in
    the starter repo's treasury-payment-execution-sources inventory. Its
    tables and model are NOT deleted — existing rows are real payout history.
    """
    gone = REPO_ROOT / "app/services/finance/payments/batch_transfer_service.py"
    assert not gone.exists(), (
        "batch_transfer_service.py is back. If batch payouts are wanted again, "
        "they go through PaymentService with an approval check, not a second "
        "service that initiates transfers and writes intent statuses itself."
    )
    # The persistence it wrote to is deliberately still here.
    assert (REPO_ROOT / "app/models/finance/payments/transfer_batch.py").is_file()
