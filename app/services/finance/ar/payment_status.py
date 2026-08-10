"""The one rule for what an invoice's payment coverage makes its status.

Before this module the question "is this invoice paid?" was answered in eight
places, with six different rules:

===========================================  ==================  ===============
site                                         fully-paid test     nothing-paid
===========================================  ==================  ===============
ar/invoice.py                                ``paid >= total``   PARTIALLY_PAID
ar/customer_payment.py (apply)               ``paid >= total``   PARTIALLY_PAID
ar/customer_payment.py (reverse, x2)         --                  POSTED/OVERDUE
ar/advance_allocation.py                     ``paid >= total``   unchanged
tasks/data_health.py (allocate)              ``paid >= total-1k``PARTIALLY_PAID
tasks/data_health.py (repair)                ``total-paid>1k``   unchanged
scripts/reconcile_invoice_amount_paid.py     ``total-paid<=1k``  unchanged
scripts/allocate_exact_match_payments.py     ``paid >= total``   PARTIALLY_PAID
===========================================  ==================  ===============

Three different tolerances, four different nothing-paid answers, and five
different sets of statuses treated as untouchable. An invoice one kobo short
was PAID down one path and PARTIALLY_PAID down another, and which one you got
depended on whether a human or a scheduler moved the money.

## The rule this settles on

**Tolerance.** A balance at or under `PAYMENT_DUST` is paid. This is not a new
policy: `advance_allocation` already declared ``DUST_THRESHOLD = 0.01``
("sub-cent rounding dust") and used it to decide what to allocate, then used
an exact ``>=`` six lines later to decide the resulting status. The repair
task likewise only treats a shortfall as real when it exceeds 0.01. The
codebase already held this view; it just applied it inconsistently, which is
what left invoices stuck at PARTIALLY_PAID over a rounding residue.

**Nothing paid.** Coverage at or under the dust threshold means the invoice is
unpaid, and an unpaid invoice is `OVERDUE` past its due date and `POSTED`
otherwise. Only the reversal paths knew this; the forward paths would write
`PARTIALLY_PAID` onto an invoice with `amount_paid == 0`.

**Statuses payment does not determine.** `DRAFT`, `SUBMITTED`, `APPROVED`,
`VOID` and `DISPUTED` are returned untouched — the union of the guards the
various call sites carried. `VOID` matters most: a voided invoice usually
carries a zero balance, so any rule keyed on balance alone relabels it `PAID`
(the sync layer had already been bitten by this and commented it).

## What this module is NOT for

Two nearby questions look similar and are deliberately out of scope, because
they are not this question:

* **Deriving a status for an invoice being imported or created**
  (`finance/import_export/invoices.py`). That asks what an invoice's status
  should be given external data, and legitimately produces `DRAFT`.
* **Translating another system's status string** (`dotmac_sub/sync/_base.py`).
  That is a mapping of a foreign vocabulary, not a decision about coverage.

Folding either into this rule would make it answer two questions badly rather
than one well.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.finance.ar.invoice import Invoice, InvoiceStatus

# Sub-cent rounding dust: a balance this small is not a debt. Shared with the
# allocation path so "too small to allocate" and "small enough to be paid"
# cannot drift apart.
PAYMENT_DUST = Decimal("0.01")

# Statuses whose meaning is not a function of payment coverage. Returned
# unchanged, whatever the numbers say.
NOT_PAYMENT_DETERMINED = frozenset(
    {
        InvoiceStatus.DRAFT,
        InvoiceStatus.SUBMITTED,
        InvoiceStatus.APPROVED,
        InvoiceStatus.VOID,
        InvoiceStatus.DISPUTED,
    }
)


def resolve_payment_status(
    *,
    total_amount: Decimal,
    amount_paid: Decimal,
    current_status: InvoiceStatus,
    due_date: date | None,
    today: date,
) -> InvoiceStatus:
    """Return the status implied by this invoice's payment coverage.

    Pure: no session, no I/O, no clock. `today` is passed in so the boundary
    between POSTED and OVERDUE is testable and cannot vary between a caller
    running at 23:59 and the assertion checking it.
    """
    if current_status in NOT_PAYMENT_DETERMINED:
        return current_status

    if total_amount - amount_paid <= PAYMENT_DUST:
        return InvoiceStatus.PAID

    if amount_paid > PAYMENT_DUST:
        return InvoiceStatus.PARTIALLY_PAID

    # Nothing meaningful has been paid — this is an open invoice again, which
    # is the case only the reversal paths used to handle.
    if due_date is not None and due_date < today:
        return InvoiceStatus.OVERDUE
    return InvoiceStatus.POSTED


def apply_payment_status(
    invoice: Invoice, *, today: date | None = None
) -> InvoiceStatus:
    """Recompute and assign `invoice.status` from its current coverage.

    The convenience form every call site wants: each of them had already
    mutated `amount_paid` and then needed the matching status. Returns the
    status set, so a caller can log or report on the transition.
    """
    resolved = resolve_payment_status(
        total_amount=invoice.total_amount,
        amount_paid=invoice.amount_paid,
        current_status=invoice.status,
        due_date=invoice.due_date,
        today=today if today is not None else date.today(),
    )
    invoice.status = resolved
    return resolved
