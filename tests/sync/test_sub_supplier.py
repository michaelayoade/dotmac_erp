"""Sub vendor to ERP supplier identity and account rules."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.schemas.sync.sub_supplier import SubSupplierCreate
from app.services.sync.sub import suppliers


def _command(**overrides):
    values = {
        "source_vendor_id": uuid4(),
        "name": "Kano Fibre Works",
        "code": "KFW",
        "contact_name": "Amina",
        "contact_email": "amina@example.test",
        "contact_phone": "+2348000000000",
    }
    values.update(overrides)
    return SubSupplierCreate(**values)


def test_duplicate_source_identity_returns_existing_supplier(monkeypatch):
    command = _command()
    existing = SimpleNamespace(supplier_id=uuid4(), supplier_code="KFW")
    db = MagicMock()
    db.scalar.return_value = existing
    monkeypatch.setattr(
        suppliers, "_account_id", lambda *_: pytest.fail("account lookup")
    )

    outcome = suppliers.create_supplier(db, organization_id=uuid4(), command=command)

    assert outcome.supplier_id == existing.supplier_id
    assert outcome.created is False


def test_new_supplier_uses_validated_org_account_and_contact(monkeypatch):
    command = _command(code="X" * 60)
    org_id = uuid4()
    account_id = uuid4()
    created = SimpleNamespace(
        supplier_id=uuid4(), supplier_code="SUB-123", is_active=True
    )
    db = MagicMock()
    db.scalar.return_value = None
    monkeypatch.setattr(suppliers, "_account_id", lambda _db, _org: account_id)
    create = MagicMock(return_value=created)
    monkeypatch.setattr(suppliers.SupplierService, "create_supplier", create)

    outcome = suppliers.create_supplier(db, organization_id=org_id, command=command)

    assert outcome.created is True
    assert created.sub_vendor_id == command.source_vendor_id
    assert create.call_args.args[2].default_payable_account_id == account_id
    assert create.call_args.args[2].primary_contact["email"] == "amina@example.test"
    assert create.call_args.args[2].supplier_code.startswith("SUB-")


def test_missing_org_account_fails_closed():
    db = MagicMock()
    db.scalar.return_value = None

    with pytest.raises(ValueError, match="not configured"):
        suppliers._account_id(db, uuid4())


def test_configured_account_is_validated_for_the_service_organization(monkeypatch):
    org_id = uuid4()
    account_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(value_text=str(account_id))
    validation = MagicMock(return_value=account_id)
    monkeypatch.setattr(suppliers, "require_ap_control_account", validation)

    assert suppliers._account_id(db, org_id) == account_id
    validation.assert_called_once_with(db, org_id, account_id)
