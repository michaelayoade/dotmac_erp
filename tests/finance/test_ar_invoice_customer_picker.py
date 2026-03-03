from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.finance.ar.web.invoice_web import InvoiceWebService


def test_invoice_form_context_includes_selected_customer_when_inactive() -> None:
    """Edit form should include the selected customer even if inactive."""
    db = MagicMock()
    org_id = uuid4()
    customer_id = uuid4()

    customer = SimpleNamespace(
        customer_id=customer_id,
        trading_name=None,
        legal_name="Dormant Customer Ltd",
        customer_code="CUST-009",
        currency_code="USD",
        credit_terms_days=30,
        default_tax_code_id=None,
        organization_id=org_id,
    )

    with (
        patch(
            "app.services.finance.ar.web.invoice_web.customer_service.list",
            return_value=[],
        ),
        patch(
            "app.services.finance.ar.web.invoice_web.customer_service.get",
            return_value=customer,
        ),
        patch(
            "app.services.finance.ar.web.invoice_web.get_accounts",
            return_value=[],
        ),
        patch(
            "app.services.finance.ar.web.invoice_web.tax_code_service.list",
            return_value=[],
        ),
        patch(
            "app.services.finance.ar.web.invoice_web.get_cost_centers",
            return_value=[],
        ),
        patch(
            "app.services.finance.ar.web.invoice_web.get_projects",
            return_value=[],
        ),
        patch(
            "app.services.finance.ar.web.invoice_web.get_currency_context",
            return_value={"currencies": [], "default_currency_code": "USD"},
        ),
    ):
        db.scalars.return_value.all.return_value = []
        context = InvoiceWebService.invoice_form_context(
            db,
            str(org_id),
            str(customer_id),
        )

    assert context["selected_customer_id"] == str(customer_id)
    assert context["locked_customer"] is True
    assert context["customers_list"][0]["customer_id"] == str(customer_id)
    assert context["customers_list"][0]["customer_name"] == "Dormant Customer Ltd"
