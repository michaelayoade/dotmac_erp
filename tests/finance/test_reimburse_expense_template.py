"""Regression checks for the expense-reimbursement response contract."""

from pathlib import Path


TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "finance"
    / "payments"
    / "reimburse_expense.html"
)


def test_reimbursement_fetches_handle_empty_or_non_json_responses() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "async function readJsonResponse" in source
    assert "HTTP ${response.status}: empty server response" in source
    assert "HTTP ${response.status}: invalid server response" in source
    assert ".json()" not in source


def test_reimbursement_errors_accept_standard_api_message_shape() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "error.message || error.detail" in source
