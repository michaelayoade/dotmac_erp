"""Tests for ReportGenerator deterministic report building logic."""

from __future__ import annotations

from app.services.coach.report_generator import ReportGenerator


class _FakeInsight:
    """Minimal stand-in for CoachInsight used in unit tests."""

    def __init__(
        self,
        *,
        category: str,
        severity: str,
        title: str,
        summary: str,
        coaching_action: str | None = None,
        evidence: dict | None = None,
    ) -> None:
        self.category = category
        self.severity = severity
        self.title = title
        self.summary = summary
        self.coaching_action = coaching_action
        self.evidence = evidence or {}


# -- _build_executive_summary --------------------------------------------------


def test_executive_summary_basic():
    insights = [
        _FakeInsight(category="CASH_FLOW", severity="INFO", title="t", summary="s"),
        _FakeInsight(category="REVENUE", severity="WARNING", title="t", summary="s"),
    ]
    gen = ReportGenerator.__new__(ReportGenerator)  # skip __init__ (no DB needed)
    result = gen._build_executive_summary(insights, "finance")
    assert "2 insight(s)" in result
    assert "Cash Flow" in result
    assert "Revenue" in result
    assert "immediate attention" in result


def test_executive_summary_no_warnings():
    insights = [
        _FakeInsight(category="EFFICIENCY", severity="INFO", title="t", summary="s"),
    ]
    gen = ReportGenerator.__new__(ReportGenerator)
    result = gen._build_executive_summary(insights, "finance")
    assert "1 insight(s)" in result
    assert "immediate attention" not in result


# -- _build_recommendations ---------------------------------------------------


def test_recommendations_ordered_by_severity():
    insights = [
        _FakeInsight(
            category="A",
            severity="INFO",
            title="t",
            summary="s",
            coaching_action="Low priority action",
        ),
        _FakeInsight(
            category="B",
            severity="WARNING",
            title="t",
            summary="s",
            coaching_action="High priority action",
        ),
        _FakeInsight(
            category="C",
            severity="URGENT",
            title="t",
            summary="s",
            coaching_action="Critical action",
        ),
    ]
    gen = ReportGenerator.__new__(ReportGenerator)
    recs = gen._build_recommendations(insights)
    assert recs[0] == "Critical action"
    assert recs[1] == "High priority action"
    assert recs[2] == "Low priority action"


def test_recommendations_deduplication():
    insights = [
        _FakeInsight(
            category="A",
            severity="WARNING",
            title="t",
            summary="s",
            coaching_action="Same action",
        ),
        _FakeInsight(
            category="B",
            severity="WARNING",
            title="t",
            summary="s",
            coaching_action="Same action",
        ),
    ]
    gen = ReportGenerator.__new__(ReportGenerator)
    recs = gen._build_recommendations(insights)
    assert len(recs) == 1


def test_recommendations_cap_at_five():
    insights = [
        _FakeInsight(
            category="A",
            severity="INFO",
            title="t",
            summary="s",
            coaching_action=f"Action {i}",
        )
        for i in range(10)
    ]
    gen = ReportGenerator.__new__(ReportGenerator)
    recs = gen._build_recommendations(insights)
    assert len(recs) == 5


# -- _extract_key_metrics ------------------------------------------------------


def test_extract_key_metrics_basic():
    insights = [
        _FakeInsight(
            category="CASH_FLOW",
            severity="INFO",
            title="t",
            summary="s",
            evidence={"dso": 30, "dpo": 25, "ccc": 5},
        ),
    ]
    gen = ReportGenerator.__new__(ReportGenerator)
    metrics = gen._extract_key_metrics(insights)
    metric_names = [m["metric"] for m in metrics]
    assert "Dso" in metric_names
    assert "Dpo" in metric_names


def test_extract_key_metrics_skips_top_overdue_customers():
    insights = [
        _FakeInsight(
            category="AR",
            severity="INFO",
            title="t",
            summary="s",
            evidence={
                "overdue_count": 5,
                "top_overdue_customers": [{"name": "A"}],
            },
        ),
    ]
    gen = ReportGenerator.__new__(ReportGenerator)
    metrics = gen._extract_key_metrics(insights)
    metric_names = [m["metric"] for m in metrics]
    assert "Top Overdue Customers" not in metric_names
    assert "Overdue Count" in metric_names


def test_extract_key_metrics_cap_at_twenty():
    evidence = {f"metric_{i}": i for i in range(30)}
    insights = [
        _FakeInsight(
            category="X",
            severity="INFO",
            title="t",
            summary="s",
            evidence=evidence,
        ),
    ]
    gen = ReportGenerator.__new__(ReportGenerator)
    metrics = gen._extract_key_metrics(insights)
    assert len(metrics) == 20


# -- _build_finance_sections ---------------------------------------------------


def test_build_finance_sections_ordering():
    insights = [
        _FakeInsight(
            category="REVENUE",
            severity="INFO",
            title="Rev",
            summary="s",
            coaching_action="a",
        ),
        _FakeInsight(
            category="CASH_FLOW",
            severity="WARNING",
            title="CF",
            summary="s",
            coaching_action="b",
        ),
    ]
    gen = ReportGenerator.__new__(ReportGenerator)
    sections = gen._build_finance_sections(insights)
    # CASH_FLOW should come before REVENUE in the ordered output
    assert sections[0]["category"] == "CASH_FLOW"
    assert sections[1]["category"] == "REVENUE"


def test_build_finance_sections_uncategorized():
    insights = [
        _FakeInsight(
            category="CUSTOM_DOMAIN",
            severity="INFO",
            title="Custom",
            summary="s",
            coaching_action="c",
        ),
    ]
    gen = ReportGenerator.__new__(ReportGenerator)
    sections = gen._build_finance_sections(insights)
    assert len(sections) == 1
    assert sections[0]["category"] == "CUSTOM_DOMAIN"


# -- _build_hr_sections --------------------------------------------------------


def test_build_hr_sections():
    insights = [
        _FakeInsight(
            category="WORKFORCE",
            severity="ATTENTION",
            title="WF",
            summary="s",
            coaching_action="a",
        ),
        _FakeInsight(
            category="DATA_QUALITY",
            severity="INFO",
            title="DQ",
            summary="s",
            coaching_action="b",
        ),
    ]
    gen = ReportGenerator.__new__(ReportGenerator)
    sections = gen._build_hr_sections(insights)
    assert sections[0]["category"] == "WORKFORCE"
    assert sections[1]["category"] == "DATA_QUALITY"
