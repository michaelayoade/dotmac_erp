from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from app.services.people.hr.organization import OrganizationService
from app.services.people.hr.organization_types import DesignationUpdateData


def test_designation_rename_queues_every_assigned_employee() -> None:
    organization_id = uuid4()
    designation_id = uuid4()
    employee_ids = [uuid4(), uuid4()]
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
    db.scalars.return_value.all.return_value = employee_ids
    service = OrganizationService(db, organization_id)
    service.get_designation = Mock(return_value=designation)  # type: ignore[method-assign]

    with (
        patch(
            "app.config.settings.mailcow_provisioning_enabled",
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
