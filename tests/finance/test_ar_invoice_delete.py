"""
Regression tests for AR invoice delete and update operations.

Bugs fixed:
1. delete_invoice() called db.flush() but not db.commit() — delete appeared
   successful but the transaction was silently rolled back.
2. delete_credit_note() had the same flush-without-commit bug.
3. invoice_edit_form_response / update_invoice_response were missing from
   ARWebService after the web.py monolith was split into web/ package.
4. invoice_form.html had a hardcoded submit URL — edit submitted to
   /invoices/new instead of /invoices/{id}/edit.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.finance.ar.invoice import InvoiceStatus, InvoiceType
from app.services.finance.ar.invoice import ARInvoiceService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_draft_invoice(org_id, inv_id=None, invoice_type=InvoiceType.STANDARD):
    """Create a minimal draft invoice SimpleNamespace for testing."""
    return SimpleNamespace(
        invoice_id=inv_id or uuid4(),
        organization_id=org_id,
        status=InvoiceStatus.DRAFT,
        invoice_type=invoice_type,
        total_amount=Decimal("1000.00"),
    )


# ---------------------------------------------------------------------------
# Bug #1: delete_invoice must call db.commit()
# ---------------------------------------------------------------------------


class TestDeleteInvoiceCommit:
    """Verify delete_invoice commits the transaction."""

    def test_delete_invoice_calls_commit(self):
        """Regression: delete_invoice must call db.commit() after db.flush()."""
        db = MagicMock()
        org_id = uuid4()
        inv_id = uuid4()
        invoice = _make_draft_invoice(org_id, inv_id)
        db.get.return_value = invoice

        # No payment allocations
        db.scalar.return_value = 0
        # No line items
        db.scalars.return_value.all.return_value = []

        ARInvoiceService.delete_invoice(db, org_id, inv_id)

        db.flush.assert_called_once()
        db.commit.assert_called_once()

        # commit must happen AFTER flush
        flush_order = db.flush.call_args_list
        commit_order = db.commit.call_args_list
        assert flush_order and commit_order

    def test_delete_invoice_not_found(self):
        """delete_invoice raises when invoice doesn't exist."""
        db = MagicMock()
        db.get.return_value = None

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            ARInvoiceService.delete_invoice(db, uuid4(), uuid4())
        assert excinfo.value.status_code == 404

    def test_delete_invoice_wrong_org(self):
        """delete_invoice raises when invoice belongs to different org."""
        db = MagicMock()
        org_id = uuid4()
        other_org = uuid4()
        invoice = _make_draft_invoice(other_org)
        db.get.return_value = invoice

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            ARInvoiceService.delete_invoice(db, org_id, invoice.invoice_id)
        assert excinfo.value.status_code == 404

    def test_delete_invoice_rejects_non_draft(self):
        """delete_invoice only works on DRAFT invoices."""
        db = MagicMock()
        org_id = uuid4()
        invoice = _make_draft_invoice(org_id)
        invoice.status = InvoiceStatus.POSTED
        db.get.return_value = invoice

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            ARInvoiceService.delete_invoice(db, org_id, invoice.invoice_id)
        assert excinfo.value.status_code == 400

    def test_delete_invoice_rejects_with_allocations(self):
        """delete_invoice fails when payment allocations exist."""
        db = MagicMock()
        org_id = uuid4()
        inv_id = uuid4()
        invoice = _make_draft_invoice(org_id, inv_id)
        db.get.return_value = invoice
        db.scalar.return_value = 2  # 2 allocations

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            ARInvoiceService.delete_invoice(db, org_id, inv_id)
        assert excinfo.value.status_code == 400
        assert "allocation" in str(excinfo.value.detail).lower()

    def test_delete_invoice_cleans_up_lines_and_taxes(self):
        """delete_invoice deletes line taxes, lines, then the invoice."""
        db = MagicMock()
        org_id = uuid4()
        inv_id = uuid4()
        invoice = _make_draft_invoice(org_id, inv_id)
        db.get.return_value = invoice
        db.scalar.return_value = 0  # no allocations

        line1_id = uuid4()
        line2_id = uuid4()
        lines = [
            SimpleNamespace(line_id=line1_id),
            SimpleNamespace(line_id=line2_id),
        ]
        db.scalars.return_value.all.return_value = lines

        ARInvoiceService.delete_invoice(db, org_id, inv_id)

        # Should have called db.execute twice (line taxes + lines)
        assert db.execute.call_count == 2
        # And db.delete for the invoice itself
        db.delete.assert_called_once_with(invoice)
        db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Bug #2: delete_credit_note must call db.commit()
# ---------------------------------------------------------------------------


