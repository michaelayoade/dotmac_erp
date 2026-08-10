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

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

PAYMENT_DERIVED = {"PAID", "PARTIALLY_PAID"}

# Each enum that expresses payment COVERAGE, and the one module allowed to
# stamp it. Both owners assign to a local `resolved`, so neither would match
# anyway — they are listed so the exemption is explicit rather than accidental.
#
# These two are the only enums here on purpose. `ExpenseClaimStatus.PAID`,
# `SalarySlipStatus.PAID`, `TaxPeriodStatus.PAID`, `CommitmentStatus.PAID` and
# the lease `PaymentStatus.PAID` have **no PARTIALLY_PAID member**: they are
# binary lifecycle transitions (approved -> paid), not a computation over
# amount_paid against a total. Sweeping them in would assert a rule that does
# not apply to them.
OWNERS = {
    "InvoiceStatus": "app/services/finance/ar/payment_status.py",
    "SupplierInvoiceStatus": "app/services/finance/ap/payment_status.py",
}


def _stamps_payment_status(path: Path, enum: str | None = None) -> list[int]:
    """Line numbers where a payment-derived status is written to an attribute.

    `enum` restricts the scan to one vocabulary; None means any owned enum.
    """
    wanted = {enum} if enum is not None else set(OWNERS)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        # `<Enum>.PAID` — an attribute access on one of the owned names.
        if not isinstance(value, ast.Attribute) or value.attr not in PAYMENT_DERIVED:
            continue
        if not isinstance(value.value, ast.Name) or value.value.id not in wanted:
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


@pytest.mark.parametrize("enum,owner", sorted(OWNERS.items()))
def test_only_the_owner_stamps_a_payment_derived_status(enum, owner):
    offenders: list[str] = []
    for path in _scanned_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == owner:
            continue
        for lineno in _stamps_payment_status(path, enum):
            offenders.append(f"{relative}:{lineno}")

    module = owner.removesuffix(".py").replace("/", ".")
    assert offenders == [], (
        f"these sites decide {enum} coverage themselves:\n  "
        + "\n  ".join(sorted(offenders))
        + f"\n\nUse {module}.apply_payment_status()"
        "\n(or resolve_payment_status() when you have values rather than a model)."
        "\nA second rule here is how the tolerance and the nothing-paid answer"
        "\ndrifted apart across eleven sites in the first place."
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


def test_the_detector_covers_ap_too(tmp_path):
    """AP carried the identical defect and gets its own owner, so the scan must
    key on both vocabularies rather than AR's alone."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus\n"
        "def f(invoice):\n"
        "    invoice.status = SupplierInvoiceStatus.PARTIALLY_PAID\n"
    )
    assert _stamps_payment_status(probe, "SupplierInvoiceStatus") == [3]
    assert _stamps_payment_status(probe) == [3]
    # ...and AR's scan must not claim AP's site.
    assert _stamps_payment_status(probe, "InvoiceStatus") == []


def test_binary_lifecycle_enums_are_not_swept_in(tmp_path):
    """`ExpenseClaimStatus`, `SalarySlipStatus`, `TaxPeriodStatus` and friends
    have no PARTIALLY_PAID member — they are approved->paid transitions, not a
    computation over coverage, and this rule does not govern them."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f(claim, slip):\n"
        "    claim.status = ExpenseClaimStatus.PAID\n"
        "    slip.status = SalarySlipStatus.PAID\n"
    )
    assert _stamps_payment_status(probe) == []


@pytest.mark.parametrize("enum,owner", sorted(OWNERS.items()))
def test_each_owner_module_exists_and_exports_the_named_functions(enum, owner):
    """The failure message names a module and two functions; a rename must not
    leave that message pointing at nothing."""
    import importlib

    assert (REPO_ROOT / owner).is_file(), f"{enum}'s owner {owner} is missing"
    module = importlib.import_module(owner.removesuffix(".py").replace("/", "."))
    assert callable(module.apply_payment_status)
    assert callable(module.resolve_payment_status)
