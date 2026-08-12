"""The one place a salary slip records how much was paid against it.

ADR-0016 stage 2 (expand). Two methods mark a slip PAID —
`PayrollService.payout_payroll_entry` and
`PayrollLifecycleService.mark_slip_paid` — and before this module NEITHER took
an amount. The slip stored `paid_at`, `paid_by_id` and `payment_reference`:
who and when, never how much. So disbursing ₦50,000 against a ₦100,000 slip
left the slip reading PAID, with no column anywhere to contradict it.

Two writers for one fact is what ADR-0016 exists to remove, so the write lives
here once rather than being copied into both.

## Why the default is the full net_pay, and why that is not the bug returning

`amount_paid` defaults to `net_pay` when a caller supplies nothing. That looks
like it re-states the defect, and it is worth being precise about why it does
not:

* Marking a slip PAID has ALWAYS asserted "this slip was settled in full".
  That assertion was previously implicit and unrecorded; writing `net_pay`
  makes the same assertion **explicit and queryable**. Nothing new is claimed.
* The alternative — leaving `amount_paid` at zero — would assert the opposite
  of what the caller meant and make every paid slip read UNPAID.
* An explicit `amounts_paid` entry always wins, so a caller who knows a
  part-disbursement can now record it. That capacity did not exist at all
  before, and it is the whole point of the expand step.

What remains open is that no caller yet HAS the real figure: `TransferBatchItem`
carries the disbursed `amount` but links to `expense_claim_id`, with no salary
slip link. Closing that is stage 2 step 3, and until then the default is the
honest record of what the system believes.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.people.payroll.salary_slip import SalarySlip


def record_disbursement(slip: SalarySlip, amount: Decimal | None = None) -> Decimal:
    """Record what was disbursed against `slip`, returning the amount written.

    `amount` is the sum that genuinely moved. `None` means the caller did not
    say, which every existing caller does — see the module docstring for why
    that records the full `net_pay` rather than nothing.
    """
    disbursed = slip.net_pay if amount is None else amount
    slip.amount_paid = disbursed
    return disbursed
