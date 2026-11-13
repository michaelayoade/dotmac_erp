from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4
from urllib.parse import unquote

import pytest
from starlette.datastructures import FormData, UploadFile

from app.models.fleet.enums import MaintenanceStatus, MaintenanceType
from app.schemas.fleet.maintenance import MaintenanceCreate
from app.services.fleet.web.fleet_web import FleetWebService


TEST_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000002")


class _FakeDb:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _Auth(SimpleNamespace):
    def has_permission(self, _permission):
        return True


class _FakeRequest:
    def __init__(self, form_data):
        self._form_data = form_data

    async def form(self):
        return self._form_data


def test_maintenance_create_accepts_technician_name():
    data = MaintenanceCreate(
        vehicle_id=uuid4(),
        maintenance_type=MaintenanceType.PREVENTIVE,
        description="Routine service",
        scheduled_date=date(2026, 7, 10),
        technician_name="Mr IDOWU (Idowu Orile Enterprise)",
    )

    assert data.technician_name == "Mr IDOWU (Idowu Orile Enterprise)"


def test_maintenance_create_accepts_status():
    data = MaintenanceCreate(
        vehicle_id=uuid4(),
        maintenance_type=MaintenanceType.CORRECTIVE,
        description="Repair brake pads",
        scheduled_date=date(2026, 7, 10),
        status=MaintenanceStatus.IN_PROGRESS,
    )

    assert data.status == MaintenanceStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_maintenance_create_response_saves_uploaded_attachments(monkeypatch):
    db = _FakeDb()
    vehicle_id = uuid4()
    maintenance_id = uuid4()
    captured = {}
    upload = UploadFile(
        BytesIO(b"%PDF maintenance estimate"),
        filename="estimate.pdf",
        headers={"content-type": "application/pdf"},
    )

    class _MaintenanceService:
        def __init__(self, db_arg, org_id):
            captured["db"] = db_arg
            captured["org_id"] = org_id

        def create(self, schema):
            captured["schema"] = schema
            return SimpleNamespace(
                maintenance_id=maintenance_id, supplier_id=schema.supplier_id
            )

    async def _fake_save_attachment(self, **kwargs):
        captured["attachment_kwargs"] = kwargs

    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", _MaintenanceService
    )
    monkeypatch.setattr(
        FleetWebService,
        "_save_maintenance_attachment",
        _fake_save_attachment,
    )

    response = await FleetWebService(db).create_entity_response(
        _FakeRequest(
            FormData(
                [
                    ("vehicle_id", str(vehicle_id)),
                    ("maintenance_type", "CORRECTIVE"),
                    ("status", "SCHEDULED"),
                    ("description", "Replace damaged tyre"),
                    ("scheduled_date", "2026-07-10"),
                    ("maintenance_files", upload),
                ]
            )
        ),
        SimpleNamespace(organization_id=TEST_ORG_ID, user_id=TEST_USER_ID),
        db,
        "maintenance",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/fleet/maintenance"
    assert db.committed is True
    assert captured["org_id"] == TEST_ORG_ID
    assert captured["schema"].vehicle_id == vehicle_id
    assert captured["schema"].status == MaintenanceStatus.SCHEDULED
    assert captured["attachment_kwargs"]["organization_id"] == TEST_ORG_ID
    assert captured["attachment_kwargs"]["maintenance_id"] == maintenance_id
    assert captured["attachment_kwargs"]["upload"] is upload
    assert captured["attachment_kwargs"]["uploaded_by"] == TEST_USER_ID


@pytest.mark.asyncio
async def test_maintenance_rejects_both_financial_options(monkeypatch):
    from unittest.mock import Mock

    db = _FakeDb()
    service = Mock()
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", lambda *_: service
    )

    response = await FleetWebService(db).create_entity_response(
        _FakeRequest(
            FormData(
                {
                    "vehicle_id": str(uuid4()),
                    "maintenance_type": "CORRECTIVE",
                    "description": "Replace damaged tyre",
                    "scheduled_date": "2026-07-10",
                    "create_draft_invoice": "1",
                    "submit_reimbursement": "1",
                }
            )
        ),
        _Auth(organization_id=TEST_ORG_ID, user_id=TEST_USER_ID),
        db,
        "maintenance",
    )

    assert "Choose only one financial handling option" in unquote(
        response.headers["location"]
    )
    assert db.rolled_back is True
    service.create.assert_not_called()


