from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.procurement.vendor import PrequalificationCreate
from app.services.common import ValidationError
from app.services.procurement.vendor import VendorPrequalificationService
from app.web import procurement


class _FakeDB:
    def __init__(self, scalar_result=None):
        self.scalar_result = scalar_result
        self.added = []
        self.flushed = False
        self.rolled_back = False

    def scalar(self, _stmt):
        return self.scalar_result

    def scalars(self, _stmt):
        return SimpleNamespace(all=lambda: ["VEN-00001", "VEN-00003", "OTHER"])

    def add(self, item):
        self.added.append(item)

    def flush(self):
        self.flushed = True

    def rollback(self):
        self.rolled_back = True


def test_vendor_create_creates_supplier(monkeypatch):
    org_id = uuid4()
    user_id = uuid4()
    supplier_id = uuid4()
    db = _FakeDB()
    captured = {}

    def fake_build_input_from_payload(*, db, organization_id, payload):
        captured["supplier_payload"] = payload
        captured["supplier_org_id"] = organization_id
        return SimpleNamespace(registration_number=None)

    def fake_create_supplier(*, db, organization_id, input):
        captured["created_supplier_org_id"] = organization_id
        captured["supplier_input"] = input
        return SimpleNamespace(supplier_id=supplier_id)

    monkeypatch.setattr(
        procurement.SupplierService,
        "build_input_from_payload",
        staticmethod(fake_build_input_from_payload),
    )
    monkeypatch.setattr(
        procurement.SupplierService,
        "create_supplier",
        staticmethod(fake_create_supplier),
    )

    response = procurement.vendor_create(
        request=SimpleNamespace(),
        supplier_name="Acme Supplies Ltd",
        trading_name="Acme",
        email="ap@example.test",
        phone="555-0100",
        currency_code="NGN",
        payment_terms_days=45,
        auth=SimpleNamespace(user_id=user_id, organization_id=org_id),
        db=db,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith(
        "/procurement/vendors?success=Vendor+created"
    )
    assert captured["supplier_payload"]["supplier_code"] == "VEN-00004"
    assert captured["supplier_payload"]["supplier_name"] == "Acme Supplies Ltd"
    assert captured["supplier_payload"]["trading_name"] == "Acme"
    assert captured["supplier_payload"]["email"] == "ap@example.test"
    assert captured["supplier_payload"]["phone"] == "555-0100"
    assert captured["supplier_payload"]["currency_code"] == "NGN"
    assert captured["supplier_payload"]["payment_terms_days"] == 45
    assert captured["created_supplier_org_id"] == org_id
    assert supplier_id


def test_prequalification_create_requires_existing_supplier_selection():
    response = procurement.prequalification_create(
        request=SimpleNamespace(),
        supplier_id=None,
        application_date="2026-07-09",
        categories=None,
        categories_json=None,
        documents_verified=None,
        tax_clearance_valid=None,
        pension_compliance=None,
        itf_compliance=None,
        nsitf_compliance=None,
        auth=SimpleNamespace(user_id=uuid4(), organization_id=uuid4()),
        db=_FakeDB(),
    )

    assert response.status_code == 303
    assert "supplier_id%20must%20be%20a%20valid%20UUID" in response.headers["location"]


def test_vendor_prequalification_create_rejects_cross_tenant_supplier():
    service = VendorPrequalificationService(_FakeDB(scalar_result=None))

    with pytest.raises(ValidationError):
        service.create(
            uuid4(),
            PrequalificationCreate(
                supplier_id=uuid4(),
                application_date=date(2026, 7, 9),
            ),
        )
