"""
Tests for AnalysisCubeService.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.rpt.analysis_cube import AnalysisCubeService


def _cube(
    *,
    source_view: str = "rpt.sales_analysis_mv",
):
    return SimpleNamespace(
        code="sales",
        source_view=source_view,
        dimensions=[
            {"field": "period_label", "label": "Period"},
            {"field": "customer_name", "label": "Customer"},
        ],
        measures=[
            {"field": "amount_total", "label": "Total", "agg": "sum"},
            {"field": "record_count", "label": "Records", "agg": "sum"},
        ],
    )


def test_query_cube_success():
    db = MagicMock()
    db.execute.return_value.mappings.return_value.all.return_value = [
        {"period_label": "Feb 2026", "amount_total": 123.45}
    ]
    service = AnalysisCubeService(db)
    organization_id = uuid4()

    with patch.object(service, "_get_cube", return_value=_cube()):
        result = service.query_cube(
            organization_id,
            "sales",
            row_dimensions=["period_label"],
            measures=["amount_total"],
            filters=[{"field": "customer_name", "value": "Acme"}],
            limit=500,
        )

    assert result.cube_code == "sales"
    assert result.columns == ["period_label", "amount_total"]
    assert result.rows == [{"period_label": "Feb 2026", "amount_total": 123.45}]
    _, params = db.execute.call_args[0]
    assert params["org_id"] == str(organization_id)
    assert params["f_0"] == "Acme"
    assert params["limit"] == 500


def test_query_cube_validates_dimension():
    service = AnalysisCubeService(MagicMock())
    with patch.object(service, "_get_cube", return_value=_cube()):
        with pytest.raises(ValueError, match="Unknown dimension"):
            service.query_cube(
                uuid4(),
                "sales",
                row_dimensions=["bad_dimension"],
                measures=["amount_total"],
            )


def test_query_cube_validates_measure():
    service = AnalysisCubeService(MagicMock())
    with patch.object(service, "_get_cube", return_value=_cube()):
        with pytest.raises(ValueError, match="Unknown measure"):
            service.query_cube(
                uuid4(),
                "sales",
                row_dimensions=["period_label"],
                measures=["bad_measure"],
            )


def test_query_cube_validates_filter_field():
    service = AnalysisCubeService(MagicMock())
    with patch.object(service, "_get_cube", return_value=_cube()):
        with pytest.raises(ValueError, match="Unknown filter field"):
            service.query_cube(
                uuid4(),
                "sales",
                row_dimensions=["period_label"],
                measures=["amount_total"],
                filters=[{"field": "bad_filter", "value": "x"}],
            )


def test_query_cube_rejects_invalid_source_view():
    service = AnalysisCubeService(MagicMock())
    with patch.object(
        service, "_get_cube", return_value=_cube(source_view="rpt.sales;drop table")
    ):
        with pytest.raises(ValueError, match="Invalid source view"):
            service.query_cube(
                uuid4(),
                "sales",
                row_dimensions=["period_label"],
                measures=["amount_total"],
            )


def test_query_cube_validates_limit_bounds():
    service = AnalysisCubeService(MagicMock())
    with patch.object(service, "_get_cube", return_value=_cube()):
        with pytest.raises(ValueError, match="between 1 and 5000"):
            service.query_cube(
                uuid4(),
                "sales",
                row_dimensions=["period_label"],
                measures=["amount_total"],
                limit=0,
            )


def test_refresh_due_cubes_refreshes_only_due():
    now = datetime(2026, 2, 25, 12, 0, tzinfo=UTC)
    due_cube = _cube()
    due_cube.is_active = True
    due_cube.last_refreshed_at = None
    due_cube.refresh_interval_minutes = 60

    fresh_cube = _cube()
    fresh_cube.code = "fresh"
    fresh_cube.is_active = True
    fresh_cube.last_refreshed_at = now
    fresh_cube.refresh_interval_minutes = 60

    db = MagicMock()
    db.scalars.return_value.all.return_value = [due_cube, fresh_cube]
    service = AnalysisCubeService(db)

    with patch.object(service, "refresh_cube") as refresh_cube:
        result = service.refresh_due_cubes(now=now)

    assert result == {"checked": 2, "refreshed": 1, "errors": 0}
    refresh_cube.assert_called_once_with(due_cube, now=now)


def test_refresh_cube_falls_back_when_concurrent_refresh_fails():
    cube = _cube()
    cube.last_refreshed_at = None
    db = MagicMock()
    db.execute.side_effect = [Exception("no unique index"), MagicMock()]
    service = AnalysisCubeService(db)
    now = datetime(2026, 2, 25, 12, 0, tzinfo=UTC)

    service.refresh_cube(cube, now=now)

    assert db.execute.call_count == 2
    assert cube.last_refreshed_at == now
