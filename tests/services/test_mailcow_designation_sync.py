from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from app.models.people.hr.employee import EmployeeStatus
from app.models.person import PersonStatus
from app.services.people.hr.employees import EmployeeService
from app.services.people.hr.organization import OrganizationService
from app.services.people.hr.organization_types import DesignationUpdateData


def test_designation_rename_queues_every_assigned_employee() -> None:
    organization_id = uuid4()
    designation_id = uuid4()
    employee_ids = [uuid4(), uuid4()]
    employees = [
        SimpleNamespace(
            employee_id=employee_id,
            organization_id=organization_id,
            status=EmployeeStatus.ACTIVE,
            date_of_leaving=None,
            person=SimpleNamespace(
                organization_id=organization_id,
                is_active=True,
                status=PersonStatus.active,
                email=f"person{index}@dotmac.ng",
            ),
        )
        for index, employee_id in enumerate(employee_ids)
    ]
    designation = SimpleNamespace(
        designation_id=designation_id,
        designation_code="ENG",
        designation_name="Engineer",
        description=None,
        is_active=True,
        updated_at=None,
        updated_by_id=None,
    )
    db = Mock()
    db.scalars.return_value.all.return_value = employees
    service = OrganizationService(db, organization_id)
    service.get_designation = Mock(return_value=designation)  # type: ignore[method-assign]

    with (
        patch(
            "app.config.settings.mailcow_provisioning_enabled",
            False,
        ),
        patch(
            "app.config.settings.mailcow_profile_sync_enabled",
            True,
            create=True,
        ),
        patch(
            "app.tasks.email.run_employee_mailcow_provisioning.apply_async"
        ) as enqueue,
    ):
        service.update_designation(
            designation_id,
            DesignationUpdateData(designation_name="Network Engineer"),
        )

    assert designation.designation_name == "Network Engineer"
    assert enqueue.call_count == 2
    assert [call.kwargs["args"][0] for call in enqueue.call_args_list] == [
        str(employee_id) for employee_id in employee_ids
    ]
    assert all(
        call.kwargs["kwargs"] == {"profile_only": True}
        for call in enqueue.call_args_list
    )


def test_employee_assignment_queues_profile_only_with_provisioning_disabled() -> None:
    organization_id = uuid4()
    employee_id = uuid4()
    employee = SimpleNamespace(
        employee_id=employee_id,
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
    service = EmployeeService(Mock(), organization_id)

    with (
        patch("app.config.settings.mailcow_provisioning_enabled", False),
        patch("app.config.settings.mailcow_profile_sync_enabled", True),
        patch(
            "app.tasks.email.run_employee_mailcow_provisioning.apply_async"
        ) as enqueue,
    ):
        service._enqueue_mailcow_profile_sync(employee)

    enqueue.assert_called_once_with(
        args=[str(employee_id), str(organization_id)],
        kwargs={"profile_only": True},
        countdown=10,
    )
