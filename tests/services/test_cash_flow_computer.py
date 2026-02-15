"""Tests for CashFlowComputer — cash position and flow metrics."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.analytics.computers.cash_flow import CashFlowComputer

DEFAULT_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
SNAPSHOT_DATE = date(2026, 2, 15)
CASH_ACCT_1 = uuid.uuid4()
CASH_ACCT_2 = uuid.uuid4()


class TestCashFlowComputer:
    """Tests for CashFlowComputer.compute_for_org()."""

    def _make_computer(self) -> tuple[CashFlowComputer, MagicMock]:
        """Create a computer with mocked DB and upsert."""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = CashFlowComputer(db)
        computer.upsert_metric = MagicMock()  # type: ignore[method-assign]
        return computer, db

    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_cash_account_ids"
    )
    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_org_currency"
    )
    def test_writes_all_seven_metrics(
        self, mock_currency: MagicMock, mock_cash_ids: MagicMock
    ) -> None:
        """Should produce exactly 7 metrics."""
        mock_currency.return_value = "NGN"
        mock_cash_ids.return_value = []

        computer, db = self._make_computer()

        # Mock scalar queries to return 0
        db.scalar.return_value = 0
        db.execute.return_value.one_or_none.return_value = (0, 0)

        written = computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        assert written == 7
        assert computer.upsert_metric.call_count == 7  # type: ignore[union-attr]

    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_cash_account_ids"
    )
    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_org_currency"
    )
    def test_net_position_sums_cash_balances(
        self, mock_currency: MagicMock, mock_cash_ids: MagicMock
    ) -> None:
        """Net position should aggregate AccountBalance.net_balance on cash accounts."""
        mock_currency.return_value = "NGN"
        mock_cash_ids.return_value = [CASH_ACCT_1, CASH_ACCT_2]

        computer, db = self._make_computer()

        # First scalar call is for net_position, then AR overdue, then AP due
        db.scalar.side_effect = [
            Decimal("5000000"),  # net_position
            Decimal("120000"),  # AR overdue
            Decimal("80000"),  # AP due 7d
        ]
        db.execute.return_value.one_or_none.side_effect = [
            (Decimal("2000000"), Decimal("1500000")),  # 30d inflow/outflow
            (Decimal("800000"), Decimal("600000")),  # monthly inflow/outflow
        ]

        written = computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)
        assert written == 7

        # Verify net_position metric
        first_call_kwargs = computer.upsert_metric.call_args_list[0].kwargs  # type: ignore[union-attr]
        assert first_call_kwargs["metric_type"] == "cash_flow.net_position"
        assert first_call_kwargs["value_numeric"] == Decimal("5000000")
        assert first_call_kwargs["currency_code"] == "NGN"

    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_cash_account_ids"
    )
    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_org_currency"
    )
    def test_flow_metrics_from_ledger_lines(
        self, mock_currency: MagicMock, mock_cash_ids: MagicMock
    ) -> None:
        """Inflow/outflow should come from PostedLedgerLine debit/credit sums."""
        mock_currency.return_value = "NGN"
        mock_cash_ids.return_value = [CASH_ACCT_1]

        computer, db = self._make_computer()

        db.scalar.side_effect = [
            Decimal("1000000"),  # net_position
            Decimal("0"),  # AR overdue
            Decimal("0"),  # AP due
        ]
        db.execute.return_value.one_or_none.side_effect = [
            (Decimal("500000"), Decimal("300000")),  # 30d flows
            (Decimal("200000"), Decimal("100000")),  # monthly
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        inflow_call = calls[1].kwargs
        outflow_call = calls[2].kwargs
        net_flow_call = calls[3].kwargs

        assert inflow_call["metric_type"] == "cash_flow.inflow_30d"
        assert inflow_call["value_numeric"] == Decimal("500000")

        assert outflow_call["metric_type"] == "cash_flow.outflow_30d"
        assert outflow_call["value_numeric"] == Decimal("300000")

        assert net_flow_call["metric_type"] == "cash_flow.net_flow_30d"
        assert net_flow_call["value_numeric"] == Decimal("200000")

    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_cash_account_ids"
    )
    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_org_currency"
    )
    def test_monthly_summary_json(
        self, mock_currency: MagicMock, mock_cash_ids: MagicMock
    ) -> None:
        """Monthly summary should be stored as JSON with inflow/outflow/net/month."""
        mock_currency.return_value = "NGN"
        mock_cash_ids.return_value = [CASH_ACCT_1]

        computer, db = self._make_computer()
        db.scalar.side_effect = [Decimal("0"), Decimal("0"), Decimal("0")]
        db.execute.return_value.one_or_none.side_effect = [
            (Decimal("0"), Decimal("0")),
            (Decimal("400000"), Decimal("250000")),  # monthly
        ]

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        monthly_call = computer.upsert_metric.call_args_list[4].kwargs  # type: ignore[union-attr]
        assert monthly_call["metric_type"] == "cash_flow.monthly_summary"
        json_val = monthly_call["value_json"]
        assert json_val["inflow"] == "400000"
        assert json_val["outflow"] == "250000"
        assert json_val["net"] == "150000"
        assert json_val["month"] == "2026-02"

    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_cash_account_ids"
    )
    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_org_currency"
    )
    def test_ar_overdue_total(
        self, mock_currency: MagicMock, mock_cash_ids: MagicMock
    ) -> None:
        """AR overdue total should sum balance_due of overdue invoices."""
        mock_currency.return_value = "NGN"
        mock_cash_ids.return_value = []

        computer, db = self._make_computer()
        db.scalar.side_effect = [
            Decimal("1500000"),  # AR overdue
            Decimal("0"),  # AP due
        ]
        db.execute.return_value.one_or_none.return_value = (0, 0)

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        ar_call = computer.upsert_metric.call_args_list[5].kwargs  # type: ignore[union-attr]
        assert ar_call["metric_type"] == "cash_flow.ar_overdue_total"
        assert ar_call["value_numeric"] == Decimal("1500000")

    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_cash_account_ids"
    )
    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_org_currency"
    )
    def test_ap_due_7d_total(
        self, mock_currency: MagicMock, mock_cash_ids: MagicMock
    ) -> None:
        """AP due 7d total should sum balance_due of AP invoices due within 7 days."""
        mock_currency.return_value = "NGN"
        mock_cash_ids.return_value = []

        computer, db = self._make_computer()
        db.scalar.side_effect = [
            Decimal("0"),  # AR overdue
            Decimal("800000"),  # AP due
        ]
        db.execute.return_value.one_or_none.return_value = (0, 0)

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        ap_call = computer.upsert_metric.call_args_list[6].kwargs  # type: ignore[union-attr]
        assert ap_call["metric_type"] == "cash_flow.ap_due_7d_total"
        assert ap_call["value_numeric"] == Decimal("800000")

    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_cash_account_ids"
    )
    @patch(
        "app.services.analytics.computers.cash_flow.CashFlowComputer._get_org_currency"
    )
    def test_no_cash_accounts_zeros(
        self, mock_currency: MagicMock, mock_cash_ids: MagicMock
    ) -> None:
        """If no cash accounts exist, position and flow metrics should be zero."""
        mock_currency.return_value = "NGN"
        mock_cash_ids.return_value = []

        computer, db = self._make_computer()
        db.scalar.side_effect = [Decimal("0"), Decimal("0")]
        db.execute.return_value.one_or_none.return_value = (0, 0)

        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        # net_position, inflow, outflow, net_flow should all be 0
        for i in range(4):
            val = calls[i].kwargs.get("value_numeric")
            if val is not None:
                assert val == Decimal("0"), (
                    f"Metric {calls[i].kwargs['metric_type']} should be 0"
                )


class TestMetricTypes:
    """Verify METRIC_TYPES and SOURCE_LABEL class attributes."""

    def test_metric_types_list(self) -> None:
        assert len(CashFlowComputer.METRIC_TYPES) == 7
        assert "cash_flow.net_position" in CashFlowComputer.METRIC_TYPES
        assert "cash_flow.ar_overdue_total" in CashFlowComputer.METRIC_TYPES

    def test_source_label(self) -> None:
        assert CashFlowComputer.SOURCE_LABEL == "CashFlowComputer"