def test_draft_invoice_maps_maintenance_fields(monkeypatch):
    from decimal import Decimal

    from app.services.fleet.maintenance_finance import (
        SupplierInvoiceService,
        create_draft_invoice,
    )

    supplier_id, vehicle_id, cost_center_id, invoice_id = (uuid4() for _ in range(4))
    scheduled = date(2026, 7, 10)
    record = SimpleNamespace(
        supplier_id=supplier_id,
        vehicle_id=vehicle_id,
        vehicle=SimpleNamespace(assigned_cost_center_id=cost_center_id),
        scheduled_date=scheduled,
        estimated_cost=Decimal("100.50"),
        notes="Replace brake pads",
    )
    supplier = SimpleNamespace(supplier_id=supplier_id, currency_code="NGN")
    db = SimpleNamespace(scalar=lambda query: supplier)

    def create_invoice(db_arg, org_id, payload, actor_id):
        assert db_arg is db
        assert org_id == TEST_ORG_ID
        assert actor_id == TEST_USER_ID
        assert payload.supplier_id == supplier_id
        assert payload.vehicle_id == vehicle_id
        assert (
            payload.invoice_date
            == payload.due_date
            == payload.received_date
            == scheduled
        )
        assert payload.purpose == record.notes
        (line,) = payload.lines
        assert line.item_id is None
        assert line.expense_account_id is None
        assert line.cost_center_id == cost_center_id
        assert line.unit_price == record.estimated_cost
        return SimpleNamespace(invoice_id=invoice_id)

    monkeypatch.setattr(SupplierInvoiceService, "create_invoice", create_invoice)
    create_draft_invoice(
        db,
        TEST_ORG_ID,
        record,
        FormData({"create_draft_invoice": "1"}),
        SimpleNamespace(
            has_permission=lambda permission: permission == "ap:invoices:create"
        ),
        TEST_USER_ID,
    )
    assert record.invoice_id == invoice_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "financial_option", ["create_draft_invoice", "submit_reimbursement"]
)
async def test_maintenance_financial_submission_runs_once(
    monkeypatch, financial_option
):
    from unittest.mock import AsyncMock, Mock

    from app.services.fleet import maintenance_finance

    db = _FakeDb()
    submission_id = uuid4()
    vehicle_id = uuid4()
    service = Mock()
    service.has_submission.side_effect = [False, True]
    service.create.return_value = SimpleNamespace(maintenance_id=submission_id)
    create_invoice = Mock()
    submit_reimbursement = AsyncMock()
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", lambda *_: service
    )
    monkeypatch.setattr(
        FleetWebService, "_resolve_active_employee_id_for_auth", lambda *_: uuid4()
    )
    monkeypatch.setattr(maintenance_finance, "create_draft_invoice", create_invoice)
    monkeypatch.setattr(
        maintenance_finance, "submit_reimbursement", submit_reimbursement
    )
    auth = SimpleNamespace(
        organization_id=TEST_ORG_ID, user_id=TEST_USER_ID, has_permission=lambda _: True
    )
    request = _FakeRequest(
        FormData(
            {
                "maintenance_submission_id": str(submission_id),
                "vehicle_id": str(vehicle_id),
                "maintenance_type": "CORRECTIVE",
                "description": "Replace damaged tyre",
                "scheduled_date": "2026-07-10",
                financial_option: "1",
            }
        )
    )
    web = FleetWebService(db)
    first = await web.create_entity_response(request, auth, db, "maintenance")
    retry = await web.create_entity_response(request, auth, db, "maintenance")

    assert (
        first.headers["location"] == retry.headers["location"] == "/fleet/maintenance"
    )
    assert db.committed
    assert not db.rolled_back
    service.create.assert_called_once()
    assert service.create.call_args.kwargs["maintenance_id"] == submission_id
    assert create_invoice.call_count == 1
    assert submit_reimbursement.await_count == (
        financial_option == "submit_reimbursement"
    )


