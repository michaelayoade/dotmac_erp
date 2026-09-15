"""Pure rules; no records, side effects or employee-ranking decisions."""

from decimal import Decimal as D
import pytest

from app.models.people.perf.kpi_measurement import (
    achievement,
    lower_is_better,
    parse_direction,
    progress_status,
    support_metric_key,
)


@pytest.mark.parametrize(
    ("actual", "target", "lower", "expected"),
    [
        ("0", "20", False, "0.00"),
        ("20", "20", False, "100.00"),
        ("16", "20", False, "80.00"),
        ("2", "4", True, "200.00"),
        ("4", "2", True, "50.00"),
        ("0", "2", True, "100.00"),
        ("0", "0", True, "100.00"),
        ("2", "0", True, "0.00"),
        ("1", "0", False, None),
        (None, "20", False, None),
        (None, "2", True, None),
        ("1", None, False, None),
        ("-1", "2", True, None),
        ("1", "-2", False, None),
        ("NaN", "2", False, None),
        ("Infinity", "2", True, None),
        ("1", "Infinity", False, None),
        ("10000000", "0.01", False, "999.99"),
    ],
)
def test_attainment_boundaries(actual, target, lower, expected):
    result = achievement(
        D(actual) if actual is not None else None,
        D(target) if target is not None else None,
        lower=lower,
    )
    assert result == (D(expected) if expected is not None else None)


@pytest.mark.parametrize(
    ("score", "status"),
    [
        (None, "PENDING"),
        ("0", "AT_RISK"),
        ("79.99", "AT_RISK"),
        ("80", "ON_TRACK"),
        ("100", "ACHIEVED"),
    ],
)
def test_zero_and_missing_are_distinct(score, status):
    assert progress_status(D(score) if score is not None else None) == status


def test_explicit_direction_and_legacy_support_tags():
    assert lower_is_better(None, "Metric key: support.avg_resolution_days") is True
    assert lower_is_better(False, "support.avg_resolution_days") is False
    assert lower_is_better(True, "Generic error count") is True
    assert lower_is_better(None, "Ordinary sales count") is False
    assert support_metric_key("support.resolution_rate") == "support.resolution_rate"
    assert support_metric_key("support.resolution_rate support.open_backlog") is None


@pytest.mark.parametrize(
    ("value", "expected"), [("", None), ("lower", True), ("higher", False)]
)
def test_direction_form_contract(value, expected):
    assert parse_direction(value) is expected


def test_invalid_direction_rejected():
    with pytest.raises(ValueError):
        parse_direction("sql")


def test_rounding_cannot_award_an_unmet_target():
    assert achievement(D("9999.99"), D("10000")) == D("99.99")
    assert achievement(D("10000.01"), D("10000"), lower=True) == D("99.99")
