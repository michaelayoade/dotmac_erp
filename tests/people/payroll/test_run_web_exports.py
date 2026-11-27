from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
from types import SimpleNamespace
from uuid import uuid4

from fastapi.responses import StreamingResponse

from app.models.people.payroll.payroll_entry import PayrollEntryStatus
from app.services.people.payroll.paye_export import PAYEExportResult
from app.services.people.payroll.web.run_web import RunWebService


def _make_auth(org_id):
    return SimpleNamespace(organization_id=str(org_id))


def _make_entry(org_id):
    return SimpleNamespace(
        organization_id=org_id,
        status=PayrollEntryStatus.POSTED,
        payroll_year=2026,
        payroll_month=11,
        start_date=date(2026, 11, 1),
    )


def test_export_paye_response_uses_requested_format(monkeypatch):
    org_id = uuid4()
    entry_id = uuid4()
    calls: dict[str, object] = {}

    class FakePAYEExportService:
        def __init__(self, db):
            self.db = db

        def generate_export(
            self,
            organization_id,
            year,
            month,
            paye_format="lirs",
            entry_id=None,
        ):
            calls["organization_id"] = organization_id
            calls["year"] = year
            calls["month"] = month
            calls["paye_format"] = paye_format
            calls["entry_id"] = entry_id
            return PAYEExportResult(
                content=b"tax",
                filename="FCTIRS_PAYE_2026_11.csv",
                content_type="text/csv",
                employee_count=1,
                total_tax=0,
                errors=[],
            )

    monkeypatch.setattr(
        "app.services.people.payroll.paye_export.PAYEExportService",
        FakePAYEExportService,
    )

    db = SimpleNamespace(get=lambda _model, _id: _make_entry(org_id))

    response = RunWebService().export_paye_response(
        _make_auth(org_id),
        db,
        str(entry_id),
        "fctirs",
    )

    assert isinstance(response, StreamingResponse)
    assert calls["organization_id"] == org_id
    assert calls["year"] == 2026
    assert calls["month"] == 11
    assert calls["paye_format"] == "fctirs"
    assert calls["entry_id"] == entry_id
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="FCTIRS_PAYE_2026_11.csv"'
    )


def test_bank_export_mismatch_refuses_before_file_generation(monkeypatch, caplog):
    from dotmac_payroll import PayrollTotalsViolation

    from app.services.finance.banking.bank_upload import BankUploadService
    from app.services.people.payroll import run_totals

    org_id, entry_id, bank_id = uuid4(), uuid4(), uuid4()
    entry = SimpleNamespace(
        organization_id=org_id,
        entry_id=entry_id,
        source_bank_account_id=bank_id,
        payroll_month=11,
        payroll_year=2026,
    )
    bank = SimpleNamespace(organization_id=org_id, account_number="0123456789")
    slip = SimpleNamespace(
        slip_id=uuid4(),
        slip_number="SAL-001",
        employee_name="Private Employee",
        employee=None,
        bank_account_name="Private Employee",
        bank_account_number="0123456789",
        bank_name="Private Bank",
        bank_branch_code="058",
        net_pay=Decimal("74123.00"),
    )
    db = MagicMock()
    db.get.side_effect = [entry, bank]
    db.scalars.return_value.all.return_value = [slip]
    generate = MagicMock()
    monkeypatch.setattr(BankUploadService, "generate_upload", generate)
    validate = MagicMock(side_effect=PayrollTotalsViolation("payment total mismatch"))
    monkeypatch.setattr(run_totals, "validate_run_payment_items", validate)

    response = RunWebService().bank_upload_response(
        _make_auth(org_id), db, str(entry_id)
    )

    assert response.status_code == 303
    assert "Payroll%20payment%20total" in response.headers["location"]
    validate.assert_called_once()
    generate.assert_not_called()
    db.commit.assert_not_called()
    db.add.assert_not_called()
    for private in ("74123", "Private Employee", "0123456789"):
        assert private not in response.headers["location"] + caplog.text
