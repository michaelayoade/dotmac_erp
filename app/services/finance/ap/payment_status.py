"""The one rule for what a supplier invoice's payment coverage makes its status.

The AP mirror of `app.services.finance.ar.payment_status`, and the same defect:
three sites decided coverage with two different rules.

===============================================  ==================  ==============
site                                             fully-paid test     nothing paid
===============================================  ==================  ==============
ap/supplier_invoice.py (record payment)          ``paid >= total``   PARTIALLY_PAID
ap/supplier_payment.py (apply allocations)       ``paid >= total``   PARTIALLY_PAID
ap/supplier_payment.py (reverse allocations)     --                  POSTED
===============================================  ==================  ==============

Both defects AR carried are here too:

* **No tolerance.** Every site used an exact ``>=``, so a bill a kobo short
  stayed PARTIALLY_PAID forever — while `supplier_payment.py` itself already
  used ``Decimal("0.01")`` as a tolerance two hundred lines earlier, to check
  an allocation total against the payment amount. Same file, same number,
  applied to one comparison and not the other.
* **PARTIALLY_PAID at zero coverage.** The two forward paths wrote
  PARTIALLY_PAID whenever the invoice was not fully covered, including when
  ``amount_paid`` was zero. Only the reversal path knew an uncovered bill is
  POSTED.

## Why this is not `ar.payment_status` with a different enum

`SupplierInvoiceStatus` has **no OVERDUE**. AR resolves an uncovered invoice to
OVERDUE past its due date, because AR invoices are chased; a supplier bill we
have not paid is simply POSTED, and lateness is the supplier's concern, tracked
by AP aging reports rather than by the row's status. So this rule takes no
`due_date` and no clock, and reusing AR's function here would introduce a
status the enum cannot hold.

The protected set is also larger: AP adds PENDING_APPROVAL, ON_HOLD and
REJECTED to the terminal statuses, none of which exist in AR.

## What this module is NOT for

`scripts/bulk_import.py::_map_bill_status` translates Zoho's status vocabulary
into ours and legitimately returns DRAFT or VOID. That is a mapping of a
foreign system's words, not a decision about coverage — the same carve-out AR
makes for `dotmac_sub/sync/_base.py`.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.services.finance.coverage import PAYMENT_DUST_DEFAULT

# Sub-cent rounding dust. This was its own `Decimal("0.01")`, with a comment
# saying AP and AR are "free to diverge — but a change to either should be a
# conscious answer to 'why not both?'". ADR-0016 §4 answers it: the tolerance is
# one business policy, so it is one setting (`payments.payment_dust`) and this
# is its DEFAULT, re-exported here so the existing call sites keep their name.
PAYMENT_DUST = PAYMENT_DUST_DEFAULT

# Statuses whose meaning is not a function of payment coverage.
NOT_PAYMENT_DETERMINED = frozenset(
    {
        SupplierInvoiceStatus.DRAFT,
        SupplierInvoiceStatus.SUBMITTED,
        SupplierInvoiceStatus.PENDING_APPROVAL,
        SupplierInvoiceStatus.APPROVED,
        SupplierInvoiceStatus.ON_HOLD,
        SupplierInvoiceStatus.REJECTED,
        SupplierInvoiceStatus.VOID,
        SupplierInvoiceStatus.DISPUTED,
    }
)


def resolve_payment_status(
    *,
    total_amount: Decimal,
    amount_paid: Decimal,
    current_status: SupplierInvoiceStatus,
) -> SupplierInvoiceStatus:
    """Return the status implied by this supplier invoice's payment coverage.

    Pure: no session, no I/O, and — unlike the AR rule — no clock, because AP
    has no OVERDUE for a date to decide.
    """
    if current_status in NOT_PAYMENT_DETERMINED:
        return current_status

    if total_amount - amount_paid <= PAYMENT_DUST:
        return SupplierInvoiceStatus.PAID

    if amount_paid > PAYMENT_DUST:
        return SupplierInvoiceStatus.PARTIALLY_PAID

    return SupplierInvoiceStatus.POSTED


def apply_payment_status(invoice: SupplierInvoice) -> SupplierInvoiceStatus:
    """Recompute and assign `invoice.status` from its current coverage.

    Returns the status set, so a caller can log or report the transition.
    """
    resolved = resolve_payment_status(
        total_amount=invoice.total_amount,
        amount_paid=invoice.amount_paid,
        current_status=invoice.status,
    )
    invoice.status = resolved
    return resolved
