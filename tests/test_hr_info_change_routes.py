from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.web.people.hr.info_changes import (
    approve_info_change_request,
    reject_info_change_request,
)


def _auth_context() -> MagicMock:
    auth = MagicMock()
    auth.organization_id = "00000000-0000-0000-0000-000000000001"
    auth.person_id = "00000000-0000-0000-0000-000000000002"
    return auth


def test_approve_info_change_request_commits_and_redirects_success():
    db = MagicMock()
    auth = _auth_context()
    request_id = uuid4()

    with patch("app.web.people.hr.info_changes.InfoChangeService") as svc_cls:
        response = approve_info_change_request(
            request_id=request_id,
            reviewer_notes="Looks good",
            auth=auth,
            db=db,
        )

    svc_cls.return_value.approve_request.assert_called_once()
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"{request_id}?success=Approved")


def test_approve_info_change_request_rolls_back_and_redirects_error():
    db = MagicMock()
    auth = _auth_context()
    request_id = uuid4()

    with patch("app.web.people.hr.info_changes.InfoChangeService") as svc_cls:
        svc_cls.return_value.approve_request.side_effect = ValueError(
            "Request is not actionable"
        )
        response = approve_info_change_request(
            request_id=request_id,
            reviewer_notes="",
            auth=auth,
            db=db,
        )

    db.commit.assert_not_called()
    db.rollback.assert_called_once()
    assert response.status_code == 303
    assert "error=Request%20is%20not%20actionable" in response.headers["location"]


def test_reject_info_change_request_commits_and_redirects_success():
    db = MagicMock()
    auth = _auth_context()
    request_id = uuid4()

    with patch("app.web.people.hr.info_changes.InfoChangeService") as svc_cls:
        response = reject_info_change_request(
            request_id=request_id,
            reviewer_notes="Insufficient evidence",
            auth=auth,
            db=db,
        )

    svc_cls.return_value.reject_request.assert_called_once()
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"{request_id}?success=Rejected")