@pytest.mark.asyncio
async def test_reimbursement_maps_items_and_submits_to_standard_routing(monkeypatch):
    from decimal import Decimal
    from unittest.mock import Mock

    from app.services import expense
    from app.services.fleet import maintenance_finance

    employee_id, vehicle_id, cost_center_id, claim_id, category_id = (
        uuid4() for _ in range(5)
    )
    service = Mock()
    service.create_claim.return_value = SimpleNamespace(claim_id=claim_id)
    service.submit_claim.return_value = SimpleNamespace(success=True)
    monkeypatch.setitem(expense.__dict__, "ExpenseService", lambda _: service)
    db = SimpleNamespace(scalars=lambda _: [category_id])
    record = SimpleNamespace(
        maintenance_id=uuid4(),
        description="Routine service",
        vehicle_id=vehicle_id,
        vehicle=SimpleNamespace(assigned_cost_center_id=cost_center_id),
    )
    form = FormData(
        [
            ("claim_date", date.today().isoformat()),
            ("claim_category", str(category_id)),
            ("claim_category", str(category_id)),
            ("claim_description", "Oil"),
            ("claim_description", "Labour"),
            ("claim_amount", "100.00"),
            ("claim_amount", "50.25"),
            ("claim_approver", str(uuid4())),
            ("recipient_name", "Test Employee"),
            ("recipient_bank_name", "Test Bank"),
            ("recipient_bank_code", "001"),
            ("recipient_account_number", "0123456789"),
        ]
    )
    await maintenance_finance.submit_reimbursement(
        db, TEST_ORG_ID, record, form, employee_id, TEST_USER_ID, []
    )

    claim = service.create_claim.call_args.kwargs
    assert claim["employee_id"] == employee_id
    assert claim["vehicle_id"] == vehicle_id
    assert claim["cost_center_id"] == cost_center_id
    assert claim.get("requested_approver_id") is None
    assert [item["claimed_amount"] for item in claim["items"]] == [
        Decimal("100.00"),
        Decimal("50.25"),
    ]
    service.submit_claim.assert_called_once_with(
        TEST_ORG_ID, claim_id, actor_id=TEST_USER_ID, defer_approver_email=True
    )
    assert service.create_claim.return_value.source_system == "fleet_maintenance"
    assert service.create_claim.return_value.source_reference == str(
        record.maintenance_id
    )


@pytest.mark.asyncio
async def test_unfinanced_maintenance_can_add_finance_during_edit(monkeypatch):
    from unittest.mock import AsyncMock, Mock

    from app.services.fleet import maintenance_finance

    db = _FakeDb()
    maintenance_id = uuid4()
    record = SimpleNamespace(
        maintenance_id=maintenance_id,
        status=MaintenanceStatus.SCHEDULED,
        invoice_id=None,
    )
    service = Mock()
    service.get_for_update.return_value = record
    service.update.return_value = record
    create_invoice = Mock()
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", lambda *_: service
    )
    monkeypatch.setattr(
        maintenance_finance, "finance_handled_maintenance_ids", lambda *_: set()
    )
    monkeypatch.setattr(maintenance_finance, "create_draft_invoice", create_invoice)
    monkeypatch.setattr(FleetWebService, "_save_maintenance_attachment", AsyncMock())

    response = await FleetWebService(db).update_entity_response(
        _FakeRequest(
            FormData(
                {
                    "maintenance_type": "CORRECTIVE",
                    "description": "Replace damaged tyre",
                    "scheduled_date": "2026-07-10",
                    "create_draft_invoice": "1",
                }
            )
        ),
        _Auth(organization_id=TEST_ORG_ID, user_id=TEST_USER_ID),
        db,
        "maintenance",
        maintenance_id,
    )

    assert response.headers["location"] == "/fleet/maintenance"
    assert db.committed is True
    service.update.assert_called_once()
    create_invoice.assert_called_once()


