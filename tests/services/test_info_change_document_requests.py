from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.people.hr.info_change_request import (
    InfoChangeOperation,
    InfoChangeStatus,
    InfoChangeType,
)
from app.services.people.hr.info_change_service import (
    DocumentBatchItemInput,
    InfoChangeService,
    PendingEvidence,
)


def test_validate_document_payload_enforces_self_service_allowlist():
    service = InfoChangeService(MagicMock())

    valid = service._validate_document_payload(
        {
            "document_type": "ID_PROOF",
            "document_name": "National ID",
            "description": "Front and back",
            "issue_date": "2026-07-01",
            "expiry_date": "2027-07-01",
        }
    )

    assert valid["document_type"] == "ID_PROOF"

    with pytest.raises(
        ValueError,
        match="cannot be submitted in self-service",
    ):
        service._validate_document_payload(
            {
                "document_type": "CONTRACT",
                "document_name": "Offer letter",
            }
        )


def test_submit_document_change_request_stores_pending_metadata():
    organization_id = uuid4()
    employee_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(employee_id=employee_id, person=None)
    service = InfoChangeService(db)
    service._notify_pending_batch = MagicMock()

    request = service.submit_document_change_request(
        organization_id,
        employee_id,
        proposed_changes={
            "document_type": "PASSPORT",
            "document_name": "International Passport",
        },
        pending_evidence=PendingEvidence(
            path=f"{organization_id}/{employee_id}/passport.pdf",
            file_name="passport.pdf",
            file_size=1024,
            mime_type="application/pdf",
            checksum="a" * 64,
        ),
    )

    assert request.change_type == InfoChangeType.DOCUMENT
    assert request.operation == InfoChangeOperation.CREATE
    assert request.status == InfoChangeStatus.PENDING
    assert request.pending_document_name == "passport.pdf"
    assert request.pending_document_checksum == "a" * 64
    assert request.proposed_changes["pending_original_filename"] == "passport.pdf"
    assert db.add.call_count == 2
    assert db.flush.call_count >= 2


def test_submit_document_change_request_rejects_duplicate_pending_upload():
    organization_id = uuid4()
    employee_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(employee_id=employee_id, person=None)
    service = InfoChangeService(db)
    service._notify_pending_batch = MagicMock()
    service.get_pending_requests = MagicMock(
        return_value=[
            SimpleNamespace(
                operation=InfoChangeOperation.CREATE,
                proposed_changes={
                    "document_type": "BANK_DETAILS",
                    "document_name": "Bank mandate",
                },
                pending_document_name="bank.pdf",
                pending_document_checksum="b" * 64,
            )
        ]
    )

    with pytest.raises(ValueError, match="already pending review"):
        service.submit_document_change_request(
            organization_id,
            employee_id,
            proposed_changes={
                "document_type": "BANK_DETAILS",
                "document_name": "Bank mandate",
            },
            pending_evidence=PendingEvidence(
                path=f"{organization_id}/{employee_id}/bank.pdf",
                file_name="bank.pdf",
                file_size=2048,
                mime_type="application/pdf",
                checksum="b" * 64,
            ),
        )

    db.add.assert_not_called()


def test_submit_document_change_batch_rejects_empty_batch():
    organization_id = uuid4()
    employee_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(employee_id=employee_id, person=None)
    service = InfoChangeService(db)

    with pytest.raises(ValueError, match="At least one non-blank row is required"):
        service.submit_document_change_batch(
            organization_id,
            employee_id,
            items=[],
        )


def test_submit_document_change_batch_rejects_duplicate_rows():
    organization_id = uuid4()
    employee_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(employee_id=employee_id, person=None)
    service = InfoChangeService(db)
    service._notify_pending_batch = MagicMock()

    evidence_one = PendingEvidence(
        path=f"{organization_id}/{employee_id}/passport-1.pdf",
        file_name="passport.pdf",
        file_size=1024,
        mime_type="application/pdf",
        checksum="c" * 64,
    )
    evidence_two = PendingEvidence(
        path=f"{organization_id}/{employee_id}/passport-2.pdf",
        file_name="passport.pdf",
        file_size=1024,
        mime_type="application/pdf",
        checksum="c" * 64,
    )

    with pytest.raises(ValueError, match="Duplicate document rows are not allowed"):
        service.submit_document_change_batch(
            organization_id,
            employee_id,
            items=[
                DocumentBatchItemInput(
                    proposed_changes={
                        "document_type": "PASSPORT",
                        "document_name": "International Passport",
                    },
                    pending_evidence=evidence_one,
                ),
                DocumentBatchItemInput(
                    proposed_changes={
                        "document_type": "PASSPORT",
                        "document_name": "International Passport",
                    },
                    pending_evidence=evidence_two,
                ),
            ],
        )
