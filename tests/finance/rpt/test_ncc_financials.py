from __future__ import annotations

import uuid

from app.services.finance.rpt import ncc_financials as mod


def _patch(monkeypatch, captured):
    def fake_income(db, org, start, end):
        captured["income"] = (org, start, end)
        return {
            "total_revenue": "N100",
            "net_income": "N20",
            "net_income_raw": 20.0,
            "start_date_iso": start,
            "end_date_iso": end,
            "period_name": "FY2026",
            "income_statement_lines": [{"line": "Revenue"}],
        }

    def fake_bs(db, org, as_of):
        captured["bs"] = (org, as_of)
        return {
            "as_of_date_iso": as_of,
            "total_assets": "N500",
            "total_liabilities": "N300",
            "total_equity": "N200",
            "is_balanced": True,
            "current_assets": [{"a": 1}],
            "non_current_assets": [],
            "current_liabilities": [],
            "non_current_liabilities": [],
            "equity": [],
        }

    def fake_exp(db, org, start, end):
        captured["exp"] = (org, start, end)
        return {
            "total_expenses": "N80",
            "total_expenses_raw": 80.0,
            "expense_items": [{"cat": "Energy"}],
        }

    monkeypatch.setattr(
        "app.services.finance.rpt.income_statement.income_statement_context",
        fake_income,
    )
    monkeypatch.setattr(
        "app.services.finance.rpt.balance_sheet.balance_sheet_context", fake_bs
    )
    monkeypatch.setattr(
        "app.services.finance.rpt.expense_summary.expense_summary_context", fake_exp
    )


def test_ncc_financials_composition_and_year(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, captured)
    org = uuid.uuid4()

    out = mod.ncc_financials_context(None, org, year=2026)

    # year fills the P&L range and the balance-sheet as-of
    assert captured["income"] == (str(org), "2026-01-01", "2026-12-31")
    assert captured["exp"] == (str(org), "2026-01-01", "2026-12-31")
    assert captured["bs"] == (str(org), "2026-12-31")

    assert out["period"]["year"] == 2026
    assert out["summary"]["total_revenue"] == "N100"
    assert out["summary"]["net_income_raw"] == 20.0
    assert out["summary"]["total_operating_expenses_raw"] == 80.0
    assert out["summary"]["total_assets"] == "N500"
    assert out["summary"]["total_equity"] == "N200"
    assert out["summary"]["is_balanced"] is True
    assert out["detail"]["expense_by_category"] == [{"cat": "Energy"}]
    assert out["detail"]["current_assets"] == [{"a": 1}]
    assert "erp chart-of-accounts" in out["note"]


def test_explicit_dates_override_year(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, captured)
    org = uuid.uuid4()

    mod.ncc_financials_context(
        None,
        org,
        year=2026,
        start_date="2026-04-01",
        end_date="2026-06-30",
        as_of_date="2026-06-30",
    )
    assert captured["income"] == (str(org), "2026-04-01", "2026-06-30")
    assert captured["bs"] == (str(org), "2026-06-30")
