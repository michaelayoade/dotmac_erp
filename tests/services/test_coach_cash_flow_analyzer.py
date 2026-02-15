"""Tests for CashFlowAnalyzer severity and summary logic."""

from __future__ import annotations

from decimal import Decimal

from app.services.coach.analyzers.cash_flow import (
    CashFlowHealthSummary,
    _severity_for_ccc,
)


def test_severity_for_ccc_info():
    assert _severity_for_ccc(Decimal("20"), Decimal("30")) == "INFO"


def test_severity_for_ccc_attention_high_dso():
    assert _severity_for_ccc(Decimal("20"), Decimal("50")) == "ATTENTION"


def test_severity_for_ccc_warning_high_ccc():
    assert _severity_for_ccc(Decimal("65"), Decimal("30")) == "WARNING"


def test_health_summary_dataclass():
    summary = CashFlowHealthSummary(
        dso=Decimal("30.5"),
        dpo=Decimal("25.0"),
        ccc=Decimal("5.5"),
        ar_outstanding=Decimal("500000"),
        ap_outstanding=Decimal("300000"),
        revenue_90d=Decimal("2000000"),
        cogs_90d=Decimal("1200000"),
        net_30d_forecast=Decimal("266666.67"),
        currency_code="NGN",
    )
    assert summary.dso == Decimal("30.5")
    assert summary.ccc == Decimal("5.5")
    assert summary.currency_code == "NGN"
