"""Tests for SupplyChainComputer — inventory and supply chain metrics."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.analytics.computers.supply_chain import SupplyChainComputer

DEFAULT_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
SNAPSHOT_DATE = date(2026, 2, 15)


class TestSupplyChainComputer:
    """Tests for SupplyChainComputer.compute_for_org()."""

    def _make_computer(self) -> tuple[SupplyChainComputer, MagicMock]:
        """Create a computer with mocked DB and upsert."""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = SupplyChainComputer(db)
        computer.upsert_metric = MagicMock()  # type: ignore[method-assign]
        return computer, db

    @patch(
        "app.services.analytics.computers.supply_chain.SupplyChainComputer._get_org_currency"
    )
    def test_writes_all_six_metrics(self, mock_currency: MagicMock) -> None:
        """Should produce exactly 6 metrics."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        # scalar calls: total_value, low_stock, stockout, volume, receipt_value, issue_value
        db.scalar.side_effect = [
            Decimal("5000000"),  # total_inventory_value
            15,  # low_stock_item_count
            3,  # stockout_count
            250,  # transaction_volume_30d
            Decimal("2000000"),  # receipt_value_30d
            Decimal("1500000"),  # issue_value_30d
        ]

        written = computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        assert written == 6
        assert computer.upsert_metric.call_count == 6  # type: ignore[union-attr]

    @patch(
        "app.services.analytics.computers.supply_chain.SupplyChainComputer._get_org_currency"
    )
    def test_inventory_value_written(self, mock_currency: MagicMock) -> None:
        """Total inventory value should use currency code."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [
            Decimal("8500000"),
            0,
            0,
            0,
            Decimal("0"),
            Decimal("0"),
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        value_call = calls[0].kwargs
        assert value_call["metric_type"] == "supply_chain.total_inventory_value"
        assert value_call["value_numeric"] == Decimal("8500000")
        assert value_call["currency_code"] == "NGN"

    @patch(
        "app.services.analytics.computers.supply_chain.SupplyChainComputer._get_org_currency"
    )
    def test_low_stock_and_stockout(self, mock_currency: MagicMock) -> None:
        """Low stock and stockout counts should be written."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [
            Decimal("0"),
            25,  # low stock
            8,  # stockout
            0,
            Decimal("0"),
            Decimal("0"),
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        assert calls[1].kwargs["metric_type"] == "supply_chain.low_stock_item_count"
        assert calls[1].kwargs["value_numeric"] == 25
        assert calls[2].kwargs["metric_type"] == "supply_chain.stockout_count"
        assert calls[2].kwargs["value_numeric"] == 8

    @patch(
        "app.services.analytics.computers.supply_chain.SupplyChainComputer._get_org_currency"
    )
    def test_receipt_and_issue_values(self, mock_currency: MagicMock) -> None:
        """Receipt and issue values should be written with currency."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [
            Decimal("0"),
            0,
            0,
            0,
            Decimal("3000000"),  # receipt
            Decimal("1200000"),  # issue
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        assert calls[4].kwargs["metric_type"] == "supply_chain.receipt_value_30d"
        assert calls[4].kwargs["value_numeric"] == Decimal("3000000")
        assert calls[5].kwargs["metric_type"] == "supply_chain.issue_value_30d"
        assert calls[5].kwargs["value_numeric"] == Decimal("1200000")

    @patch(
        "app.services.analytics.computers.supply_chain.SupplyChainComputer._get_org_currency"
    )
    def test_zero_inventory(self, mock_currency: MagicMock) -> None:
        """Should handle empty inventory gracefully."""
        mock_currency.return_value = "NGN"

        computer, db = self._make_computer()
        db.scalar.side_effect = [Decimal("0"), 0, 0, 0, Decimal("0"), Decimal("0")]

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
        assert len(SupplyChainComputer.METRIC_TYPES) == 6
        assert "supply_chain.total_inventory_value" in SupplyChainComputer.METRIC_TYPES
        assert "supply_chain.stockout_count" in SupplyChainComputer.METRIC_TYPES

    def test_source_label(self) -> None:
        assert SupplyChainComputer.SOURCE_LABEL == "SupplyChainComputer"
