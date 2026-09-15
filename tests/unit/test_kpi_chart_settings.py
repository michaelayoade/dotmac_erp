"""Strict chart-visibility input validation at the dashboard form boundary."""

from io import BytesIO

import pytest
from starlette.datastructures import UploadFile

from app.services.people.perf.kpi_dashboard_contract import DashboardValidationError
from app.services.people.perf.web.kpi_dashboard_web import _chart_setting


@pytest.mark.parametrize("value, expected", [("0", False), ("1", True)])
def test_chart_setting_returns_a_boolean(value, expected):
    assert _chart_setting(value) is expected


@pytest.mark.parametrize("value", [0, 1, False, True, None, [], {}, "true"])
def test_chart_setting_rejects_non_string_or_unknown_values(value):
    with pytest.raises(DashboardValidationError):
        _chart_setting(value)


def test_chart_setting_rejects_uploaded_content():
    with BytesIO(b"1") as contents:
        upload = UploadFile(file=contents, filename="show_charts.txt")
        with pytest.raises(DashboardValidationError):
            _chart_setting(upload)
