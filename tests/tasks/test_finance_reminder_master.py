from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.tasks import finance


def _task_results():
    return {
        "fiscal_periods": {"notifications_sent": 1},
        "tax_periods": {"notifications_sent": 2},
        "bank_reconciliation": {"notifications_sent": 3},
        "ar_collection": {"notifications_sent": 4},
        "subledger_reconciliation": {"notifications_sent": 5},
    }


def _patch_task_runners(monkeypatch, results):
    task_names = {
        "fiscal_periods": "process_fiscal_period_reminders",
        "tax_periods": "process_tax_period_reminders",
        "bank_reconciliation": "process_bank_reconciliation_reminders",
        "ar_collection": "process_ar_collection_reminders",
        "subledger_reconciliation": "process_subledger_reconciliation",
    }
    runners = {}
    for result_name, function_name in task_names.items():
        runner = MagicMock(return_value=results.get(result_name, {}))
        monkeypatch.setattr(finance, function_name, runner)
        runners[result_name] = runner
    return runners


def test_finance_reminder_master_reports_and_releases_lock(monkeypatch):
    lock = object()
    monkeypatch.setattr(finance, "_try_acquire_finance_reminders_lock", lambda: lock)
    release = MagicMock()
    monkeypatch.setattr(finance, "_release_finance_reminders_lock", release)
    runners = _patch_task_runners(monkeypatch, _task_results())

    result = finance.process_all_finance_reminders.run()

    assert result["outcome"] == "completed"
    assert result["blocked"] == 0
    assert result["total_notifications"] == 15
    assert result["task_errors"] == []
    assert all(runner.call_count == 1 for runner in runners.values())
    release.assert_called_once_with(lock)


def test_finance_reminder_master_skips_when_another_run_holds_lock(monkeypatch):
    monkeypatch.setattr(finance, "_try_acquire_finance_reminders_lock", lambda: None)
    release = MagicMock()
    monkeypatch.setattr(finance, "_release_finance_reminders_lock", release)
    runners = _patch_task_runners(monkeypatch, {})

    result = finance.process_all_finance_reminders.run()

    assert result["outcome"] == "skipped_overlap"
    assert result["blocked"] == 1
    assert result["total_notifications"] == 0
    assert all(runner.call_count == 0 for runner in runners.values())
    release.assert_not_called()


def test_finance_reminder_master_continues_after_component_error_and_fails_task(
    monkeypatch,
):
    lock = object()
    monkeypatch.setattr(finance, "_try_acquire_finance_reminders_lock", lambda: lock)
    release = MagicMock()
    monkeypatch.setattr(finance, "_release_finance_reminders_lock", release)
    results = _task_results()
    runners = _patch_task_runners(monkeypatch, results)
    runners["tax_periods"].side_effect = RuntimeError("tax store unavailable")

    with pytest.raises(finance.FinanceReminderTaskFailure, match="1 component error"):
        finance.process_all_finance_reminders.run()

    assert runners["tax_periods"].call_count == 1
    assert runners["ar_collection"].call_count == 1
    assert runners["subledger_reconciliation"].call_count == 1
    release.assert_called_once_with(lock)


def test_finance_reminder_lock_uses_session_advisory_lock():
    engine = MagicMock()
    connection = engine.connect.return_value
    connection.execution_options.return_value = connection
    connection.scalar.return_value = True

    with patch("app.db.get_engine", return_value=engine):
        acquired = finance._try_acquire_finance_reminders_lock()

    assert acquired is connection
    connection.execution_options.assert_called_once_with(isolation_level="AUTOCOMMIT")
    statement = str(connection.scalar.call_args.args[0])
    assert "pg_try_advisory_lock" in statement
    assert connection.scalar.call_args.args[1] == {
        "lock_identity": "dotmac_erp:finance:all_reminders"
    }
    connection.close.assert_not_called()


def test_finance_reminder_lock_releases_on_the_acquiring_connection():
    connection = MagicMock()
    connection.scalar.return_value = True

    finance._release_finance_reminders_lock(connection)

    statement = str(connection.scalar.call_args.args[0])
    assert "pg_advisory_unlock" in statement
    assert connection.scalar.call_args.args[1] == {
        "lock_identity": "dotmac_erp:finance:all_reminders"
    }
    connection.close.assert_called_once_with()


def test_finance_reminder_lock_invalidates_connection_when_unlock_unconfirmed():
    connection = MagicMock()
    connection.scalar.return_value = False

    with pytest.raises(RuntimeError, match="unlock was not acknowledged"):
        finance._release_finance_reminders_lock(connection)

    connection.invalidate.assert_called_once_with()
    connection.close.assert_called_once_with()
