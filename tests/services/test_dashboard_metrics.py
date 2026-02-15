"""Tests for DashboardMetricsService — MetricStore-backed dashboard read layer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.analytics.dashboard_metrics import (
    ALL_DASHBOARD_METRICS,
    DashboardMetricsService,
)

DEFAULT_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _make_metric_value(
    metric_type: str,
    value_numeric: Decimal | int | None = None,
    value_json: dict | None = None,
    computed_at: datetime | None = None,
    currency_code: str | None = None,
) -> MagicMock:
    mv = MagicMock()
    mv.metric_type = metric_type
    mv.value_numeric = (
        Decimal(str(value_numeric)) if value_numeric is not None else None
    )
    mv.value_json = value_json
    mv.computed_at = computed_at or (datetime.now(UTC) - timedelta(hours=1))
    mv.currency_code = currency_code
    mv.dimension_type = "ORG"
    mv.dimension_id = "ALL"
    return mv


class TestGetOrgSnapshot:
    """Tests for DashboardMetricsService.get_org_snapshot()."""

    @patch("app.services.analytics.dashboard_metrics.MetricStore")
    def test_returns_none_when_no_metrics(self, MockStore: MagicMock) -> None:
        """Should return None when MetricStore has no data for this org."""
        db = MagicMock()
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {}

        svc = DashboardMetricsService(db)
        result = svc.get_org_snapshot(DEFAULT_ORG)

        assert result is None

    @patch("app.services.analytics.dashboard_metrics.MetricStore")
    def test_returns_none_when_all_stale(self, MockStore: MagicMock) -> None:
        """Should return None when all metrics are older than 24 hours."""
        db = MagicMock()
        stale_time = datetime.now(UTC) - timedelta(hours=48)
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.net_position": _make_metric_value(
                "cash_flow.net_position", 50000, computed_at=stale_time
            ),
        }

        svc = DashboardMetricsService(db)
        result = svc.get_org_snapshot(DEFAULT_ORG)

        assert result is None

    @patch("app.services.analytics.dashboard_metrics.MetricStore")
    def test_returns_structured_dict_when_fresh(self, MockStore: MagicMock) -> None:
        """Should return structured dict grouped by domain when metrics are fresh."""
        db = MagicMock()
        fresh_time = datetime.now(UTC) - timedelta(hours=2)
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.net_position": _make_metric_value(
                "cash_flow.net_position", 5000000, computed_at=fresh_time
            ),
            "cash_flow.inflow_30d": _make_metric_value(
                "cash_flow.inflow_30d", 2000000, computed_at=fresh_time
            ),
            "cash_flow.outflow_30d": _make_metric_value(
                "cash_flow.outflow_30d", 1500000, computed_at=fresh_time
            ),
            "cash_flow.net_flow_30d": _make_metric_value(
                "cash_flow.net_flow_30d", 500000, computed_at=fresh_time
            ),
            "revenue.ytd_total": _make_metric_value(
                "revenue.ytd_total", 10000000, computed_at=fresh_time
            ),
            "efficiency.dso": _make_metric_value(
                "efficiency.dso", 45, computed_at=fresh_time
            ),
        }

        svc = DashboardMetricsService(db)
        result = svc.get_org_snapshot(DEFAULT_ORG)

        assert result is not None
        assert "cash_flow" in result
        assert "revenue" in result
        assert "efficiency" in result
        assert result["cash_flow"]["net_position"] == Decimal("5000000")
        assert result["cash_flow"]["inflow_30d"] == Decimal("2000000")
        assert result["revenue"]["ytd_total"] == Decimal("10000000")
        assert result["efficiency"]["dso"] == Decimal("45")

    @patch("app.services.analytics.dashboard_metrics.MetricStore")
    def test_missing_metrics_default_to_zero(self, MockStore: MagicMock) -> None:
        """Metrics not present in store should default to Decimal(0) for _or_zero fields."""
        db = MagicMock()
        fresh_time = datetime.now(UTC) - timedelta(hours=1)
        store_instance = MockStore.return_value
        # Only one metric present
        store_instance.get_latest.return_value = {
            "cash_flow.net_position": _make_metric_value(
                "cash_flow.net_position", 100, computed_at=fresh_time
            ),
        }

        svc = DashboardMetricsService(db)
        result = svc.get_org_snapshot(DEFAULT_ORG)

        assert result is not None
        # Missing metrics that use _numeric_or_zero should be Decimal("0")
        assert result["cash_flow"]["inflow_30d"] == Decimal("0")
        assert result["cash_flow"]["outflow_30d"] == Decimal("0")
        assert result["revenue"]["monthly_total"] == Decimal("0")
        assert result["supply_chain"]["stockout_count"] == Decimal("0")
        # Missing metrics that use _numeric should be None
        assert result["efficiency"]["dso"] is None
        assert result["revenue"]["conversion_rate"] is None

    @patch("app.services.analytics.dashboard_metrics.MetricStore")
    def test_json_metrics_returned(self, MockStore: MagicMock) -> None:
        """JSON-valued metrics should be returned as dicts."""
        db = MagicMock()
        fresh_time = datetime.now(UTC) - timedelta(hours=1)
        dept_data = {
            "total": 100,
            "departments": [
                {"department": "Engineering", "count": 40},
                {"department": "Sales", "count": 60},
            ],
        }
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "workforce.department_distribution": _make_metric_value(
                "workforce.department_distribution",
                value_json=dept_data,
                computed_at=fresh_time,
            ),
        }

        svc = DashboardMetricsService(db)
        result = svc.get_org_snapshot(DEFAULT_ORG)

        assert result is not None
        assert result["workforce"]["department_distribution"] == dept_data

    @patch("app.services.analytics.dashboard_metrics.MetricStore")
    def test_custom_max_age(self, MockStore: MagicMock) -> None:
        """Should respect custom max_age_hours parameter."""
        db = MagicMock()
        # 4 hours old
        four_hours_ago = datetime.now(UTC) - timedelta(hours=4)
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.net_position": _make_metric_value(
                "cash_flow.net_position", 100, computed_at=four_hours_ago
            ),
        }

        svc = DashboardMetricsService(db)
        # 6h window → should be fresh
        result_6h = svc.get_org_snapshot(DEFAULT_ORG, max_age_hours=6)
        assert result_6h is not None

        # 2h window → should be stale
        result_2h = svc.get_org_snapshot(DEFAULT_ORG, max_age_hours=2)
        assert result_2h is None

    @patch("app.services.analytics.dashboard_metrics.MetricStore")
    def test_all_domains_in_result(self, MockStore: MagicMock) -> None:
        """Result dict should contain all 6 domain keys."""
        db = MagicMock()
        fresh_time = datetime.now(UTC) - timedelta(hours=1)
        store_instance = MockStore.return_value
        store_instance.get_latest.return_value = {
            "cash_flow.net_position": _make_metric_value(
                "cash_flow.net_position", 0, computed_at=fresh_time
            ),
        }

        svc = DashboardMetricsService(db)
        result = svc.get_org_snapshot(DEFAULT_ORG)

        assert result is not None
        expected_domains = {
            "cash_flow",
            "efficiency",
            "revenue",
            "compliance",
            "workforce",
            "supply_chain",
        }
        assert set(result.keys()) == expected_domains


class TestAllDashboardMetrics:
    """Verify the ALL_DASHBOARD_METRICS constant."""

    def test_contains_all_metric_types(self) -> None:
        """Should have metrics from all 6 computers."""
        assert len(ALL_DASHBOARD_METRICS) == 37  # 7 + 6 + 6 + 6 + 6 + 6
        assert "cash_flow.net_position" in ALL_DASHBOARD_METRICS
        assert "efficiency.dso" in ALL_DASHBOARD_METRICS
        assert "revenue.ytd_total" in ALL_DASHBOARD_METRICS
        assert "compliance.overdue_tax_filings" in ALL_DASHBOARD_METRICS
        assert "workforce.active_headcount" in ALL_DASHBOARD_METRICS
        assert "supply_chain.stockout_count" in ALL_DASHBOARD_METRICS

    def test_no_duplicates(self) -> None:
        """No duplicate metric types."""
        assert len(ALL_DASHBOARD_METRICS) == len(set(ALL_DASHBOARD_METRICS))
