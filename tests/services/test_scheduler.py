"""
Tests for app/services/scheduler.py

Tests for ScheduledTasks service class that manages scheduled task CRUD operations,
and standalone functions for schedule refresh and task enqueueing.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.scheduler import ScheduledTask, ScheduleType
from app.schemas.scheduler import ScheduledTaskCreate, ScheduledTaskUpdate
from app.services.scheduler import (
    ScheduledTasks,
    _validate_schedule_type,
    refresh_schedule,
    enqueue_task,
)


# ============ TestValidateScheduleType ============

class TestValidateScheduleType:
    """Tests for the _validate_schedule_type function."""

    def test_validate_schedule_type_none_returns_none(self):
        """None input should return None."""
        result = _validate_schedule_type(None)
        assert result is None

    def test_validate_schedule_type_enum_passthrough(self):
        """ScheduleType enum should pass through unchanged."""
        result = _validate_schedule_type(ScheduleType.interval)
        assert result is ScheduleType.interval

    def test_validate_schedule_type_valid_string(self):
        """Valid string should be converted to ScheduleType."""
        result = _validate_schedule_type("interval")
        assert result == ScheduleType.interval

    def test_validate_schedule_type_invalid_raises_400(self):
        """Invalid value should raise HTTPException with 400."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_schedule_type("invalid_type")

        assert exc_info.value.status_code == 400
        assert "Invalid schedule_type" in exc_info.value.detail


# ============ TestScheduledTasksCreate ============

class TestScheduledTasksCreate:
    """Tests for ScheduledTasks.create method."""

    def test_create_valid_interval_task(self, mock_db):
        """Should create a valid interval task."""
        payload = MagicMock()
        payload.interval_seconds = 300
        payload.model_dump.return_value = {
            "name": "test_task",
            "task_name": "app.tasks.test",
            "schedule_type": ScheduleType.interval,
            "interval_seconds": 300,
            "enabled": True,
        }

        with patch("app.services.scheduler.ScheduledTask") as MockTask:
            mock_task = MagicMock()
            MockTask.return_value = mock_task

            result = ScheduledTasks.create(mock_db, payload)

            mock_db.add.assert_called_once_with(mock_task)
            mock_db.commit.assert_called_once()
            mock_db.refresh.assert_called_once_with(mock_task)

    def test_create_interval_seconds_zero_raises_400(self, mock_db):
        """interval_seconds of 0 should raise HTTPException."""
        payload = MagicMock()
        payload.interval_seconds = 0

        with pytest.raises(HTTPException) as exc_info:
            ScheduledTasks.create(mock_db, payload)

        assert exc_info.value.status_code == 400
        assert "interval_seconds must be >= 1" in exc_info.value.detail

    def test_create_interval_seconds_negative_raises_400(self, mock_db):
        """Negative interval_seconds should raise HTTPException."""
        payload = MagicMock()
        payload.interval_seconds = -10

        with pytest.raises(HTTPException) as exc_info:
            ScheduledTasks.create(mock_db, payload)

        assert exc_info.value.status_code == 400

    def test_create_minimum_interval_1(self, mock_db):
        """Minimum interval of 1 second should be accepted."""
        payload = MagicMock()
        payload.interval_seconds = 1
        payload.model_dump.return_value = {
            "name": "quick_task",
            "task_name": "app.tasks.quick",
            "interval_seconds": 1,
        }

        with patch("app.services.scheduler.ScheduledTask"):
            # Should not raise
            ScheduledTasks.create(mock_db, payload)

    def test_create_refreshes_from_db(self, mock_db):
        """Created task should be refreshed from database."""
        payload = MagicMock()
        payload.interval_seconds = 60
        payload.model_dump.return_value = {"interval_seconds": 60}

        with patch("app.services.scheduler.ScheduledTask") as MockTask:
            mock_task = MagicMock()
            MockTask.return_value = mock_task

            ScheduledTasks.create(mock_db, payload)

            mock_db.refresh.assert_called_once_with(mock_task)


# ============ TestScheduledTasksGet ============

class TestScheduledTasksGet:
    """Tests for ScheduledTasks.get method."""

    def test_get_existing_task(self, mock_db, mock_scheduled_task):
        """Should return existing task by ID."""
        mock_db.get.return_value = mock_scheduled_task
        task_id = str(uuid.uuid4())

        result = ScheduledTasks.get(mock_db, task_id)

        assert result == mock_scheduled_task

    def test_get_nonexistent_raises_404(self, mock_db):
        """Nonexistent task ID should raise HTTPException with 404."""
        mock_db.get.return_value = None
        task_id = str(uuid.uuid4())

        with pytest.raises(HTTPException) as exc_info:
            ScheduledTasks.get(mock_db, task_id)

        assert exc_info.value.status_code == 404
        assert "Scheduled task not found" in exc_info.value.detail

    def test_get_invalid_uuid_raises(self, mock_db):
        """Invalid UUID format should raise HTTPException(400)."""
        with pytest.raises(HTTPException) as exc_info:
            ScheduledTasks.get(mock_db, "not-a-uuid")
        assert exc_info.value.status_code == 400


