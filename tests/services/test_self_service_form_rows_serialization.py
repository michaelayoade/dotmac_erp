"""Collected form rows must survive the templates' ``| tojson`` filter.

Regression cover for the self-service batch submit path: a row keeps the raw
``UploadFile`` under ``_upload`` for the submit logic, and every failure path
hands those same rows back to a template that serialises them. Before the fix
that raised ``TypeError`` and returned a 500 instead of the inline field error,
on exactly the sections that carry files (qualifications, certifications,
documents).
"""

import io
import json
from pathlib import Path

import jinja2
import pytest
from starlette.datastructures import UploadFile

from app.services.people.self_service_web import (
    TRANSPORT_ROW_KEYS,
    SelfServiceWebService,
)

FILE_BEARING_TEMPLATES = [
    "qualifications.html",
    "certifications.html",
    "document_form.html",
]


def _upload_file() -> UploadFile:
    return UploadFile(filename="degree.pdf", file=io.BytesIO(b"%PDF-1.4 fake"))


def _row_with_upload() -> dict[str, object]:
    return {
        "qualification_name": "BSc Computer Science",
        "institution_name": "University of Abuja",
        "is_ongoing": False,
        "_errors": {"file": "Select a file to upload"},
        "_upload": _upload_file(),
    }


def _tojson(value: object) -> str:
    """Render through Jinja's real filter, as the templates do."""
    return (
        jinja2.Environment(autoescape=True)
        .from_string("{{ value | tojson }}")
        .render(value=value)
    )


def test_raw_collected_row_is_not_json_serialisable() -> None:
    """Guard against this suite passing vacuously if _upload ever stops being set."""
    with pytest.raises(TypeError):
        json.dumps(_row_with_upload())


def test_renderable_form_rows_strips_upload_and_keeps_field_state() -> None:
    rows = SelfServiceWebService._renderable_form_rows([_row_with_upload()])

    assert rows is not None
    (row,) = rows
    assert "_upload" not in row
    assert row["qualification_name"] == "BSc Computer Science"
    assert row["institution_name"] == "University of Abuja"
    assert row["is_ongoing"] is False
    # Inline errors are what the template renders on the failure path; the fix
    # is worthless if sanitising drops them too.
    assert row["_errors"] == {"file": "Select a file to upload"}


def test_renderable_form_rows_keeps_the_filename_for_reselection() -> None:
    """A browser drops the file on a failed post; the name must survive to ask again."""
    rows = SelfServiceWebService._renderable_form_rows([_row_with_upload()])

    assert rows is not None
    assert rows[0]["_upload_filename"] == "degree.pdf"


def test_renderable_form_rows_reports_no_filename_when_none_was_chosen() -> None:
    row = _row_with_upload()
    row["_upload"] = None

    rows = SelfServiceWebService._renderable_form_rows([row])

    assert rows is not None
    # Falsy, so the template's x-show hides the reselect prompt entirely.
    assert rows[0]["_upload_filename"] == ""


def test_renderable_form_rows_survives_jinja_tojson() -> None:
    rows = SelfServiceWebService._renderable_form_rows([_row_with_upload()])

    rendered = _tojson(rows)

    assert "BSc Computer Science" in rendered
    # Check the key, not a substring: "_upload_filename" legitimately contains
    # "_upload", so a naive substring assertion passes for the wrong reason.
    (payload,) = json.loads(rendered)
    assert "_upload" not in payload
    assert payload["_upload_filename"] == "degree.pdf"


def test_renderable_form_rows_does_not_mutate_the_caller_row() -> None:
    """The submit path still reads _upload after the response is built."""
    original = _row_with_upload()

    SelfServiceWebService._renderable_form_rows([original])

    assert "_upload" in original


def test_renderable_form_rows_passes_none_through() -> None:
    # The context builders rely on `... or [default_row()]`, so None must stay
    # falsy rather than becoming an empty list that skips the default row.
    assert SelfServiceWebService._renderable_form_rows(None) is None


def test_transport_row_keys_covers_the_collected_upload_key() -> None:
    """The route writes this literal key; keep the two in step."""
    collector = Path("app/web/people/self_service.py").read_text(encoding="utf-8")

    assert 'row["_upload"]' in collector
    assert "_upload" in TRANSPORT_ROW_KEYS


def test_file_bearing_templates_still_serialise_form_rows() -> None:
    """If a template stops using tojson, this cover is no longer needed there."""
    for name in FILE_BEARING_TEMPLATES:
        template = Path(f"templates/people/self/{name}").read_text(encoding="utf-8")
        assert "form_rows | tojson" in template


def test_file_bearing_templates_prompt_for_reselection() -> None:
    for name in FILE_BEARING_TEMPLATES:
        template = Path(f"templates/people/self/{name}").read_text(encoding="utf-8")
        assert "row._upload_filename" in template


def test_file_bearing_templates_render_the_inline_file_error() -> None:
    """The conflict/validation path sets these; before the fix they never rendered."""
    error_keys = {
        "qualifications.html": "row._errors.supporting_file",
        "certifications.html": "row._errors.supporting_file",
        "document_form.html": "row._errors.file",
    }
    for name, key in error_keys.items():
        template = Path(f"templates/people/self/{name}").read_text(encoding="utf-8")
        assert key in template
