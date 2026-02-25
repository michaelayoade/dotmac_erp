"""Tests for sales order web reservation badges."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.finance.ar.sales_order import FulfillmentStatus, SOStatus
from app.services.finance.ar.web.sales_order_web import SalesOrderWebService


def _build_sales_order(org_id):
    line_id = uuid4()
    line = SimpleNamespace(
        line_id=line_id,
        line_number=1,
        item_code="ITEM-001",
        description="Test line",
        quantity_ordered=Decimal("10"),
        quantity_shipped=Decimal("2"),
        quantity_invoiced=Decimal("1"),
        unit_price=Decimal("100"),
        discount_amount=Decimal("0"),
        tax_amount=Decimal("0"),
        line_total=Decimal("1000"),
        fulfillment_status=FulfillmentStatus.PARTIAL,
    )

    so = SimpleNamespace(
        so_id=uuid4(),
        organization_id=org_id,
        so_number="SO-001",
        order_date=date.today(),
        customer=None,
        customer_po_number=None,
        reference=None,
        requested_date=None,
        promised_date=None,
        subtotal=Decimal("1000"),
        discount_amount=Decimal("0"),
        tax_amount=Decimal("0"),
        shipping_amount=Decimal("0"),
        total_amount=Decimal("1000"),
        invoiced_amount=Decimal("100"),
        currency_code="USD",
        status=SOStatus.CONFIRMED,
        customer_notes="",
        internal_notes="",
        payment_terms=None,
        ship_to_name=None,
        ship_to_address="",
        ship_to_city="",
        ship_to_state="",
        ship_to_postal_code="",
        ship_to_country="",
        shipping_method=None,
        allow_partial_shipment=True,
        submitted_at=None,
        approved_at=None,
        confirmed_at=None,
        completed_at=None,
        cancelled_at=None,
        cancellation_reason=None,
        created_at=None,
        lines=[line],
        shipments=[],
        is_fully_shipped=False,
        is_fully_invoiced=False,
    )
    return so, line_id


@patch("app.services.finance.ar.web.sales_order_web.is_feature_enabled")
@patch("app.services.inventory.stock_reservation.StockReservationService")
def test_detail_context_includes_reservation_fields(
    mock_reservation_service_class,
    mock_feature_enabled,
):
    org_id = uuid4()
    so, line_id = _build_sales_order(org_id)

    db = MagicMock()
    db.get.return_value = so

    mock_feature_enabled.return_value = True

    reservation = SimpleNamespace(
        source_line_id=line_id,
        status=SimpleNamespace(value="RESERVED"),
        quantity_reserved=Decimal("5"),
        quantity_remaining=Decimal("3"),
    )
    reservation_service = MagicMock()
    reservation_service.get_reservations_for_source.return_value = [reservation]
    mock_reservation_service_class.return_value = reservation_service

    context = SalesOrderWebService.detail_context(db, str(org_id), str(so.so_id))

    assert context["lines"][0]["reservation_status"] == "RESERVED"
    assert context["lines"][0]["reserved_quantity"] == "5"
    assert context["lines"][0]["reserved_remaining"] == "3"
