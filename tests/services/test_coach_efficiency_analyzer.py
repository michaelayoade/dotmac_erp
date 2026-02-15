"""Tests for EfficiencyAnalyzer severity helpers and dataclass construction."""

from __future__ import annotations

from decimal import Decimal

from app.services.coach.analyzers.efficiency import (
    LeaveApprovalBacklogSummary,
    PeriodCloseVelocitySummary,
    WorkflowHealthSummary,
    _severity_for_leave_backlog,
    _severity_for_period_close,
    _severity_for_workflow_health,
)

# -- _severity_for_period_close ------------------------------------------------


def test_period_close_info_healthy() -> None:
    assert _severity_for_period_close(Decimal("5"), 0) == "INFO"


def test_period_close_info_no_data() -> None:
    assert _severity_for_period_close(None, 0) == "INFO"


def test_period_close_attention_high_lag() -> None:
    assert _severity_for_period_close(Decimal("16"), 0) == "ATTENTION"


def test_period_close_attention_one_open() -> None:
    assert _severity_for_period_close(Decimal("5"), 1) == "ATTENTION"


def test_period_close_warning_many_open() -> None:
    assert _severity_for_period_close(Decimal("5"), 3) == "WARNING"
    assert _severity_for_period_close(Decimal("30"), 5) == "WARNING"


def test_period_close_boundary() -> None:
    # 15 = NOT > 15, so INFO with 0 open
    assert _severity_for_period_close(Decimal("15"), 0) == "INFO"
    # 15.1 > 15 = ATTENTION
    assert _severity_for_period_close(Decimal("15.1"), 0) == "ATTENTION"
    # 2 open = ATTENTION (< 3)
    assert _severity_for_period_close(None, 2) == "ATTENTION"


# -- _severity_for_leave_backlog -----------------------------------------------


def test_leave_backlog_info() -> None:
    assert _severity_for_leave_backlog(3, 2) == "INFO"


def test_leave_backlog_attention_many_pending() -> None:
    assert _severity_for_leave_backlog(10, 3) == "ATTENTION"


def test_leave_backlog_attention_week_old() -> None:
    assert _severity_for_leave_backlog(2, 7) == "ATTENTION"


def test_leave_backlog_warning_two_weeks() -> None:
    assert _severity_for_leave_backlog(1, 14) == "WARNING"
    assert _severity_for_leave_backlog(20, 21) == "WARNING"


def test_leave_backlog_boundary() -> None:
    # 9 pending with 6 days = INFO
    assert _severity_for_leave_backlog(9, 6) == "INFO"
    # 10 pending = ATTENTION
    assert _severity_for_leave_backlog(10, 0) == "ATTENTION"
    # 13 days = ATTENTION (< 14)
    assert _severity_for_leave_backlog(1, 13) == "ATTENTION"


# -- _severity_for_workflow_health ---------------------------------------------


def test_workflow_health_info() -> None:
    assert _severity_for_workflow_health(Decimal("95"), 2) == "INFO"


def test_workflow_health_attention_low_rate() -> None:
    assert _severity_for_workflow_health(Decimal("88"), 3) == "ATTENTION"


def test_workflow_health_attention_some_failures() -> None:
    assert _severity_for_workflow_health(Decimal("95"), 5) == "ATTENTION"


def test_workflow_health_warning_very_low_rate() -> None:
    assert _severity_for_workflow_health(Decimal("75"), 10) == "WARNING"


def test_workflow_health_warning_many_failures() -> None:
    assert _severity_for_workflow_health(Decimal("95"), 20) == "WARNING"


def test_workflow_health_boundary() -> None:
    # 90% = NOT < 90, 4 failures = NOT >= 5 → INFO
    assert _severity_for_workflow_health(Decimal("90"), 4) == "INFO"
    # 89.9% < 90 → ATTENTION
    assert _severity_for_workflow_health(Decimal("89.9"), 0) == "ATTENTION"
    # 80% = NOT < 80 but < 90 → ATTENTION
    assert _severity_for_workflow_health(Decimal("80"), 4) == "ATTENTION"
    # 79.9% < 80 → WARNING
    assert _severity_for_workflow_health(Decimal("79.9"), 0) == "WARNING"
    # 19 failures → ATTENTION (< 20)
    assert _severity_for_workflow_health(Decimal("95"), 19) == "ATTENTION"


# -- Dataclass construction ---------------------------------------------------


def test_period_close_velocity_summary() -> None:
    summary = PeriodCloseVelocitySummary(
        avg_close_lag_days=Decimal("8.5"),
        periods_closed_90d=6,
        periods_still_open_past_end=2,
    )
    assert summary.avg_close_lag_days == Decimal("8.5")
    assert summary.periods_closed_90d == 6
    assert summary.periods_still_open_past_end == 2


def test_period_close_velocity_no_data() -> None:
    summary = PeriodCloseVelocitySummary(
        avg_close_lag_days=None,
        periods_closed_90d=0,
        periods_still_open_past_end=0,
    )
    assert summary.avg_close_lag_days is None


def test_leave_approval_backlog_summary() -> None:
    summary = LeaveApprovalBacklogSummary(
        total_pending=12,
        oldest_pending_days=9,
        top_approvers=[
            {"employee_id": "abc", "name": "John Doe", "pending_count": 5},
            {"employee_id": "def", "name": "Jane Smith", "pending_count": 4},
        ],
    )
    assert summary.total_pending == 12
    assert summary.oldest_pending_days == 9
    assert len(summary.top_approvers) == 2
    assert summary.top_approvers[0]["name"] == "John Doe"


def test_leave_approval_backlog_empty() -> None:
    summary = LeaveApprovalBacklogSummary(
        total_pending=0,
        oldest_pending_days=0,
        top_approvers=[],
    )
    assert summary.total_pending == 0
    assert summary.top_approvers == []


def test_workflow_health_summary() -> None:
    summary = WorkflowHealthSummary(
        total_executions=100,
        success_count=85,
        failure_count=15,
        success_rate_pct=Decimal("85.0"),
        failing_rules=[
            {
                "rule_id": "r1",
                "rule_name": "Auto-approve small claims",
                "failure_count": 8,
            },
        ],
    )
    assert summary.total_executions == 100
    assert summary.success_rate_pct == Decimal("85.0")
    assert summary.failing_rules[0]["failure_count"] == 8


def test_workflow_health_summary_perfect() -> None:
    summary = WorkflowHealthSummary(
        total_executions=50,
        success_count=50,
        failure_count=0,
        success_rate_pct=Decimal("100"),
        failing_rules=[],
    )
    assert summary.failure_count == 0
    assert summary.failing_rules == []
