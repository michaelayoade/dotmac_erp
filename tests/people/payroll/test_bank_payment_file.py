from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.finance.banking.bank_upload import BankUploadResult
from app.services.people.payroll.bank_payment_file import (
    PayrollBankFileIncomplete,
    PayrollBankFileUnresolved,
    payment_items_for_slips,
    require_resolved_bank_codes,
)


def _slip(
    *,
    account_number: str | None = "0123456789",
    bank_name: str | None = "Example Bank",
) -> SimpleNamespace:
    return SimpleNamespace(
        slip_id=uuid4(),
        slip_number="PAY-001",
        employee_name="Payroll Beneficiary",
        bank_account_name="Payroll Beneficiary",
        bank_account_number=account_number,
        bank_name=bank_name,
        bank_branch_code="058",
        net_pay=Decimal("125000.00"),
        employee=SimpleNamespace(employee_code="EMP-001"),
    )


@pytest.mark.parametrize(
    ("account_number", "bank_name", "field"),
    [
        (None, "Example Bank", "bank_account_number"),
        ("0123456789", None, "bank_name"),
    ],
)
def test_one_incomplete_slip_refuses_the_whole_payment_file(
    account_number: str | None,
    bank_name: str | None,
    field: str,
) -> None:
    slips = [_slip(), _slip(account_number=account_number, bank_name=bank_name)]

    with pytest.raises(PayrollBankFileIncomplete) as raised:
        payment_items_for_slips(slips, payroll_month=8, payroll_year=2026)

    assert raised.value.missing_slip_count == 1
    assert raised.value.missing_fields == frozenset({field})
    assert "Payroll Beneficiary" not in str(raised.value)
    assert "0123456789" not in str(raised.value)


def test_complete_slips_are_all_mapped_without_partial_omission() -> None:
    slips = [_slip(), _slip()]

    items = payment_items_for_slips(slips, payroll_month=8, payroll_year=2026)

    assert len(items) == len(slips) == 2
    assert all(item.amount == Decimal("125000.00") for item in items)
    assert all(item.narration == "Salary 8/2026" for item in items)


def test_an_unresolved_bank_code_refuses_the_generated_file_without_leaking_detail() -> (
    None
):
    result = BankUploadResult(
        content=b"would-have-been-downloaded",
        filename=f"bank_upload_{date.today().isoformat()}.xlsx",
        content_type="application/octet-stream",
        row_count=1,
        total_amount=Decimal("125000.00"),
        errors=["Bank code not found for: Payroll Beneficiary (Private Bank)"],
    )

    with pytest.raises(PayrollBankFileUnresolved) as raised:
        require_resolved_bank_codes(result)

    assert "Payroll Beneficiary" not in str(raised.value)
    assert "Private Bank" not in str(raised.value)
