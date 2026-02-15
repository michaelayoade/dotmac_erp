"""Tests for RevenueAnalyzer severity and summary logic."""

from __future__ import annotations

from decimal import Decimal

from app.services.coach.analyzers.revenue import (
    CustomerConcentrationSummary,
    PipelineHealthSummary,
    _severity_for_concentration,
    _severity_for_pipeline,
)

# -- _severity_for_pipeline ----------------------------------------------------


def test_pipeline_info_healthy():
    assert _severity_for_pipeline(0, Decimal("45")) == "INFO"


def test_pipeline_attention_low_conversion():
    assert _severity_for_pipeline(0, Decimal("15")) == "ATTENTION"


def test_pipeline_warning_many_expired():
    assert _severity_for_pipeline(10, Decimal("50")) == "WARNING"
    assert _severity_for_pipeline(15, Decimal("10")) == "WARNING"


def test_pipeline_boundary_values():
    # Exactly 20% conversion = NOT < 20 = INFO (with 0 expired)
    assert _severity_for_pipeline(0, Decimal("20")) == "INFO"
    # 19.9% is < 20 = ATTENTION
    assert _severity_for_pipeline(0, Decimal("19.9")) == "ATTENTION"
    # 9 expired = INFO if conversion OK
    assert _severity_for_pipeline(9, Decimal("30")) == "INFO"


# -- _severity_for_concentration -----------------------------------------------


def test_concentration_info_diversified():
    assert _severity_for_concentration(Decimal("20")) == "INFO"


def test_concentration_attention_moderate():
    assert _severity_for_concentration(Decimal("30")) == "ATTENTION"
    assert _severity_for_concentration(Decimal("49")) == "ATTENTION"


def test_concentration_warning_high():
    assert _severity_for_concentration(Decimal("50")) == "WARNING"
    assert _severity_for_concentration(Decimal("80")) == "WARNING"


def test_concentration_boundary():
    assert _severity_for_concentration(Decimal("29.9")) == "INFO"
    assert _severity_for_concentration(Decimal("49.9")) == "ATTENTION"


# -- Dataclasses ----------------------------------------------------------------


def test_pipeline_health_summary():
    summary = PipelineHealthSummary(
        open_quotes=15,
        open_quote_value=Decimal("5000000"),
        expired_quotes=3,
        conversion_rate_pct=Decimal("42.5"),
        open_sales_orders=8,
        open_so_value=Decimal("3200000"),
        currency_code="NGN",
    )
    assert summary.open_quotes == 15
    assert summary.conversion_rate_pct == Decimal("42.5")
    assert summary.currency_code == "NGN"


def test_customer_concentration_summary():
    summary = CustomerConcentrationSummary(
        total_revenue_90d=Decimal("10000000"),
        top_customer_name="Acme Corp",
        top_customer_revenue=Decimal("3500000"),
        top_customer_pct=Decimal("35.0"),
        top_3_pct=Decimal("72.0"),
        active_customer_count=45,
        currency_code="NGN",
    )
    assert summary.top_customer_name == "Acme Corp"
    assert summary.top_customer_pct == Decimal("35.0")
    assert summary.active_customer_count == 45


def test_customer_concentration_no_revenue():
    summary = CustomerConcentrationSummary(
        total_revenue_90d=Decimal("0"),
        top_customer_name=None,
        top_customer_revenue=Decimal("0"),
        top_customer_pct=Decimal("0"),
        top_3_pct=Decimal("0"),
        active_customer_count=0,
        currency_code="NGN",
    )
    assert summary.top_customer_name is None
    assert summary.active_customer_count == 0
