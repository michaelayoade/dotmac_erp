"""Tests for ComplianceAnalyzer severity and summary logic."""

from __future__ import annotations

from datetime import date

from app.services.coach.analyzers.compliance import (
    FiscalPeriodHealthSummary,
    _severity_for_compliance,
)


def test_severity_info_no_overdue():
    assert _severity_for_compliance(0) == "INFO"


def test_severity_attention_one_overdue():
    assert _severity_for_compliance(1) == "ATTENTION"


def test_severity_warning_many_overdue():
    assert _severity_for_compliance(3) == "WARNING"
    assert _severity_for_compliance(5) == "WARNING"


def test_health_summary_dataclass():
    summary = FiscalPeriodHealthSummary(
        open_period_count=3,
        overdue_close_count=1,
        oldest_open_period_name="Q3 2025",
        oldest_open_end_date=date(2025, 9, 30),
    )
    assert summary.open_period_count == 3
    assert summary.overdue_close_count == 1
    assert summary.oldest_open_period_name == "Q3 2025"


def test_health_summary_none_oldest():
    summary = FiscalPeriodHealthSummary(
        open_period_count=0,
        overdue_close_count=0,
        oldest_open_period_name=None,
        oldest_open_end_date=None,
    )
    assert summary.oldest_open_period_name is None
