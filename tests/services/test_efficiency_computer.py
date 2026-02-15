"""Tests for EfficiencyComputer — operational efficiency metrics."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.analytics.computers.efficiency import EfficiencyComputer

DEFAULT_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
SNAPSHOT_DATE = date(2026, 2, 15)


class TestEfficiencyComputer:
    """Tests for EfficiencyComputer.compute_for_org()."""

    def _make_computer(self) -> tuple[EfficiencyComputer, MagicMock]:
        """Create a computer with mocked DB and upsert."""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = EfficiencyComputer(db)
        computer.upsert_metric = MagicMock()  # type: ignore[method-assign]
        return computer, db

    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._get_org_currency"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._count_pending_expenses"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_recon_freshness"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_dpo"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_dso"
    )
    def test_writes_all_six_metrics(
        self,
        mock_dso: MagicMock,
        mock_dpo: MagicMock,
        mock_recon: MagicMock,
        mock_pending: MagicMock,
        mock_currency: MagicMock,
    ) -> None:
        """Should produce exactly 6 metrics."""
        mock_currency.return_value = "NGN"
        mock_dso.return_value = Decimal("45")
        mock_dpo.return_value = Decimal("30")
        mock_recon.return_value = (Decimal("7"), 1)
        mock_pending.return_value = 3

        computer, _db = self._make_computer()
        written = computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        assert written == 6
        assert computer.upsert_metric.call_count == 6  # type: ignore[union-attr]

    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._get_org_currency"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._count_pending_expenses"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_recon_freshness"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_dpo"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_dso"
    )
    def test_ccc_is_dso_minus_dpo(
        self,
        mock_dso: MagicMock,
        mock_dpo: MagicMock,
        mock_recon: MagicMock,
        mock_pending: MagicMock,
        mock_currency: MagicMock,
    ) -> None:
        """CCC should be DSO - DPO."""
        mock_currency.return_value = "NGN"
        mock_dso.return_value = Decimal("60")
        mock_dpo.return_value = Decimal("25")
        mock_recon.return_value = (None, 0)
        mock_pending.return_value = 0

        computer, _db = self._make_computer()
        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        dso_call = calls[0].kwargs
        dpo_call = calls[1].kwargs
        ccc_call = calls[2].kwargs

        assert dso_call["metric_type"] == "efficiency.dso"
        assert dso_call["value_numeric"] == Decimal("60")

        assert dpo_call["metric_type"] == "efficiency.dpo"
        assert dpo_call["value_numeric"] == Decimal("25")

        assert ccc_call["metric_type"] == "efficiency.ccc"
        assert ccc_call["value_numeric"] == Decimal("35")

    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._get_org_currency"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._count_pending_expenses"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_recon_freshness"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_dpo"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_dso"
    )
    def test_ccc_none_when_dso_or_dpo_missing(
        self,
        mock_dso: MagicMock,
        mock_dpo: MagicMock,
        mock_recon: MagicMock,
        mock_pending: MagicMock,
        mock_currency: MagicMock,
    ) -> None:
        """CCC should be None if DSO or DPO is None (no revenue/COGS data)."""
        mock_currency.return_value = "NGN"
        mock_dso.return_value = None  # No revenue
        mock_dpo.return_value = Decimal("30")
        mock_recon.return_value = (None, 0)
        mock_pending.return_value = 0

        computer, _db = self._make_computer()
        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        ccc_call = computer.upsert_metric.call_args_list[2].kwargs  # type: ignore[union-attr]
        assert ccc_call["metric_type"] == "efficiency.ccc"
        assert ccc_call["value_numeric"] is None

    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._get_org_currency"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._count_pending_expenses"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_recon_freshness"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_dpo"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_dso"
    )
    def test_recon_freshness_written(
        self,
        mock_dso: MagicMock,
        mock_dpo: MagicMock,
        mock_recon: MagicMock,
        mock_pending: MagicMock,
        mock_currency: MagicMock,
    ) -> None:
        """Reconciliation freshness and stale count should be written."""
        mock_currency.return_value = "NGN"
        mock_dso.return_value = None
        mock_dpo.return_value = None
        mock_recon.return_value = (Decimal("21.5"), 3)
        mock_pending.return_value = 0

        computer, _db = self._make_computer()
        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        calls = computer.upsert_metric.call_args_list  # type: ignore[union-attr]
        freshness_call = calls[3].kwargs
        stale_call = calls[4].kwargs

        assert (
            freshness_call["metric_type"] == "efficiency.reconciliation_freshness_days"
        )
        assert freshness_call["value_numeric"] == Decimal("21.5")

        assert stale_call["metric_type"] == "efficiency.unreconciled_account_count"
        assert stale_call["value_numeric"] == 3

    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._get_org_currency"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._count_pending_expenses"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_recon_freshness"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_dpo"
    )
    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._compute_dso"
    )
    def test_pending_expenses_written(
        self,
        mock_dso: MagicMock,
        mock_dpo: MagicMock,
        mock_recon: MagicMock,
        mock_pending: MagicMock,
        mock_currency: MagicMock,
    ) -> None:
        """Pending expense approval count should be written."""
        mock_currency.return_value = "NGN"
        mock_dso.return_value = None
        mock_dpo.return_value = None
        mock_recon.return_value = (None, 0)
        mock_pending.return_value = 12

        computer, _db = self._make_computer()
        computer.compute_for_org(DEFAULT_ORG, SNAPSHOT_DATE)

        pending_call = computer.upsert_metric.call_args_list[5].kwargs  # type: ignore[union-attr]
        assert pending_call["metric_type"] == "efficiency.pending_expense_approvals"
        assert pending_call["value_numeric"] == 12


class TestDSOComputation:
    """Tests for the DSO computation helper."""

    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._get_annual_revenue"
    )
    def test_dso_formula(self, mock_revenue: MagicMock) -> None:
        """DSO = AR_balance / (annual_revenue / 365)."""
        mock_revenue.return_value = Decimal("3650000")  # 10,000/day

        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = EfficiencyComputer(db)

        # Mock AR balance query
        db.scalar.return_value = Decimal("450000")  # AR balance

        result = computer._compute_dso(DEFAULT_ORG, SNAPSHOT_DATE)

        assert result is not None
        # DSO = 450,000 / (3,650,000 / 365) = 450,000 / 10,000 = 45
        assert result == Decimal("45")

    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._get_annual_revenue"
    )
    def test_dso_none_when_no_revenue(self, mock_revenue: MagicMock) -> None:
        """DSO should be None when there's no revenue."""
        mock_revenue.return_value = Decimal("0")

        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = EfficiencyComputer(db)
        db.scalar.return_value = Decimal("100000")

        result = computer._compute_dso(DEFAULT_ORG, SNAPSHOT_DATE)
        assert result is None


