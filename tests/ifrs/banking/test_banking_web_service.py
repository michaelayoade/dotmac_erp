import json
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from openpyxl import Workbook
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.requests import Request

from app.schemas.finance.banking import BankStatementImport
from app.services.finance.banking.web import BankingWebService
from app.web.deps import WebAuthContext


def _request_with_form(path: str, form: dict) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
        }
    )
    request.state.csrf_form = form
    return request


def _xlsx_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buf = BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def test_rule_duplicate_form_context_not_found(mock_db):
    service = BankingWebService()
    mock_db.get.return_value = None

    with pytest.raises(HTTPException) as excinfo:
        service.rule_duplicate_form_context(mock_db, str(uuid4()), str(uuid4()))

    assert excinfo.value.status_code == 404


def test_duplicate_rule_response_redirects_with_copy_count(mock_db):
    service = BankingWebService()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
    )
    request = _request_with_form(
        "/finance/banking/rules/test/duplicate",
        {},
    )

    with patch(
        "app.services.finance.banking.categorization."
        "TransactionCategorizationService.duplicate_rule_to_accounts",
        return_value=[SimpleNamespace(), SimpleNamespace()],
    ) as mock_duplicate:
        response = service.duplicate_rule_response(
            request=request,
            auth=auth,
            db=mock_db,
            rule_id=str(uuid4()),
            bank_account_ids=[str(uuid4()), str(uuid4())],
            include_global=False,
        )

    assert response.status_code == 303
    assert "Rule+duplicated+(2+copy)" in response.headers["location"]
    assert mock_duplicate.called
    assert mock_db.flush.call_count >= 1


def test_bulk_rule_duplicate_form_context_requires_rule_ids(mock_db):
    service = BankingWebService()

    with pytest.raises(ValueError):
        service.bulk_rule_duplicate_form_context(mock_db, str(uuid4()), [])


def test_bulk_duplicate_rules_response_redirects_with_total_count(mock_db):
    service = BankingWebService()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
    )
    request = _request_with_form(
        "/finance/banking/rules/duplicate/bulk",
        {},
    )

    with patch(
        "app.services.finance.banking.categorization."
        "TransactionCategorizationService.duplicate_rule_to_accounts",
        side_effect=[
            [SimpleNamespace(), SimpleNamespace()],
            [SimpleNamespace()],
        ],
    ) as mock_duplicate:
        response = service.bulk_duplicate_rules_response(
            request=request,
            auth=auth,
            db=mock_db,
            rule_ids=[str(uuid4()), str(uuid4())],
            bank_account_ids=[str(uuid4())],
            include_global=False,
        )

    assert response.status_code == 303
    assert "Rules+duplicated+(3+copies)" in response.headers["location"]
    assert mock_duplicate.call_count == 2
    assert mock_db.flush.call_count >= 1


@pytest.mark.asyncio
async def test_statement_import_preview_response_for_xlsx(mock_db):
    service = BankingWebService()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
    )
    content = _xlsx_bytes(
        headers=["transaction_date", "transaction_type", "amount", "description"],
        rows=[["2026-02-01", "credit", 1200.50, "Client payment"]],
    )
    upload = UploadFile(filename="statement.xlsx", file=BytesIO(content))
    request = _request_with_form(
        "/finance/banking/statements/import/preview",
        {"statement_file": upload},
    )

    response = await service.statement_import_preview_response(request, auth, mock_db)

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["detected_columns"] == [
        "transaction_date",
        "transaction_type",
        "amount",
        "description",
    ]
    assert payload["sample_data"][0]["transaction_date"] == "2026-02-01"
    assert payload["sample_data"][0]["transaction_type"] == "credit"
    assert payload["total_rows"] == 1


