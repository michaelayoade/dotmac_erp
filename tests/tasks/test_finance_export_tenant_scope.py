"""Queued finance exports carry tenant scope into the worker."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.finance.rpt.report_instance import ReportStatus
from app.services.finance.rpt import async_exports
from app.tasks import finance


def test_queue_dispatches_persisted_organization_id(monkeypatch) -> None:
    instance_id = uuid4()
    organization_id = uuid4()
    requested_organization_id = uuid4()
    instance = SimpleNamespace(
        instance_id=instance_id,
        organization_id=organization_id,
    )
    delay = MagicMock()

    monkeypatch.setattr(
        async_exports.ReportInstanceService,
        "queue_report",
        MagicMock(return_value=instance),
    )
    monkeypatch.setattr(finance.process_ar_invoices_export, "delay", delay)

    queued = async_exports.queue_background_export(
        MagicMock(),
        requested_organization_id,
        uuid4(),
        report_code="AR_INVOICES",
        parameters={},
        output_format="XLSX",
    )

    assert queued is instance
    delay.assert_called_once_with(str(instance_id), str(organization_id))


def test_list_export_uses_tenant_scope_from_task_payload(monkeypatch) -> None:
    instance_id = uuid4()
    organization_id = uuid4()
    report_definition_id = uuid4()
    instance = SimpleNamespace(
        instance_id=instance_id,
        organization_id=organization_id,
        report_def_id=report_definition_id,
        status=ReportStatus.COMPLETED,
    )
    db = MagicMock()
    db.get.return_value = instance
    db.scalar.return_value = "AR_INVOICES"
    opened: list[object] = []

    @contextmanager
    def scoped_session(scope):
        opened.append(scope)
        yield db

    monkeypatch.setattr(finance, "session_for_org", scoped_session)

    result = finance._process_list_export(
        str(instance_id),
        str(organization_id),
        "AR_INVOICES",
    )

    assert opened == [organization_id]
    assert result == {"success": False, "status": ReportStatus.COMPLETED.value}
    db.get.assert_called_once_with(finance.ReportInstance, instance_id)
