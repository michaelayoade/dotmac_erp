"""A document with a money total must be able to say how much was paid.

ADR-0016's second enforcement item: "a test that every model carrying a
monetary total also carries the coverage mixin, so a new money document cannot
ship without partial-payment support."

The failure it prevents is the one stage 2 exists to repair. `SalarySlip`
carried `net_pay` and recorded `paid_at`, `paid_by_id` and `payment_reference`
— who and when, never how much — so disbursing ₦50,000 against a ₦100,000 slip
left the slip reading PAID and no column anywhere disagreed. Nobody decided
that; partial payment was simply never asked about when the model was written.
This test asks.

## What counts as a monetary document

A mapped class declaring a column whose name is in `TOTAL_COLUMNS` — the
"this is what the document is worth" names actually used in this codebase.
Deliberately narrow: matching every `Numeric` column would sweep in tax
amounts, line totals, running balances and rates, and a guard that fires
constantly on things nobody can fix gets muted.

Such a class must declare `amount_paid` and `balance_due`, or appear in
`NOT_A_PAYABLE_DOCUMENT` with a reason.
"""

from __future__ import annotations

import ast
import pathlib

MODELS = pathlib.Path(__file__).resolve().parents[2] / "app" / "models"

# The names this codebase uses for "what the document is worth".
TOTAL_COLUMNS = frozenset(
    {
        "total_amount",
        "net_pay",
        "net_payable_amount",
        "total_payment",
    }
)

COVERAGE_COLUMNS = ("amount_paid", "balance_due")

# A class carrying one of those names that is nonetheless not a document
# somebody pays, with the reason. ADR-0018: an exemption states its premise.
#
# They fall into three kinds, and the kind is the reason. Carrying a total is
# not the same as being settled: an INTENT is what might be owed, a CONTAINER
# is how payments were grouped, and a PLAN is what was allowed to be spent.
# Only the document that creates the obligation gets a coverage question.

# INTENT — a total that may become payable, through a document that is not this
# one. Coverage belongs to the invoice these turn into, and putting
# `amount_paid` here would mean two places answering "was this paid?".
_INTENT_NOT_OBLIGATION = {
    "app/models/finance/ar/quote.py::Quote": (
        "An offer. Nothing is owed until it is accepted and invoiced."
    ),
    "app/models/finance/ar/sales_order.py::SalesOrder": (
        "An order. The AR invoice raised from it carries the obligation and "
        "its coverage."
    ),
    "app/models/finance/ap/purchase_order.py::PurchaseOrder": (
        "A commitment to buy. You pay the supplier invoice, not the PO — "
        "which is exactly why `ap.supplier_invoice` got the columns and this "
        "did not."
    ),
    "app/models/procurement/quotation_response.py::QuotationResponse": (
        "A supplier's offered price. Not an obligation in either direction."
    ),
}

# CONTAINER — a batch whose total is the sum of its items. Coverage would be a
# second, weaker copy of what the items already say.
_CONTAINER_OF_PAYMENTS = {
    "app/models/finance/ap/payment_batch.py::APPaymentBatch": (
        "A batch of payments. It IS the paying; asking how much of it was "
        "paid inverts the relationship."
    ),
    "app/models/finance/payments/transfer_batch.py::TransferBatch": (
        "As above — its `TransferBatchItem` rows carry the amounts that "
        "actually moved. (Those items are also where a salary slip's real "
        "disbursed amount will come from; see the disbursement module.)"
    ),
}

# PLAN — an authorisation to spend, not a debt.
_NOT_A_DEBT = {
    "app/models/finance/gl/budget.py::Budget": (
        "An allowance, consumed by actuals rather than settled by a payment."
    ),
}

# DUPLICATE — see the ownership ruling.
_RETIRING = {
    "app/models/people/exp/expense_claim.py::ExpenseClaim": (
        "A duplicate of the expense-domain model, retiring rather than "
        "gaining columns — expanding both would create the second writer "
        "ADR-0016 exists to remove. See "
        "test_coverage_is_not_a_lifecycle_status.py."
    ),
}

NOT_A_PAYABLE_DOCUMENT = {
    **_INTENT_NOT_OBLIGATION,
    **_CONTAINER_OF_PAYMENTS,
    **_NOT_A_DEBT,
    **_RETIRING,
}


def _classes_with_totals() -> dict[str, set[str]]:
    """`path::ClassName` -> the column names it declares, for money documents."""
    found: dict[str, set[str]] = {}
    for path in sorted(MODELS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            columns = {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            }
            if columns & TOTAL_COLUMNS:
                relative = path.relative_to(MODELS.parents[1])
                found[f"{relative}::{node.name}"] = columns
    return found


def test_every_money_document_can_record_a_partial_payment() -> None:
    missing = sorted(
        f"{key} (no {', '.join(c for c in COVERAGE_COLUMNS if c not in columns)})"
        for key, columns in _classes_with_totals().items()
        if key not in NOT_A_PAYABLE_DOCUMENT
        and not all(c in columns for c in COVERAGE_COLUMNS)
    )
    assert missing == [], (
        "These carry a monetary total but cannot record how much was paid, so "
        "a part-payment reads as settled in full (ADR-0016). Add `amount_paid` "
        "and a `balance_due` Computed column, or list the model in "
        "NOT_A_PAYABLE_DOCUMENT with its reason:\n  " + "\n  ".join(missing)
    )


def test_every_exemption_states_a_reason() -> None:
    thin = sorted(k for k, v in NOT_A_PAYABLE_DOCUMENT.items() if len(v.strip()) < 40)
    assert thin == [], f"entries with no real reason: {thin}"


def test_every_exemption_is_still_a_money_document() -> None:
    """A stale exemption is an exemption nobody is checking. If the model was
    renamed or deleted, the entry must go with it."""
    stale = sorted(set(NOT_A_PAYABLE_DOCUMENT) - set(_classes_with_totals()))
    assert stale == [], f"exemptions for models that no longer qualify: {stale}"


def test_the_detector_finds_the_documents_it_should() -> None:
    """Sensitivity. Without this a detector matching nothing would make every
    assertion above pass while checking nothing at all."""
    found = _classes_with_totals()
    for expected in (
        "app/models/finance/ar/invoice.py::Invoice",
        "app/models/finance/ap/supplier_invoice.py::SupplierInvoice",
        "app/models/people/payroll/salary_slip.py::SalarySlip",
        "app/models/expense/expense_claim.py::ExpenseClaim",
        "app/models/finance/lease/lease_payment_schedule.py::LeasePaymentSchedule",
    ):
        assert expected in found, f"detector missed {expected}"
        assert all(c in found[expected] for c in COVERAGE_COLUMNS), (
            f"{expected} should carry the coverage columns by now"
        )