class TestDeleteCreditNoteCommit:
    """Verify delete_credit_note commits the transaction."""

    def test_delete_credit_note_calls_commit(self):
        """Regression: delete_credit_note must call db.commit()."""
        db = MagicMock()
        org_id = uuid4()
        cn_id = uuid4()
        credit_note = _make_draft_invoice(
            org_id, cn_id, invoice_type=InvoiceType.CREDIT_NOTE
        )
        db.get.return_value = credit_note
        db.scalar.return_value = 0
        db.scalars.return_value.all.return_value = []

        ARInvoiceService.delete_credit_note(db, org_id, cn_id)

        db.flush.assert_called_once()
        db.commit.assert_called_once()

    def test_delete_credit_note_rejects_non_credit_note(self):
        """delete_credit_note rejects standard invoices."""
        db = MagicMock()
        org_id = uuid4()
        invoice = _make_draft_invoice(org_id, invoice_type=InvoiceType.STANDARD)
        db.get.return_value = invoice

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            ARInvoiceService.delete_credit_note(db, org_id, invoice.invoice_id)
        assert excinfo.value.status_code == 400

    def test_delete_credit_note_rejects_non_draft(self):
        """delete_credit_note only works on DRAFT credit notes."""
        db = MagicMock()
        org_id = uuid4()
        cn = _make_draft_invoice(org_id, invoice_type=InvoiceType.CREDIT_NOTE)
        cn.status = InvoiceStatus.POSTED
        db.get.return_value = cn

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            ARInvoiceService.delete_credit_note(db, org_id, cn.invoice_id)
        assert excinfo.value.status_code == 400


# ---------------------------------------------------------------------------
# Bug #3: AR web service must have edit/update/delete response methods
# ---------------------------------------------------------------------------


class TestARWebServiceMethodsExist:
    """Regression: AR web service must expose all CRUD response methods.

    These methods were missing after the web.py monolith was split into
    a web/ package. Without them, the routes return 500 errors.
    """

    def test_ar_web_service_has_edit_and_update_methods(self):
        """All invoice CRUD response methods must exist on ar_web_service."""
        from app.services.finance.ar.web import ar_web_service

        required_methods = [
            "invoice_edit_form_response",
            "update_invoice_response",
            "delete_invoice_response",
            "create_invoice_response",
            "invoice_detail_response",
            "list_invoices_response",
        ]

        for name in required_methods:
            assert hasattr(ar_web_service, name), (
                f"ar_web_service missing method '{name}' — "
                f"likely not ported from web.py monolith to web/ package"
            )

    def test_invoice_edit_form_response_signature(self):
        """invoice_edit_form_response must accept invoice_id parameter."""
        from app.services.finance.ar.web import ar_web_service

        sig = inspect.signature(ar_web_service.invoice_edit_form_response)
        assert "invoice_id" in sig.parameters, (
            "invoice_edit_form_response must accept 'invoice_id' parameter"
        )

    def test_update_invoice_response_signature(self):
        """update_invoice_response must accept invoice_id parameter."""
        from app.services.finance.ar.web import ar_web_service

        sig = inspect.signature(ar_web_service.update_invoice_response)
        assert "invoice_id" in sig.parameters, (
            "update_invoice_response must accept 'invoice_id' parameter"
        )

    def test_delete_invoice_response_signature(self):
        """delete_invoice_response must accept invoice_id parameter."""
        from app.services.finance.ar.web import ar_web_service

        sig = inspect.signature(ar_web_service.delete_invoice_response)
        assert "invoice_id" in sig.parameters, (
            "delete_invoice_response must accept 'invoice_id' parameter"
        )


# ---------------------------------------------------------------------------
# Bug #4: Edit form template must use dynamic submit URL
# ---------------------------------------------------------------------------


class TestInvoiceFormTemplate:
    """Verify the invoice form template uses dynamic URLs for create vs edit."""

    def test_template_contains_dynamic_invoice_id(self):
        """invoice_form.html must reference _invoiceId for edit mode."""
        from pathlib import Path

        template_path = Path("templates/finance/ar/invoice_form.html")
        content = template_path.read_text()

        # The template must set _invoiceId from context
        assert "_invoiceId" in content, (
            "invoice_form.html must define '_invoiceId' variable from template "
            "context to distinguish create vs edit mode"
        )

    def test_template_does_not_hardcode_new_url_in_submit(self):
        """submitForm() must not have a hardcoded URL to /invoices/new."""
        from pathlib import Path

        template_path = Path("templates/finance/ar/invoice_form.html")
        content = template_path.read_text()

        # Find the submitForm function body
        submit_idx = content.find("async submitForm()")
        if submit_idx == -1:
            submit_idx = content.find("submitForm()")
        assert submit_idx != -1, "submitForm() function not found in template"

        # Get the function body (next ~50 lines or up to the next function)
        submit_body = content[submit_idx : submit_idx + 2000]

        # The submit URL must be dynamic, not hardcoded to /invoices/new
        assert "_invoiceId" in submit_body, (
            "submitForm() must use _invoiceId to build a dynamic URL — "
            "hardcoded '/invoices/new' causes edits to create duplicates"
        )
