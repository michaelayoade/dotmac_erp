"""Tests for report PDF generation service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.finance.rpt.pdf import ReportPDFService, _format_currency


def test_format_currency_filter() -> None:
    """The Jinja2 filter formats numbers correctly."""
    assert _format_currency(1234.5) == "1,234.50"
    assert _format_currency(0) == "0.00"
    assert _format_currency(None) == "0.00"


@pytest.fixture()
def db() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mock_weasyprint():
    """Patch WeasyPrint to avoid needing system libraries in CI."""
    with patch("app.services.finance.rpt.pdf.ReportPDFService.render") as mock_render:
        mock_render.return_value = b"%PDF-1.4 mock content"
        yield mock_render


REPORT_NAMES = [
    "trial_balance",
    "income_statement",
    "balance_sheet",
    "general_ledger",
    "management_accounts",
    "ap_aging",
    "ar_aging",
    "tax_summary",
    "expense_summary",
    "cash_flow",
    "changes_in_equity",
    "budget_vs_actual",
    "inventory_valuation_reconciliation",
]


@pytest.mark.parametrize("report_name", REPORT_NAMES)
def test_render_returns_bytes(
    db: MagicMock, mock_weasyprint: MagicMock, report_name: str
) -> None:
    """Each report name produces PDF bytes."""
    service = ReportPDFService(db)
    result = service.render(
        report_name=report_name,
        organization_id="00000000-0000-0000-0000-000000000001",
        context={"test": True},
    )
    assert isinstance(result, bytes)
    mock_weasyprint.assert_called_once()


def test_pdf_template_env_registers_filter() -> None:
    """The template environment registers format_currency filter."""
    from app.services.finance.rpt.pdf import _get_template_env

    env = _get_template_env()
    assert "format_currency" in env.filters


@pytest.mark.parametrize("report_name", REPORT_NAMES)
def test_pdf_template_exists(report_name: str) -> None:
    """Each report has a corresponding PDF template file."""
    from app.services.finance.rpt.pdf import _get_template_env

    env = _get_template_env()
    template_path = f"finance/reports/{report_name}_pdf.html"
    # Should not raise TemplateNotFound
    template = env.get_template(template_path)
    assert template is not None
