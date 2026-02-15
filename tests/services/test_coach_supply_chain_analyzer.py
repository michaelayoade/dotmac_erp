"""Tests for SupplyChainAnalyzer severity and summary logic."""

from __future__ import annotations

from decimal import Decimal

from app.services.coach.analyzers.supply_chain import (
    DeadStockSummary,
    StockoutRiskSummary,
    _severity_for_dead_stock,
    _severity_for_stockout,
)

# -- _severity_for_stockout ----------------------------------------------------


def test_stockout_info_no_issues():
    assert _severity_for_stockout(0, 0) == "INFO"


def test_stockout_attention_one_at_zero():
    assert _severity_for_stockout(1, 0) == "ATTENTION"


def test_stockout_attention_many_below_reorder():
    assert _severity_for_stockout(0, 10) == "ATTENTION"


def test_stockout_warning_many_at_zero():
    assert _severity_for_stockout(5, 0) == "WARNING"
    assert _severity_for_stockout(10, 20) == "WARNING"


def test_stockout_info_below_threshold():
    # 0 at zero and 9 below reorder = INFO (need >=1 at zero or >=10 below)
    assert _severity_for_stockout(0, 9) == "INFO"


# -- _severity_for_dead_stock ---------------------------------------------------


def test_dead_stock_info_low():
    assert _severity_for_dead_stock(0) == "INFO"
    assert _severity_for_dead_stock(4) == "INFO"


def test_dead_stock_attention():
    assert _severity_for_dead_stock(5) == "ATTENTION"
    assert _severity_for_dead_stock(19) == "ATTENTION"


def test_dead_stock_warning():
    assert _severity_for_dead_stock(20) == "WARNING"
    assert _severity_for_dead_stock(100) == "WARNING"


# -- Dataclasses ----------------------------------------------------------------


def test_stockout_risk_summary():
    summary = StockoutRiskSummary(
        items_below_reorder=8,
        items_at_zero_stock=2,
        total_tracked_items=150,
    )
    assert summary.items_below_reorder == 8
    assert summary.items_at_zero_stock == 2
    assert summary.total_tracked_items == 150


def test_dead_stock_summary():
    summary = DeadStockSummary(
        dead_stock_count=12,
        dead_stock_value=Decimal("450000.50"),
        currency_code="NGN",
    )
    assert summary.dead_stock_count == 12
    assert summary.dead_stock_value == Decimal("450000.50")
    assert summary.currency_code == "NGN"