@pytest.mark.asyncio
async def test_statement_import_submit_prefers_uploaded_file_over_manual_lines(
    mock_db, monkeypatch
):
    service = BankingWebService()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
    )

    csv_content = (
        b"transaction_date,transaction_type,amount,description\n"
        b"2026-02-01,credit,100.00,from-file\n"
    )
    upload = UploadFile(filename="statement.csv", file=BytesIO(csv_content))
    form = {
        "bank_account_id": str(uuid4()),
        "statement_number": "ST-1001",
        "statement_date": "2026-02-01",
        "period_start": "2026-02-01",
        "period_end": "2026-02-28",
        "opening_balance": "0.00",
        "closing_balance": "200.00",
        "lines[0][line_number]": "1",
        "lines[0][transaction_date]": "2026-02-10",
        "lines[0][transaction_type]": "credit",
        "lines[0][amount]": "200.00",
        "lines[0][description]": "from-mapping",
        "statement_file": upload,
    }
    request = _request_with_form("/finance/banking/statements/import", form)

    import app.services.finance.banking.web as banking_web_module

    captured = {}

    def _fake_import_statement(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            statement=SimpleNamespace(statement_id=uuid4()),
            lines_imported=0,
            auto_matched=0,
        )

    def _fake_parse_csv_rows(content, csv_format=None, date_format=None):
        _ = (content, csv_format, date_format)
        return (
            [
                {
                    "line_number": 1,
                    "transaction_date": "2026-02-01",
                    "transaction_type": "credit",
                    "amount": "100.00",
                    "description": "from-file",
                }
            ],
            [],
        )

    monkeypatch.setattr(
        banking_web_module.bank_statement_service,
        "parse_csv_rows",
        _fake_parse_csv_rows,
    )
    monkeypatch.setattr(
        banking_web_module.bank_statement_service,
        "import_statement",
        _fake_import_statement,
    )

    response = await service.statement_import_submit_response(request, auth, mock_db)

    assert response.status_code == 303
    assert "/finance/banking/statements/" in response.headers["location"]
    assert captured["import_source"] == "csv"
    assert len(captured["lines"]) == 1
    assert captured["lines"][0].description == "from-file"


@pytest.mark.asyncio
async def test_statement_import_submit_normalizes_mapped_dates_and_decimals(
    mock_db, monkeypatch
):
    service = BankingWebService()
    org_id = uuid4()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=org_id,
    )
    mock_db.get.return_value = SimpleNamespace(
        date_format="DD/MM/YYYY",
        number_format="1,234.56",
        decimal_places=2,
        currency_code="USD",
        timezone="UTC",
    )

    form = {
        "bank_account_id": str(uuid4()),
        "statement_number": "ST-1002",
        "statement_date": "2026-02-01",
        "period_start": "2026-02-01",
        "period_end": "2026-02-28",
        "opening_balance": "0.00",
        "closing_balance": "1200.50",
        "lines[0][line_number]": "1",
        "lines[0][transaction_date]": "10/02/2026",
        "lines[0][value_date]": "10/02/2026",
        "lines[0][transaction_type]": "CREDIT",
        "lines[0][amount]": "1,200.50",
        "lines[0][running_balance]": "1,200.50",
        "lines[0][description]": "mapped-values",
        "lines[1][line_number]": "2",
        "lines[1][transaction_date]": "11/02/2026",
        "lines[1][value_date]": "11/02/2026",
        "lines[1][transaction_type]": "",
        "lines[1][amount]": "",
        "lines[1][credit]": "200.75",
        "lines[1][running_balance]": "1,401.25",
        "lines[1][description]": "credit-only",
    }
    request = _request_with_form("/finance/banking/statements/import", form)

    captured = {}

    def _fake_import_statement(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            statement=SimpleNamespace(statement_id=uuid4()),
            lines_imported=0,
            auto_matched=0,
        )

    import app.services.finance.banking.web as banking_web_module

    monkeypatch.setattr(
        banking_web_module.bank_statement_service,
        "import_statement",
        _fake_import_statement,
    )

    response = await service.statement_import_submit_response(request, auth, mock_db)

    assert response.status_code == 303
    assert len(captured["lines"]) == 2
    first = captured["lines"][0]
    second = captured["lines"][1]
    assert first.transaction_date.isoformat() == "2026-02-10"
    assert first.value_date.isoformat() == "2026-02-10"
    assert first.transaction_type.value == "credit"
    assert first.amount == Decimal("1200.50")
    assert first.running_balance == Decimal("1200.50")
    assert second.transaction_date.isoformat() == "2026-02-11"
    assert second.value_date.isoformat() == "2026-02-11"
    assert second.transaction_type.value == "credit"
    assert second.amount == Decimal("200.75")
    assert second.running_balance == Decimal("1401.25")


