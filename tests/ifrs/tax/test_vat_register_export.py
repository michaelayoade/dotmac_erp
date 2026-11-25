"""VAT register exports use the screen query and include every matching page."""

import csv
import io
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import html5lib
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.finance.tax.tax_code import TaxCode
from app.models.finance.tax.tax_transaction import TaxTransaction, TaxTransactionType
from app.services.finance.tax.tax_transaction import TaxTransactionService
from app.services.finance.tax.web import TaxWebService


@pytest.fixture
def register():
    """Small real database with other tenants, types, dates and tax codes."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    metadata = MetaData()
    with engine.connect() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS tax")
    tables = {}
    for model in (TaxCode, TaxTransaction):
        original = model.__table__
        tables[model] = Table(
            original.name,
            metadata,
            *(Column(c.name, c.type) for c in original.columns),
            schema="tax",
        )
    metadata.create_all(engine)
    org, other_org, code, other_code = (uuid4() for _ in range(4))
    with Session(engine) as db:
        db.info["organization_id"] = org
        db.execute(
            tables[TaxCode].insert(),
            [
                {
                    "tax_code_id": code,
                    "organization_id": org,
                    "tax_code": "WHT",
                    "tax_name": "Withholding",
                    "tax_rate": Decimal("0.02"),
                },
                {
                    "tax_code_id": other_code,
                    "organization_id": org,
                    "tax_code": "OTHER",
                    "tax_name": "Other code",
                    "tax_rate": Decimal("0.05"),
                },
            ],
        )
        transactions = []
        for index in range(80):
            transactions.append(
                {
                    "transaction_id": uuid4(),
                    "organization_id": org,
                    "tax_code_id": code,
                    "transaction_type": "WITHHOLDING",
                    "transaction_date": date(2025, 1, 1)
                    if index == 0
                    else date(2026, 11, 25),
                    "source_document_type": "AP_INVOICE",
                    "source_document_id": uuid4(),
                    "source_document_reference": f"INV-{index}",
                    "counterparty_name": 'Supplier, "A"',
                    "currency_code": "NGN",
                    "base_amount": Decimal("1000.123456"),
                    "tax_amount": Decimal("20.002469"),
                    "functional_tax_amount": Decimal("20.002469"),
                }
            )
        for override in [
            {"organization_id": other_org},
            {"transaction_date": date(2024, 12, 31)},
            {"transaction_date": date(2026, 11, 26)},
            {"transaction_type": "OUTPUT"},
            {"tax_code_id": other_code},
        ]:
            transactions.append(
                {**transactions[0], "transaction_id": uuid4(), **override}
            )
        db.execute(tables[TaxTransaction].insert(), transactions)
        db.commit()
        yield db, org, code
    engine.dispose()


def test_export_includes_all_filtered_pages_and_date_boundaries(register):
    db, org, code = register
    filters = dict(
        organization_id=str(org),
        start_date=date(2025, 1, 1),
        end_date=date(2026, 11, 25),
        transaction_type=TaxTransactionType.WITHHOLDING,
        tax_code_id=str(code),
    )
    page, total = TaxTransactionService.get_vat_register(db, **filters)
    assert len(page) == 50
    assert total == 80
    content = TaxTransactionService.export_vat_register_csv(db, **filters)
    rows = list(csv.DictReader(io.StringIO(content)))
    assert len(rows) == total
    assert len({r["Transaction ID"] for r in rows}) == total
    assert {r["Date"] for r in rows} == {"2025-01-01", "2026-11-25"}
    assert {r["Type"] for r in rows} == {"WITHHOLDING"}
    assert {r["Tax Code"] for r in rows} == {"WHT"}
    assert rows[0]["Counterparty"] == 'Supplier, "A"'
    assert rows[0]["Base Amount"] == "1000.123456"


@pytest.mark.parametrize("dates", [(None, None), ("", "")])
def test_no_filters_export_all_records_in_current_organization(register, dates):
    db, org, _ = register
    content = TaxWebService.export_vat_register_csv(
        db,
        str(org),
        start_date=dates[0],
        end_date=dates[1],
    )
    rows = list(csv.DictReader(io.StringIO(content)))
    assert len(rows) == 84
    assert {r["Type"] for r in rows} == {"WITHHOLDING", "OUTPUT"}
    assert "2024-12-31" in {r["Date"] for r in rows}
    context = TaxWebService.vat_register_context(db, str(org), None, None)
    assert context["total_count"] == len(rows)
    assert context["start_date"] == context["end_date"] == ""


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"start_date": "2026-11-25"}, 80),
        ({"end_date": "2025-01-01"}, 4),
        ({"transaction_type": "WITHHOLDING"}, 83),
    ],
)
def test_partial_filters(register, filters, expected):
    db, org, _ = register
    content = TaxWebService.export_vat_register_csv(db, str(org), **filters)
    assert len(list(csv.DictReader(io.StringIO(content)))) == expected


@pytest.mark.parametrize(
    "filters",
    [
        {"start_date": "bad"},
        {"end_date": "2026-02-30"},
        {"start_date": "2026-11-25", "end_date": "2025-01-01"},
        {"transaction_type": "INVALID"},
        {"tax_code_id": "bad"},
    ],
)
def test_invalid_filters_are_rejected(register, filters):
    db, org, _ = register
    with pytest.raises(HTTPException) as exc:
        TaxWebService.export_vat_register_csv(db, str(org), **filters)
    assert exc.value.status_code == 422


def test_empty_export_still_contains_headers(register):
    db, org, _ = register
    content = TaxWebService.export_vat_register_csv(db, str(org), end_date="2020-01-01")
    reader = csv.DictReader(io.StringIO(content))
    assert reader.fieldnames[:7] == [
        "Date",
        "Type",
        "Reference",
        "Counterparty",
        "Base Amount",
        "Rate",
        "Tax Amount",
    ]
    assert list(reader) == []


def test_export_escapes_spreadsheet_formulas(register):
    db, org, code = register
    db.execute(
        TaxTransaction.__table__.update()
        .where(
            TaxTransaction.organization_id == org,
        )
        .values(counterparty_name="=1+1", source_document_reference="@SUM(A1)")
    )
    rows = list(
        csv.DictReader(io.StringIO(TaxWebService.export_vat_register_csv(db, str(org))))
    )
    assert rows[0]["Counterparty"] == "'=1+1"
    assert rows[0]["Reference"] == "'@SUM(A1)"
    assert rows[0]["Tax Amount"] == "20.002469"


def test_export_route_uses_finance_auth_and_download_response(register):
    from app.web.deps import get_db_for_org, require_finance_access
    from app.web.finance.tax import router

    db, org, _ = register
    app = FastAPI()
    app.include_router(router, prefix="/finance")
    app.dependency_overrides[get_db_for_org] = lambda: db
    app.dependency_overrides[require_finance_access] = lambda: SimpleNamespace(
        organization_id=org
    )
    with TestClient(app) as client:
        response = client.get(
            "/finance/tax/vat-register/export?transaction_type=WITHHOLDING&page=2"
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="vat_register.csv"'
    )
    assert len(list(csv.DictReader(io.StringIO(response.text)))) == 83


@pytest.mark.parametrize(
    "start,end,txn_type,code",
    [
        ("2025-01-01", "2026-11-25", "WITHHOLDING", str(uuid4())),
        ("", "", "", ""),
    ],
)
def test_export_component_preserves_filters_without_pagination(
    start, end, txn_type, code
):
    from app.templates import templates

    template = templates.env.get_template("finance/tax/vat_register.html")
    context = template.new_context(
        dict(
            start_date=start,
            end_date=end,
            transaction_type=txn_type,
            tax_code_id=code,
        )
    )
    macros = templates.env.get_template("components/macros.html").module
    context.vars.update(export_menu=macros.export_menu, icon_svg=macros.icon_svg)
    html = "".join(template.blocks["header_actions"](context))
    document = html5lib.parseFragment(html, namespaceHTMLElements=False)
    link = next(
        a
        for a in document.iter("a")
        if "/vat-register/export" in a.attrib.get("href", "")
    )
    query = parse_qs(urlsplit(link.attrib["href"]).query, keep_blank_values=True)
    assert query == dict(
        format=["csv"],
        start_date=[start],
        end_date=[end],
        transaction_type=[txn_type],
        tax_code_id=[code],
    )
    assert "page" not in query


def test_export_route_requires_finance_access(register):
    from app.web.deps import get_db_for_org, require_finance_access
    from app.web.finance.tax import router

    db, _, _ = register
    app = FastAPI()
    app.include_router(router, prefix="/finance")
    app.dependency_overrides[get_db_for_org] = lambda: db

    def deny_access():
        raise HTTPException(status_code=403, detail="Finance access required")

    app.dependency_overrides[require_finance_access] = deny_access
    with TestClient(app) as client:
        response = client.get("/finance/tax/vat-register/export")
    assert response.status_code == 403
