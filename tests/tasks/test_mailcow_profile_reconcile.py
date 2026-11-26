from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from app.models.people.hr.employee import EmployeeStatus
from app.models.person import PersonStatus
from app.tasks.hr import reconcile_employee_mailcow_profiles


def test_profile_reconciliation_queues_only_eligible_with_provisioning_disabled() -> (
    None
):
    organization_id = uuid4()
    eligible = SimpleNamespace(
        employee_id=uuid4(),
        organization_id=organization_id,
        status=EmployeeStatus.ACTIVE,
        date_of_leaving=None,
        person=SimpleNamespace(
            organization_id=organization_id,
            is_active=True,
            status=PersonStatus.active,
            email="ada@dotmac.ng",
        ),
    )
    inactive = SimpleNamespace(
        employee_id=uuid4(),
        organization_id=organization_id,
        status=EmployeeStatus.ACTIVE,
        date_of_leaving=None,
        person=SimpleNamespace(
            organization_id=organization_id,
            is_active=False,
            status=PersonStatus.inactive,
            email="other@dotmac.ng",
        ),
    )
    db = Mock()
    db.scalars.return_value.all.return_value = [eligible, inactive]

    @contextmanager
    def org_session(_organization_id):
        yield db

    with (
        patch("app.config.settings.mailcow_provisioning_enabled", False),
        patch("app.config.settings.mailcow_profile_sync_enabled", True),
        patch("app.tasks.hr._list_organization_ids", return_value=[organization_id]),
        patch("app.tasks.hr.session_for_org", org_session),
        patch(
            "app.tasks.email.run_employee_mailcow_provisioning.apply_async"
        ) as enqueue,
    ):
        result = reconcile_employee_mailcow_profiles.run()

    assert result == {"queued": 1, "ineligible": 1, "errors": []}
    enqueue.assert_called_once_with(
        args=[str(eligible.employee_id), str(organization_id)],
        kwargs={"profile_only": True},
    )
