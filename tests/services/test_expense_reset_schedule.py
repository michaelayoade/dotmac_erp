from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from zoneinfo import ZoneInfoNotFoundError

from app.services.expense import reset_schedule
from app.services.expense.limit_service import ExpenseLimitService


def test_default_preserves_monday_midnight_utc():
    now = datetime(2026, 11, 4, tzinfo=UTC)
    assert reset_schedule.boundary(reset_schedule.DEFAULT, now) == datetime(
        2026, 11, 2, tzinfo=UTC
    )


def test_saved_schedule_activates_at_its_boundary_and_preserves_previous_period():
    cutoff = datetime(2026, 11, 4, 8, 30, tzinfo=UTC)
    history = [
        {
            "weekday": 2,
            "time": "09:30",
            "timezone": "Africa/Lagos",
            "effective_at": cutoff.isoformat(),
        },
        {
            "weekday": 2,
            "time": "07:00",
            "timezone": "UTC",
            "effective_at": datetime(2026, 11, 4, 7, tzinfo=UTC).isoformat(),
            "superseded_at": datetime(2026, 11, 3, tzinfo=UTC).isoformat(),
        },
    ]
    db = MagicMock()
    db.scalar.return_value.value_json = {"history": history}
    service = ExpenseLimitService(db)
    before = datetime(2026, 11, 4, 8, 29, tzinfo=UTC)
    org_id = uuid4()
    assert reset_schedule.next_boundary(history, before) == cutoff
    assert service.get_weekly_boundary(org_id, before) == datetime(
        2026, 11, 2, tzinfo=UTC
    )
    assert service.get_weekly_boundary(org_id, cutoff) == cutoff
    assert reset_schedule.next_boundary(history, cutoff) == datetime(
        2026, 11, 11, 8, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "form,error",
    [
        ({"weekday": "7", "time": "09:30", "timezone": "UTC"}, ValueError),
        ({"weekday": "2", "time": "25:30", "timezone": "UTC"}, ValueError),
        (
            {"weekday": "2", "time": "09:30", "timezone": "Invalid/Zone"},
            ZoneInfoNotFoundError,
        ),
    ],
)
def test_invalid_schedules_are_rejected(form, error):
    with pytest.raises(error):
        reset_schedule.parse_schedule(form)


def test_only_admins_can_open_or_post_expense_settings():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.web.admin import router
    from app.web.deps import WebAuthContext, require_web_auth

    web = FastAPI()
    web.include_router(router)
    web.dependency_overrides[require_web_auth] = lambda: WebAuthContext(
        organization_id=uuid4(), person_id=uuid4(), roles=["employee"]
    )
    with TestClient(web) as client:
        assert client.get("/admin/settings/expense").status_code == 403
        assert client.post("/admin/settings/expense").status_code == 403
        assert client.post("/admin/settings/expense/reset-schedule").status_code == 403


def test_legacy_expense_settings_module_is_retired():
    from fastapi import HTTPException

    from app.web.settings import _get_module_config

    with pytest.raises(HTTPException) as rejected:
        _get_module_config("expense")
    assert rejected.value.status_code == 404
