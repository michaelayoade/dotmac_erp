from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.finance.ap.web.purchase_order_web import PurchaseOrderWebService


def test_purchase_order_form_context_serializes_edit_order_fields() -> None:
    """Edit form context should be safe for Jinja tojson serialization."""
    db = MagicMock()
    org_id = uuid4()
    po_id = uuid4()
    supplier_id = uuid4()

    po = SimpleNamespace(
        po_id=po_id,
        organization_id=org_id,
        supplier_id=supplier_id,
        po_number="PO-000001",
        po_date=None,
        expected_delivery_date=None,
        currency_code="USD",
        exchange_rate=Decimal("1.50"),
        terms_and_conditions="Net 30",
        status=SimpleNamespace(value="DRAFT"),
    )
    supplier = SimpleNamespace(
        supplier_id=supplier_id,
        organization_id=org_id,
        supplier_code="SUP-001",
        legal_name="Paper Supplier Ltd",
        trading_name=None,
        currency_code="USD",
        payment_terms_days=30,
        withholding_tax_code_id=None,
    )

    item = SimpleNamespace(
        item_id=uuid4(),
        item_code="ITEM-001",
        item_name="Paper",
        standard_cost=Decimal("12.50"),
        currency_code="USD",
    )
    line = SimpleNamespace(
        line_id=uuid4(),
        item_id=None,
        description="Paper",
        quantity_ordered=Decimal("2"),
        unit_price=Decimal("12.50"),
        tax_amount=Decimal("0"),
        expense_account_id=None,
        line_number=1,
    )

    items_result = MagicMock()
    items_result.all.return_value = [item]
    lines_result = MagicMock()
    lines_result.all.return_value = [line]
    db.scalars.side_effect = [items_result, lines_result]
    db.get.side_effect = [po, supplier]

    with (
        patch(
            "app.services.finance.ap.web.purchase_order_web.supplier_service.list",
            return_value=[],
        ),
        patch(
            "app.services.finance.ap.web.purchase_order_web.get_accounts",
            return_value=[],
        ),
        patch(
            "app.services.finance.ap.web.purchase_order_web.get_cost_centers",
            return_value=[],
        ),
        patch(
            "app.services.finance.ap.web.purchase_order_web.get_projects",
            return_value=[],
        ),
        patch(
            "app.services.finance.ap.web.purchase_order_web.get_currency_context",
            return_value={"currencies": [], "default_currency_code": "USD"},
        ),
        patch(
            "app.services.finance.ap.web.purchase_order_web.format_date",
            side_effect=lambda value: value.isoformat() if value else "",
        ),
    ):
        context = PurchaseOrderWebService.purchase_order_form_context(
            db,
            str(org_id),
            str(po_id),
        )

    assert context["order"]["po_id"] == str(po_id)
    assert context["order"]["exchange_rate"] == 1.5


def test_purchase_order_form_context_includes_current_supplier_when_inactive() -> None:
    """Edit form should still hydrate the current supplier even if inactive."""
    db = MagicMock()
    org_id = uuid4()
    po_id = uuid4()
    supplier_id = uuid4()

    po = SimpleNamespace(
        po_id=po_id,
        organization_id=org_id,
        supplier_id=supplier_id,
        po_number="PO-000002",
        po_date=None,
        expected_delivery_date=None,
        currency_code="USD",
        exchange_rate=None,
        terms_and_conditions=None,
        status=SimpleNamespace(value="DRAFT"),
    )
    supplier = SimpleNamespace(
        supplier_id=supplier_id,
        organization_id=org_id,
        supplier_code="SUP-009",
        legal_name="Dormant Supplier Ltd",
        trading_name=None,
        currency_code="USD",
        payment_terms_days=30,
        withholding_tax_code_id=None,
    )

    items_result = MagicMock()
    items_result.all.return_value = []
    lines_result = MagicMock()
    lines_result.all.return_value = []
    db.scalars.side_effect = [items_result, lines_result]
    db.get.side_effect = [po, supplier]

    with (
        patch(
            "app.services.finance.ap.web.purchase_order_web.supplier_service.list",
            return_value=[],
        ),
        patch(
            "app.services.finance.ap.web.purchase_order_web.get_accounts",
            return_value=[],
        ),
        patch(
            "app.services.finance.ap.web.purchase_order_web.get_cost_centers",
            return_value=[],
        ),
        patch(
            "app.services.finance.ap.web.purchase_order_web.get_projects",
            return_value=[],
        ),
        patch(
            "app.services.finance.ap.web.purchase_order_web.get_currency_context",
            return_value={"currencies": [], "default_currency_code": "USD"},
        ),
        patch(
            "app.services.finance.ap.web.purchase_order_web.format_date",
            side_effect=lambda value: value.isoformat() if value else "",
        ),
    ):
        context = PurchaseOrderWebService.purchase_order_form_context(
            db,
            str(org_id),
            str(po_id),
        )

    assert context["order"]["supplier_id"] == str(supplier_id)
    assert context["order"]["supplier_name"] == "Dormant Supplier Ltd"
    assert context["suppliers_list"][0]["supplier_id"] == str(supplier_id)