@pytest.mark.asyncio
async def test_statement_import_submit_uses_column_mapping_for_all_uploaded_rows(
    mock_db, monkeypatch
):
    service = BankingWebService()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
    )

    csv_content = (
        b"Txn Date,Txn Type,Txn Amount,Narration\n"
        b"2026-02-01,credit,10.00,row-1\n"
        b"2026-02-02,credit,20.00,row-2\n"
        b"2026-02-03,credit,30.00,row-3\n"
        b"2026-02-04,credit,40.00,row-4\n"
        b"2026-02-05,credit,50.00,row-5\n"
        b"2026-02-06,credit,60.00,row-6\n"
        b"2026-02-07,credit,70.00,row-7\n"
    )
    upload = UploadFile(filename="statement.csv", file=BytesIO(csv_content))
    form = {
        "bank_account_id": str(uuid4()),
        "statement_number": "ST-1003",
        "statement_date": "2026-02-01",
        "period_start": "2026-02-01",
        "period_end": "2026-02-28",
        "opening_balance": "0.00",
        "closing_balance": "280.00",
        "column_map[Txn Date]": "transaction_date",
        "column_map[Txn Type]": "transaction_type",
        "column_map[Txn Amount]": "amount",
        "column_map[Narration]": "description",
        "statement_file": upload,
    }
    request = _request_with_form("/finance/banking/statements/import", form)

    import app.services.finance.banking.web as banking_web_module

    def _fail_parse(*_args, **_kwargs):
        raise AssertionError(
            "Format parser should not run when column mapping is provided."
        )

    captured = {}

    def _fake_import_statement(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            statement=SimpleNamespace(statement_id=uuid4()),
            lines_imported=0,
            auto_matched=0,
        )

    monkeypatch.setattr(
        banking_web_module.bank_statement_service, "parse_csv_rows", _fail_parse
    )
    monkeypatch.setattr(
        banking_web_module.bank_statement_service,
        "import_statement",
        _fake_import_statement,
    )

    response = await service.statement_import_submit_response(request, auth, mock_db)

    assert response.status_code == 303
    assert len(captured["lines"]) == 7
    assert captured["lines"][0].description == "row-1"
    assert captured["lines"][6].description == "row-7"


@pytest.mark.asyncio
async def test_statement_import_submit_column_mapping_handles_trimmed_csv_headers(
    mock_db, monkeypatch
):
    service = BankingWebService()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
    )

    csv_content = b"Txn Date ,Txn Type,Txn Amount\n2026-02-01,credit,10.00\n"
    upload = UploadFile(filename="statement.csv", file=BytesIO(csv_content))
    form = {
        "bank_account_id": str(uuid4()),
        "statement_number": "ST-1004",
        "statement_date": "2026-02-01",
        "period_start": "2026-02-01",
        "period_end": "2026-02-28",
        "column_map[Txn Date]": "transaction_date",
        "column_map[Txn Type]": "transaction_type",
        "column_map[Txn Amount]": "amount",
        "statement_file": upload,
    }
    request = _request_with_form("/finance/banking/statements/import", form)

    captured = {}

    def _fake_import_statement(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            statement=SimpleNamespace(statement_id=uuid4()),
            lines_imported=0,
            auto_matched=0,
        )

    import app.services.finance.banking.web as banking_web_module

    monkeypatch.setattr(
        banking_web_module.bank_statement_service,
        "import_statement",
        _fake_import_statement,
    )

    response = await service.statement_import_submit_response(request, auth, mock_db)

    assert response.status_code == 303
    assert len(captured["lines"]) == 1
    assert captured["lines"][0].transaction_date.isoformat() == "2026-02-01"


def test_parse_column_map_filters_empty_values():
    mapping = BankingWebService._parse_column_map(
        {
            "column_map[Txn Date]": "transaction_date",
            "column_map[Amount]": "  ",
            "column_map[Description]": "description",
            "other_field": "ignored",
        }
    )
    assert mapping == {
        "Txn Date": "transaction_date",
        "Description": "description",
    }


def test_map_rows_with_column_map_skips_empty_rows():
    rows = BankingWebService._map_rows_with_column_map(
        source_rows=[
            {"Txn Date": "2026-02-01", "Amount": "10.00"},
            {"Txn Date": "", "Amount": ""},
            {"Txn Date": "2026-02-03", "Amount": "30.00"},
        ],
        column_map={"Txn Date": "transaction_date", "Amount": "amount"},
    )
    assert len(rows) == 2
    assert rows[0]["line_number"] == 1
    assert rows[0]["transaction_date"] == "2026-02-01"
    assert rows[1]["line_number"] == 3
    assert rows[1]["amount"] == "30.00"


def test_parse_manual_lines_handles_empty_and_populated_rows():
    parsed_rows, errors = BankingWebService._parse_manual_lines(
        {
            "lines[0][transaction_date]": "",
            "lines[0][amount]": "",
            "lines[1][line_number]": "2",
            "lines[1][transaction_date]": "2026-02-01",
            "lines[1][transaction_type]": "credit",
            "lines[1][amount]": "10.00",
            "lines[1][description]": "ok",
        }
    )
    assert errors == []
    assert len(parsed_rows) == 1
    assert parsed_rows[0]["line_number"] == 2
    assert parsed_rows[0]["description"] == "ok"


