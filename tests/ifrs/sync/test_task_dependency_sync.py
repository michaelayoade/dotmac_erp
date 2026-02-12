"""
Tests for Task Dependency and Assignment Sync.

Tests cover:
- _assign JSON parsing and employee resolution
- Task Depends On child table → TaskDependency creation
- Dedup of dependencies (no duplicate pairs)
- Self-reference guard
- Dependency replacement on update
- fetch_records attaching _depends_on to each task
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.erpnext.mappings.tasks import (
    TaskMapping,
)

# ============ Mapping Tests ============


class TestTaskMappingPassthrough:
    """Tests that _assign and _depends_on are passed through the mapping."""

    def setup_method(self) -> None:
        self.mapping = TaskMapping()

    def test_assign_raw_passed_through(self) -> None:
        """_assign field is mapped to _assign_raw."""
        record = {
            "name": "TASK-001",
            "subject": "Test Task",
            "_assign": '["user@example.com"]',
            "modified": "2026-01-01",
        }
        result = self.mapping.transform_record(record)
        assert result["_assign_raw"] == '["user@example.com"]'

    def test_depends_on_passed_through(self) -> None:
        """_depends_on field is mapped through."""
        deps = [{"task": "TASK-002"}, {"task": "TASK-003"}]
        record = {
            "name": "TASK-001",
            "subject": "Test Task",
            "_depends_on": deps,
            "modified": "2026-01-01",
        }
        result = self.mapping.transform_record(record)
        assert result["_depends_on"] == deps

    def test_assign_none_when_missing(self) -> None:
        """_assign_raw is None when _assign not in record."""
        record = {
            "name": "TASK-001",
            "subject": "Test Task",
            "modified": "2026-01-01",
        }
        result = self.mapping.transform_record(record)
        assert result["_assign_raw"] is None

    def test_depends_on_none_when_missing(self) -> None:
        """_depends_on is None when not in record."""
        record = {
            "name": "TASK-001",
            "subject": "Test Task",
            "modified": "2026-01-01",
        }
        result = self.mapping.transform_record(record)
        assert result["_depends_on"] is None


# ============ Assignment Resolution Tests ============


class TestResolveAssignedTo:
    """Tests for _resolve_assigned_to method."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.org_id = uuid.uuid4()
        self.user_id = uuid.uuid4()

    def _make_service(self):
        from app.services.erpnext.sync.tasks import TaskSyncService

        return TaskSyncService(self.db, self.org_id, self.user_id)

    def test_parse_json_string(self) -> None:
        """Parses JSON string with email list."""
        service = self._make_service()
        employee_id = uuid.uuid4()

        with patch.object(
            service, "_resolve_employee_by_email", return_value=employee_id
        ):
            result = service._resolve_assigned_to('["john@example.com"]')

        assert result == employee_id

    def test_parse_list_directly(self) -> None:
        """Handles already-parsed list."""
        service = self._make_service()
        employee_id = uuid.uuid4()

        with patch.object(
            service, "_resolve_employee_by_email", return_value=employee_id
        ):
            result = service._resolve_assigned_to(["john@example.com"])

        assert result == employee_id

    def test_takes_first_email_only(self) -> None:
        """Takes the first email from multi-assignee list."""
        service = self._make_service()

        with patch.object(
            service, "_resolve_employee_by_email", return_value=None
        ) as mock:
            service._resolve_assigned_to('["first@example.com", "second@example.com"]')

        mock.assert_called_once_with("first@example.com")

    def test_returns_none_for_empty_string(self) -> None:
        """Returns None for empty string."""
        service = self._make_service()
        assert service._resolve_assigned_to("") is None

    def test_returns_none_for_none(self) -> None:
        """Returns None for None."""
        service = self._make_service()
        assert service._resolve_assigned_to(None) is None

    def test_returns_none_for_empty_list(self) -> None:
        """Returns None for empty list."""
        service = self._make_service()
        assert service._resolve_assigned_to([]) is None

    def test_returns_none_for_invalid_json(self) -> None:
        """Returns None for invalid JSON."""
        service = self._make_service()
        assert service._resolve_assigned_to("not-json{") is None

    def test_returns_none_for_empty_json_array(self) -> None:
        """Returns None for empty JSON array."""
        service = self._make_service()
        assert service._resolve_assigned_to("[]") is None


# ============ Dependency Sync Tests ============