class TestDPOComputation:
    """Tests for the DPO computation helper."""

    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._get_annual_cogs"
    )
    def test_dpo_formula(self, mock_cogs: MagicMock) -> None:
        """DPO = AP_balance / (annual_COGS / 365)."""
        mock_cogs.return_value = Decimal("7300000")  # 20,000/day

        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = EfficiencyComputer(db)
        db.scalar.return_value = Decimal("600000")  # AP balance

        result = computer._compute_dpo(DEFAULT_ORG, SNAPSHOT_DATE)

        assert result is not None
        # DPO = 600,000 / (7,300,000 / 365) = 600,000 / 20,000 = 30
        assert result == Decimal("30")

    @patch(
        "app.services.analytics.computers.efficiency.EfficiencyComputer._get_annual_cogs"
    )
    def test_dpo_none_when_no_cogs(self, mock_cogs: MagicMock) -> None:
        """DPO should be None when there's no COGS."""
        mock_cogs.return_value = Decimal("0")

        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = EfficiencyComputer(db)
        db.scalar.return_value = Decimal("100000")

        result = computer._compute_dpo(DEFAULT_ORG, SNAPSHOT_DATE)
        assert result is None


class TestReconFreshness:
    """Tests for the reconciliation freshness helper."""

    def test_no_bank_accounts(self) -> None:
        """Should return (None, 0) when no active bank accounts exist."""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = EfficiencyComputer(db)
        db.execute.return_value.all.return_value = []

        avg_days, stale_count = computer._compute_recon_freshness(
            DEFAULT_ORG, SNAPSHOT_DATE
        )
        assert avg_days is None
        assert stale_count == 0

    def test_never_reconciled_accounts(self) -> None:
        """Accounts never reconciled should count as stale with 365 day penalty."""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = EfficiencyComputer(db)
        db.execute.return_value.all.return_value = [
            (uuid.uuid4(), None),  # Never reconciled
        ]

        avg_days, stale_count = computer._compute_recon_freshness(
            DEFAULT_ORG, SNAPSHOT_DATE
        )
        assert avg_days == Decimal("365")
        assert stale_count == 1

    def test_mixed_accounts(self) -> None:
        """Should average days across all accounts and count stale ones."""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        computer = EfficiencyComputer(db)
        db.execute.return_value.all.return_value = [
            (uuid.uuid4(), date(2026, 2, 10)),  # 5 days ago (fresh)
            (uuid.uuid4(), date(2026, 1, 1)),  # 45 days ago (stale)
        ]

        avg_days, stale_count = computer._compute_recon_freshness(
            DEFAULT_ORG, SNAPSHOT_DATE
        )

        # avg = (5 + 45) / 2 = 25
        assert avg_days == Decimal("25")
        assert stale_count == 1  # Only the 45-day one is stale


class TestMetricTypes:
    """Verify METRIC_TYPES and SOURCE_LABEL class attributes."""

    def test_metric_types_list(self) -> None:
        assert len(EfficiencyComputer.METRIC_TYPES) == 6
        assert "efficiency.dso" in EfficiencyComputer.METRIC_TYPES
        assert "efficiency.ccc" in EfficiencyComputer.METRIC_TYPES
        assert "efficiency.pending_expense_approvals" in EfficiencyComputer.METRIC_TYPES

    def test_source_label(self) -> None:
        assert EfficiencyComputer.SOURCE_LABEL == "EfficiencyComputer"
