from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.expense.reset_audit import reset_evidence


def make_reset(**changes):
    values = dict(
        reset_at=datetime(2026, 11, 4, 12, tzinfo=UTC),
        reset_kind="MANUAL",
        reset_reason="Emergency repairs",
        reviewer_name_snapshot="Alice Reviewer",
        budget_snapshot=Decimal("200000"),
        currency_snapshot="NGN",
        paid_before_reset=Decimal("197000"),
        committed_at_reset=Decimal("1000"),
        remaining_before_reset=Decimal("2000"),
        remaining_after_reset=Decimal("199000"),
    )
    return SimpleNamespace(**(values | changes))


def payment(day, amount, at=None):
    return SimpleNamespace(paid_on=day, amount_paid=Decimal(amount), paid_at=at)


def test_reset_snapshots_preserve_reason_reviewer_and_exact_allowance_restored():
    result = reset_evidence(make_reset(), "Renamed reviewer", [])
    assert result["reviewer"] == "Alice Reviewer"
    assert result["reason"] == "Emergency repairs"
    assert result["restored"] == Decimal("197000")
    assert result["remaining_after"] == Decimal("199000")


def test_paid_history_stays_intact_and_splits_around_reset():
    rows = [
        payment(date(2026, 11, 3), "197000"),
        payment(date(2026, 11, 4), "500", datetime(2026, 11, 4, 11, tzinfo=UTC)),
        payment(date(2026, 11, 4), "50000", datetime(2026, 11, 4, 12, tzinfo=UTC)),
        payment(date(2026, 11, 5), "100000"),
        payment(date(2026, 11, 4), "2500"),
    ]
    result = reset_evidence(make_reset(), None, rows)
    assert result["period_paid_before"] == Decimal("197500")
    assert result["period_paid_after"] == Decimal("150000")
    assert result["period_paid_timing_unknown"] == Decimal("2500")
    assert sum(
        result[key]
        for key in (
            "period_paid_before",
            "period_paid_after",
            "period_paid_timing_unknown",
        )
    ) == sum(row.amount_paid for row in rows)


@pytest.mark.parametrize(
    "name,expected",
    [("Former Reviewer", "Former Reviewer"), (None, "Reviewer no longer available")],
)
def test_legacy_reset_is_not_presented_as_proof_of_authorized_excess(name, expected):
    result = reset_evidence(
        make_reset(
            budget_snapshot=None,
            remaining_before_reset=None,
            remaining_after_reset=None,
            reviewer_name_snapshot=None,
        ),
        name,
        [],
    )
    assert not result["has_snapshot"]
    assert result["restored"] is None
    assert result["reviewer"] == expected


def test_automatic_reset_does_not_claim_additional_allowance():
    result = reset_evidence(
        make_reset(
            reset_kind="AUTOMATIC",
            remaining_before_reset=Decimal("200000"),
            remaining_after_reset=Decimal("200000"),
        ),
        None,
        [],
    )
    assert result["kind"] == "AUTOMATIC"
    assert result["restored"] == 0


def test_history_template_renders_evidence_and_escapes_reasons():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from pathlib import Path
    from app.templates import templates as app_templates

    environment = Environment(
        loader=FileSystemLoader(Path(__file__).parents[2] / "templates"),
        autoescape=select_autoescape(),
    )
    environment.filters.update(app_templates.env.filters)
    macro = environment.get_template(
        "expense/limits/_reset_history.html"
    ).module.reset_history
    html = str(
        macro(
            [
                reset_evidence(
                    make_reset(reset_reason="<script>alert(1)</script>"), None, []
                )
            ]
        )
    )
    assert "197,000.00" in html
    assert "Alice Reviewer" in html
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
