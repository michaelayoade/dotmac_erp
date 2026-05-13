"""Tests for PMS background tasks."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.tasks import pms


def _make_scalars_result(items):
    result = MagicMock()
    result.all.return_value = items
    return result


class TestPMSMonthlyReviewReminder:
    def test_uses_person_recipient_and_dedup_helper(self) -> None:
        org_id = uuid4()
        supervisor_employee_id = uuid4()
        supervisor_person_id = uuid4()
        contract = SimpleNamespace(
            contract_id=uuid4(),
            employee_id=uuid4(),
            supervisor_id=supervisor_employee_id,
        )
        org = SimpleNamespace(organization_id=org_id)

        mock_db = MagicMock()
        mock_db.scalars.side_effect = [
            _make_scalars_result([org]),
            _make_scalars_result([contract]),
        ]
        mock_db.scalar.return_value = None

        notification_service = MagicMock()
        notification_service.create_if_not_sent_since.return_value = object()

        with (
            patch("app.tasks.pms.SessionLocal") as mock_session,
            patch(
                "app.services.notification.NotificationService",
                return_value=notification_service,
            ),
            patch(
                "app.tasks.pms._resolve_person_id",
                return_value=supervisor_person_id,
            ) as resolve_person,
        ):
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = pms.pms_monthly_review_reminder()

        assert result["reminders_sent"] == 1
        resolve_person.assert_called_once_with(mock_db, org_id, supervisor_employee_id)
        call = notification_service.create_if_not_sent_since.call_args.kwargs
        assert call["recipient_id"] == supervisor_person_id
        assert call["since"] == pms._window_start(date.today().replace(day=1))


class TestPMSProbationCheck:
    def test_notifies_manager_person_for_probation_milestone(self) -> None:
        org_id = uuid4()
        employee_id = uuid4()
        manager_employee_id = uuid4()
        manager_person_id = uuid4()
        org = SimpleNamespace(organization_id=org_id)
        from app.models.people.hr.employee import EmployeeStatus

        employee = SimpleNamespace(
            employee_id=employee_id,
            organization_id=org_id,
            reports_to_id=manager_employee_id,
            status=EmployeeStatus.ACTIVE,
        )
        manager = SimpleNamespace(
            employee_id=manager_employee_id,
            person_id=manager_person_id,
        )
        milestone = {"employee_id": employee_id, "months_of_service": 20}

        mock_db = MagicMock()
        mock_db.scalars.return_value = _make_scalars_result([org])
        mock_db.get.return_value = employee

        underperformance_service = MagicMock()
        underperformance_service.check_probation_milestones.return_value = [milestone]
        notification_service = MagicMock()
        notification_service.create_if_not_sent_since.return_value = object()

        with (
            patch("app.tasks.pms.SessionLocal") as mock_session,
            patch(
                "app.services.notification.NotificationService",
                return_value=notification_service,
            ),
            patch(
                "app.services.people.perf.underperformance_service.UnderperformanceService",
                return_value=underperformance_service,
            ),
            patch("app.services.people.hr.org_resolver.OrgResolver") as resolver_cls,
        ):
            resolver_cls.return_value.get_manager.return_value = manager
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            result = pms.pms_probation_check()

        assert result["notifications_sent"] == 1
        resolver_cls.return_value.get_manager.assert_called_once_with(
            employee_id, org_id
        )
        call = notification_service.create_if_not_sent_since.call_args.kwargs
        assert call["recipient_id"] == manager_person_id
        assert call["entity_id"] == employee_id
        assert "direct report" in call["message"]
