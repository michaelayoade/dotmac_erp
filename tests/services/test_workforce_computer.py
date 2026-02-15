"""Tests for WorkforceComputer — HR and workforce metrics."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from app.services.analytics.computers.workforce import WorkforceComputer

DEFAULT_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
SNAPSHOT_DATE = date(2026, 2, 15)


class TestWorkforceComputer:
    """Tests for WorkforceComputer.compute_for_org()."""

    def _make_computer(self) -> tuple[WorkforceComputer, MagicMock]:
        """Create a computer with mocked DB and upsert."""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = WorkforceComputer(db)
        computer.upsert_metric = MagicMock()  # type: ignore[method-assign]
        return computer, db

    def test_writes_all_six_metrics(self) -> None:
        """Should produce exactly 6 metrics."""
        computer, db = self._make_computer()

        # scalar calls: headcount, turnover, leave_days, total_attendance,
        #               present_count, pending_leaves
        db.scalar.side_effect = [120, 3, Decimal("45"), 2400, 2200, 5]
        # execute call for department distribution
        db.execute.return_value.all.return_value = [
            ("Engineering", 40),
            ("Sales", 30),
            ("Operations", 50),
        ]

        written = computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        assert written == 6
        assert computer.upsert_metric.call_count == 6  # type: ignore[union-attr]

    def test_headcount_written(self) -> None:
        """Active headcount should be written correctly."""
        computer, db = self._make_computer()
        db.scalar.side_effect = [85, 0, Decimal("0"), 0, 0, 0]
        db.execute.return_value.all.return_value = []

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        assert calls[0].kwargs["metric_type"] == "workforce.active_headcount"
        assert calls[0].kwargs["value_numeric"] == 85

    def test_turnover_counts_departed_employees(self) -> None:
        """Turnover should count RESIGNED/TERMINATED/RETIRED in last 30 days."""
        computer, db = self._make_computer()
        db.scalar.side_effect = [100, 7, Decimal("0"), 0, 0, 0]
        db.execute.return_value.all.return_value = []

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        assert calls[1].kwargs["metric_type"] == "workforce.turnover_30d"
        assert calls[1].kwargs["value_numeric"] == 7

    def test_attendance_rate_percentage(self) -> None:
        """Attendance rate should be present/total * 100."""
        computer, db = self._make_computer()
        # headcount=50, turnover=0, leave=0, total_attendance=1000, present=900, pending=0
        db.scalar.side_effect = [50, 0, Decimal("0"), 1000, 900, 0]
        db.execute.return_value.all.return_value = []

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        rate_call = calls[3].kwargs
        assert rate_call["metric_type"] == "workforce.attendance_rate_30d"
        assert rate_call["value_numeric"] == Decimal("90.0")

    def test_attendance_rate_none_when_no_records(self) -> None:
        """Attendance rate should be None when no attendance records exist."""
        computer, db = self._make_computer()
        db.scalar.side_effect = [0, 0, Decimal("0"), 0, 0, 0]
        db.execute.return_value.all.return_value = []

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        rate_call = calls[3].kwargs
        assert rate_call["value_numeric"] is None

    def test_department_distribution_json(self) -> None:
        """Department distribution should be stored as JSON."""
        computer, db = self._make_computer()
        db.scalar.side_effect = [100, 0, Decimal("0"), 0, 0, 0]
        db.execute.return_value.all.return_value = [
            ("Engineering", 40),
            ("Sales", 35),
            ("HR", 25),
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        dept_call = calls[5].kwargs
        assert dept_call["metric_type"] == "workforce.department_distribution"
        json_val = dept_call["value_json"]
        assert json_val["total"] == 100
        assert len(json_val["departments"]) == 3
        assert json_val["departments"][0]["department"] == "Engineering"
        assert json_val["departments"][0]["count"] == 40

    def test_leave_utilization(self) -> None:
        """Leave utilization should sum approved leave days."""
        computer, db = self._make_computer()
        db.scalar.side_effect = [50, 0, Decimal("32.5"), 0, 0, 0]
        db.execute.return_value.all.return_value = []

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        leave_call = calls[2].kwargs
        assert leave_call["metric_type"] == "workforce.leave_utilization_30d"
        assert leave_call["value_numeric"] == Decimal("32.5")


class TestMetricTypes:
    """Verify class attributes."""

    def test_metric_types_list(self) -> None:
        assert len(WorkforceComputer.METRIC_TYPES) == 6
        assert "workforce.active_headcount" in WorkforceComputer.METRIC_TYPES
        assert "workforce.attendance_rate_30d" in WorkforceComputer.METRIC_TYPES
        assert "workforce.department_distribution" in WorkforceComputer.METRIC_TYPES

    def test_source_label(self) -> None:
        assert WorkforceComputer.SOURCE_LABEL == "WorkforceComputer"
