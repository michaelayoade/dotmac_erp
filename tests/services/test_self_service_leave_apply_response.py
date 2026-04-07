from datetime import date
from urllib.parse import quote
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.services.people.leave.leave_service import LeaveServiceError
from app.services.people.self_service_web import SelfServiceWebService


def _make_auth():
    auth = MagicMock()
    auth.organization_id = "00000000-0000-0000-0000-000000000001"
    auth.person_id = "00000000-0000-0000-0000-000000000002"
    return auth


def test_leave_apply_response_redirects_on_overlap_error():
    svc = SelfServiceWebService()
    auth = _make_auth()
    db = MagicMock()
    overlap_message = "Overlapping leave application exists for period 2026-04-07 to 2026-04-08"

    with (
        patch.object(
            svc, "_get_employee_id", return_value="00000000-0000-0000-0000-000000000003"
        ),
        patch("app.services.people.self_service_web.LeaveService") as leave_service,
    ):
        leave_service.return_value.create_application.side_effect = LeaveServiceError(
            overlap_message
        )

        response = svc.leave_apply_response(
            auth=auth,
            db=db,
            leave_type_id="11111111-1111-1111-1111-111111111111",
            from_date=date(2026, 4, 7),
            to_date=date(2026, 4, 8),
            half_day=None,
            reason="Personal",
        )

    assert response.status_code == 303
    assert response.headers["location"] == f"/people/self/leave?error={quote(overlap_message)}"
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_leave_apply_response_redirects_on_success():
    svc = SelfServiceWebService()
    auth = _make_auth()
    db = MagicMock()

    with (
        patch.object(
            svc,
            "_get_employee_id",
            return_value=UUID("00000000-0000-0000-0000-000000000003"),
        ),
        patch("app.services.people.self_service_web.LeaveService") as leave_service,
    ):
        response = svc.leave_apply_response(
            auth=auth,
            db=db,
            leave_type_id="11111111-1111-1111-1111-111111111111",
            from_date=date(2026, 4, 7),
            to_date=date(2026, 4, 8),
            half_day=None,
            reason="Personal",
        )

        leave_service.return_value.create_application.assert_called_once_with(
            UUID("00000000-0000-0000-0000-000000000001"),
            employee_id=UUID("00000000-0000-0000-0000-000000000003"),
            leave_type_id=UUID("11111111-1111-1111-1111-111111111111"),
            from_date=date(2026, 4, 7),
            to_date=date(2026, 4, 8),
            half_day=False,
            half_day_date=None,
            reason="Personal",
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/people/self/leave"
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
