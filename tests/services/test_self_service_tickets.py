from unittest.mock import MagicMock, patch
from uuid import UUID

from app.services.people.self_service_web import SelfServiceWebService


def _make_auth() -> MagicMock:
    auth = MagicMock()
    auth.organization_id = "00000000-0000-0000-0000-000000000001"
    auth.person_id = "00000000-0000-0000-0000-000000000002"
    auth.user_id = "00000000-0000-0000-0000-000000000004"
    return auth


def test_ticket_create_response_creates_support_ticket() -> None:
    svc = SelfServiceWebService()
    auth = _make_auth()
    db = MagicMock()

    with (
        patch.object(
            svc,
            "_get_employee_id",
            return_value=UUID("00000000-0000-0000-0000-000000000003"),
        ),
        patch(
            "app.services.support.ticket.ticket_service.create_ticket"
        ) as create_ticket,
    ):
        response = svc.ticket_create_response(
            request=MagicMock(),
            auth=auth,
            db=db,
            subject="Need HR document correction",
            description="My name is misspelled on a generated letter.",
            priority="HIGH",
            category_id="11111111-1111-1111-1111-111111111111",
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/people/self/tickets?saved=1"
    create_ticket.assert_called_once_with(
        db,
        UUID("00000000-0000-0000-0000-000000000001"),
        UUID("00000000-0000-0000-0000-000000000004"),
        subject="Need HR document correction",
        description="My name is misspelled on a generated letter.",
        priority="HIGH",
        raised_by_id=UUID("00000000-0000-0000-0000-000000000003"),
        category_id=UUID("11111111-1111-1111-1111-111111111111"),
    )
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


def test_ticket_create_response_validates_subject() -> None:
    svc = SelfServiceWebService()
    auth = _make_auth()
    db = MagicMock()

    with patch.object(
        svc,
        "_get_employee_id",
        return_value=UUID("00000000-0000-0000-0000-000000000003"),
    ):
        response = svc.ticket_create_response(
            request=MagicMock(),
            auth=auth,
            db=db,
            subject="",
            description="Details",
            priority="MEDIUM",
            category_id=None,
        )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/people/self/tickets?error=Subject+is+required"
    )
    db.commit.assert_not_called()
