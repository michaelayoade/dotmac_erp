"""Regression tests for qualification type validation."""

from unittest.mock import MagicMock

import pytest

from app.models.people.hr.employee_extended import QualificationType
from app.services.people.hr.info_change_service import InfoChangeService


def _qualification_payload(qualification_type: object) -> dict[str, object]:
    return {
        "qualification_type": qualification_type,
        "qualification_name": "Senior Secondary School Certificate",
        "institution_name": "Example Secondary School",
        "start_date": "2014-09-01",
        "end_date": "2020-07-31",
    }


@pytest.mark.parametrize("qualification_type", list(QualificationType))
def test_qualification_validation_is_idempotent(
    qualification_type: QualificationType,
) -> None:
    service = InfoChangeService(MagicMock())

    first_pass = service._validate_qualification_payload(
        _qualification_payload(qualification_type.value)
    )
    second_pass = service._validate_qualification_payload(first_pass)

    assert first_pass["qualification_type"] is qualification_type
    assert second_pass["qualification_type"] is qualification_type
    assert second_pass == first_pass


def test_high_school_survives_batch_style_double_validation() -> None:
    service = InfoChangeService(MagicMock())
    browser_payload = _qualification_payload("HIGH_SCHOOL")

    web_validation = service._validate_qualification_payload(browser_payload)
    batch_validation = service._validate_qualification_payload(web_validation)

    assert batch_validation["qualification_type"] is QualificationType.HIGH_SCHOOL
