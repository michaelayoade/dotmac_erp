"""Tests for ComplianceComputer — tax and regulatory compliance metrics."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.analytics.computers.compliance import ComplianceComputer

DEFAULT_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
SNAPSHOT_DATE = date(2026, 2, 15)


class TestComplianceComputer:
    """Tests for ComplianceComputer.compute_for_org()."""

    def _make_computer(self) -> tuple[ComplianceComputer, MagicMock]:
        """Create a computer with mocked DB and upsert."""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = ComplianceComputer(db)
        computer.upsert_metric = MagicMock()  # type: ignore[method-assign]
        return computer, db

    @patch(
        "app.services.analytics.computers.compliance.ComplianceComputer._get_org_currency"
    )
    def test_writes_all_six_metrics(self, mock_currency: MagicMock) -> None:
        """Should produce exactly 6 metrics."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        # scalar calls: overdue_filings, upcoming, open_fp, tax_payable, filed_ytd, overdue_fp
        db.scalar.side_effect = [2, 3, 4, Decimal("500000"), 8, 1]

        written = computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        assert written == 6
        assert computer.upsert_metric.call_count == 6  # type: ignore[union-attr]

    @patch(
        "app.services.analytics.computers.compliance.ComplianceComputer._get_org_currency"
    )
    def test_overdue_filings_written(self, mock_currency: MagicMock) -> None:
        """Overdue tax filings count should be written."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [5, 0, 0, Decimal("0"), 0, 0]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        assert calls[0].kwargs["metric_type"] == "compliance.overdue_tax_filings"
        assert calls[0].kwargs["value_numeric"] == 5

    @patch(
        "app.services.analytics.computers.compliance.ComplianceComputer._get_org_currency"
    )
    def test_upcoming_deadlines(self, mock_currency: MagicMock) -> None:
        """Upcoming tax deadlines should count open periods due within 30 days."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [0, 4, 0, Decimal("0"), 0, 0]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        assert calls[1].kwargs["metric_type"] == "compliance.upcoming_tax_deadlines"
        assert calls[1].kwargs["value_numeric"] == 4

    @patch(
        "app.services.analytics.computers.compliance.ComplianceComputer._get_org_currency"
    )
    def test_tax_payable_with_currency(self, mock_currency: MagicMock) -> None:
        """Total tax payable should include currency code."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [0, 0, 0, Decimal("1250000"), 0, 0]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        payable_call = calls[3].kwargs
        assert payable_call["metric_type"] == "compliance.total_tax_payable"
        assert payable_call["value_numeric"] == Decimal("1250000")
        assert payable_call["currency_code"] == "NGN"

    @patch(
        "app.services.analytics.computers.compliance.ComplianceComputer._get_org_currency"
    )
    def test_filed_returns_ytd(self, mock_currency: MagicMock) -> None:
        """Filed returns YTD should count FILED and AMENDED returns."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [0, 0, 0, Decimal("0"), 12, 0]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        filed_call = calls[4].kwargs
        assert filed_call["metric_type"] == "compliance.filed_returns_ytd"
        assert filed_call["value_numeric"] == 12

    @patch(
        "app.services.analytics.computers.compliance.ComplianceComputer._get_org_currency"
    )
    def test_overdue_fiscal_periods(self, mock_currency: MagicMock) -> None:
        """Overdue fiscal periods should count open/reopened past end_date."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [0, 0, 0, Decimal("0"), 0, 3]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        fp_call = calls[5].kwargs
        assert fp_call["metric_type"] == "compliance.overdue_fiscal_periods"
        assert fp_call["value_numeric"] == 3

    @patch(
        "app.services.analytics.computers.compliance.ComplianceComputer._get_org_currency"
    )
    def test_all_zeros(self, mock_currency: MagicMock) -> None:
        """Should handle fully compliant org (all zeros)."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [0, 0, 0, Decimal("0"), 0, 0]

        written = computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        assert written == 6
        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        for call in calls:
            val = call.kwargs.get("value_numeric")
            if val is not None:
                assert val == 0 or val == Decimal("0")


class TestMetricTypes:
    """Verify class attributes."""

    def test_metric_types_list(self) -> None:
        assert len(ComplianceComputer.METRIC_TYPES) == 6
        assert "compliance.overdue_tax_filings" in ComplianceComputer.METRIC_TYPES
        assert "compliance.total_tax_payable" in ComplianceComputer.METRIC_TYPES
        assert "compliance.overdue_fiscal_periods" in ComplianceComputer.METRIC_TYPES

    def test_source_label(self) -> None:
        assert ComplianceComputer.SOURCE_LABEL == "ComplianceComputer"
