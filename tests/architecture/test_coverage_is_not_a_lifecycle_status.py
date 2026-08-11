"""A lifecycle enum must not declare a coverage member.

ADR-0016's first enforcement item. `PAID` and `PARTIALLY_PAID` are answers to
"how much has been paid", which is arithmetic over `total_amount` and
`amount_paid`. Putting them in a lifecycle enum stores a computation as a
vocabulary, and ADR-0016 records what that produced: twelve code paths
recomputing coverage with seven different rules, a voided invoice reading PAID,
a partial payment erasing OVERDUE, and a repair task that exists only because a
cache can drift.

## Why an allowlist and not a plain ban

Eight enums declare one today. Failing the build on all eight would make this
red from its first run, and a permanently red check is one nobody reads. So
each is listed WITH ITS REASON and its removal step — ADR-0018: an exemption
states an enforceable premise, or the region is unmonitored rather than
exempt.

It is a ratchet in both directions. A new enum declaring a coverage member
fails immediately (that is the whole point — the ADR's rejected alternative was
"add PARTIALLY_PAID to the three enums that lack it"). And an entry that stops
declaring one must be DELETED from this list, so contract progress is recorded
rather than assumed.

The list only shrinks. When it empties, this test's subject no longer exists
and the test retires with it, along with `test_paid_status_single_owner.py` —
a guard whose subject is gone is noise.
"""

from __future__ import annotations

import ast
import pathlib

MODELS = pathlib.Path(__file__).resolve().parents[2] / "app" / "models"

COVERAGE_MEMBERS = frozenset({"PAID", "PARTIALLY_PAID"})

# Every enum that currently conflates coverage with lifecycle, why it is still
# here, and what removing it takes. Keyed `<repo-relative path>::<ClassName>`.
#
# The two shapes ADR-0016 identifies are both represented, and they contract
# differently:
#
#   * "crammed in" — the enum HAS coverage members and code writes them. These
#     retire at stage 2 step 4, once reads have moved to derived coverage.
#   * "left out" — the enum has PAID but no PARTIALLY_PAID, and the model has no
#     `amount_paid` column at all, so partial payment is unrepresentable. These
#     need stage 2 step 1 (expand) FIRST; removing PAID before there is an
#     `amount_paid` to derive from would delete the only record that anything
#     was paid.
GRANDFATHERED = {
    # --- crammed in: coverage is written by application code ---
    "app/models/finance/ar/invoice.py::InvoiceStatus": (
        "The reference case. `ar/payment_status.py` is the single writer since "
        "#242; retires when AR reads move to `coverage_of`."
    ),
    "app/models/finance/ap/supplier_invoice.py::SupplierInvoiceStatus": (
        "As AR. Single writer is `ap/payment_status.py` since #243."
    ),
    "app/models/finance/ipsas/enums.py::CommitmentStatus": (
        "Reached the same conflation independently, over "
        "`expended_amount`/`obligated_amount`, with EXPENDED as its full-"
        "coverage word. Needs the coverage derivation generalised past "
        "total/paid naming before it can move."
    ),
    # --- left out: no `amount_paid` column, so partial payment cannot be
    #     recorded at all. Expand before contract.
    "app/models/people/payroll/salary_slip.py::SalarySlipStatus": (
        "No `amount_paid`. `payroll_service.py` sets PAID unconditionally with "
        "no comparison against `net_pay`, so disbursing part of a slip leaves "
        "it reading PAID. Needs expand (amount_paid + balance_due) first."
    ),
    "app/models/expense/expense_claim.py::ExpenseClaimStatus": (
        "No `amount_paid`; MARK_PAID is an action, not a computation. Expand "
        "first. Note this enum exists TWICE — see the people/exp entry."
    ),
    "app/models/people/exp/expense_claim.py::ExpenseClaimStatus": (
        "The second copy of the expense-claim status enum. Which module owns "
        "expense claims is a prior question to this one, and answering it is "
        "not in ADR-0016's scope."
    ),
    "app/models/finance/lease/lease_payment_schedule.py::PaymentStatus": (
        "No `amount_paid`, though it carries `total_payment` plus a "
        "principal/interest split. Expand first."
    ),
    # --- not a monetary document ---
    "app/models/finance/tax/tax_period.py::TaxPeriodStatus": (
        "A tax PERIOD, not a document with a total and a paid amount. PAID "
        "here means the period's liability was settled, which is a lifecycle "
        "fact about a reporting window. Reviewed and believed correct — but "
        "kept on the list rather than exempted, because confirming it needs "
        "the filing/payment model this ADR does not cover."
    ),
}


def _declaring_enums() -> dict[str, list[str]]:
    """`path::ClassName` -> the coverage members it declares."""
    found: dict[str, list[str]] = {}
    for path in sorted(MODELS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            members = [
                target.id
                for statement in node.body
                if isinstance(statement, ast.Assign)
                for target in statement.targets
                if isinstance(target, ast.Name)
            ]
            hits = [m for m in members if m in COVERAGE_MEMBERS]
            if hits:
                relative = path.relative_to(MODELS.parents[1])
                found[f"{relative}::{node.name}"] = hits
    return found


def test_no_new_enum_conflates_coverage_with_lifecycle() -> None:
    """The load-bearing direction: this is what the ADR's rejected alternative
    ("add PARTIALLY_PAID to the three enums that lack it") would trip."""
    new = sorted(set(_declaring_enums()) - set(GRANDFATHERED))
    assert new == [], (
        "A lifecycle status enum declares PAID/PARTIALLY_PAID. Coverage is "
        "derived from `total_amount` and `amount_paid` — see "
        "`app.services.finance.coverage.coverage_of` and ADR-0016:\n  "
        + "\n  ".join(new)
    )


def test_the_list_only_shrinks() -> None:
    """A contracted enum must be deleted from the list, so progress is
    recorded. Otherwise the list outlives the problem and starts reading as
    approval."""
    stale = sorted(set(GRANDFATHERED) - set(_declaring_enums()))
    assert stale == [], (
        "These no longer declare a coverage member. Delete them from "
        "GRANDFATHERED to record the progress:\n  " + "\n  ".join(stale)
    )


def test_every_entry_states_a_reason() -> None:
    """ADR-0018: an exemption states an enforceable premise, or the region is
    unmonitored rather than exempt. A bare count would not distinguish
    `tax_period` (reviewed, believed correct) from `salary_slip` (an active
    defect awaiting expand)."""
    thin = sorted(k for k, v in GRANDFATHERED.items() if len(v.strip()) < 40)
    assert thin == [], f"entries with no real reason: {thin}"


def test_the_detector_finds_the_known_conflations() -> None:
    """Sensitivity: a detector that silently matched nothing would let every
    test above pass while checking nothing at all."""
    found = _declaring_enums()
    assert "app/models/finance/ar/invoice.py::InvoiceStatus" in found
    assert found["app/models/finance/ar/invoice.py::InvoiceStatus"] == [
        "PARTIALLY_PAID",
        "PAID",
    ]
    assert len(found) == len(GRANDFATHERED)
