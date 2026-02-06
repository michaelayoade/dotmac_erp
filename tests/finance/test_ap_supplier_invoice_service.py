from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.finance.ap.supplier_invoice import (
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.services.finance.ap.supplier_invoice import (
    InvoiceLineInput,
    SupplierInvoiceInput,
    SupplierInvoiceService,
)
from app.services.finance.tax.tax_calculation import (
    LineCalculationResult,
    LineTaxResult,
)
from app.models.finance.ap.supplier_invoice_line_tax import SupplierInvoiceLineTax


def _make_supplier(org_id, active=True):
    return SimpleNamespace(
        supplier_id=uuid4(),
        organization_id=org_id,
        is_active=active,
        default_expense_account_id=uuid4(),
        ap_control_account_id=uuid4(),
    )


def test_create_invoice_requires_active_supplier_and_lines():
    db = MagicMock()
    org_id = uuid4()
    db.get.return_value = None

    with pytest.raises(HTTPException):
        SupplierInvoiceService.create_invoice(
            db,
            org_id,
            SupplierInvoiceInput(
                supplier_id=uuid4(),
                invoice_type=SupplierInvoiceType.STANDARD,
                invoice_date=date.today(),
                received_date=date.today(),
                due_date=date.today(),
                currency_code="NGN",
                lines=[
                    InvoiceLineInput(
                        description="A", quantity=Decimal("1"), unit_price=Decimal("10")
                    )
                ],
            ),
            created_by_user_id=uuid4(),
        )

    supplier = _make_supplier(org_id, active=False)
    db.get.return_value = supplier

    with pytest.raises(HTTPException):
        SupplierInvoiceService.create_invoice(
            db,
            org_id,
            SupplierInvoiceInput(
                supplier_id=supplier.supplier_id,
                invoice_type=SupplierInvoiceType.STANDARD,
                invoice_date=date.today(),
                received_date=date.today(),
                due_date=date.today(),
                currency_code="NGN",
                lines=[
                    InvoiceLineInput(
                        description="A", quantity=Decimal("1"), unit_price=Decimal("10")
                    )
                ],
            ),
            created_by_user_id=uuid4(),
        )

    supplier.is_active = True
    db.get.return_value = supplier
    with pytest.raises(HTTPException):
        SupplierInvoiceService.create_invoice(
            db,
            org_id,
            SupplierInvoiceInput(
                supplier_id=supplier.supplier_id,
                invoice_type=SupplierInvoiceType.STANDARD,
                invoice_date=date.today(),
                received_date=date.today(),
                due_date=date.today(),
                currency_code="NGN",
                lines=[],
            ),
            created_by_user_id=uuid4(),
        )


def test_create_credit_note_negative_amounts_and_tax_lines():
    db = MagicMock()
    org_id = uuid4()
    supplier = _make_supplier(org_id)
    db.get.return_value = supplier

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
                is_recoverable=True,
                recoverable_amount=Decimal("10.00"),
                non_recoverable_amount=Decimal("0"),
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
            "app.services.finance.ap.supplier_invoice.SequenceService.get_next_number",
            return_value="SI-1",
        ),
        patch(
            "app.services.finance.ap.supplier_invoice.SupplierInvoiceService._require_org_match",
            return_value=None,
        ),
        patch(
            "app.services.finance.ap.supplier_invoice.SupplierInvoiceService._require_po_line_org",
            return_value=None,
        ),
        patch(
            "app.services.finance.ap.supplier_invoice.SupplierInvoiceService._require_gr_line_org",
            return_value=None,
        ),
        patch(
            "app.services.finance.ap.supplier_invoice.TaxCalculationService.calculate_line_taxes",
            return_value=tax_result,
        ),
    ):
        invoice = SupplierInvoiceService.create_invoice(
            db,
            org_id,
            SupplierInvoiceInput(
                supplier_id=supplier.supplier_id,
                invoice_type=SupplierInvoiceType.CREDIT_NOTE,
                invoice_date=date.today(),
                received_date=date.today(),
                due_date=date.today(),
                currency_code="NGN",
                lines=[
                    InvoiceLineInput(
                        description="A",
                        quantity=Decimal("1"),
                        unit_price=Decimal("100"),
                        tax_code_ids=[uuid4()],
                    )
                ],
            ),
            created_by_user_id=uuid4(),
        )

    assert invoice.total_amount < 0
    tax_lines = [x for x in added if isinstance(x, SupplierInvoiceLineTax)]
    assert tax_lines
    assert all(t.tax_amount < 0 for t in tax_lines)
    assert all(t.recoverable_amount < 0 for t in tax_lines)