@pytest.mark.asyncio
async def test_financed_maintenance_cannot_be_edited(monkeypatch):
    from unittest.mock import Mock

    from app.services.fleet import maintenance_finance

    db = _FakeDb()
    maintenance_id = uuid4()
    record = SimpleNamespace(
        maintenance_id=maintenance_id,
        status=MaintenanceStatus.SCHEDULED,
        invoice_id=uuid4(),
    )
    service = Mock()
    service.get_for_update.return_value = record
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", lambda *_: service
    )
    monkeypatch.setattr(
        maintenance_finance,
        "finance_handled_maintenance_ids",
        lambda *_: {maintenance_id},
    )

    response = await FleetWebService(db).update_entity_response(
        _FakeRequest(FormData({"description": "Changed"})),
        _Auth(organization_id=TEST_ORG_ID, user_id=TEST_USER_ID),
        db,
        "maintenance",
        maintenance_id,
    )

    assert "financial handling cannot be edited" in unquote(
        response.headers["location"]
    )
    assert db.rolled_back is True
    service.update.assert_not_called()


@pytest.mark.parametrize(
    "status", [MaintenanceStatus.SCHEDULED, MaintenanceStatus.IN_PROGRESS]
)
def test_cancel_maintenance_soft_cancels_open_record(monkeypatch, status):
    from unittest.mock import Mock

    from app.services.fleet import maintenance_finance

    db = _FakeDb()
    maintenance_id = uuid4()
    service = Mock()
    service.get_for_update.return_value = SimpleNamespace(
        maintenance_id=maintenance_id,
        status=status,
    )
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", lambda *_: service
    )
    cancel_finance = Mock()
    monkeypatch.setattr(maintenance_finance, "cancel_finance_handling", cancel_finance)
    person_id = uuid4()
    auth = _Auth(
        organization_id=TEST_ORG_ID,
        user_id=TEST_USER_ID,
        person_id=person_id,
    )

    response = FleetWebService(db).cancel_maintenance_response(auth, maintenance_id)

    cancel_finance.assert_called_once_with(
        db,
        TEST_ORG_ID,
        service.get_for_update.return_value,
        TEST_USER_ID,
        person_id,
    )
    service.cancel.assert_called_once_with(
        maintenance_id, reason="Cancelled from maintenance schedule"
    )
    assert db.committed is True
    assert response.headers["location"].startswith("/fleet/maintenance?success=")


def test_start_maintenance_from_detail_commits_transition(monkeypatch):
    from unittest.mock import Mock

    db = _FakeDb()
    maintenance_id = uuid4()
    service = Mock()
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", lambda *_: service
    )

    response = FleetWebService(db).start_maintenance_response(
        _Auth(organization_id=TEST_ORG_ID), maintenance_id
    )

    service.get_for_update.assert_called_once_with(maintenance_id)
    service.start.assert_called_once_with(maintenance_id)
    assert db.committed is True
    assert response.headers["location"].startswith(
        f"/fleet/maintenance/{maintenance_id}?success="
    )


@pytest.mark.asyncio
async def test_complete_maintenance_from_detail_validates_and_commits(monkeypatch):
    from unittest.mock import Mock

    db = _FakeDb()
    maintenance_id = uuid4()
    service = Mock()
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", lambda *_: service
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            csrf_form=FormData(
                {
                    "csrf_token": "test-token",
                    "actual_cost": "1250.50",
                    "completed_date": "2026-11-13",
                    "work_performed": "Changed brake pads",
                    "odometer_at_service": "45000",
                }
            )
        )
    )

    response = await FleetWebService(db).complete_maintenance_response(
        request, _Auth(organization_id=TEST_ORG_ID), maintenance_id
    )

    service.get_for_update.assert_called_once_with(maintenance_id)
    service.complete.assert_called_once()
    saved_id, data = service.complete.call_args.args
    assert saved_id == maintenance_id
    assert data.actual_cost == Decimal("1250.50")
    assert data.completed_date == date(2026, 11, 13)
    assert data.odometer_at_service == 45000
    assert data.work_performed == "Changed brake pads"
    assert db.committed is True
    assert response.headers["location"].startswith(
        f"/fleet/maintenance/{maintenance_id}?success="
    )


