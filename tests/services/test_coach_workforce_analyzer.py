"""Tests for WorkforceAnalyzer severity and summary logic."""

from __future__ import annotations

from decimal import Decimal

from app.services.coach.analyzers.workforce import (
    LeaveUtilizationSummary,
    WorkforceHealthSummary,
    _severity_for_leave,
    _severity_for_workforce,
)

# -- _severity_for_workforce --------------------------------------------------


def test_severity_info_low_turnover_no_gaps():
    assert _severity_for_workforce(Decimal("5"), 0) == "INFO"


def test_severity_attention_moderate_turnover():
    assert _severity_for_workforce(Decimal("12"), 0) == "ATTENTION"


def test_severity_attention_depts_without_head():
    assert _severity_for_workforce(Decimal("5"), 2) == "ATTENTION"


def test_severity_warning_high_turnover():
    assert _severity_for_workforce(Decimal("25"), 0) == "WARNING"


def test_severity_attention_boundary():
    # Exactly 10% is NOT > 10, so INFO if no depts_no_head
    assert _severity_for_workforce(Decimal("10"), 0) == "INFO"
    # 10.1% triggers ATTENTION
    assert _severity_for_workforce(Decimal("10.1"), 0) == "ATTENTION"


# -- _severity_for_leave -------------------------------------------------------


def test_leave_severity_info():
    assert _severity_for_leave(0) == "INFO"
    assert _severity_for_leave(4) == "INFO"


def test_leave_severity_attention():
    assert _severity_for_leave(5) == "ATTENTION"
    assert _severity_for_leave(9) == "ATTENTION"


def test_leave_severity_warning():
    assert _severity_for_leave(10) == "WARNING"
    assert _severity_for_leave(50) == "WARNING"


# -- Dataclasses ---------------------------------------------------------------


def test_workforce_health_summary():
    summary = WorkforceHealthSummary(
        active_headcount=120,
        recent_departures_90d=3,
        annualized_turnover_pct=Decimal("10.1"),
        departments_without_head=1,
        employees_without_manager=5,
    )
    assert summary.active_headcount == 120
    assert summary.annualized_turnover_pct == Decimal("10.1")
    assert summary.departments_without_head == 1


def test_leave_utilization_summary():
    summary = LeaveUtilizationSummary(
        pending_leave_requests=7,
        avg_approval_days=Decimal("2.5"),
        leave_days_used_90d=Decimal("180"),
    )
    assert summary.pending_leave_requests == 7
    assert summary.avg_approval_days == Decimal("2.5")


def test_leave_utilization_summary_none_avg():
    summary = LeaveUtilizationSummary(
        pending_leave_requests=0,
        avg_approval_days=None,
        leave_days_used_90d=Decimal("0"),
    )
    assert summary.avg_approval_days is None