def test_normalize_mapped_lines_parses_dates_decimals_and_type():
    normalized = BankingWebService._normalize_mapped_lines(
        [
            {
                "line_number": 1,
                "transaction_date": "10/02/2026",
                "value_date": "10/02/2026",
                "transaction_type": " CREDIT ",
                "amount": "1,200.50",
                "running_balance": "abc",
            }
        ],
        org_date_fmt="%d/%m/%Y",
    )
    row = normalized[0]
    assert row["transaction_date"].isoformat() == "2026-02-10"
    assert row["value_date"].isoformat() == "2026-02-10"
    assert row["transaction_type"] == "credit"
    assert row["amount"] == Decimal("1200.50")
    # Invalid decimal is left as-is for schema layer to report.
    assert row["running_balance"] == "abc"


def test_resolve_org_date_format_returns_mapped_format(mock_db):
    org_id = uuid4()
    mock_db.get.return_value = SimpleNamespace(date_format="DD/MM/YYYY")
    resolved = BankingWebService._resolve_org_date_format(mock_db, org_id)
    assert resolved == "%d/%m/%Y"


def test_resolve_org_date_format_returns_none_for_missing_or_unknown(mock_db):
    assert BankingWebService._resolve_org_date_format(mock_db, None) is None
    mock_db.get.return_value = SimpleNamespace(date_format="UNKNOWN")
    assert BankingWebService._resolve_org_date_format(mock_db, uuid4()) is None
    mock_db.get.return_value = None
    assert BankingWebService._resolve_org_date_format(mock_db, uuid4()) is None


def test_format_validation_errors_includes_locators():
    with pytest.raises(ValidationError) as exc_info:
        BankStatementImport.model_validate(
            {
                "bank_account_id": str(uuid4()),
                "period_start": "2026-02-01",
                "period_end": "2026-02-28",
                "lines": [
                    {
                        "line_number": 1,
                        "transaction_date": "",
                        "transaction_type": "credit",
                        "amount": "bad-decimal",
                    }
                ],
            }
        )
    errors = BankingWebService._format_validation_errors(exc_info.value)
    assert any("lines -> 0 -> transaction_date" in err for err in errors)


def test_preview_upload_content_rejects_unsupported_extension():
    with pytest.raises(ValueError, match="Supported statement files"):
        BankingWebService._preview_upload_content(b"a,b\n1,2\n", "statement.txt")


# ── Auto-Match Response Tests ────────────────────────────────────────


def test_statement_auto_match_response_matched(mock_db, monkeypatch):
    """Redirects with success when lines are matched."""
    from unittest.mock import patch

    service = BankingWebService()
    stmt_id = uuid4()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
    )
    request = _request_with_form(
        f"/finance/banking/statements/{stmt_id}/auto-match", {}
    )

    with patch(
        "app.services.finance.banking.auto_reconciliation.AutoReconciliationService"
    ) as mock_cls:
        mock_svc = mock_cls.return_value
        mock_svc.auto_match_statement.return_value = SimpleNamespace(
            matched=5, skipped=2, errors=[]
        )
        response = service.statement_auto_match_response(
            request, auth, mock_db, str(stmt_id)
        )

    assert response.status_code == 303
    assert "success" in response.headers["location"]
    assert "5" in response.headers["location"]
    assert mock_db.flush.call_count >= 1


def test_statement_auto_match_response_no_matches(mock_db, monkeypatch):
    """Redirects with info when no matches found."""
    from unittest.mock import patch

    service = BankingWebService()
    stmt_id = uuid4()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
    )
    request = _request_with_form(
        f"/finance/banking/statements/{stmt_id}/auto-match", {}
    )

    with patch(
        "app.services.finance.banking.auto_reconciliation.AutoReconciliationService"
    ) as mock_cls:
        mock_svc = mock_cls.return_value
        mock_svc.auto_match_statement.return_value = SimpleNamespace(
            matched=0, skipped=10, errors=[]
        )
        response = service.statement_auto_match_response(
            request, auth, mock_db, str(stmt_id)
        )

    assert response.status_code == 303
    assert "info" in response.headers["location"]
    assert "No+new+matches" in response.headers["location"]
    mock_db.commit.assert_not_called()


def test_statement_auto_match_response_with_errors(mock_db, monkeypatch):
    """Redirects with error when auto-match encounters errors."""
    from unittest.mock import patch

    service = BankingWebService()
    stmt_id = uuid4()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
    )
    request = _request_with_form(
        f"/finance/banking/statements/{stmt_id}/auto-match", {}
    )

    with patch(
        "app.services.finance.banking.auto_reconciliation.AutoReconciliationService"
    ) as mock_cls:
        mock_svc = mock_cls.return_value
        mock_svc.auto_match_statement.return_value = SimpleNamespace(
            matched=0, skipped=0, errors=["Line 3: DB error"]
        )
        response = service.statement_auto_match_response(
            request, auth, mock_db, str(stmt_id)
        )

    assert response.status_code == 303
    assert "error" in response.headers["location"]
