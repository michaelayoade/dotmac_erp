from unittest.mock import Mock

from app.services.finance.ap.web.payment_web import PaymentWebService


class _EmptyResult:
    def all(self):
        return []


def test_payment_form_open_invoices_order_newest_first(monkeypatch):
    """Open invoices on the AP payment form should show recent invoices first."""
    executed = []
    db = Mock()
    db.execute.side_effect = lambda stmt: executed.append(stmt) or _EmptyResult()
    db.scalars.return_value.all.return_value = []

    monkeypatch.setattr(
        "app.services.finance.ap.web.payment_web.supplier_service.list",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.payment_web.get_currency_context",
        lambda *args, **kwargs: {},
    )

    PaymentWebService.payment_form_context(
        db,
        "00000000-0000-0000-0000-000000000001",
    )

    order_by = [str(clause) for clause in executed[0]._order_by_clauses]
    assert order_by == [
        "ap.supplier_invoice.invoice_date DESC",
        "ap.supplier_invoice.due_date DESC",
        "ap.supplier_invoice.invoice_number DESC",
    ]
