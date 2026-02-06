from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.finance.ar.invoice import InvoiceStatus, InvoiceType
from app.services.finance.ar.invoice import (
    ARInvoiceInput,
    ARInvoiceLineInput,
    ARInvoiceService,
)
from app.services.finance.tax.tax_calculation import (
    LineCalculationResult,
    LineTaxResult,
)


def _make_customer(org_id, active=True):
    return SimpleNamespace(
        customer_id=uuid4(),
        organization_id=org_id,
        is_active=active,
        billing_address={"line1": "Billing"},
        shipping_address={"line1": "Shipping"},
        ar_control_account_id=uuid4(),
        default_revenue_account_id=uuid4(),
    )


def test_create_invoice_customer_missing():
    db = MagicMock()
    svc = ARInvoiceService()
    org_id = uuid4()
    db.get.return_value = None

    with pytest.raises(HTTPException) as excinfo:
        svc.create_invoice(
            db,
            org_id,
            ARInvoiceInput(
                customer_id=uuid4(),
                invoice_type=InvoiceType.STANDARD,
                invoice_date=date.today(),
                due_date=date.today(),
                currency_code="NGN",
                lines=[
                    ARInvoiceLineInput(
                        description="A", quantity=Decimal("1"), unit_price=Decimal("10")
                    )
                ],
            ),
            created_by_user_id=uuid4(),
        )

    assert excinfo.value.status_code == 404


def test_create_invoice_inactive_customer():
    db = MagicMock()
    svc = ARInvoiceService()
    org_id = uuid4()
    db.get.return_value = _make_customer(org_id, active=False)

    with pytest.raises(HTTPException) as excinfo:
        svc.create_invoice(
            db,
            org_id,
            ARInvoiceInput(
                customer_id=uuid4(),
                invoice_type=InvoiceType.STANDARD,
                invoice_date=date.today(),
                due_date=date.today(),
                currency_code="NGN",
                lines=[
                    ARInvoiceLineInput(
                        description="A", quantity=Decimal("1"), unit_price=Decimal("10")
                    )
                ],
            ),
            created_by_user_id=uuid4(),
        )

    assert excinfo.value.status_code == 400


def test_create_invoice_requires_lines():
    db = MagicMock()
    svc = ARInvoiceService()
    org_id = uuid4()
    db.get.return_value = _make_customer(org_id)

    with pytest.raises(HTTPException) as excinfo:
        svc.create_invoice(
            db,
            org_id,
            ARInvoiceInput(
                customer_id=uuid4(),
                invoice_type=InvoiceType.STANDARD,
                invoice_date=date.today(),
                due_date=date.today(),
                currency_code="NGN",
                lines=[],
            ),
            created_by_user_id=uuid4(),
        )

    assert excinfo.value.status_code == 400


def test_create_credit_note_applies_negative_amounts():
    db = MagicMock()
    svc = ARInvoiceService()
    org_id = uuid4()
    customer = _make_customer(org_id)
    db.get.return_value = customer

    tax_result = LineCalculationResult(
        net_amount=Decimal("100.00"),
        taxes=[
            LineTaxResult(
                tax_code_id=uuid4(),
                tax_code="VAT",
                tax_name="VAT",
                base_amount=Decimal("100.00"),
                tax_rate=Decimal("0.10"),
                tax_amount=Decimal("10.00"),
                is_inclusive=False,
                is_recoverable=False,
                recoverable_amount=Decimal("0"),
                non_recoverable_amount=Decimal("10.00"),
                sequence=1,
            )
        ],
        total_tax=Decimal("10.00"),
        gross_amount=Decimal("110.00"),
    )

    added = []
    db.add.side_effect = lambda obj: added.append(obj)

    with (
        patch(
            "app.services.finance.ar.invoice._batch_validate_org_refs",
            return_value=None,
        ),
        patch(
            "app.services.finance.ar.invoice.SequenceService.get_next_number",
            return_value="INV-1",
        ),
        patch(
            "app.services.finance.ar.invoice.TaxCalculationService.calculate_line_taxes",
            return_value=tax_result,
        ),
    ):
        invoice = svc.create_invoice(
            db,
            org_id,
            ARInvoiceInput(
                customer_id=customer.customer_id,
                invoice_type=InvoiceType.CREDIT_NOTE,
                invoice_date=date(2024, 1, 1),
                due_date=date(2024, 1, 15),
                currency_code="NGN",
                lines=[
                    ARInvoiceLineInput(
                        description="Credit",
                        quantity=Decimal("1"),
                        unit_price=Decimal("100.00"),
                        tax_code_ids=[uuid4()],
                    )
                ],
            ),
            created_by_user_id=uuid4(),
        )

    assert invoice.total_amount < 0
    assert invoice.subtotal < 0
    assert invoice.tax_amount < 0

    tax_lines = [x for x in added if x.__class__.__name__ == "InvoiceLineTax"]
    assert tax_lines
    assert all(t.tax_amount < 0 for t in tax_lines)


