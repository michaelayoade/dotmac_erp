from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.models.people.training import TrainingEventStatus, TrainingProgramStatus
from app.services.common import PaginatedResult
from app.services.help_center import build_help_center_payload
from app.services.people.training import TrainingService
from app.services.people.training.training_service import TrainingEventStatusError
from app.services.people.training.web.event_web import EventWebService
from app.services.people.training.web.program_web import ProgramWebService

TEST_ORG_ID = uuid4()


def test_training_event_starts_in_draft_and_schedules_once(monkeypatch):
    db = Mock()
    svc = TrainingService(db)
    program = SimpleNamespace(program_id=uuid4(), status=TrainingProgramStatus.DRAFT)

    monkeypatch.setattr(svc, "get_program", lambda org_id, program_id: program)

    event = svc.create_event(
        TEST_ORG_ID,
        program_id=program.program_id,
        event_name="Leadership Essentials Cohort 1",
        start_date=date(2026, 3, 10),
        end_date=date(2026, 3, 11),
        event_type="IN_PERSON",
    )

    assert event.status == TrainingEventStatus.DRAFT

    monkeypatch.setattr(svc, "get_event", lambda org_id, event_id: event)
    scheduled = svc.schedule_event(TEST_ORG_ID, uuid4())

    assert scheduled.status == TrainingEventStatus.SCHEDULED

    with pytest.raises(TrainingEventStatusError):
        svc.schedule_event(TEST_ORG_ID, uuid4())


def test_training_program_list_context_includes_pagination_metadata(monkeypatch):
    result = PaginatedResult(
        items=[SimpleNamespace(program_id=uuid4())], total=1, limit=20
    )

    monkeypatch.setattr(
        TrainingService,
        "list_programs",
        lambda self, organization_id, **kwargs: result,
    )

    context = ProgramWebService.list_programs_context(Mock(), TEST_ORG_ID)

    assert context["total_count"] == 1
    assert context["limit"] == 20
    assert context["page"] == 1


def test_training_event_list_context_includes_pagination_metadata(monkeypatch):
    event_result = PaginatedResult(
        items=[SimpleNamespace(event_id=uuid4())], total=1, limit=20
    )
    program_result = PaginatedResult(
        items=[SimpleNamespace(program_id=uuid4())], total=1, limit=200
    )

    def fake_list_programs(self, organization_id, **kwargs):
        return program_result

    def fake_list_events(self, organization_id, **kwargs):
        return event_result

    monkeypatch.setattr(TrainingService, "list_programs", fake_list_programs)
    monkeypatch.setattr(TrainingService, "list_events", fake_list_events)

    context = EventWebService.list_events_context(Mock(), TEST_ORG_ID)

    assert context["total_count"] == 1
    assert context["limit"] == 20
    assert context["page"] == 1


def test_help_center_people_content_includes_training_resources():
    payload = build_help_center_payload(
        accessible_modules=["people"],
        roles=[],
        scopes=[],
        is_admin=False,
    )

    people_manual = next(
        item for item in payload["manuals"] if item["module_key"] == "people"
    )
    manual_hrefs = {link["href"] for link in people_manual["links"]}
    journey_hrefs = {
        item["href"] for item in payload["journeys"] if item["module_key"] == "people"
    }
    playbook_titles = {item["title"] for item in payload["role_playbooks"]}

    assert "/people/training/programs" in manual_hrefs
    assert "/people/training/programs" in journey_hrefs
    assert "Learning & Development" in playbook_titles
