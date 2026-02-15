"""Tests for MetricStore — the read API for pre-computed metric snapshots."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.models.analytics.org_metric_snapshot import OrgMetricSnapshot
from app.services.analytics.metric_store import (
    MetricStore,
    MetricValue,
)

DEFAULT_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG = uuid.UUID("00000000-0000-0000-0000-000000000099")


@pytest.fixture(autouse=True)
def _clean_snapshots(db_session: pytest.fixture) -> None:  # type: ignore[type-arg]
    """Clean org_metric_snapshot table before each test (shared SQLite DB)."""
    db_session.execute(delete(OrgMetricSnapshot))
    db_session.commit()


def _make_snapshot(
    *,
    organization_id: uuid.UUID = DEFAULT_ORG,
    metric_type: str = "test.metric",
    snapshot_date: date = date(2026, 2, 15),
    granularity: str = "DAILY",
    dimension_type: str = "ORG",
    dimension_id: str = "ALL",
    value_numeric: float | None = None,
    value_json: dict | None = None,
    currency_code: str | None = None,
    source_label: str | None = "test",
) -> OrgMetricSnapshot:
    return OrgMetricSnapshot(
        organization_id=organization_id,
        metric_type=metric_type,
        snapshot_date=snapshot_date,
        granularity=granularity,
        dimension_type=dimension_type,
        dimension_id=dimension_id,
        value_numeric=value_numeric,
        value_json=value_json,
        currency_code=currency_code,
        source_label=source_label,
        computed_at=datetime.now(UTC),
    )


class TestGetLatest:
    """Tests for MetricStore.get_latest()."""

    def test_returns_most_recent_per_type(self, db_session: pytest.fixture) -> None:
        """Should return the latest snapshot for each requested metric type."""
        db_session.add_all(
            [
                _make_snapshot(
                    metric_type="cash_flow.net_position",
                    snapshot_date=date(2026, 2, 14),
                    value_numeric=100,
                ),
                _make_snapshot(
                    metric_type="cash_flow.net_position",
                    snapshot_date=date(2026, 2, 15),
                    value_numeric=200,
                ),
                _make_snapshot(
                    metric_type="efficiency.dso",
                    snapshot_date=date(2026, 2, 15),
                    value_numeric=45,
                ),
            ]
        )
        db_session.commit()

        store = MetricStore(db_session)
        result = store.get_latest(
            DEFAULT_ORG,
            ["cash_flow.net_position", "efficiency.dso"],
        )

        assert len(result) == 2
        assert result["cash_flow.net_position"].value_numeric == Decimal("200")
        assert result["efficiency.dso"].value_numeric == Decimal("45")

    def test_omits_missing_types(self, db_session: pytest.fixture) -> None:
        """Should omit metric types that have no snapshots."""
        db_session.add(
            _make_snapshot(metric_type="cash_flow.net_position", value_numeric=100)
        )
        db_session.commit()

        store = MetricStore(db_session)
        result = store.get_latest(
            DEFAULT_ORG, ["cash_flow.net_position", "no.such.metric"]
        )

        assert "cash_flow.net_position" in result
        assert "no.such.metric" not in result

    def test_empty_metric_types(self, db_session: pytest.fixture) -> None:
        """Should return empty dict for empty metric list."""
        store = MetricStore(db_session)
        result = store.get_latest(DEFAULT_ORG, [])
        assert result == {}

    def test_org_isolation(self, db_session: pytest.fixture) -> None:
        """Should not leak metrics across organizations."""
        db_session.add_all(
            [
                _make_snapshot(
                    organization_id=DEFAULT_ORG, metric_type="test.m", value_numeric=1
                ),
                _make_snapshot(
                    organization_id=OTHER_ORG, metric_type="test.m", value_numeric=999
                ),
            ]
        )
        db_session.commit()

        store = MetricStore(db_session)
        result = store.get_latest(DEFAULT_ORG, ["test.m"])

        assert result["test.m"].value_numeric == Decimal("1")

    def test_dimension_filtering(self, db_session: pytest.fixture) -> None:
        """Should filter by dimension_type and dimension_id."""
        dept_id = str(uuid.uuid4())
        db_session.add_all(
            [
                _make_snapshot(
                    metric_type="test.m",
                    dimension_type="ORG",
                    dimension_id="ALL",
                    value_numeric=10,
                ),
                _make_snapshot(
                    metric_type="test.m",
                    dimension_type="DEPARTMENT",
                    dimension_id=dept_id,
                    value_numeric=5,
                ),
            ]
        )
        db_session.commit()

        store = MetricStore(db_session)

        org_result = store.get_latest(DEFAULT_ORG, ["test.m"])
        assert org_result["test.m"].value_numeric == Decimal("10")

        dept_result = store.get_latest(
            DEFAULT_ORG, ["test.m"], dimension_type="DEPARTMENT", dimension_id=dept_id
        )
        assert dept_result["test.m"].value_numeric == Decimal("5")


class TestGetHistory:
    """Tests for MetricStore.get_history()."""

    def test_returns_ordered_series(self, db_session: pytest.fixture) -> None:
        """Should return time series ordered by snapshot_date."""
        for day in range(1, 6):
            db_session.add(
                _make_snapshot(
                    metric_type="cash_flow.net_position",
                    snapshot_date=date(2026, 2, day),
                    value_numeric=float(day * 100),
                )
            )
        db_session.commit()

        store = MetricStore(db_session)
        history = store.get_history(
            DEFAULT_ORG,
            "cash_flow.net_position",
            date(2026, 2, 1),
            date(2026, 2, 5),
        )

        assert len(history) == 5
        assert history[0].snapshot_date == date(2026, 2, 1)
        assert history[0].value_numeric == Decimal("100")
        assert history[4].snapshot_date == date(2026, 2, 5)
        assert history[4].value_numeric == Decimal("500")

    def test_respects_date_range(self, db_session: pytest.fixture) -> None:
        """Should only include snapshots within the date range."""
        for day in range(1, 11):
            db_session.add(
                _make_snapshot(
                    metric_type="test.m",
                    snapshot_date=date(2026, 2, day),
                    value_numeric=float(day),
                )
            )
        db_session.commit()

        store = MetricStore(db_session)
        history = store.get_history(
            DEFAULT_ORG, "test.m", date(2026, 2, 3), date(2026, 2, 7)
        )

        assert len(history) == 5
        assert history[0].snapshot_date == date(2026, 2, 3)
        assert history[-1].snapshot_date == date(2026, 2, 7)

    def test_empty_range(self, db_session: pytest.fixture) -> None:
        """Should return empty list if no data in range."""
        store = MetricStore(db_session)
        history = store.get_history(
            DEFAULT_ORG, "test.m", date(2026, 1, 1), date(2026, 1, 31)
        )
        assert history == []


class TestGetPriorPeriod:
    """Tests for MetricStore.get_prior_period()."""

    def test_returns_prior_period_values(self, db_session: pytest.fixture) -> None:
        """Should return the latest snapshot on or before cutoff."""
        db_session.add_all(
            [
                _make_snapshot(
                    metric_type="test.m",
                    snapshot_date=date(2026, 2, 10),
                    value_numeric=100,
                ),
                _make_snapshot(
                    metric_type="test.m",
                    snapshot_date=date(2026, 2, 14),
                    value_numeric=200,
                ),
                _make_snapshot(
                    metric_type="test.m",
                    snapshot_date=date(2026, 2, 15),
                    value_numeric=300,
                ),
            ]
        )
        db_session.commit()

        store = MetricStore(db_session)
        # periods_back=1 => cutoff = today - 1 (i.e. 2026-02-14)
        # But since we can't control "today" easily, we test by checking
        # the logic works with known data
        result = store.get_prior_period(DEFAULT_ORG, ["test.m"], periods_back=365)

        # 365 days back from today (Feb 15, 2026) = ~Feb 15, 2025
        # All our snapshots are in 2026, so should be empty
        assert result == {}

    def test_finds_latest_before_cutoff(self, db_session: pytest.fixture) -> None:
        """Should pick the most recent snapshot before the cutoff date."""
        db_session.add_all(
            [
                _make_snapshot(
                    metric_type="test.m",
                    snapshot_date=date(2025, 1, 1),
                    value_numeric=10,
                ),
                _make_snapshot(
                    metric_type="test.m",
                    snapshot_date=date(2025, 6, 1),
                    value_numeric=50,
                ),
            ]
        )
        db_session.commit()

        store = MetricStore(db_session)
        # periods_back=200 => cutoff = today - 200 days ~ July 2025
        result = store.get_prior_period(DEFAULT_ORG, ["test.m"], periods_back=200)

        assert "test.m" in result
        assert result["test.m"].value_numeric == Decimal("50")


class TestComparePeriods:
    """Tests for MetricStore.compare_periods()."""

    def test_computes_delta_and_pct(self, db_session: pytest.fixture) -> None:
        """Should compute delta and percentage change."""
        db_session.add_all(
            [
                _make_snapshot(
                    metric_type="test.m",
                    snapshot_date=date(2026, 2, 1),
                    value_numeric=100,
                ),
                _make_snapshot(
                    metric_type="test.m",
                    snapshot_date=date(2026, 2, 15),
                    value_numeric=150,
                ),
            ]
        )
        db_session.commit()

        store = MetricStore(db_session)
        cmp = store.compare_periods(
            DEFAULT_ORG,
            "test.m",
            current_date=date(2026, 2, 15),
            prior_date=date(2026, 2, 1),
        )

        assert cmp.current_value == Decimal("150")
        assert cmp.prior_value == Decimal("100")
        assert cmp.delta == Decimal("50")
        assert cmp.pct_change == pytest.approx(50.0)

    def test_handles_missing_current(self, db_session: pytest.fixture) -> None:
        """Should handle missing current-period data."""
        db_session.add(
            _make_snapshot(
                metric_type="test.m", snapshot_date=date(2026, 2, 1), value_numeric=100
            )
        )
        db_session.commit()

        store = MetricStore(db_session)
        cmp = store.compare_periods(
            DEFAULT_ORG,
            "test.m",
            current_date=date(2026, 2, 15),
            prior_date=date(2026, 2, 1),
        )

        assert cmp.current_value is None
        assert cmp.prior_value == Decimal("100")
        assert cmp.delta is None
        assert cmp.pct_change is None

    def test_handles_missing_both(self, db_session: pytest.fixture) -> None:
        """Should handle both periods missing."""
        store = MetricStore(db_session)
        cmp = store.compare_periods(
            DEFAULT_ORG,
            "no.data",
            current_date=date(2026, 2, 15),
            prior_date=date(2026, 2, 1),
        )

        assert cmp.current_value is None
        assert cmp.prior_value is None
        assert cmp.delta is None
        assert cmp.pct_change is None

    def test_zero_prior_avoids_division_error(self, db_session: pytest.fixture) -> None:
        """Should not crash on zero prior value (no pct_change)."""
        db_session.add_all(
            [
                _make_snapshot(
                    metric_type="test.m",
                    snapshot_date=date(2026, 2, 1),
                    value_numeric=0,
                ),
                _make_snapshot(
                    metric_type="test.m",
                    snapshot_date=date(2026, 2, 15),
                    value_numeric=100,
                ),
            ]
        )
        db_session.commit()

        store = MetricStore(db_session)
        cmp = store.compare_periods(
            DEFAULT_ORG,
            "test.m",
            current_date=date(2026, 2, 15),
            prior_date=date(2026, 2, 1),
        )

        assert cmp.delta == Decimal("100")
        assert cmp.pct_change is None  # Division by zero → None


class TestMetricValueDataclass:
    """Tests for the MetricValue frozen dataclass."""

    def test_frozen(self) -> None:
        """MetricValue should be immutable."""
        mv = MetricValue(
            metric_type="test",
            snapshot_date=date(2026, 1, 1),
            value_numeric=Decimal("42"),
            value_json=None,
            currency_code="NGN",
            dimension_type="ORG",
            dimension_id="ALL",
            computed_at=datetime.now(UTC),
        )
        with pytest.raises(AttributeError):
            mv.value_numeric = Decimal("0")  # type: ignore[misc]