# ============ TestScheduledTasksList ============

class TestScheduledTasksList:
    """Tests for ScheduledTasks.list method."""

    def test_list_all_tasks(self, mock_db, mock_scheduled_task):
        """Should list all tasks when no filters applied."""
        mock_db.query.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = [
            mock_scheduled_task
        ]

        result = ScheduledTasks.list(
            mock_db,
            enabled=None,
            order_by="name",
            order_dir="asc",
            limit=10,
            offset=0,
        )

        assert len(result) == 1
        assert result[0] == mock_scheduled_task

    def test_list_enabled_only(self, mock_db, mock_scheduled_task):
        """Should filter by enabled=True."""
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = [mock_scheduled_task]

        result = ScheduledTasks.list(
            mock_db,
            enabled=True,
            order_by="name",
            order_dir="asc",
            limit=10,
            offset=0,
        )

        mock_query.filter.assert_called()

    def test_list_disabled_only(self, mock_db):
        """Should filter by enabled=False."""
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = []

        result = ScheduledTasks.list(
            mock_db,
            enabled=False,
            order_by="name",
            order_dir="asc",
            limit=10,
            offset=0,
        )

        mock_query.filter.assert_called()

    def test_list_order_by_name_asc(self, mock_db):
        """Should order by name ascending."""
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = []

        ScheduledTasks.list(
            mock_db,
            enabled=None,
            order_by="name",
            order_dir="asc",
            limit=10,
            offset=0,
        )

        mock_query.order_by.assert_called()

    def test_list_order_by_name_desc(self, mock_db):
        """Should order by name descending."""
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = []

        ScheduledTasks.list(
            mock_db,
            enabled=None,
            order_by="name",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_query.order_by.assert_called()

    def test_list_order_by_created_at(self, mock_db):
        """Should order by created_at."""
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = []

        ScheduledTasks.list(
            mock_db,
            enabled=None,
            order_by="created_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_query.order_by.assert_called()

    def test_list_invalid_order_by_raises_400(self, mock_db):
        """Invalid order_by column should raise HTTPException."""
        with pytest.raises(HTTPException) as exc_info:
            ScheduledTasks.list(
                mock_db,
                enabled=None,
                order_by="invalid_column",
                order_dir="asc",
                limit=10,
                offset=0,
            )

        assert exc_info.value.status_code == 400
        assert "Invalid order_by" in exc_info.value.detail

    def test_list_pagination(self, mock_db):
        """Should apply limit and offset correctly."""
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_limited = MagicMock()
        mock_query.limit.return_value = mock_limited
        mock_offset = MagicMock()
        mock_limited.offset.return_value = mock_offset
        mock_offset.all.return_value = []

        ScheduledTasks.list(
            mock_db,
            enabled=None,
            order_by="name",
            order_dir="asc",
            limit=25,
            offset=50,
        )

        mock_query.limit.assert_called_once_with(25)
        mock_limited.offset.assert_called_once_with(50)


# ============ TestScheduledTasksUpdate ============

class TestScheduledTasksUpdate:
    """Tests for ScheduledTasks.update method."""

    def test_update_interval(self, mock_db, mock_scheduled_task):
        """Should update interval_seconds."""
        mock_db.get.return_value = mock_scheduled_task
        payload = MagicMock()
        payload.model_dump.return_value = {"interval_seconds": 600}

        result = ScheduledTasks.update(mock_db, str(mock_scheduled_task.id), payload)

        assert mock_scheduled_task.interval_seconds == 600
        mock_db.commit.assert_called_once()

    def test_update_enabled(self, mock_db, mock_scheduled_task):
        """Should update enabled status."""
        mock_db.get.return_value = mock_scheduled_task
        payload = MagicMock()
        payload.model_dump.return_value = {"enabled": False}

        result = ScheduledTasks.update(mock_db, str(mock_scheduled_task.id), payload)

        assert mock_scheduled_task.enabled is False

    def test_update_schedule_type(self, mock_db, mock_scheduled_task):
        """Should update and validate schedule_type."""
        mock_db.get.return_value = mock_scheduled_task
        payload = MagicMock()
        payload.model_dump.return_value = {"schedule_type": "interval"}

        result = ScheduledTasks.update(mock_db, str(mock_scheduled_task.id), payload)

        assert mock_scheduled_task.schedule_type == ScheduleType.interval

    def test_update_nonexistent_raises_404(self, mock_db):
        """Updating nonexistent task should raise HTTPException."""
        mock_db.get.return_value = None
        payload = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            ScheduledTasks.update(mock_db, str(uuid.uuid4()), payload)

        assert exc_info.value.status_code == 404

    def test_update_interval_zero_raises_400(self, mock_db, mock_scheduled_task):
        """Zero interval_seconds in update should raise HTTPException."""
        mock_db.get.return_value = mock_scheduled_task
        payload = MagicMock()
        payload.model_dump.return_value = {"interval_seconds": 0}

        with pytest.raises(HTTPException) as exc_info:
            ScheduledTasks.update(mock_db, str(mock_scheduled_task.id), payload)

        assert exc_info.value.status_code == 400

    def test_update_invalid_schedule_type_raises_400(self, mock_db, mock_scheduled_task):
        """Invalid schedule_type should raise HTTPException."""
        mock_db.get.return_value = mock_scheduled_task
        payload = MagicMock()
        payload.model_dump.return_value = {"schedule_type": "invalid"}

        with pytest.raises(HTTPException) as exc_info:
            ScheduledTasks.update(mock_db, str(mock_scheduled_task.id), payload)

        assert exc_info.value.status_code == 400

    def test_update_partial_preserves_fields(self, mock_db, mock_scheduled_task):
        """Partial update should preserve other fields."""
        mock_scheduled_task.name = "original_name"
        mock_scheduled_task.interval_seconds = 300
        mock_db.get.return_value = mock_scheduled_task
        payload = MagicMock()
        payload.model_dump.return_value = {"enabled": True}

        ScheduledTasks.update(mock_db, str(mock_scheduled_task.id), payload)

        # Original values should be preserved
        assert mock_scheduled_task.name == "original_name"
        assert mock_scheduled_task.interval_seconds == 300


# ============ TestScheduledTasksDelete ============

class TestScheduledTasksDelete:
    """Tests for ScheduledTasks.delete method."""

    def test_delete_existing(self, mock_db, mock_scheduled_task):
        """Should delete existing task."""
        mock_db.get.return_value = mock_scheduled_task

        ScheduledTasks.delete(mock_db, str(mock_scheduled_task.id))

        mock_db.delete.assert_called_once_with(mock_scheduled_task)
        mock_db.commit.assert_called_once()

    def test_delete_nonexistent_raises_404(self, mock_db):
        """Deleting nonexistent task should raise HTTPException."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            ScheduledTasks.delete(mock_db, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404


# ============ TestRefreshSchedule ============

class TestRefreshSchedule:
    """Tests for the refresh_schedule function."""

    def test_refresh_schedule_returns_detail(self):
        """Should return a dict with detail message."""
        result = refresh_schedule()

        assert isinstance(result, dict)
        assert "detail" in result
        assert "Celery" in result["detail"]


# ============ TestEnqueueTask ============

class TestEnqueueTask:
    """Tests for the enqueue_task function."""

    @patch("app.celery_app.celery_app")
    def test_enqueue_task_success(self, mock_celery):
        """Should enqueue task and return task_id."""
        mock_result = MagicMock()
        mock_result.id = uuid.uuid4()
        mock_celery.send_task.return_value = mock_result

        result = enqueue_task("app.tasks.test", None, None)

        assert result["queued"] is True
        assert "task_id" in result
        mock_celery.send_task.assert_called_once()

    @patch("app.celery_app.celery_app")
    def test_enqueue_task_with_args(self, mock_celery):
        """Should pass args to send_task."""
        mock_result = MagicMock()
        mock_result.id = uuid.uuid4()
        mock_celery.send_task.return_value = mock_result

        enqueue_task("app.tasks.test", ["arg1", "arg2"], {"key": "value"})

        mock_celery.send_task.assert_called_once_with(
            "app.tasks.test", args=["arg1", "arg2"], kwargs={"key": "value"}
        )

    @patch("app.celery_app.celery_app")
    def test_enqueue_task_none_args_defaults(self, mock_celery):
        """None args/kwargs should default to empty list/dict."""
        mock_result = MagicMock()
        mock_result.id = uuid.uuid4()
        mock_celery.send_task.return_value = mock_result

        enqueue_task("app.tasks.test", None, None)

        mock_celery.send_task.assert_called_once_with(
            "app.tasks.test", args=[], kwargs={}
        )
