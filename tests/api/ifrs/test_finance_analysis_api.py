"""Tests for finance analysis API endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.finance import analysis as analysis_api


def test_list_analysis_cubes():
    """List cubes maps service objects to response model."""
    db = MagicMock()
    org_id = uuid4()
    cube = SimpleNamespace(
        code="sales",
        name="Sales Analysis",
        description="Sales cube",
        dimensions=[{"field": "period_label", "label": "Period"}],
        measures=[{"field": "amount_total", "label": "Total", "agg": "sum"}],
        default_rows=["period_label"],
        default_columns=[],
        default_measures=["amount_total"],
    )

    with patch(
        "app.api.finance.analysis.AnalysisCubeService.list_cubes"
    ) as mock_list_cubes:
        mock_list_cubes.return_value = [cube]

        result = analysis_api.list_analysis_cubes(
            org_id=org_id,
            db=db,
        )

    assert len(result) == 1
    assert result[0].code == "sales"
    assert result[0].default_measures == ["amount_total"]


def test_query_analysis_cube_success():
    """Query endpoint returns mapped cube query results."""
    db = MagicMock()
    org_id = uuid4()
    payload = analysis_api.AnalysisQueryRequest(
        row_dimensions=["period_label"],
        measures=["amount_total"],
        filters=[{"field": "customer_name", "value": "Acme"}],
        limit=500,
    )
    query_result = SimpleNamespace(
        cube_code="sales",
        columns=["period_label", "amount_total"],
        rows=[{"period_label": "Feb 2026", "amount_total": 100.0}],
    )

    with patch(
        "app.api.finance.analysis.AnalysisCubeService.query_cube"
    ) as mock_query_cube:
        mock_query_cube.return_value = query_result

        result = analysis_api.query_analysis_cube(
            cube_code="sales",
            payload=payload,
            org_id=org_id,
            db=db,
        )

    assert result.cube_code == "sales"
    assert result.columns == ["period_label", "amount_total"]
    assert result.rows == [{"period_label": "Feb 2026", "amount_total": 100.0}]


def test_query_analysis_cube_maps_validation_error():
    """Service validation errors are exposed as HTTP 400."""
    payload = analysis_api.AnalysisQueryRequest(
        row_dimensions=["bad_dim"],
        measures=["amount_total"],
    )

    with patch(
        "app.api.finance.analysis.AnalysisCubeService.query_cube"
    ) as mock_query_cube:
        mock_query_cube.side_effect = ValueError("Unknown dimension: bad_dim")

        with pytest.raises(HTTPException) as exc_info:
            analysis_api.query_analysis_cube(
                cube_code="sales",
                payload=payload,
                org_id=uuid4(),
                db=MagicMock(),
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unknown dimension: bad_dim"
