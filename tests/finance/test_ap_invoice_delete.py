from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.services.common import ValidationError
from app.services.finance.ap.supplier_invoice import SupplierInvoiceService


def _draft_invoice(organization_id, invoice_id):
    return SimpleNamespace(
        invoice_id=invoice_id,
        organization_id=organization_id,
        status=SupplierInvoiceStatus.DRAFT,
    )


@pytest.mark.parametrize(
    ("scalar_results", "message"),
    [
        (
            [0, "EXP-2026-001"],
            "generated from expense claim EXP-2026-001",
        ),
        (
            [0, None, uuid4()],
            "linked to a fleet maintenance record",
        ),
        (
            [0, None, None, uuid4()],
            "has an inventory receipt approval",
        ),
    ],
)
def test_delete_invoice_refuses_domain_owned_references(scalar_results, message):
    organization_id = uuid4()
    invoice_id = uuid4()
    db = MagicMock()
    db.get.return_value = _draft_invoice(organization_id, invoice_id)
    db.scalar.side_effect = scalar_results

    with pytest.raises(ValidationError, match=message):
        SupplierInvoiceService.delete_invoice(db, organization_id, invoice_id)

    db.execute.assert_not_called()
    db.delete.assert_not_called()
    db.flush.assert_not_called()


def test_delete_invoice_reference_queries_are_tenant_scoped():
    organization_id = uuid4()
    invoice_id = uuid4()
    db = MagicMock()
    db.get.return_value = _draft_invoice(organization_id, invoice_id)
    db.scalar.side_effect = [0, "EXP-2026-002"]

    with pytest.raises(ValidationError):
        SupplierInvoiceService.delete_invoice(db, organization_id, invoice_id)

    claim_statement = db.scalar.call_args_list[1].args[0]
    rendered = str(claim_statement)
    assert "expense.expense_claim.organization_id" in rendered
    assert "expense.expense_claim.supplier_invoice_id" in rendered


def test_delete_unreferenced_draft_invoice_flushes_without_committing():
    organization_id = uuid4()
    invoice_id = uuid4()
    invoice = _draft_invoice(organization_id, invoice_id)
    db = MagicMock()
    db.get.return_value = invoice
    db.scalar.side_effect = [0, None, None, None]
    db.scalars.return_value.all.return_value = []

    SupplierInvoiceService.delete_invoice(db, organization_id, invoice_id)

    db.delete.assert_called_once_with(invoice)
    db.flush.assert_called_once()
    db.commit.assert_not_called()
