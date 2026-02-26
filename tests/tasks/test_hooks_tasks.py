"""Tests for hook-related Celery tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.models.finance.platform.service_hook import (
    HookExecutionMode,
    HookHandlerType,
    ServiceHook,
)
from app.models.finance.platform.service_hook_execution import (
    ExecutionStatus,
    ServiceHookExecution,
)


class TestCleanupOldHookExecutions:
    """Tests for cleanup_old_hook_executions task."""

    def test_deletes_old_hook_executions(self) -> None:
        mock_db = MagicMock()
        mock_db.scalars.return_value.all.return_value = [uuid4(), uuid4()]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.delete.return_value = 2
        mock_db.query.return_value = mock_query

        with patch("app.tasks.hooks.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            from app.tasks.hooks import cleanup_old_hook_executions

            result = cleanup_old_hook_executions(retention_days=90, batch_size=5000)

        assert result["deleted"] == 2
        assert result["errors"] == []
        mock_db.commit.assert_called_once()

    def test_returns_zero_when_no_rows(self) -> None:
        mock_db = MagicMock()
        mock_db.scalars.return_value.all.return_value = []

        with patch("app.tasks.hooks.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            from app.tasks.hooks import cleanup_old_hook_executions

            result = cleanup_old_hook_executions()

        assert result["deleted"] == 0
        assert result["errors"] == []
        mock_db.commit.assert_not_called()

    def test_handles_exception_gracefully(self) -> None:
        mock_db = MagicMock()
        mock_db.scalars.return_value.all.side_effect = RuntimeError("DB failure")

        with patch("app.tasks.hooks.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            from app.tasks.hooks import cleanup_old_hook_executions

            result = cleanup_old_hook_executions()

        assert len(result["errors"]) == 1
        assert "DB failure" in result["errors"][0]
        mock_db.rollback.assert_called_once()

    def test_validates_inputs(self) -> None:
        from app.tasks.hooks import cleanup_old_hook_executions

        with pytest.raises(ValueError, match="retention_days"):
            cleanup_old_hook_executions(retention_days=0)
        with pytest.raises(ValueError, match="batch_size"):
            cleanup_old_hook_executions(batch_size=0)


class TestExecuteAsyncHook:
    """Tests for execute_async_hook task."""

    def test_retries_with_exponential_backoff(self) -> None:
        execution_id = uuid4()
        hook_id = uuid4()
        org_id = uuid4()
        entity_id = uuid4()
        hook = ServiceHook(
            hook_id=hook_id,
            organization_id=org_id,
            event_name="invoice.posted",
            handler_type=HookHandlerType.WEBHOOK,
            execution_mode=HookExecutionMode.ASYNC,
            name="Retry Hook",
            max_retries=3,
            retry_backoff_seconds=60,
            handler_config={"url": "https://example.com/hook"},
            conditions={},
        )
        execution = ServiceHookExecution(
            execution_id=execution_id,
            hook_id=hook_id,
            organization_id=org_id,
            event_name="invoice.posted",
            event_payload={
                "_hook_meta": {"entity_type": "ARInvoice", "entity_id": str(entity_id)}
            },
            status=ExecutionStatus.PENDING,
            retry_count=0,
            created_at=datetime.now(UTC),
        )

        mock_db = MagicMock()
        mock_db.get.side_effect = [execution, hook]
        request = httpx.Request("POST", "https://example.com/hook")
        retryable_error = httpx.ConnectError("boom", request=request)

        with (
            patch("app.tasks.hooks.SessionLocal") as mock_session,
            patch("app.tasks.hooks._execute_hook_handler", side_effect=retryable_error),
        ):
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            from app.tasks.hooks import execute_async_hook

            retry_exc = RuntimeError("retry requested")
            with patch.object(
                execute_async_hook, "retry", side_effect=retry_exc
            ) as mock_retry:
                with pytest.raises(RuntimeError, match="retry requested"):
                    execute_async_hook.run(
                        execution_id=str(execution_id),
                        hook_id=str(hook_id),
                    )

        assert execution.status == ExecutionStatus.RETRYING
        assert execution.retry_count == 1
        mock_retry.assert_called_once()
        assert mock_retry.call_args.kwargs["countdown"] == 60

    def test_marks_dead_after_max_retries(self) -> None:
        execution_id = uuid4()
        hook_id = uuid4()
        org_id = uuid4()
        entity_id = uuid4()
        hook = ServiceHook(
            hook_id=hook_id,
            organization_id=org_id,
            event_name="invoice.posted",
            handler_type=HookHandlerType.WEBHOOK,
            execution_mode=HookExecutionMode.ASYNC,
            name="Dead Hook",
            max_retries=1,
            retry_backoff_seconds=30,
            handler_config={
                "url": "https://example.com/hook",
                "circuit_breaker_failures": 1,
            },
            conditions={},
        )
        execution = ServiceHookExecution(
            execution_id=execution_id,
            hook_id=hook_id,
            organization_id=org_id,
            event_name="invoice.posted",
            event_payload={
                "_hook_meta": {"entity_type": "ARInvoice", "entity_id": str(entity_id)}
            },
            status=ExecutionStatus.PENDING,
            retry_count=0,
            created_at=datetime.now(UTC),
        )

        mock_db = MagicMock()
        mock_db.get.side_effect = [execution, hook]
        mock_db.scalars.return_value.all.return_value = [ExecutionStatus.DEAD]
        request = httpx.Request("POST", "https://example.com/hook")
        retryable_error = httpx.ConnectError("boom", request=request)

        with (
            patch("app.tasks.hooks.SessionLocal") as mock_session,
            patch("app.tasks.hooks._execute_hook_handler", side_effect=retryable_error),
        ):
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            from app.tasks.hooks import execute_async_hook

            with patch.object(execute_async_hook, "retry") as mock_retry:
                result = execute_async_hook.run(
                    execution_id=str(execution_id),
                    hook_id=str(hook_id),
                )

        assert result["ok"] is False
        assert execution.status == ExecutionStatus.DEAD
        assert execution.retry_count == 1
        assert hook.is_active is False
        mock_retry.assert_not_called()

    def test_webhook_client_error_fails_without_retry(self) -> None:
        execution_id = uuid4()
        hook_id = uuid4()
        org_id = uuid4()
        entity_id = uuid4()
        hook = ServiceHook(
            hook_id=hook_id,
            organization_id=org_id,
            event_name="invoice.posted",
            handler_type=HookHandlerType.WEBHOOK,
            execution_mode=HookExecutionMode.ASYNC,
            name="Client Error Hook",
            max_retries=3,
            retry_backoff_seconds=30,
            handler_config={"url": "https://example.com/hook"},
            conditions={},
        )
        execution = ServiceHookExecution(
            execution_id=execution_id,
            hook_id=hook_id,
            organization_id=org_id,
            event_name="invoice.posted",
            event_payload={
                "_hook_meta": {"entity_type": "ARInvoice", "entity_id": str(entity_id)}
            },
            status=ExecutionStatus.PENDING,
            retry_count=0,
            created_at=datetime.now(UTC),
        )
        request = httpx.Request("POST", "https://example.com/hook")
        response = httpx.Response(status_code=400, request=request)
        error = httpx.HTTPStatusError(
            "client error", request=request, response=response
        )

        mock_db = MagicMock()
        mock_db.get.side_effect = [execution, hook]

        with (
            patch("app.tasks.hooks.SessionLocal") as mock_session,
            patch("app.tasks.hooks._execute_hook_handler", side_effect=error),
        ):
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            from app.tasks.hooks import execute_async_hook

            with patch.object(execute_async_hook, "retry") as mock_retry:
                result = execute_async_hook.run(
                    execution_id=str(execution_id),
                    hook_id=str(hook_id),
                )

        assert result["ok"] is False
        assert execution.status == ExecutionStatus.FAILED
        assert execution.retry_count == 1
        mock_retry.assert_not_called()

    def test_webhook_server_error_retries(self) -> None:
        execution_id = uuid4()
        hook_id = uuid4()
        org_id = uuid4()
        entity_id = uuid4()
        hook = ServiceHook(
            hook_id=hook_id,
            organization_id=org_id,
            event_name="invoice.posted",
            handler_type=HookHandlerType.WEBHOOK,
            execution_mode=HookExecutionMode.ASYNC,
            name="Server Error Hook",
            max_retries=3,
            retry_backoff_seconds=30,
            handler_config={"url": "https://example.com/hook"},
            conditions={},
        )
        execution = ServiceHookExecution(
            execution_id=execution_id,
            hook_id=hook_id,
            organization_id=org_id,
            event_name="invoice.posted",
            event_payload={
                "_hook_meta": {"entity_type": "ARInvoice", "entity_id": str(entity_id)}
            },
            status=ExecutionStatus.PENDING,
            retry_count=0,
            created_at=datetime.now(UTC),
        )
        request = httpx.Request("POST", "https://example.com/hook")
        response = httpx.Response(status_code=502, request=request)
        error = httpx.HTTPStatusError(
            "server error", request=request, response=response
        )

        mock_db = MagicMock()
        mock_db.get.side_effect = [execution, hook]

        with (
            patch("app.tasks.hooks.SessionLocal") as mock_session,
            patch("app.tasks.hooks._execute_hook_handler", side_effect=error),
        ):
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            from app.tasks.hooks import execute_async_hook

            retry_exc = RuntimeError("retry requested")
            with patch.object(
                execute_async_hook, "retry", side_effect=retry_exc
            ) as mock_retry:
                with pytest.raises(RuntimeError, match="retry requested"):
                    execute_async_hook.run(
                        execution_id=str(execution_id),
                        hook_id=str(hook_id),
                    )

        assert execution.status == ExecutionStatus.RETRYING
        assert execution.retry_count == 1
        mock_retry.assert_called_once()