def test_update_invoice_requires_draft():
    db = MagicMock()
    org_id = uuid4()
    invoice = SimpleNamespace(organization_id=org_id, status=InvoiceStatus.POSTED)
    db.get.return_value = invoice

    with pytest.raises(HTTPException) as excinfo:
        ARInvoiceService.update_invoice(
            db,
            org_id,
            uuid4(),
            ARInvoiceInput(
                customer_id=uuid4(),
                invoice_type=InvoiceType.STANDARD,
                invoice_date=date.today(),
                due_date=date.today(),
                currency_code="NGN",
                lines=[
                    ARInvoiceLineInput(
                        description="A", quantity=Decimal("1"), unit_price=Decimal("10")
                    )
                ],
            ),
            updated_by_user_id=uuid4(),
        )

    assert excinfo.value.status_code == 400


def test_submit_approve_and_segregation():
    db = MagicMock()
    org_id = uuid4()
    invoice = SimpleNamespace(
        organization_id=org_id,
        status=InvoiceStatus.DRAFT,
        submitted_by_user_id=None,
        submitted_at=None,
    )
    db.get.return_value = invoice

    submitted = ARInvoiceService.submit_invoice(
        db, org_id, uuid4(), submitted_by_user_id=uuid4()
    )
    assert submitted.status == InvoiceStatus.SUBMITTED

    invoice.status = InvoiceStatus.SUBMITTED
    invoice.submitted_by_user_id = uuid4()
    with pytest.raises(HTTPException):
        ARInvoiceService.approve_invoice(
            db, org_id, uuid4(), approved_by_user_id=invoice.submitted_by_user_id
        )


def test_post_invoice_and_void_cancel_and_record_payment():
    db = MagicMock()
    org_id = uuid4()

    invoice = SimpleNamespace(
        organization_id=org_id,
        status=InvoiceStatus.APPROVED,
        invoice_date=date.today(),
        posting_status=None,
        journal_entry_id=None,
        posting_batch_id=None,
        amount_paid=Decimal("0"),
        total_amount=Decimal("100.00"),
        balance_due=Decimal("100.00"),
        due_date=date.today() - timedelta(days=1),
        approved_by_user_id=None,
        approved_at=None,
    )
    db.get.return_value = invoice

    with patch(
        "app.services.finance.ar.ar_posting_adapter.ARPostingAdapter.post_invoice"
    ) as post_invoice:
        post_invoice.return_value = SimpleNamespace(
            success=True, journal_entry_id=uuid4(), posting_batch_id=uuid4()
        )
        posted = ARInvoiceService.post_invoice(
            db, org_id, uuid4(), posted_by_user_id=uuid4()
        )
        assert posted.status == InvoiceStatus.POSTED

    invoice.status = InvoiceStatus.DRAFT
    voided = ARInvoiceService.void_invoice(
        db, org_id, uuid4(), voided_by_user_id=uuid4(), reason="bad"
    )
    assert voided.status == InvoiceStatus.VOID

    invoice.status = InvoiceStatus.SUBMITTED
    cancelled = ARInvoiceService.cancel_invoice(
        db, org_id, uuid4(), cancelled_by_user_id=uuid4()
    )
    assert cancelled.status == InvoiceStatus.DRAFT

    invoice.status = InvoiceStatus.POSTED
    paid = ARInvoiceService.record_payment(
        db, org_id, uuid4(), payment_amount=Decimal("30.00")
    )
    assert paid.status == InvoiceStatus.PARTIALLY_PAID


def test_mark_overdue_and_list_requires_org():
    db = MagicMock()
    org_id = uuid4()

    invoice = SimpleNamespace(balance_due=Decimal("10.00"), status=InvoiceStatus.POSTED)
    query = MagicMock()
    query.filter.return_value.all.return_value = [invoice]
    db.query.return_value = query

    count = ARInvoiceService.mark_overdue(db, org_id, as_of_date=date.today())
    assert count == 1
    assert invoice.status == InvoiceStatus.OVERDUE

    with pytest.raises(HTTPException):
        ARInvoiceService.list(db, organization_id="")
