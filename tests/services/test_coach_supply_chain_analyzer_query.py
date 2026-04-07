"""Query-shape regression tests for SupplyChainAnalyzer."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.services.coach.analyzers.supply_chain import SupplyChainAnalyzer


def test_stockout_risk_uses_window_function_instead_of_max_uuid():
    org_id = uuid4()
    observed_sql: list[str] = []

    def _scalar_side_effect(stmt):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        observed_sql.append(sql)
        if len(observed_sql) == 1:
            return 3
        if len(observed_sql) == 2:
            return 1
        return 0

    db = MagicMock()
    db.scalar.side_effect = _scalar_side_effect

    analyzer = SupplyChainAnalyzer(db)
    summary = analyzer.stockout_risk(org_id)

    assert summary.total_tracked_items == 3
    assert summary.items_below_reorder == 1
    assert summary.items_at_zero_stock == 0

    stockout_sql = observed_sql[1]
    assert "row_number() OVER" in stockout_sql
    assert "max(inv.inventory_transaction.transaction_id)" not in stockout_sql
