"""A conflicting self-service submission must produce an inline error, never a 500.

The batch refactor made ordinary retries hit an item-level conflict: because
submissions sit PENDING until HR approves, a staff member who does not see their
upload appear submits again and trips "A matching request is already pending
review". That conflict is raised as a ValueError and is *meant* to render as an
inline field error — but the rows handed back to the template still carried the
raw UploadFile, so rendering them raised TypeError and the user got HTTP 500
instead. These tests pin both halves: the conflict is detected for each
file-bearing section, and the resulting error re-render stays serialisable.
"""

from __future__ import annotations

import io
import json
from typing import Any

import jinja2
import pytest
from starlette.datastructures import UploadFile

from app.models.people.hr.info_change_request import (
    InfoChangeOperation,
    InfoChangeType,
)
from app.services.people.hr.info_change_service import InfoChangeService
from app.services.people.self_service_web import SelfServiceWebService

ORG_ID = "11111111-1111-1111-1111-111111111111"
EMPLOYEE_ID = "22222222-2222-2222-2222-222222222222"

# One realistic payload per file-bearing section, keyed by the fields that
# actually take part in that section's duplicate key.
SECTION_PAYLOADS: dict[InfoChangeType, dict[str, Any]] = {
    InfoChangeType.QUALIFICATION: {
        "qualification_type": "DEGREE",
        "qualification_name": "BSc Computer Science",
        "institution_name": "University of Abuja",
        "start_date": "2018-09-01",
        "end_date": "2022-07-01",
    },
    InfoChangeType.CERTIFICATION: {
        "certification_name": "CCNA",
        "issuing_authority": "Cisco",
        "credential_id": "CSCO-12345",
    },
    InfoChangeType.DOCUMENT: {
        "document_type": "CERTIFICATE",
        "document_name": "Degree certificate",
        "pending_original_filename": "degree.pdf",
        "pending_checksum": "abc123",
    },
}


class _PendingRequest:
    def __init__(self, change_type, proposed_changes):
        self.operation = InfoChangeOperation.CREATE
        self.change_type = change_type
        self.proposed_changes = proposed_changes
        self.target_record_id = None


def _service_with_pending(monkeypatch, pending):
    """InfoChangeService with the DB reads stubbed — the conflict rule is the unit."""
    service = InfoChangeService(db=None)
    monkeypatch.setattr(service, "expire_requests", lambda *a, **k: None)
    monkeypatch.setattr(service, "get_pending_requests", lambda *a, **k: pending)
    monkeypatch.setattr(
        service, "_assert_no_approved_duplicate", lambda *a, **k: None, raising=False
    )
    return service


@pytest.mark.parametrize("change_type", list(SECTION_PAYLOADS))
def test_matching_pending_request_is_rejected_per_section(monkeypatch, change_type):
    payload = SECTION_PAYLOADS[change_type]
    service = _service_with_pending(
        monkeypatch, [_PendingRequest(change_type, dict(payload))]
    )

    with pytest.raises(ValueError) as excinfo:
        service._assert_extended_conflicts(
            ORG_ID,
            EMPLOYEE_ID,
            change_type=change_type,
            operation=InfoChangeOperation.CREATE,
            proposed_changes=dict(payload),
            target_record_id=None,
        )

    assert "already pending review" in str(excinfo.value)


@pytest.mark.parametrize("change_type", list(SECTION_PAYLOADS))
def test_a_genuinely_different_item_is_not_a_conflict(monkeypatch, change_type):
    """Guard against the conflict rule being so broad it blocks legitimate entries."""
    payload = SECTION_PAYLOADS[change_type]
    other = dict(payload)
    distinguishing_field = next(iter(payload))
    other[distinguishing_field] = "something else entirely"
    service = _service_with_pending(monkeypatch, [_PendingRequest(change_type, other)])

    service._assert_extended_conflicts(
        ORG_ID,
        EMPLOYEE_ID,
        change_type=change_type,
        operation=InfoChangeOperation.CREATE,
        proposed_changes=dict(payload),
        target_record_id=None,
    )


@pytest.mark.parametrize("change_type", list(SECTION_PAYLOADS))
def test_no_pending_requests_means_no_conflict(monkeypatch, change_type):
    service = _service_with_pending(monkeypatch, [])

    service._assert_extended_conflicts(
        ORG_ID,
        EMPLOYEE_ID,
        change_type=change_type,
        operation=InfoChangeOperation.CREATE,
        proposed_changes=dict(SECTION_PAYLOADS[change_type]),
        target_record_id=None,
    )


@pytest.mark.parametrize("change_type", list(SECTION_PAYLOADS))
def test_conflict_re_render_is_serialisable_for_each_section(change_type):
    """The half that used to 500: rows still hold the UploadFile on the error path."""
    row: dict[str, Any] = dict(SECTION_PAYLOADS[change_type])
    row["_errors"] = {"row": "A matching request is already pending review"}
    row["_upload"] = UploadFile(filename="degree.pdf", file=io.BytesIO(b"%PDF-1.4"))

    rows = SelfServiceWebService._renderable_form_rows([row])
    rendered = (
        jinja2.Environment(autoescape=True)
        .from_string("{{ rows | tojson }}")
        .render(rows=rows)
    )

    (payload,) = json.loads(rendered)
    assert "_upload" not in payload
    assert payload["_upload_filename"] == "degree.pdf"
    assert payload["_errors"]["row"] == "A matching request is already pending review"


def test_unsanitised_conflict_row_would_still_break_rendering():
    """Proves the previous assertion is meaningful rather than trivially true."""
    row = {
        "_errors": {"row": "A matching request is already pending review"},
        "_upload": UploadFile(filename="degree.pdf", file=io.BytesIO(b"%PDF-1.4")),
    }

    with pytest.raises(TypeError):
        json.dumps(row)
