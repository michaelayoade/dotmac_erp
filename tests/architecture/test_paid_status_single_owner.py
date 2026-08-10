"""One owner for the invoice paid-status decision.

The rule that decides whether an invoice is PAID or PARTIALLY_PAID had grown
to eight implementations with six different rules — three tolerances, four
different answers for "nothing paid", five different sets of statuses treated
as untouchable. An invoice one kobo short resolved differently depending on
whether a human, a scheduler or a hand-run script moved the money.

Consolidating them fixes today. This test is what stops it regrowing: the
decision now lives in `app.services.finance.ar.payment_status`, and nothing
else may write a payment-derived status onto an object.

## What this detects, precisely

An assignment of `InvoiceStatus.PAID` / `PARTIALLY_PAID` to an ATTRIBUTE —
`invoice.status = InvoiceStatus.PAID`. That is the act of stamping a
payment-coverage verdict onto a record, which is the owner's job.

It deliberately does NOT flag assignment to a local name
(`status = InvoiceStatus.PAID`), because the two legitimate neighbours both
do exactly that and neither is this decision:

* `finance/import_export/invoices.py` derives a status for an invoice being
  imported, from external data, and may legitimately produce DRAFT.
* `dotmac_sub/sync/_base.py` and `_credit_notes.py` translate another
  system's status vocabulary into ours.

Folding either into the owner would make it answer two questions badly.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The module that owns the decision. It assigns to a local `resolved`, so it
# would not match anyway — listed so the intent is explicit rather than
# accidental.
OWNER = "app/services/finance/ar/payment_status.py"

PAYMENT_DERIVED = {"PAID", "PARTIALLY_PAID"}

# Scoped to AR's InvoiceStatus on purpose. `SupplierInvoiceStatus.PAID`,
# `ExpenseClaimStatus.PAID`, `PayrollStatus.PAID` and friends are different
# enums in different domains, each with its own lifecycle. Whether they carry
# the same duplication is a separate question and a separate owner; sweeping
# them in here would assert a rule this module does not define.
ENUM = "InvoiceStatus"


def _stamps_payment_status(path: Path) -> list[int]:
    """Line numbers where a payment-derived status is written to an attribute."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        # `InvoiceStatus.PAID` — an attribute access on that exact name.
        if not isinstance(value, ast.Attribute) or value.attr not in PAYMENT_DERIVED:
            continue
        if not isinstance(value.value, ast.Name) or value.value.id != ENUM:
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr == "status":
                hits.append(node.lineno)
    return hits


def _scanned_files():
    for root in ("app", "scripts"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" in path.parts or "archive" in path.parts:
                continue
            yield path


def test_only_the_owner_stamps_a_payment_derived_status():
    offenders: list[str] = []
    for path in _scanned_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == OWNER:
            continue
        for lineno in _stamps_payment_status(path):
            offenders.append(f"{relative}:{lineno}")

    assert offenders == [], (
        "these sites decide invoice paid-status themselves:\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nUse app.services.finance.ar.payment_status.apply_payment_status()"
        "\n(or resolve_payment_status() when you have values rather than a model)."
        "\nA second rule here is how the tolerance and the nothing-paid answer"
        "\ndrifted apart across eight sites in the first place."
    )


def test_the_detector_actually_fires(tmp_path):
    """Sensitivity proof. The test above must pass because the call sites were
    migrated, not because the AST pattern stopped matching anything."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from app.models.finance.ar.invoice import InvoiceStatus\n"
        "def f(invoice):\n"
        "    invoice.status = InvoiceStatus.PAID\n"
    )
    assert _stamps_payment_status(probe) == [3]


def test_the_detector_ignores_local_derivation(tmp_path):
    """The two legitimate neighbours assign to a local name, not an attribute,
    and must not be swept up."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from app.models.finance.ar.invoice import InvoiceStatus\n"
        "def derive(paid, total):\n"
        "    status = InvoiceStatus.PAID\n"
        "    return status\n"
    )
    assert _stamps_payment_status(probe) == []


def test_the_owner_module_exists_where_the_message_says():
    """The failure message names a module; a rename must not leave it lying."""
    assert (REPO_ROOT / OWNER).is_file()
    from app.services.finance.ar.payment_status import (  # noqa: F401
        apply_payment_status,
        resolve_payment_status,
    )
