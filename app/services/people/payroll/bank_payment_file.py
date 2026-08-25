"""Map a complete payroll run to a bank-file command, or refuse it wholly.

A bank upload is one disbursement batch. Omitting one salary slip does not make
the batch partially valid; it makes the file an inaccurate representation of
the authorized payroll run. This module owns that all-or-nothing mapping so a
web adapter cannot silently ``continue`` past an employee it cannot pay.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.models.people.payroll.salary_slip import SalarySlip
from app.services.finance.banking.bank_upload import BankUploadResult, PaymentItem


@dataclass(frozen=True, slots=True)
class PayrollBankFileIncomplete(ValueError):
    """The run contains positive-net slips that cannot enter a payment file."""

    missing_slip_count: int
    missing_fields: frozenset[str]

    def __str__(self) -> str:
        # This text may be put into a URL. Keep employee names, account numbers
        # and salary values out of it; the readiness screen owns row detail.
        return (
            f"Payroll payment file blocked: {self.missing_slip_count} salary "
            "slip(s) have incomplete bank details"
        )


class PayrollBankFileUnresolved(ValueError):
    """At least one complete destination did not resolve to a bank code."""

    def __str__(self) -> str:
        return (
            "Payroll payment file blocked: one or more bank codes could not be resolved"
        )


def require_resolved_bank_codes(result: BankUploadResult) -> BankUploadResult:
    """Refuse an otherwise generated file if any destination stayed unresolved."""
    if result.errors:
        raise PayrollBankFileUnresolved
    return result


def payment_items_for_slips(
    slips: Sequence[SalarySlip],
    *,
    payroll_month: int | None,
    payroll_year: int | None,
) -> list[PaymentItem]:
    """Return every payment item, refusing the entire run on missing details."""
    missing_fields: set[str] = set()
    incomplete_count = 0
    for slip in slips:
        slip_missing = False
        if not slip.bank_account_number:
            missing_fields.add("bank_account_number")
            slip_missing = True
        if not slip.bank_name:
            missing_fields.add("bank_name")
            slip_missing = True
        if slip_missing:
            incomplete_count += 1

    if incomplete_count:
        raise PayrollBankFileIncomplete(
            missing_slip_count=incomplete_count,
            missing_fields=frozenset(missing_fields),
        )

    narration = (
        f"Salary {payroll_month}/{payroll_year}"
        if payroll_month is not None and payroll_year is not None
        else "Salary Payment"
    )
    items: list[PaymentItem] = []
    for slip in slips:
        base_ref = slip.slip_number or f"SAL-{slip.slip_id.hex[:8].upper()}"
        suffix = (
            slip.employee.employee_code
            if slip.employee and slip.employee.employee_code
            else slip.slip_id.hex[:6].upper()
        )
        items.append(
            PaymentItem(
                reference=f"{base_ref}-{suffix}",
                beneficiary_name=slip.bank_account_name
                or slip.employee_name
                or "Unknown",
                amount=slip.net_pay,
                # Proven non-empty above. The model is nullable because the
                # readiness lifecycle admits an incomplete employee record.
                account_number=slip.bank_account_number or "",
                bank_name=slip.bank_name or "",
                bank_code=slip.bank_branch_code,
                beneficiary_code=(
                    slip.employee.employee_code if slip.employee else None
                ),
                narration=narration,
            )
        )
    return items