class TestSyncDependencies:
    """Tests for _sync_dependencies method."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.org_id = uuid.uuid4()
        self.user_id = uuid.uuid4()

    def _make_service(self):
        from app.services.erpnext.sync.tasks import TaskSyncService

        return TaskSyncService(self.db, self.org_id, self.user_id)

    def _mock_task(self) -> MagicMock:
        """Create a mock Task with a UUID task_id."""
        task = MagicMock()
        task.task_id = uuid.uuid4()
        return task

    def test_creates_dependency(self) -> None:
        """Creates a TaskDependency for a resolved predecessor."""
        service = self._make_service()
        task = self._mock_task()
        predecessor_id = uuid.uuid4()

        with patch.object(service, "_resolve_task_id", return_value=predecessor_id):
            count = service._sync_dependencies(task, [{"task": "TASK-PRED-001"}])

        assert count == 1
        self.db.add.assert_called()

    def test_skips_unresolved_predecessor(self) -> None:
        """Skips dependencies where predecessor task is not synced."""
        service = self._make_service()
        task = self._mock_task()

        with patch.object(service, "_resolve_task_id", return_value=None):
            count = service._sync_dependencies(task, [{"task": "UNKNOWN-TASK"}])

        assert count == 0

    def test_skips_self_reference(self) -> None:
        """Skips self-referencing dependencies."""
        service = self._make_service()
        task = self._mock_task()

        # Predecessor resolves to the same task
        with patch.object(service, "_resolve_task_id", return_value=task.task_id):
            count = service._sync_dependencies(task, [{"task": "SELF-TASK"}])

        assert count == 0

    def test_deduplicates_within_batch(self) -> None:
        """Doesn't create duplicate dependencies within the same batch."""
        service = self._make_service()
        task = self._mock_task()
        pred_id = uuid.uuid4()

        with patch.object(service, "_resolve_task_id", return_value=pred_id):
            count = service._sync_dependencies(
                task,
                [
                    {"task": "TASK-PRED-001"},
                    {"task": "TASK-PRED-001"},  # duplicate
                ],
            )

        assert count == 1

    def test_multiple_different_dependencies(self) -> None:
        """Creates multiple dependencies for different predecessors."""
        service = self._make_service()
        task = self._mock_task()
        pred_ids = [uuid.uuid4(), uuid.uuid4()]

        with patch.object(service, "_resolve_task_id", side_effect=pred_ids):
            count = service._sync_dependencies(
                task,
                [{"task": "TASK-A"}, {"task": "TASK-B"}],
            )

        assert count == 2

    def test_replace_deletes_existing_first(self) -> None:
        """With replace=True, existing dependencies are deleted first."""
        service = self._make_service()
        task = self._mock_task()

        # Mock existing dependencies
        existing_dep = MagicMock()
        self.db.execute.return_value.scalars.return_value.all.return_value = [
            existing_dep
        ]

        with patch.object(service, "_resolve_task_id", return_value=uuid.uuid4()):
            count = service._sync_dependencies(
                task,
                [{"task": "TASK-NEW"}],
                replace=True,
            )

        # Should have deleted the old dependency
        self.db.delete.assert_called_once_with(existing_dep)
        assert count == 1

    def test_skips_empty_task_field(self) -> None:
        """Skips dependency records with empty or missing task field."""
        service = self._make_service()
        task = self._mock_task()

        count = service._sync_dependencies(
            task,
            [{"task": ""}, {"task": None}, {}],
        )

        assert count == 0


# ============ Fetch Records Tests ============


class TestFetchRecordsWithDependencies:
    """Tests that fetch_records attaches _depends_on to each task record."""

    def test_fetch_attaches_dependencies(self) -> None:
        """fetch_records fetches dependencies per task and attaches them."""
        from app.services.erpnext.sync.tasks import TaskSyncService

        db = MagicMock()
        service = TaskSyncService(db, uuid.uuid4(), uuid.uuid4())

        mock_client = MagicMock()
        task_record = {"name": "TASK-001", "subject": "Test"}
        mock_client.get_all_documents.return_value = [task_record]
        mock_client.get_task_dependencies.return_value = [{"task": "TASK-002"}]

        records = list(service.fetch_records(mock_client))

        assert len(records) == 1
        assert records[0]["_depends_on"] == [{"task": "TASK-002"}]
        mock_client.get_task_dependencies.assert_called_once_with("TASK-001")

    def test_fetch_empty_name_skips_dependencies(self) -> None:
        """Records with empty name get empty _depends_on."""
        from app.services.erpnext.sync.tasks import TaskSyncService

        db = MagicMock()
        service = TaskSyncService(db, uuid.uuid4(), uuid.uuid4())

        mock_client = MagicMock()
        task_record = {"name": "", "subject": "Test"}
        mock_client.get_all_documents.return_value = [task_record]

        records = list(service.fetch_records(mock_client))

        assert records[0]["_depends_on"] == []
        mock_client.get_task_dependencies.assert_not_called()

    def test_fetch_includes_assign_field(self) -> None:
        """fetch_records requests the _assign field."""
        from app.services.erpnext.sync.tasks import TaskSyncService

        db = MagicMock()
        service = TaskSyncService(db, uuid.uuid4(), uuid.uuid4())

        mock_client = MagicMock()
        mock_client.get_all_documents.return_value = []

        list(service.fetch_records(mock_client))

        # Verify _assign is in the fields list
        call_args = mock_client.get_all_documents.call_args
        fields = call_args.kwargs.get("fields") or call_args[1].get("fields", [])
        assert "_assign" in fields
