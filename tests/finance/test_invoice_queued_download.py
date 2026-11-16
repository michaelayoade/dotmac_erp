"""Queued invoice downloads retain file type and request ownership."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

pytest.importorskip("dotmac_files")
pytest.importorskip("dotmac_kernel")

from app.models.finance.rpt.report_instance import ReportStatus  # noqa: E402
from app.services.finance.rpt import async_exports  # noqa: E402


@pytest.mark.parametrize("code", ["AP_INVOICES", "AR_INVOICES"])
@pytest.mark.parametrize(
    "format,suffix,mime",
    [
        (
            "XLSX",
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ("CSV", "csv", "text/csv"),
    ],
)
def test_queued_invoice_download_format_and_scope(
    monkeypatch, code, format, suffix, mime
):
    org_id, user_id, instance_id = uuid4(), uuid4(), uuid4()
    instance = SimpleNamespace(
        instance_id=instance_id,
        organization_id=org_id,
        generated_by_user_id=user_id,
        report_def_id=uuid4(),
        output_format=format,
        status=ReportStatus.COMPLETED,
        output_file_path=f"s3://generated_reports/{org_id}/export.{suffix}",
    )
    db = MagicMock()
    db.get.return_value = instance
    db.scalar.return_value = code
    storage = MagicMock()
    storage.exists.return_value = True
    storage.stream.return_value = (
        iter([b"PK" if suffix == "xlsx" else b"csv"]),
        mime,
        2,
    )
    monkeypatch.setattr(async_exports, "get_storage", lambda: storage)

    body, filename, media_type, _ = async_exports.get_completed_export_for_download(
        db, org_id, user_id, str(instance_id), report_code=code
    )
    assert filename.endswith("." + suffix)
    assert media_type == mime
    assert next(body)

    with pytest.raises(HTTPException) as wrong_user:
        async_exports.get_completed_export_for_download(
            db, org_id, uuid4(), str(instance_id), report_code=code
        )
    assert wrong_user.value.status_code == 404

    db.scalar.return_value = "OTHER_REPORT"
    with pytest.raises(HTTPException) as wrong_report:
        async_exports.get_completed_export_for_download(
            db, org_id, user_id, str(instance_id), report_code=code
        )
    assert wrong_report.value.status_code == 404