@pytest.mark.asyncio
async def test_complete_maintenance_requires_actual_cost(monkeypatch):
    from unittest.mock import Mock

    db = _FakeDb()
    maintenance_id = uuid4()
    service = Mock()
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", lambda *_: service
    )
    request = SimpleNamespace(
        state=SimpleNamespace(csrf_form=FormData({"actual_cost": ""}))
    )

    response = await FleetWebService(db).complete_maintenance_response(
        request, _Auth(organization_id=TEST_ORG_ID), maintenance_id
    )

    assert db.rolled_back is True
    assert db.committed is False
    service.complete.assert_not_called()
    assert "?error=" in response.headers["location"]


def test_cancel_maintenance_rolls_back_when_finance_requires_reversal(monkeypatch):
    from unittest.mock import Mock

    from app.services.common import ValidationError
    from app.services.fleet import maintenance_finance

    db = _FakeDb()
    maintenance_id = uuid4()
    service = Mock()
    service.get_for_update.return_value = SimpleNamespace(
        maintenance_id=maintenance_id,
        status=MaintenanceStatus.SCHEDULED,
    )
    monkeypatch.setattr(
        "app.services.fleet.web.fleet_web.MaintenanceService", lambda *_: service
    )
    monkeypatch.setattr(
        maintenance_finance,
        "cancel_finance_handling",
        Mock(side_effect=ValidationError("Finance must reverse it first.")),
    )
    auth = _Auth(
        organization_id=TEST_ORG_ID,
        user_id=TEST_USER_ID,
        person_id=uuid4(),
    )

    response = FleetWebService(db).cancel_maintenance_response(auth, maintenance_id)

    assert "Finance%20must%20reverse%20it%20first" in response.headers["location"]
    assert db.rolled_back is True
    assert db.committed is False
    service.cancel.assert_not_called()


def test_cancel_finance_handling_voids_invoice_and_cancels_open_claim(monkeypatch):
    from unittest.mock import Mock

    from app.models.expense import ExpenseClaimStatus
    from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
    from app.services import expense
    from app.services.fleet.maintenance_finance import (
        SupplierInvoiceService,
        cancel_finance_handling,
    )

    maintenance_id, invoice_id, claim_id = (uuid4() for _ in range(3))
    invoice = SimpleNamespace(
        invoice_id=invoice_id,
        status=SupplierInvoiceStatus.DRAFT,
    )
    claim = SimpleNamespace(
        claim_id=claim_id,
        status=ExpenseClaimStatus.PENDING_APPROVAL,
    )
    db = Mock()
    db.scalar.return_value = invoice
    db.scalars.return_value.all.return_value = [claim]
    void_invoice = Mock()
    expense_service = Mock()
    monkeypatch.setattr(SupplierInvoiceService, "void_invoice", void_invoice)
    monkeypatch.setitem(expense.__dict__, "ExpenseService", lambda _: expense_service)
    record = SimpleNamespace(
        maintenance_id=maintenance_id,
        invoice_id=invoice_id,
    )

    person_id = uuid4()
    cancel_finance_handling(db, TEST_ORG_ID, record, TEST_USER_ID, person_id)

    reason = f"Fleet maintenance {maintenance_id} was cancelled."
    void_invoice.assert_called_once_with(
        db, TEST_ORG_ID, invoice_id, TEST_USER_ID, reason
    )
    expense_service.cancel_claim.assert_called_once_with(
        TEST_ORG_ID,
        claim_id,
        reason=reason,
        actor_id=person_id,
    )


@pytest.mark.parametrize(
    "invoice_status",
    ["POSTED", "PARTIALLY_PAID", "PAID"],
)
def test_cancel_finance_handling_blocks_gl_impacted_invoice(invoice_status):
    from unittest.mock import Mock

    from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
    from app.services.common import ValidationError
    from app.services.fleet.maintenance_finance import cancel_finance_handling

    db = Mock()
    db.scalar.return_value = SimpleNamespace(
        invoice_id=uuid4(),
        status=SupplierInvoiceStatus(invoice_status),
    )
    record = SimpleNamespace(
        maintenance_id=uuid4(),
        invoice_id=db.scalar.return_value.invoice_id,
    )

    with pytest.raises(ValidationError, match="Finance must reverse it first"):
        cancel_finance_handling(db, TEST_ORG_ID, record, TEST_USER_ID, uuid4())


