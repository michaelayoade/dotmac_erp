import uuid
from unittest.mock import patch

from app.models.scheduler import ScheduledTask, ScheduleType


class TestScheduledTasksAPI:
    """Tests for the /scheduler/tasks endpoints."""

    def test_list_scheduled_tasks(self, client, auth_headers, scheduled_task):
        """Test listing scheduled tasks."""
        response = client.get("/scheduler/tasks", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data
        assert isinstance(data["items"], list)

    def test_list_scheduled_tasks_with_pagination(
        self, client, auth_headers, db_session
    ):
        """Test listing scheduled tasks with pagination."""
        # Create multiple tasks
        for i in range(5):
            task = ScheduledTask(
                name=f"paginated_task_{i}_{uuid.uuid4().hex[:8]}",
                task_name=f"app.tasks.task_{i}",
                schedule_type=ScheduleType.interval,
                interval_seconds=60,
                enabled=True,
            )
            db_session.add(task)
        db_session.commit()

        response = client.get("/scheduler/tasks?limit=2&offset=0", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 2

    def test_list_scheduled_tasks_filter_by_enabled(
        self, client, auth_headers, db_session
    ):
        """Test filtering scheduled tasks by enabled status."""
        # Create enabled and disabled tasks
        enabled_task = ScheduledTask(
            name=f"enabled_task_{uuid.uuid4().hex[:8]}",
            task_name="app.tasks.enabled",
            schedule_type=ScheduleType.interval,
            interval_seconds=60,
            enabled=True,
        )
        disabled_task = ScheduledTask(
            name=f"disabled_task_{uuid.uuid4().hex[:8]}",
            task_name="app.tasks.disabled",
            schedule_type=ScheduleType.interval,
            interval_seconds=60,
            enabled=False,
        )
        db_session.add(enabled_task)
        db_session.add(disabled_task)
        db_session.commit()

        response = client.get("/scheduler/tasks?enabled=true", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["enabled"] is True

    def test_list_scheduled_tasks_with_ordering(self, client, auth_headers):
        """Test listing scheduled tasks with custom ordering."""
        response = client.get(
            "/scheduler/tasks?order_by=name&order_dir=asc", headers=auth_headers
        )
        assert response.status_code == 200

    def test_list_scheduled_tasks_unauthorized(self, client):
        """Test listing scheduled tasks without auth."""
        response = client.get("/scheduler/tasks", follow_redirects=False)
        # Returns 302 redirect to login when unauthorized
        assert response.status_code in [401, 302]

    def test_create_scheduled_task(self, client, auth_headers):
        """Test creating a new scheduled task."""
        payload = {
            "name": f"new_task_{uuid.uuid4().hex[:8]}",
            "task_name": "app.tasks.new_task",
            "interval_seconds": 300,
            "enabled": True,
        }
        response = client.post("/scheduler/tasks", json=payload, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == payload["name"]
        assert data["task_name"] == payload["task_name"]
        assert data["interval_seconds"] == payload["interval_seconds"]
        assert "id" in data

    def test_create_scheduled_task_with_args(self, client, auth_headers):
        """Test creating a scheduled task with arguments."""
        payload = {
            "name": f"args_task_{uuid.uuid4().hex[:8]}",
            "task_name": "app.tasks.args_task",
            "interval_seconds": 600,
            "enabled": True,
            "args_json": ["arg1", "arg2"],
            "kwargs_json": {"key1": "value1"},
        }
        response = client.post("/scheduler/tasks", json=payload, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["args_json"] == ["arg1", "arg2"]
        assert data["kwargs_json"] == {"key1": "value1"}

    def test_create_scheduled_task_unauthorized(self, client):
        """Test creating a scheduled task without auth."""
        payload = {
            "name": "unauthorized_task",
            "task_name": "app.tasks.unauthorized",
            "interval_seconds": 120,
        }
        response = client.post("/scheduler/tasks", json=payload, follow_redirects=False)
        # Returns 302 redirect to login when unauthorized
        assert response.status_code in [401, 302]

    def test_get_scheduled_task(self, client, auth_headers, scheduled_task):
        """Test getting a scheduled task by ID."""
        response = client.get(
            f"/scheduler/tasks/{scheduled_task.id}", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(scheduled_task.id)
        assert data["name"] == scheduled_task.name

    def test_get_scheduled_task_not_found(self, client, auth_headers):
        """Test getting a non-existent scheduled task."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/scheduler/tasks/{fake_id}", headers=auth_headers)
        assert response.status_code == 404

    def test_update_scheduled_task(self, client, auth_headers, scheduled_task):
        """Test updating a scheduled task."""
        payload = {"interval_seconds": 900, "enabled": False}
        response = client.patch(
            f"/scheduler/tasks/{scheduled_task.id}", json=payload, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["interval_seconds"] == 900
        assert data["enabled"] is False

    def test_update_scheduled_task_not_found(self, client, auth_headers):
        """Test updating a non-existent scheduled task."""
        fake_id = str(uuid.uuid4())
        payload = {"interval_seconds": 120}
        response = client.patch(
            f"/scheduler/tasks/{fake_id}", json=payload, headers=auth_headers
        )
        assert response.status_code == 404

    def test_delete_scheduled_task(self, client, auth_headers, db_session):
        """Test deleting a scheduled task."""
        task = ScheduledTask(
            name=f"to_delete_{uuid.uuid4().hex[:8]}",
            task_name="app.tasks.to_delete",
            schedule_type=ScheduleType.interval,
            interval_seconds=60,
            enabled=True,
        )
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)

        response = client.delete(f"/scheduler/tasks/{task.id}", headers=auth_headers)
        assert response.status_code == 204

    def test_delete_scheduled_task_not_found(self, client, auth_headers):
        """Test deleting a non-existent scheduled task."""
        fake_id = str(uuid.uuid4())
        response = client.delete(f"/scheduler/tasks/{fake_id}", headers=auth_headers)
        assert response.status_code == 404

    def test_refresh_schedule(self, client, auth_headers):
        """Test refreshing the scheduler."""
        with patch(
            "app.api.scheduler.scheduler_service.refresh_schedule",
            return_value={"detail": "ok"},
        ):
            response = client.post("/scheduler/tasks/refresh", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["detail"] == "ok"

    def test_enqueue_scheduled_task(self, client, auth_headers, scheduled_task):
        """Test manually enqueuing a scheduled task."""
        with patch(
            "app.api.scheduler.scheduler_service.enqueue_task",
            return_value={"queued": True, "task_id": "test-task"},
        ):
            response = client.post(
                f"/scheduler/tasks/{scheduled_task.id}/enqueue", headers=auth_headers
            )
        assert response.status_code == 202
        assert response.json()["queued"] is True


class TestSchedulerAPIV1:
    """Tests for the /api/v1/scheduler endpoints."""

    def test_list_scheduled_tasks_v1(self, client, auth_headers):
        """Test listing scheduled tasks via v1 API."""
        response = client.get("/api/v1/scheduler/tasks", headers=auth_headers)
        assert response.status_code == 200

    def test_create_scheduled_task_v1(self, client, auth_headers):
        """Test creating a scheduled task via v1 API."""
        payload = {
            "name": f"v1_task_{uuid.uuid4().hex[:8]}",
            "task_name": "app.tasks.v1_task",
            "interval_seconds": 300,
            "enabled": True,
        }
        response = client.post(
            "/api/v1/scheduler/tasks", json=payload, headers=auth_headers
        )
        assert response.status_code == 201

    def test_get_scheduled_task_v1(self, client, auth_headers, scheduled_task):
        """Test getting a scheduled task via v1 API."""
        response = client.get(
            f"/api/v1/scheduler/tasks/{scheduled_task.id}", headers=auth_headers
        )
        assert response.status_code == 200


class TestScheduledTaskValidation:
    """Tests for scheduled task input validation."""

    def test_create_task_missing_required_fields(self, client, auth_headers):
        """Test creating a task without required fields."""
        payload = {"name": "incomplete_task"}
        response = client.post("/scheduler/tasks", json=payload, headers=auth_headers)
        assert response.status_code == 422

    def test_create_task_invalid_cron(self, client, auth_headers):
        """Test creating a task with invalid interval."""
        payload = {
            "name": f"invalid_interval_{uuid.uuid4().hex[:8]}",
            "task_name": "app.tasks.invalid",
            "interval_seconds": 0,
            "enabled": True,
        }
        response = client.post("/scheduler/tasks", json=payload, headers=auth_headers)
        assert response.status_code == 400
