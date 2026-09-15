"""Rendered navigation and HTTP/service regressions for the PR 578 repair."""

from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader
from starlette.datastructures import QueryParams


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.targets = []
        self.links = []
        self.options = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"])
        if attributes.get("hx-target"):
            self.targets.append(attributes["hx-target"])
        if tag == "a" and attributes.get("aria-label", "").endswith("page"):
            self.links.append(attributes["href"])
        if tag == "option" and attributes.get("value", "").startswith("?page="):
            self.options.append(attributes["value"])


def render_list(name, **overrides):
    from app.templates import templates

    shells = {
        f"{module}/base_{module}.html": "{% block content %}{% endblock %}"
        for module in ("inventory", "procurement", "expense")
    }
    env = templates.env.overlay(
        loader=ChoiceLoader([DictLoader(shells), templates.env.loader])
    )
    context = {
        "request": SimpleNamespace(
            query_params=QueryParams(), state=SimpleNamespace(csrf_form="")
        ),
        "transactions": [],
        "transaction_types": ["RECEIPT", "ISSUE"],
        "transaction_type": "RECEIPT",
        "evaluations": [],
        "contracts": [],
        "claims": [
            SimpleNamespace(
                claim_id=uuid4(),
                claim_number="EXP-001",
                purpose="Equipment delivery",
                employee=None,
                claim_date=None,
                total_claimed_amount=2500,
                currency_code="NGN",
                status=SimpleNamespace(value="DRAFT"),
            )
        ],
        "claim_approver_names": {},
        "active_filters": [],
        "status_labels": {},
        "statuses": [],
        "filter_status": "DRAFT",
        "filter_view": "all",
        "filter_start_date": "",
        "filter_end_date": "",
        "filter_employee_id": "",
        "filter_approver_id": "",
        "search": "cable & fibre",
        "page": 2,
        "limit": 25,
        "total": 75,
        "total_pages": 3,
        "total_count": 75,
    }
    context.update(overrides)
    html = env.get_template(name).render(**context)
    parser = NavigationParser()
    parser.feed(html)
    return html, parser


@pytest.mark.parametrize("filtered", [True, False])
def test_inventory_filters_always_have_a_real_swap_target(filtered):
    html, parsed = render_list(
        "inventory/transactions.html",
        search="cable" if filtered else "",
        transaction_type="RECEIPT" if filtered else "",
        total_count=0,
    )
    assert parsed.ids.count("results-container") == 1
    assert parsed.targets
    assert all(target[1:] in parsed.ids for target in parsed.targets)
    assert (
        "No transactions found" in html if filtered else "No transactions yet" in html
    )


@pytest.mark.parametrize(
    "name,filter_name,filter_value,sizes",
    [
        ("procurement/evaluations/list.html", "status", "DRAFT", [10, 25, 50, 100]),
        ("procurement/contracts/list.html", "status", "DRAFT", [10, 25, 50, 100]),
        ("expense/claims_list.html", "status", "DRAFT", [10, 25, 50, 100]),
        (
            "inventory/transactions.html",
            "transaction_type",
            "RECEIPT",
            [10, 25, 50, 100, 200],
        ),
    ],
)
def test_rendered_pagers_preserve_filters_search_and_supported_page_sizes(
    name, filter_name, filter_value, sizes
):
    _, parsed = render_list(name)
    assert len(parsed.links) == 2
    for url in parsed.links + parsed.options:
        query = parse_qs(urlsplit(url).query)
        assert query[filter_name] == [filter_value]
        assert query["search"] == ["cable & fibre"]
    assert [
        int(parse_qs(urlsplit(url).query)["limit"][0]) for url in parsed.options
    ] == sizes
    for url in parsed.options:
        assert parse_qs(urlsplit(url).query)["page"] == ["1"]


@pytest.mark.parametrize("limit", [10, 25, 50, 100, 200])
def test_inventory_page_size_reaches_http_adapter(monkeypatch, limit):
    from app.web import inventory as routes

    api = FastAPI()
    user = SimpleNamespace(organization_id=uuid4())
    api.dependency_overrides[routes.require_inventory_access] = lambda: user
    api.dependency_overrides[routes.get_db_for_org] = lambda: Mock()
    handler = Mock(return_value=JSONResponse({"ok": True}))
    monkeypatch.setattr(routes.inv_web_service, "list_transactions_response", handler)
    api.add_api_route("/transactions", routes.list_transactions)
    with TestClient(api) as client:
        response = client.get("/transactions", params={"page": 3, "limit": limit})
    assert response.status_code == 200
    assert handler.call_args.args[4] == 3
    assert handler.call_args.kwargs == {"limit": limit}


