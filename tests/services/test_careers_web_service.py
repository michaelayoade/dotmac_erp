"""Tests for careers web service behavior."""

from types import SimpleNamespace

from app.services.careers.web import CareersWebService


def test_should_not_send_application_confirmation_for_fiber_academy() -> None:
    service = CareersWebService.__new__(CareersWebService)

    assert not service._should_send_application_confirmation(
        SimpleNamespace(job_code="FA1")
    )
    assert not service._should_send_application_confirmation(
        SimpleNamespace(job_code=" fa1 ")
    )


def test_should_send_application_confirmation_for_other_jobs() -> None:
    service = CareersWebService.__new__(CareersWebService)

    assert service._should_send_application_confirmation(
        SimpleNamespace(job_code="ENG-001")
    )