@pytest.mark.parametrize("claim_status", ["APPROVED", "PAID"])
def test_cancel_finance_handling_blocks_irreversible_claim(claim_status):
    from unittest.mock import Mock

    from app.models.expense import ExpenseClaimStatus
    from app.services.common import ValidationError
    from app.services.fleet.maintenance_finance import cancel_finance_handling

    db = Mock()
    db.scalars.return_value.all.return_value = [
        SimpleNamespace(claim_id=uuid4(), status=ExpenseClaimStatus(claim_status))
    ]
    record = SimpleNamespace(maintenance_id=uuid4(), invoice_id=None)

    with pytest.raises(ValidationError, match="Finance must reverse it first"):
        cancel_finance_handling(db, TEST_ORG_ID, record, TEST_USER_ID, uuid4())


def test_expense_claim_ids_are_resolved_by_tenant_scoped_maintenance_reference():
    from unittest.mock import Mock

    from app.services.fleet.maintenance_finance import (
        expense_claim_ids_by_maintenance,
    )

    first_maintenance_id, second_maintenance_id = uuid4(), uuid4()
    first_claim_id, second_claim_id = uuid4(), uuid4()
    db = Mock()
    db.execute.return_value.all.return_value = [
        (first_claim_id, str(first_maintenance_id), None),
        (
            second_claim_id,
            None,
            f"Fleet maintenance {second_maintenance_id}",
        ),
    ]
    records = [
        SimpleNamespace(maintenance_id=first_maintenance_id),
        SimpleNamespace(maintenance_id=second_maintenance_id),
    ]

    result = expense_claim_ids_by_maintenance(db, TEST_ORG_ID, records)

    assert result == {
        first_maintenance_id: first_claim_id,
        second_maintenance_id: second_claim_id,
    }
    statement = db.execute.call_args.args[0]
    assert "expense_claim.organization_id" in str(statement)


def test_maintenance_list_actions_are_inline_and_confirm_cancellation():
    template = Path("templates/fleet/maintenance.html").read_text(encoding="utf-8")

    assert 'edit_href="/fleet/maintenance/"' in template
    assert '"action": "/fleet/maintenance/"' in template
    assert "Do you still want to cancel this maintenance" in template
    assert "record.maintenance_id not in finance_handled_ids" in template


def test_maintenance_list_action_macro_receives_request_context():
    from app.templates import templates

    source = Path("templates/fleet/maintenance.html").read_text(encoding="utf-8")
    macro_import = next(
        line
        for line in source.splitlines()
        if line.startswith('{% from "components/macros.html" import ')
    )
    probe = templates.env.from_string(
        f"{macro_import}\n{{{{ action_buttons(delete_action=delete_action) }}}}"
    )
    csrf_form = '<input type="hidden" name="csrf_token" value="token">'
    request = SimpleNamespace(state=SimpleNamespace(csrf_form=csrf_form))

    rendered = probe.render(
        request=request,
        delete_action={
            "action": "/fleet/maintenance/record-id/cancel",
            "title": "Cancel",
            "aria_label": "Cancel scheduled maintenance",
            "icon": "trash",
            "confirm": "Cancel this maintenance?",
        },
    )

    assert csrf_form in rendered
    assert 'action="/fleet/maintenance/record-id/cancel"' in rendered


def test_maintenance_details_show_role_aware_financial_reference_links():
    maintenance_template = Path("templates/fleet/maintenance_detail.html").read_text(
        encoding="utf-8"
    )
    vehicle_template = Path("templates/fleet/vehicle_detail.html").read_text(
        encoding="utf-8"
    )

    for template in (maintenance_template, vehicle_template):
        assert "/finance/ap/invoices/{{ record.invoice_id }}" in template
        assert "/expense/claims/{{ expense_claim_id }}" in template
        assert 'auth.has_permission("ap:invoices:read")' in template
        assert 'auth.has_module_access("expense")' in template
        assert "not auth.is_technician" in template
