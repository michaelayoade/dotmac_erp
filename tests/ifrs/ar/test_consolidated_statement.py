"""Tests for the consolidated statement of account (running balance, ordering)."""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.models.finance.ar.invoice import InvoiceStatus
from app.services.finance.ar.customer import customer_service
from app.services.finance.ar.web.customer_web import CustomerWebService


def _mock_invoice(date_, total, number, cust_id, status, paid="0", due=None):
    return MagicMock(
        invoice_date=date_,
        total_amount=Decimal(total),
        invoice_number=number,
        customer_id=cust_id,
        status=status,
        amount_paid=Decimal(paid),
        due_date=due or date_,
    )


def _mock_payment(date_, amount, number, cust_id):
    return MagicMock(
        payment_date=date_,
        amount=Decimal(amount),
        payment_number=number,
        customer_id=cust_id,
    )


def _build_context(invoices, payments, family, attribution):
    org = uuid.uuid4()
    parent = family[0]
    customer = MagicMock(customer_id=parent, organization_id=org, currency_code="NGN")
    db = MagicMock()
    # consolidated_statement_context calls db.scalars().all() twice:
    # first invoices, then payments.
    db.scalars.return_value.all.side_effect = [invoices, payments]

    with (
        patch.object(customer_service, "get", return_value=customer),
        patch(
            "app.services.finance.ar.web.customer_web.CustomerFamilyResolver"
        ) as mock_resolver_cls,
        patch(
            "app.services.finance.ar.web.customer_web.customer_detail_view",
            return_value={},
        ),
    ):
        resolver = mock_resolver_cls.return_value
        resolver.family_ids.return_value = family
        resolver.attribution_map.return_value = attribution
        return CustomerWebService.consolidated_statement_context(
            db, str(org), str(parent)
        )


class TestConsolidatedStatement:
    def test_running_balance_and_chronological_ordering(self) -> None:
        parent, child = uuid.uuid4(), uuid.uuid4()
        invoices = [
            _mock_invoice(
                datetime.date(2025, 1, 1), "100", "INV-1", parent, InvoiceStatus.POSTED
            ),
            _mock_invoice(
                datetime.date(2025, 2, 1),
                "50",
                "INV-2",
                child,
                InvoiceStatus.PAID,
                paid="50",
            ),
        ]
        payments = [_mock_payment(datetime.date(2025, 2, 1), "30", "PMT-1", parent)]
        ctx = _build_context(
            invoices,
            payments,
            [parent, child],
            {
                parent: {"code": "P", "name": "Parent"},
                child: {"code": "C", "name": "Sub"},
            },
        )

        assert ctx["is_consolidated"] is True
        assert ctx["family_count"] == 2
        # Same-day invoice is ordered before the payment.
        assert [t["type"] for t in ctx["transactions"]] == [
            "Invoice",
            "Invoice",
            "Payment",
        ]
        # Closing balance = 100 + 50 - 30 = 120, and ties to the last row.
        assert "120" in ctx["closing_balance"]
        assert ctx["transactions"][-1]["balance"] == ctx["closing_balance"]
        # Per-row sub-account attribution is present when consolidated.
        assert ctx["transactions"][1]["sub_account"] == "C"

    def test_standalone_customer_is_not_consolidated(self) -> None:
        cust = uuid.uuid4()
        invoices = [
            _mock_invoice(
                datetime.date(2025, 1, 1), "200", "INV-9", cust, InvoiceStatus.POSTED
            )
        ]
        ctx = _build_context(invoices, [], [cust], {})
        assert ctx["is_consolidated"] is False
        assert ctx["transactions"][0]["sub_account"] == ""
        assert "200" in ctx["closing_balance"]
