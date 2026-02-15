"""Tests for MetricStore fast-path integration in coach analyzers.

Verifies that each analyzer's ``_quick_check_from_store`` method correctly
skips expensive live queries when MetricStore reports "nothing to report".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

DEFAULT_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Shared fixture: a mock MetricValue that is "fresh" (computed < 24h ago)
# ---------------------------------------------------------------------------
def _fresh_metric_value(value_numeric: Decimal | int) -> MagicMock:
    mv = MagicMock()
    mv.value_numeric = Decimal(str(value_numeric))
    mv.computed_at = datetime.now(UTC) - timedelta(hours=1)
    return mv


def _stale_metric_value(value_numeric: Decimal | int) -> MagicMock:
    mv = MagicMock()
    mv.value_numeric = Decimal(str(value_numeric))
    mv.computed_at = datetime.now(UTC) - timedelta(hours=48)
    return mv


# ============================================================
# APDueAnalyzer fast-path
# ============================================================
class TestAPDueFastPath:
    """APDueAnalyzer should skip live queries when MetricStore shows zero AP due."""

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_true_when_zero_and_fresh(
        self, MockStore: MagicMock
    ) -> None:
        from app.services.coach.analyzers.ap_due import APDueAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.ap_due_7d_total": _fresh_metric_value(0)
        }

        analyzer = APDueAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is True

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_false_when_nonzero(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers.ap_due import APDueAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.ap_due_7d_total": _fresh_metric_value(50000)
        }

        analyzer = APDueAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is False

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_false_when_stale(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers.ap_due import APDueAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.ap_due_7d_total": _stale_metric_value(0)
        }

        analyzer = APDueAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is False

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_false_when_no_metric(
        self, MockStore: MagicMock
    ) -> None:
        from app.services.coach.analyzers.ap_due import APDueAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {}

        analyzer = APDueAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is False


# ============================================================
# AROverdueAnalyzer fast-path
# ============================================================
class TestAROverdueFastPath:
    """AROverdueAnalyzer should skip live queries when MetricStore shows zero overdue AR."""

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_true_when_zero_and_fresh(
        self, MockStore: MagicMock
    ) -> None:
        from app.services.coach.analyzers.ar_overdue import AROverdueAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.ar_overdue_total": _fresh_metric_value(0)
        }

        analyzer = AROverdueAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is True

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_false_when_nonzero(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers.ar_overdue import AROverdueAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.ar_overdue_total": _fresh_metric_value(1500000)
        }

        analyzer = AROverdueAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is False

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_false_when_missing(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers.ar_overdue import AROverdueAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {}

        analyzer = AROverdueAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is False


# ============================================================
# BankingHealthAnalyzer fast-path
# ============================================================
class TestBankingHealthFastPath:
    """BankingHealthAnalyzer should skip when MetricStore shows zero unreconciled accounts."""

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_true_when_zero_and_fresh(
        self, MockStore: MagicMock
    ) -> None:
        from app.services.coach.analyzers.banking import BankingHealthAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "efficiency.unreconciled_account_count": _fresh_metric_value(0)
        }

        analyzer = BankingHealthAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is True

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_false_when_nonzero(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers.banking import BankingHealthAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "efficiency.unreconciled_account_count": _fresh_metric_value(3)
        }

        analyzer = BankingHealthAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is False


# ============================================================
# ExpenseApprovalAnalyzer fast-path
# ============================================================
class TestExpenseApprovalFastPath:
    """ExpenseApprovalAnalyzer should skip when MetricStore shows zero pending approvals."""

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_true_when_zero_and_fresh(
        self, MockStore: MagicMock
    ) -> None:
        from app.services.coach.analyzers.expense import ExpenseApprovalAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "efficiency.pending_expense_approvals": _fresh_metric_value(0)
        }

        analyzer = ExpenseApprovalAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is True

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_false_when_nonzero(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers.expense import ExpenseApprovalAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "efficiency.pending_expense_approvals": _fresh_metric_value(7)
        }

        analyzer = ExpenseApprovalAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is False


# ============================================================
# DataQualityAnalyzer fast-path
# ============================================================
class TestDataQualityFastPath:
    """DataQualityAnalyzer should skip when MetricStore shows zero active employees."""

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_true_when_zero_and_fresh(
        self, MockStore: MagicMock
    ) -> None:
        from app.services.coach.analyzers.data_quality import DataQualityAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "workforce.active_headcount": _fresh_metric_value(0)
        }

        analyzer = DataQualityAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is True

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_quick_check_returns_false_when_nonzero(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers.data_quality import DataQualityAnalyzer

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "workforce.active_headcount": _fresh_metric_value(50)
        }

        analyzer = DataQualityAnalyzer(db)
        assert analyzer._quick_check_from_store(DEFAULT_ORG) is False


# ============================================================
# metric_is_fresh utility
# ============================================================
class TestMetricIsFresh:
    """Tests for the metric_is_fresh() shared utility."""

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_returns_true_and_value_when_fresh(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers import metric_is_fresh

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.net_position": _fresh_metric_value(100000)
        }

        is_fresh, value = metric_is_fresh(db, DEFAULT_ORG, "cash_flow.net_position")
        assert is_fresh is True
        assert value == Decimal("100000")

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_returns_false_when_stale(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers import metric_is_fresh

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.net_position": _stale_metric_value(100000)
        }

        is_fresh, value = metric_is_fresh(db, DEFAULT_ORG, "cash_flow.net_position")
        assert is_fresh is False
        assert value is None

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_returns_false_when_missing(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers import metric_is_fresh

        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {}

        is_fresh, value = metric_is_fresh(db, DEFAULT_ORG, "cash_flow.net_position")
        assert is_fresh is False
        assert value is None

    @patch("app.services.analytics.metric_store.MetricStore")
    def test_custom_max_age(self, MockStore: MagicMock) -> None:
        from app.services.coach.analyzers import metric_is_fresh

        db = MagicMock()
        # 4 hours old — fresh if max_age=6, stale if max_age=2
        mv = MagicMock()
        mv.value_numeric = Decimal("500")
        mv.computed_at = datetime.now(UTC) - timedelta(hours=4)

        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {"test.metric": mv}

        fresh_6, _ = metric_is_fresh(db, DEFAULT_ORG, "test.metric", max_age_hours=6)
        assert fresh_6 is True

        fresh_2, _ = metric_is_fresh(db, DEFAULT_ORG, "test.metric", max_age_hours=2)
        assert fresh_2 is False
