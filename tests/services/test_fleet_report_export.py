from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.services.fleet.report_export import (
    _render_pdf_html,
    _report_data,
    vehicle_report_export_response,
)


@pytest.mark.parametrize(
    ("kind", "row_key", "number_key", "date_key", "sheet_name"),
    [
        ("expense", "claim_rows", "claim_number", "claim_date", "Claims"),
        ("invoice", "invoice_rows", "invoice_number", "invoice_date", "Invoices"),
    ],
)
def test_vehicle_report_excel_includes_all_filtered_rows(
    kind, row_key, number_key, date_key, sheet_name
):
    vehicle = SimpleNamespace(vehicle_id=uuid4(), registration_number="ABC-123")
    rows = [
        {
            number_key: f"{kind}-{index}",
            date_key: date(2026, 4, 1),
            "status": "paid",
            "total_amount": Decimal("1.25"),
        }
        for index in range(205)
    ]
    rows[0][number_key] = "=unsafe()"
    context = {
        "vehicle": vehicle,
        "vehicle_name": "Hilux",
        "assigned_to": "Alex",
        "filter_start_date": "2026-04-01",
        "filter_end_date": "2026-04-30",
        "filter_year": "",
        "filter_month": "",
        "claim_count" if kind == "expense" else "invoice_count": 205,
        "total_amount": Decimal("256.25"),
        "category_rows": [{"category_name": "Fuel", "total_amount": Decimal("256.25")}],
        row_key: rows,
    }

    response = vehicle_report_export_response(context, kind, "xlsx")

    assert response.headers["content-disposition"] == (
        f'attachment; filename="fleet-{kind}-report-ABC-123.xlsx"'
    )
    workbook = load_workbook(BytesIO(response.body), read_only=True)
    assert workbook.sheetnames == ["Summary", "Breakdown", sheet_name]
    exported_rows = list(workbook[sheet_name].values)
    assert len(exported_rows) == 206
    assert exported_rows[1][0] == "'=unsafe()"
    assert exported_rows[-1][0] == f"{kind}-204"
    assert list(workbook["Summary"].values)[5] == ("Start Date", "2026-04-01")


def test_vehicle_report_pdf_response(monkeypatch):
    monkeypatch.setattr(
        "app.services.fleet.report_export._build_pdf", lambda data: b"%PDF-test"
    )
    context = {
        "vehicle": SimpleNamespace(vehicle_id=uuid4(), registration_number="ABC-123"),
        "invoice_rows": [],
        "invoice_count": 0,
    }

    response = vehicle_report_export_response(context, "invoice", "pdf")

    assert response.body == b"%PDF-test"
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].endswith('ABC-123.pdf"')


def test_vehicle_report_pdf_html_contains_all_rows_and_escapes_values():
    context = {
        "vehicle": SimpleNamespace(vehicle_id=uuid4(), registration_number="ABC-123"),
        "vehicle_name": "Hilux",
        "assigned_to": "Alex",
        "filter_start_date": "",
        "filter_end_date": "",
        "filter_year": "",
        "filter_month": "",
        "claim_count": 205,
        "total_amount": Decimal("205.00"),
        "category_rows": [],
        "claim_rows": [
            {
                "claim_number": f"Claim-{index}",
                "claim_date": date(2026, 4, 1),
                "status": "paid",
                "total_amount": Decimal("1.00"),
            }
            for index in range(205)
        ],
    }
    context["claim_rows"][0]["claim_number"] = "<script>bad</script>"

    html = _render_pdf_html(_report_data(context, "expense"))

    assert "Claim-204" in html
    assert "&lt;script&gt;bad&lt;/script&gt;" in html
    assert "<script>bad</script>" not in html
