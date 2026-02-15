"""Tests for RevenueComputer — revenue and sales pipeline metrics."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.analytics.computers.revenue import RevenueComputer

DEFAULT_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
SNAPSHOT_DATE = date(2026, 2, 15)


class TestRevenueComputer:
    """Tests for RevenueComputer.compute_for_org()."""

    def _make_computer(self) -> tuple[RevenueComputer, MagicMock]:
        """Create a computer with mocked DB and upsert."""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = RevenueComputer(db)
        computer.upsert_metric = MagicMock()  # type: ignore[method-assign]
        return computer, db

    @patch("app.services.analytics.computers.revenue.RevenueComputer._get_org_currency")
    def test_writes_all_six_metrics(self, mock_currency: MagicMock) -> None:
        """Should produce exactly 6 metrics."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        # scalar calls: monthly, ytd, pipeline, total_quotes, converted_quotes, avg, open_so
        db.scalar.side_effect = [
            Decimal("500000"),  # monthly_total
            Decimal("2000000"),  # ytd_total
            Decimal("1000000"),  # pipeline_value
            10,  # total_quotes
            3,  # converted_quotes
            Decimal("50000"),  # avg_invoice
            Decimal("750000"),  # open_so_value
        ]

        written = computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        assert written == 6
        assert computer.upsert_metric.call_count == 6  # type: ignore[union-attr]

    @patch("app.services.analytics.computers.revenue.RevenueComputer._get_org_currency")
    def test_monthly_and_ytd_revenue(self, mock_currency: MagicMock) -> None:
        """Monthly and YTD should reflect invoice sums."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [
            Decimal("300000"),  # monthly
            Decimal("1500000"),  # ytd
            Decimal("0"),  # pipeline
            0,  # total quotes
            0,  # converted
            None,  # avg (no invoices)
            Decimal("0"),  # open SO
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        assert calls[0].kwargs["metric_type"] == "revenue.monthly_total"
        assert calls[0].kwargs["value_numeric"] == Decimal("300000")
        assert calls[1].kwargs["metric_type"] == "revenue.ytd_total"
        assert calls[1].kwargs["value_numeric"] == Decimal("1500000")

    @patch("app.services.analytics.computers.revenue.RevenueComputer._get_org_currency")
    def test_conversion_rate_calculation(self, mock_currency: MagicMock) -> None:
        """Conversion rate should be converted/total * 100."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [
            Decimal("0"),  # monthly
            Decimal("0"),  # ytd
            Decimal("0"),  # pipeline
            20,  # total quotes
            5,  # converted
            None,  # avg
            Decimal("0"),  # open SO
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        rate_call = calls[3].kwargs
        assert rate_call["metric_type"] == "revenue.conversion_rate"
        assert rate_call["value_numeric"] == Decimal("25.0")

    @patch("app.services.analytics.computers.revenue.RevenueComputer._get_org_currency")
    def test_conversion_rate_none_when_no_quotes(
        self, mock_currency: MagicMock
    ) -> None:
        """Conversion rate should be None when there are no quotes."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [
            Decimal("0"),  # monthly
            Decimal("0"),  # ytd
            Decimal("0"),  # pipeline
            0,  # total quotes (zero!)
            0,  # converted
            None,  # avg
            Decimal("0"),  # open SO
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        rate_call = calls[3].kwargs
        assert rate_call["value_numeric"] is None

    @patch("app.services.analytics.computers.revenue.RevenueComputer._get_org_currency")
    def test_average_invoice_value(self, mock_currency: MagicMock) -> None:
        """Average invoice value should come from func.avg()."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [
            Decimal("0"),  # monthly
            Decimal("0"),  # ytd
            Decimal("0"),  # pipeline
            5,  # total quotes
            1,  # converted
            Decimal("75000.50"),  # avg invoice
            Decimal("0"),  # open SO
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        avg_call = calls[4].kwargs
        assert avg_call["metric_type"] == "revenue.average_invoice_value"
        assert avg_call["value_numeric"] == Decimal("75000.5")

    @patch("app.services.analytics.computers.revenue.RevenueComputer._get_org_currency")
    def test_open_so_value(self, mock_currency: MagicMock) -> None:
        """Open SO value should be total_amount - invoiced_amount."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            0,
            0,
            None,
            Decimal("2500000"),  # open SO value
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        so_call = calls[5].kwargs
        assert so_call["metric_type"] == "revenue.open_so_value"
        assert so_call["value_numeric"] == Decimal("2500000")


class TestMetricTypes:
    """Verify class attributes."""

    def test_metric_types_list(self) -> None:
        assert len(RevenueComputer.METRIC_TYPES) == 6
        assert "revenue.monthly_total" in RevenueComputer.METRIC_TYPES
        assert "revenue.conversion_rate" in RevenueComputer.METRIC_TYPES
        assert "revenue.open_so_value" in RevenueComputer.METRIC_TYPES

    def test_source_label(self) -> None:
        assert RevenueComputer.SOURCE_LABEL == "RevenueComputer"
