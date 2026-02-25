"""Tests for ServiceHookService."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.finance.platform.service_hook import HookHandlerType, ServiceHook
from app.models.finance.platform.service_hook_execution import ExecutionStatus
from app.services.hooks.service_hook import ServiceHookService


def _hook(**overrides) -> ServiceHook:
    data = {
        "event_name": "inventory.stock.reserved",
        "handler_type": HookHandlerType.EVENT_OUTBOX,
        "handler_config": {},
        "name": "Hook",
        "organization_id": uuid4(),
    }
    data.update(overrides)
    return ServiceHook(**data)


class TestServiceHookService:
    def test_create(self):
        db = MagicMock()
        svc = ServiceHookService(db)

        hook = svc.create(
            organization_id=uuid4(),
            event_name="inventory.stock.reserved",
            handler_type=HookHandlerType.EVENT_OUTBOX,
            handler_config={"event_name_override": "custom.event"},
            name="Outbox Hook",
        )

        assert isinstance(hook, ServiceHook)
        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_update_not_found(self):
        db = MagicMock()
        db.get.return_value = None
        svc = ServiceHookService(db)

        with pytest.raises(ValueError):
            svc.update(uuid4(), name="Updated")

    def test_toggle(self):
        db = MagicMock()
        hook = _hook(is_active=True)
        db.get.return_value = hook
        svc = ServiceHookService(db)

        updated = svc.toggle(hook.hook_id, False)

        assert updated.is_active is False
        db.flush.assert_called_once()

    def test_list_executions(self):
        db = MagicMock()
        execution = MagicMock()
        db.scalars.return_value.all.return_value = [execution]
        svc = ServiceHookService(db)

        rows = svc.list_executions(uuid4(), status=ExecutionStatus.FAILED, limit=5)

        assert rows == [execution]

    def test_count_executions(self):
        db = MagicMock()
        db.scalar.return_value = 7
        svc = ServiceHookService(db)

        total = svc.count_executions(uuid4(), status=ExecutionStatus.FAILED)

        assert total == 7

    def test_retry_execution_rejects_invalid_status(self):
        db = MagicMock()
        org_id = uuid4()
        hook = _hook(organization_id=org_id)
        execution = MagicMock(
            hook_id=hook.hook_id,
            organization_id=org_id,
            status=ExecutionStatus.SUCCESS,
        )
        db.get.side_effect = [hook, execution]
        svc = ServiceHookService(db)

        with pytest.raises(ValueError, match="FAILED or DEAD"):
            svc.retry_execution(hook.hook_id, uuid4(), organization_id=org_id)

    def test_get_execution_success(self):
        db = MagicMock()
        org_id = uuid4()
        hook = _hook(organization_id=org_id)
        execution = MagicMock(
            hook_id=hook.hook_id,
            organization_id=org_id,
        )
        db.get.side_effect = [hook, execution]
        svc = ServiceHookService(db)

        row = svc.get_execution(hook.hook_id, uuid4(), organization_id=org_id)

        assert row is execution

    def test_bulk_toggle(self):
        db = MagicMock()
        org_id = uuid4()
        hook_a = _hook(organization_id=org_id)
        db.scalars.return_value.all.return_value = [hook_a]
        svc = ServiceHookService(db)

        result = svc.bulk_toggle(
            [hook_a.hook_id, uuid4()],
            organization_id=org_id,
            is_active=False,
        )

        assert result["requested"] == 2
        assert result["updated"] == 1
        assert len(result["not_found_ids"]) == 1
        assert hook_a.is_active is False

    def test_bulk_delete(self):
        db = MagicMock()
        org_id = uuid4()
        hook_a = _hook(organization_id=org_id)
        db.scalars.return_value.all.return_value = [hook_a]
        svc = ServiceHookService(db)

        result = svc.bulk_delete(
            [hook_a.hook_id, uuid4()],
            organization_id=org_id,
        )

        assert result["requested"] == 2
        assert result["deleted"] == 1
        assert len(result["not_found_ids"]) == 1
        db.delete.assert_called_once_with(hook_a)
