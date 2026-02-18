from __future__ import annotations

from datetime import date
from io import BytesIO
from unittest.mock import MagicMock, patch

from starlette.datastructures import Headers, UploadFile

from app.services.file_upload import (
    FileTooLargeError,
    InvalidContentTypeError,
    UploadResult,
)
from app.services.people.self_service_web import SelfServiceWebService


def _make_auth():
    auth = MagicMock()
    auth.organization_id = "00000000-0000-0000-0000-000000000001"
    auth.person_id = "00000000-0000-0000-0000-000000000002"
    return auth


def test_self_service_expense_claim_create_redirects_on_invalid_receipt_content_type():
    svc = SelfServiceWebService()
    auth = _make_auth()
    db = MagicMock()

    upload = UploadFile(
        filename="receipt.docx",
        file=BytesIO(b"PK\x03\x04..."),
        headers=Headers(
            {
                "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            }
        ),
    )

    upload_svc = MagicMock()
    upload_svc.save.side_effect = InvalidContentTypeError(
        "Content type 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' not allowed."
    )

    with (
        patch.object(
            svc, "_get_employee_id", return_value="00000000-0000-0000-0000-000000000003"
        ),
        patch(
            "app.services.file_upload.get_expense_receipt_upload",
            return_value=upload_svc,
        ),
        patch("app.services.people.self_service_web.ExpenseService") as expense_service,
    ):
        response = svc.expense_claim_create_response(
            auth,
            db,
            claim_date=date(2026, 2, 17),
            purpose="Test",
            expense_date=date(2026, 2, 17),
            category_id="00000000-0000-0000-0000-000000000010",
            description="Test",
            claimed_amount="12.34",
            recipient_bank_code="001",
            recipient_account_number="1234567890",
            requested_approver_id="00000000-0000-0000-0000-000000000020",
            receipt_file=upload,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/people/self/expenses?error=")
    upload_svc.delete.assert_not_called()
    expense_service.assert_not_called()


def test_self_service_expense_claim_create_cleans_up_partial_uploads():
    svc = SelfServiceWebService()
    auth = _make_auth()
    db = MagicMock()

    upload1 = UploadFile(
        filename="r1.pdf",
        file=BytesIO(b"%PDF-1.7 test"),
        headers=Headers({"content-type": "application/pdf"}),
    )
    upload2 = UploadFile(
        filename="r2.pdf",
        file=BytesIO(b"%PDF-1.7 test"),
        headers=Headers({"content-type": "application/pdf"}),
    )

    upload_svc = MagicMock()
    upload_svc.save.side_effect = [
        UploadResult(
            s3_key="expense_receipts/00000000-0000-0000-0000-000000000001/file1.pdf",
            relative_path="00000000-0000-0000-0000-000000000001/file1.pdf",
            filename="file1.pdf",
            file_size=123,
            checksum="0" * 64,
        ),
        FileTooLargeError("File too large"),
    ]

    with (
        patch.object(
            svc, "_get_employee_id", return_value="00000000-0000-0000-0000-000000000003"
        ),
        patch(
            "app.services.file_upload.get_expense_receipt_upload",
            return_value=upload_svc,
        ),
        patch("app.services.people.self_service_web.ExpenseService") as expense_service,
    ):
        response = svc.expense_claim_create_response(
            auth,
            db,
            claim_date=date(2026, 2, 17),
            purpose="Test",
            expense_date=date(2026, 2, 17),
            category_id="00000000-0000-0000-0000-000000000010",
            description="Test",
            claimed_amount="12.34",
            recipient_bank_code="001",
            recipient_account_number="1234567890",
            requested_approver_id="00000000-0000-0000-0000-000000000020",
            receipt_files=[upload1, upload2],
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/people/self/expenses?error=")
    upload_svc.delete.assert_called_once_with(
        "expense_receipts/00000000-0000-0000-0000-000000000001/file1.pdf"
    )
    expense_service.assert_not_called()
