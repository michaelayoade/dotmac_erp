from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.sync.dotmac_sub import (
    get_sub_purchase_invoice_status,
    require_sub_ap_read_scope,
)
from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.services.sync.sub_purchase_invoice_status import (
    PurchaseInvoiceStatusNotFoundError,
    get_purchase_invoice_status,
)


def _invoice(*, organization_id):
    invoice = MagicMock()
    invoice.organization_id = organization_id
    invoice.invoice_id = uuid4()
    invoice.invoice_number = "PINV-2026-0042"
    invoice.status = SupplierInvoiceStatus.PARTIALLY_PAID
    invoice.currency_code = "NGN"
    invoice.total_amount = Decimal("100000.00")
    invoice.amount_paid = Decimal("40000.00")
    invoice.updated_at = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    return invoice


def test_status_owner_returns_authoritative_amounts_and_status() -> None:
    db = MagicMock()
    organization_id = uuid4()
    source_invoice_id = "i" * 120
    invoice = _invoice(organization_id=organization_id)
    db.scalar.return_value = invoice

    result = get_purchase_invoice_status(
        db,
        organization_id=organization_id,
        source_invoice_id=source_invoice_id,
    )

    assert result.source_invoice_id == source_invoice_id
    assert result.purchase_invoice_id == invoice.invoice_id
    assert result.status == "partially_paid"
    assert result.total_amount == Decimal("100000.00")
    assert result.amount_paid == Decimal("40000.00")
    assert result.balance_due == Decimal("60000.00")
    statement = db.scalar.call_args.args[0]
    assert "organization_id" in str(statement)
    assert f"sub-invoice:{source_invoice_id}" in statement.compile().params.values()


def test_status_owner_raises_transport_neutral_not_found() -> None:
    db = MagicMock()
    db.scalar.return_value = None
    source_invoice_id = "sub-purchase-invoice-missing"

    with pytest.raises(PurchaseInvoiceStatusNotFoundError) as exc_info:
        get_purchase_invoice_status(
            db,
            organization_id=uuid4(),
            source_invoice_id=source_invoice_id,
        )

    assert exc_info.value.code == "purchase_invoice_status_not_found"


def test_http_adapter_translates_owner_not_found() -> None:
    db = MagicMock()
    db.scalar.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        get_sub_purchase_invoice_status(
            "sub-purchase-invoice-missing",
            auth={"organization_id": uuid4()},
            db=db,
        )

    assert exc_info.value.status_code == 404


def test_read_scope_accepts_read_or_write_but_rejects_other_domains() -> None:
    assert require_sub_ap_read_scope({"scopes": ["sub:ap:read"]})
    assert require_sub_ap_read_scope({"scopes": ["sub:ap:write"]})

    with pytest.raises(HTTPException) as exc_info:
        require_sub_ap_read_scope({"scopes": ["sub:domain:write"]})

    assert exc_info.value.status_code == 403


def test_status_owner_has_no_http_framework_dependency() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "app/services/sync/sub_purchase_invoice_status.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(
        module == "fastapi"
        or module.startswith("fastapi.")
        or module == "starlette"
        or module.startswith("starlette.")
        for module in imports
    )
