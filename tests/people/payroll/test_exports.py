from __future__ import annotations

import csv
import io
from decimal import Decimal
from types import SimpleNamespace

from app.services.people.payroll.nhf_export import NHFExportService
from app.services.people.payroll.paye_export import PAYEExportService
from app.services.people.payroll.pension_export import PensionExportService


def _make_component(code: str) -> SimpleNamespace:
    return SimpleNamespace(component_code=code)


def _make_earning(code: str, amount: str) -> SimpleNamespace:
    return SimpleNamespace(component=_make_component(code), amount=Decimal(amount))


def _make_deduction(code: str, amount: str) -> SimpleNamespace:
    return SimpleNamespace(component=_make_component(code), amount=Decimal(amount))


def _make_employee() -> SimpleNamespace:
    designation = SimpleNamespace(designation_name="Analyst")
    tax_profile = SimpleNamespace(
        tin="TIN-123",
        pfa_code="PFA01",
        rsa_pin="RSA123",
        pfa=None,
        nhf_number="NHF001",
    )
    return SimpleNamespace(
        full_name="Jane Doe",
        designation=designation,
        employee_code="EMP001",
        employee_number="EMP001",
        current_tax_profile=tax_profile,
    )


def test_paye_export_uses_deductions_and_earnings():
    service = PAYEExportService(db=None)
    slip = SimpleNamespace(
        slip_number="SLIP-2026-00001",
        employee=_make_employee(),
        gross_pay=Decimal("2200.00"),
        earnings=[
            _make_earning("BASIC", "1000"),
            _make_earning("HOUSING", "500"),
            _make_earning("TRANSPORT", "500"),
            _make_earning("BONUS", "200"),
        ],
        deductions=[
            _make_deduction("PAYE", "100"),
            _make_deduction("NHF", "25"),
            _make_deduction("NHIS", "0"),
            _make_deduction("PENSION", "80"),
        ],
    )

    result = service._generate_lirs_format([slip], 2026, 1)
    content = result.content.decode("utf-8")
    rows = list(csv.reader(io.StringIO(content)))

    # Header + 1 data row
    assert len(rows) == 2
    data = rows[1]

    # Basic, Housing, Transport, Bonus columns
    assert data[5] == "1000.00"
    assert data[6] == "500.00"
    assert data[7] == "500.00"
    assert data[13] == "200.00"

    # NHF, NHIS, Pension, PAYE
    assert data[17] == "25.00"
    assert data[18] == "0.00"
    assert data[19] == "80.00"
    assert data[22] == "100.00"


def test_pension_export_uses_deductions_and_earnings():
    service = PensionExportService(db=None)
    slip = SimpleNamespace(
        slip_number="SLIP-2026-00002",
        employee=_make_employee(),
        earnings=[
            _make_earning("BASIC", "1000"),
            _make_earning("HOUSING", "500"),
            _make_earning("TRANSPORT", "500"),
        ],
        deductions=[
            _make_deduction("PENSION", "80"),
            _make_deduction("PENSION_EMPLOYER", "100"),
        ],
    )

    result = service._generate_generic_format([slip], 2026, 1)
    content = result.content.decode("utf-8")
    rows = list(csv.reader(io.StringIO(content)))

    assert len(rows) == 2
    data = rows[1]

    # Basic, Housing, Transport, BHT
    assert data[6] == "1000.00"
    assert data[7] == "500.00"
    assert data[8] == "500.00"
    assert data[9] == "2000.00"

    # Employee/Employer pension
    assert data[10] == "80.00"
    assert data[11] == "100.00"


def test_nhf_export_uses_deductions():
    service = NHFExportService(db=None)
    slip = SimpleNamespace(
        slip_number="SLIP-2026-00003",
        employee=_make_employee(),
        deductions=[_make_deduction("NHF", "25")],
    )

    result = service._generate_fmbn_format([slip], 2026, 1)
    content = result.content.decode("utf-8")
    rows = list(csv.reader(io.StringIO(content)))

    assert len(rows) == 2
    data = rows[1]
    assert data[4] == "25.00"