def test_update_invoice_requires_draft():
    db = MagicMock()
    org_id = uuid4()
    invoice = SimpleNamespace(
        organization_id=org_id, status=SupplierInvoiceStatus.POSTED
    )
    db.get.return_value = invoice

    with pytest.raises(HTTPException):
        SupplierInvoiceService.update_invoice(
            db,
            org_id,
            uuid4(),
            SupplierInvoiceInput(
                supplier_id=uuid4(),
                invoice_type=SupplierInvoiceType.STANDARD,
                invoice_date=date.today(),
                received_date=date.today(),
                due_date=date.today(),
                currency_code="NGN",
                lines=[
                    InvoiceLineInput(
                        description="A", quantity=Decimal("1"), unit_price=Decimal("10")
                    )
                ],
            ),
        )


def test_submit_approve_post_void_hold_release_record_payment():
    db = MagicMock()
    org_id = uuid4()
    invoice = SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=org_id,
        status=SupplierInvoiceStatus.DRAFT,
        invoice_date=date.today(),
        amount_paid=Decimal("0"),
        total_amount=Decimal("100.00"),
        due_date=date.today() - timedelta(days=1),
        approved_by_user_id=None,
    )
    db.get.return_value = invoice

    submitted = SupplierInvoiceService.submit_invoice(
        db, org_id, invoice.invoice_id, submitted_by_user_id=uuid4()
    )
    assert submitted.status == SupplierInvoiceStatus.SUBMITTED

    invoice.status = SupplierInvoiceStatus.SUBMITTED
    invoice.submitted_by_user_id = uuid4()
    with pytest.raises(HTTPException):
        SupplierInvoiceService.approve_invoice(
            db, org_id, invoice.invoice_id, invoice.submitted_by_user_id
        )

    invoice.submitted_by_user_id = uuid4()
    approved = SupplierInvoiceService.approve_invoice(
        db, org_id, invoice.invoice_id, approved_by_user_id=uuid4()
    )
    assert approved.status == SupplierInvoiceStatus.APPROVED

    with (
        patch(
            "app.services.finance.ap.ap_posting_adapter.APPostingAdapter.post_invoice"
        ) as post_invoice,
        patch(
            "app.services.finance.ap.supplier_invoice.SupplierInvoiceService._update_item_costs_from_invoice"
        ) as update_costs,
    ):
        post_invoice.return_value = SimpleNamespace(
            success=True, journal_entry_id=uuid4(), posting_batch_id=uuid4()
        )
        posted = SupplierInvoiceService.post_invoice(
            db, org_id, invoice.invoice_id, posted_by_user_id=uuid4()
        )
        assert posted.status == SupplierInvoiceStatus.POSTED

    invoice.status = SupplierInvoiceStatus.DRAFT
    voided = SupplierInvoiceService.void_invoice(
        db, org_id, invoice.invoice_id, voided_by_user_id=uuid4(), reason="bad"
    )
    assert voided.status == SupplierInvoiceStatus.VOID

    invoice.status = SupplierInvoiceStatus.SUBMITTED
    on_hold = SupplierInvoiceService.put_on_hold(
        db, org_id, invoice.invoice_id, reason="hold"
    )
    assert on_hold.status == SupplierInvoiceStatus.ON_HOLD

    invoice.status = SupplierInvoiceStatus.ON_HOLD
    invoice.approved_by_user_id = None
    released = SupplierInvoiceService.release_from_hold(db, org_id, invoice.invoice_id)
    assert released.status == SupplierInvoiceStatus.SUBMITTED

    invoice.status = SupplierInvoiceStatus.POSTED
    paid = SupplierInvoiceService.record_payment(
        db, org_id, invoice.invoice_id, payment_amount=Decimal("40.00")
    )
    assert paid.status == SupplierInvoiceStatus.PARTIALLY_PAID


def test_list_overdue_requires_filters():
    db = MagicMock()
    org_id = uuid4()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value.offset.return_value.all.return_value = []

    SupplierInvoiceService.list(db, organization_id=str(org_id), overdue_only=True)
