"""Trial balance export filter disclosure regressions."""

from __future__ import annotations

import csv
import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.rpt.pdf import _get_template_env
from app.services.finance.rpt.trial_balance import export_trial_balance_csv
from app.services.finance.rpt.web import reports_web_service
from app.web.finance.reports import export_trial_balance


@pytest.mark.parametrize(
    ("is_cash_basis", "expected_basis"),
    [(False, "Accrual"), (True, "Cash")],
)
def test_csv_discloses_as_of_date_and_basis(
    is_cash_basis: bool, expected_basis: str
) -> None:
    context = {
        "as_of_date_iso": "2026-01-31",
        "is_cash_basis": is_cash_basis,
        "assets": [
            {
                "account_code": "1100",
                "account_name": "Office Equipment",
                "debit_raw": 100.0,
                "credit_raw": 0.0,
            }
        ],
        "liabilities": [],
        "equity": [],
        "revenue": [],
        "expenses": [],
        "total_debit": "NGN 100.00",
        "total_credit": "NGN 100.00",
    }
    with patch(
        "app.services.finance.rpt.trial_balance.trial_balance_context",
        return_value=context,
    ):
        content = export_trial_balance_csv(
            "organization-id",
            MagicMock(),
            "2026-01-31",
            basis=expected_basis.lower(),
        )

    rows = list(csv.reader(io.StringIO(content)))
    assert rows[0][:2] == ["As Of Date", "Basis"]
    assert rows[1][:2] == ["2026-01-31", expected_basis]
    assert rows[-1][:2] == ["2026-01-31", expected_basis]


@pytest.mark.parametrize(
    ("basis", "expected"),
    [("accrual", "Basis: Accrual"), ("cash", "Basis: Cash")],
)
def test_pdf_discloses_basis(basis: str, expected: str) -> None:
    template = _get_template_env().get_template(
        "finance/reports/trial_balance_pdf.html"
    )
    html = template.render(
        org_name="Dotmac",
        as_of_date="31/01/2026",
        period_name="January 2026",
        is_balanced=True,
        is_cash_basis=basis == "cash",
        assets=[],
        liabilities=[],
        equity=[],
        revenue=[],
        expenses=[],
        total_debit="NGN 0.00",
        total_credit="NGN 0.00",
    )
    assert expected in html


@pytest.mark.parametrize(
    ("fmt", "basis", "expected_filename"),
    [
        ("csv", "accrual", "trial_balance_2026-01-31.csv"),
        ("pdf", "cash", "trial_balance_2026-01-31_cash_basis.pdf"),
    ],
)
def test_download_filename_discloses_filter_date(
    fmt: str, basis: str, expected_filename: str
) -> None:
    export_method = (
        "export_trial_balance_pdf" if fmt == "pdf" else "export_trial_balance_csv"
    )
    content = b"%PDF" if fmt == "pdf" else "csv"
    with patch.object(reports_web_service, export_method, return_value=content):
        response = export_trial_balance(
            as_of_date="2026-01-31",
            basis=basis,
            fmt=fmt,
            auth=SimpleNamespace(organization_id=uuid4()),
            db=MagicMock(),
        )
    assert expected_filename in response.headers["content-disposition"]