@pytest.mark.parametrize("limit", [0, -1, 201])
def test_inventory_page_size_is_bounded(monkeypatch, limit):
    from app.web import inventory as routes

    api = FastAPI()
    api.dependency_overrides[routes.require_inventory_access] = lambda: Mock()
    api.dependency_overrides[routes.get_db_for_org] = lambda: Mock()
    handler = Mock()
    monkeypatch.setattr(routes.inv_web_service, "list_transactions_response", handler)
    api.add_api_route("/transactions", routes.list_transactions)
    with TestClient(api) as client:
        assert client.get("/transactions", params={"limit": limit}).status_code == 422
    handler.assert_not_called()


def test_inventory_response_forwards_limit_to_tenant_scoped_query(monkeypatch):
    from app.services.inventory import web

    user = SimpleNamespace(organization_id=uuid4())
    db = Mock()
    context = Mock(return_value={"transactions": []})
    monkeypatch.setattr(web, "base_context", lambda *args, **kwargs: {})
    monkeypatch.setattr(web.inv_web_service, "list_transactions_context", context)
    monkeypatch.setattr(web.templates, "TemplateResponse", Mock())
    web.inv_web_service.list_transactions_response(
        Mock(), user, "cable", "RECEIPT", 3, db, limit=25
    )
    context.assert_called_once_with(
        db,
        str(user.organization_id),
        search="cable",
        transaction_type="RECEIPT",
        page=3,
        limit=25,
    )


def test_inventory_query_uses_selected_size_and_normalizes_empty_search():
    from app.services.inventory.web import InventoryWebService

    db = Mock()
    db.scalar.return_value = 75
    db.execute.return_value.all.return_value = []
    organization_id = uuid4()
    context = InventoryWebService.list_transactions_context(
        db, str(organization_id), None, "RECEIPT", 3, limit=25
    )
    statement = db.execute.call_args.args[0]
    params = statement.compile().params
    assert organization_id in params.values()
    assert statement._limit_clause.value == 25
    assert statement._offset_clause.value == 50
    assert context["search"] == ""
    assert context["total_pages"] == 3


@pytest.mark.parametrize("action", ["submit", "reject"])
def test_approval_permission_alone_cannot_submit_or_reject_claims(action):
    from app.services.expense.web_claims import ExpenseClaimsWebMixin
    from app.web.deps import WebAuthContext

    user = WebAuthContext(
        person_id=uuid4(),
        organization_id=uuid4(),
        scopes=["expense:claims:approve:tier3"],
    )
    db = Mock()
    with patch("app.services.expense.web_claims.ExpenseService") as service:
        if action == "submit":
            response = ExpenseClaimsWebMixin.submit_claim_response(
                str(uuid4()), user, db
            )
        else:
            response = ExpenseClaimsWebMixin.reject_claim_response(
                str(uuid4()), "Reason", user, db
            )
    assert response.status_code == 302
    assert "error=permission" in response.headers["location"]
    service.assert_not_called()
    db.scalars.assert_not_called()


@pytest.mark.parametrize("tier", [1, 2, 3])
def test_each_approval_tier_passes_the_route_permission_gate(tier):
    from app.web.deps import WebAuthContext
    from app.web.finance.exp import _require_claim_approve

    user = WebAuthContext(scopes=[f"expense:claims:approve:tier{tier}"])
    assert _require_claim_approve(user) is user
    with pytest.raises(HTTPException) as error:
        _require_claim_approve(WebAuthContext(scopes=["expense:claims:read"]))
    assert error.value.status_code == 403


@pytest.mark.parametrize("route_name", ["claims", "evaluations", "contracts"])
@pytest.mark.parametrize(
    "page,offset,expected", [(None, 0, 0), (None, 50, 50), (3, 0, 50), (2, 100, 25)]
)
def test_page_navigation_preserves_legacy_offsets(
    monkeypatch, route_name, page, offset, expected
):
    from app.web import procurement
    from app.web.finance import exp

    user = SimpleNamespace(organization_id=uuid4())
    handler = Mock(return_value={})
    if route_name == "claims":
        monkeypatch.setattr(
            exp.expense_claims_web_service, "claims_list_response", handler
        )
        route = exp.expense_claims_list
    else:
        service = SimpleNamespace(
            evaluation_list_context=handler, contract_list_context=handler
        )
        monkeypatch.setattr(procurement, "ProcurementWebService", lambda db: service)
        monkeypatch.setattr(procurement, "base_context", lambda *args, **kwargs: {})
        monkeypatch.setattr(procurement.templates, "TemplateResponse", Mock())
        route = (
            procurement.evaluation_list
            if route_name == "evaluations"
            else procurement.contract_list
        )
    route(request=Mock(), auth=user, db=Mock(), page=page, offset=offset, limit=25)
    assert handler.call_args.kwargs["offset"] == expected
    assert handler.call_args.kwargs["limit"] == 25
