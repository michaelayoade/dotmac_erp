import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.dotmac_sub import DotmacSubPermanentSyncError


def _load_task_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "app" / "tasks" / "staff_sync.py"
    )
    spec = importlib.util.spec_from_file_location("staff_sync_task_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load staff sync task module")
    missing = object()
    previous_tasks = sys.modules.get("app.tasks", missing)
    previous_dotmac_sub = sys.modules.get("app.tasks.dotmac_sub", missing)
    task_package = ModuleType("app.tasks")
    task_package.__path__ = []
    dotmac_sub_module = ModuleType("app.tasks.dotmac_sub")
    dotmac_sub_module._resolve_org_id = lambda organization_id: organization_id
    task_package.dotmac_sub = dotmac_sub_module
    sys.modules["app.tasks"] = task_package
    sys.modules["app.tasks.dotmac_sub"] = dotmac_sub_module
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_tasks is missing:
            sys.modules.pop("app.tasks", None)
        else:
            sys.modules["app.tasks"] = previous_tasks
        if previous_dotmac_sub is missing:
            sys.modules.pop("app.tasks.dotmac_sub", None)
        else:
            sys.modules["app.tasks.dotmac_sub"] = previous_dotmac_sub


task_module = _load_task_module()


def test_staff_sync_permanent_error_does_not_retry() -> None:
    employee_id = uuid4()
    org_id = uuid4()
    db = MagicMock()
    db.get.return_value = SimpleNamespace(employee_id=employee_id)

    with (
        patch.object(task_module, "_resolve_org_id", return_value=org_id),
        patch.object(task_module, "session_for_org") as mock_session_for_org,
        patch.object(
            task_module.staff_sync,
            "sync_employee",
            side_effect=DotmacSubPermanentSyncError("Department is not mapped"),
        ),
        patch.object(task_module.sync_employee_staff_account, "retry") as mock_retry,
    ):
        mock_session_for_org.return_value.__enter__ = MagicMock(return_value=db)
        mock_session_for_org.return_value.__exit__ = MagicMock(return_value=False)

        result = task_module.sync_employee_staff_account.run(
            str(employee_id), str(org_id)
        )

    assert result == {
        "success": False,
        "retryable": False,
        "error": "Department is not mapped",
    }
    mock_retry.assert_not_called()


def test_nightly_reconcile_repairs_projection_before_external_sync() -> None:
    org_id = uuid4()
    db = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = db
    context.__exit__.return_value = False
    projection_outcome = SimpleNamespace(
        employees_seen=4,
        mapped_employees_seen=3,
        account_statuses_seen=4,
        leave_restrictions_seen=2,
    )
    projection_service = MagicMock()
    projection_service.reconcile_organization_projections.return_value = (
        projection_outcome
    )

    with (
        patch.object(task_module, "_resolve_org_id", return_value=org_id),
        patch.object(task_module, "session_for_org", return_value=context),
        patch.object(
            task_module,
            "StaffAccessProjectionService",
            return_value=projection_service,
        ),
        patch.object(
            task_module.staff_sync,
            "reconcile_staff_accounts",
            return_value={"success": True, "counts": {"noop": 3}, "errors": []},
        ) as external_reconcile,
    ):
        result = task_module.run_staff_sync_reconcile.run()

    projection_service.reconcile_organization_projections.assert_called_once_with(
        org_id
    )
    db.commit.assert_called_once_with()
    external_reconcile.assert_called_once_with(db, org_id)
    assert result["success"] is True
    assert result["staff_access_projection"] == {
        "employees_seen": 4,
        "mapped_employees_seen": 3,
        "account_statuses_seen": 4,
        "leave_restrictions_seen": 2,
    }


def test_staff_sync_forwards_active_access_revocation_flag() -> None:
    employee_id = uuid4()
    org_id = uuid4()
    employee = SimpleNamespace(employee_id=employee_id)
    db = MagicMock()
    db.get.return_value = employee

    with (
        patch.object(task_module, "_resolve_org_id", return_value=org_id),
        patch.object(task_module, "session_for_org") as mock_session_for_org,
        patch.object(
            task_module.staff_sync,
            "sync_employee",
            return_value={"action": "disabled"},
        ) as mock_sync_employee,
    ):
        mock_session_for_org.return_value.__enter__ = MagicMock(return_value=db)
        mock_session_for_org.return_value.__exit__ = MagicMock(return_value=False)

        result = task_module.sync_employee_staff_account.run(
            str(employee_id),
            str(org_id),
            allow_active_access_revocation=True,
        )

    assert result == {"success": True, "action": "disabled"}
    mock_sync_employee.assert_called_once_with(
        db,
        employee,
        allow_active_access_revocation=True,
    )
