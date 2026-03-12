"""Management accounts report context builder and CSV export."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.common import coerce_uuid
from app.services.finance.rpt.common import (
    _build_csv,
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
)


def _account_level_balances(
    db: Session,
    organization_id: str,
    ifrs_categories: list[IFRSCategory],
    start_date: date | None = None,
    end_date: date | None = None,
    as_of_date: date | None = None,
) -> list[dict[str, Any]]:
    """Get account-level balances for given IFRS categories."""
    org_id = coerce_uuid(organization_id)

    stmt = (
        select(
            Account.account_code,
            Account.account_name,
            Account.normal_balance,
            AccountCategory.category_code,
            AccountCategory.ifrs_category,
            func.coalesce(func.sum(JournalEntryLine.debit_amount_functional), 0).label(
                "debit"
            ),
            func.coalesce(func.sum(JournalEntryLine.credit_amount_functional), 0).label(
                "credit"
            ),
        )
        .join(
            JournalEntry,
            JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
        )
        .join(Account, JournalEntryLine.account_id == Account.account_id)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(
            JournalEntry.organization_id == org_id,
            JournalEntry.status == JournalStatus.POSTED,
            AccountCategory.ifrs_category.in_(ifrs_categories),
            Account.is_active.is_(True),
        )
    )

    if as_of_date:
        stmt = stmt.where(JournalEntry.posting_date <= as_of_date)
    else:
        if start_date:
            stmt = stmt.where(JournalEntry.posting_date >= start_date)
        if end_date:
            stmt = stmt.where(JournalEntry.posting_date <= end_date)

    rows = db.execute(
        stmt.group_by(
            Account.account_code,
            Account.account_name,
            Account.normal_balance,
            AccountCategory.category_code,
            AccountCategory.ifrs_category,
        ).order_by(Account.account_code)
    ).all()

    items: list[dict[str, Any]] = []
    for code, name, normal_bal, cat_code, ifrs_cat, debit, credit in rows:
        debit = Decimal(str(debit or 0))
        credit = Decimal(str(credit or 0))
        if ifrs_cat in {IFRSCategory.ASSETS, IFRSCategory.EXPENSES}:
            amount = debit - credit
        else:
            amount = credit - debit
        if abs(amount) < Decimal("0.01"):
            continue
        items.append(
            {
                "account_code": code,
                "account_name": name,
                "category_code": cat_code,
                "ifrs_category": ifrs_cat,
                "normal_balance": normal_bal,
                "amount": _format_currency(amount),
                "amount_raw": float(amount),
                "debit_raw": float(debit),
                "credit_raw": float(credit),
            }
        )
    return items


def _monthly_pl_breakdown(
    db: Session,
    organization_id: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Get monthly revenue, COS, and OPEX breakdown."""
    org_id = coerce_uuid(organization_id)

    month_extract = func.date_trunc("month", JournalEntry.posting_date)

    rows = db.execute(
        select(
            month_extract.label("month"),
            AccountCategory.category_code,
            func.coalesce(func.sum(JournalEntryLine.debit_amount_functional), 0).label(
                "debit"
            ),
            func.coalesce(func.sum(JournalEntryLine.credit_amount_functional), 0).label(
                "credit"
            ),
        )
        .join(
            JournalEntry,
            JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
        )
        .join(Account, JournalEntryLine.account_id == Account.account_id)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(
            JournalEntry.organization_id == org_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.posting_date >= start_date,
            JournalEntry.posting_date <= end_date,
            AccountCategory.category_code.in_(["REV", "COS", "EXP"]),
        )
        .group_by(month_extract, AccountCategory.category_code)
        .order_by(month_extract)
    ).all()

    # Aggregate by month
    monthly: dict[str, dict[str, Any]] = {}
    for month_dt, cat_code, debit, credit in rows:
        debit = Decimal(str(debit or 0))
        credit = Decimal(str(credit or 0))
        month_key = month_dt.strftime("%Y-%m")
        if month_key not in monthly:
            monthly[month_key] = {
                "month": month_dt.strftime("%b %Y"),
                "month_short": month_dt.strftime("%b"),
                "revenue": 0.0,
                "cos": 0.0,
                "opex": 0.0,
            }
        if cat_code == "REV":
            monthly[month_key]["revenue"] = float(credit - debit)
        elif cat_code == "COS":
            monthly[month_key]["cos"] = float(debit - credit)
        elif cat_code == "EXP":
            monthly[month_key]["opex"] = float(debit - credit)

    result: list[dict[str, Any]] = []
    for _key in sorted(monthly):
        m = monthly[_key]
        net = m["revenue"] - m["cos"] - m["opex"]
        margin = (net / m["revenue"] * 100) if m["revenue"] else 0.0
        m["net_profit"] = net
        m["margin"] = round(margin, 1)
        m["net_profit_formatted"] = _format_currency(Decimal(str(net)))
        m["revenue_formatted"] = _format_currency(Decimal(str(m["revenue"])))
        m["cos_formatted"] = _format_currency(Decimal(str(m["cos"])))
        m["opex_formatted"] = _format_currency(Decimal(str(m["opex"])))
        result.append(m)

    return result


def management_accounts_context(
    db: Session,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Get context for management accounts report.

    Combines income statement, balance sheet, monthly trends,
    and key financial ratios into a single comprehensive view.
    """
    org_id = coerce_uuid(organization_id)
    today = date.today()
    from_date = _parse_date(start_date) or date(today.year, 1, 1)
    to_date = _parse_date(end_date) or date(today.year, 12, 31)

    # Find fiscal period
    period = db.scalars(
        select(FiscalPeriod)
        .where(
            FiscalPeriod.organization_id == org_id,
            FiscalPeriod.start_date <= to_date,
            FiscalPeriod.end_date >= from_date,
        )
        .order_by(FiscalPeriod.start_date.desc())
    ).first()

    # Get org name
    from app.models.finance.core_org.organization import Organization

    org = db.get(Organization, org_id)
    org_name = org.legal_name if org else "Organization"
    currency_code = org.functional_currency_code if org else "NGN"

    # ─── P&L: Account-level items ───
    revenue_items = _account_level_balances(
        db,
        organization_id,
        [IFRSCategory.REVENUE],
        start_date=from_date,
        end_date=to_date,
    )
    cos_items = _account_level_balances(
        db,
        organization_id,
        [IFRSCategory.EXPENSES],
        start_date=from_date,
        end_date=to_date,
    )
    # Split COS vs OPEX by category_code
    cost_of_sales_items = [i for i in cos_items if i["category_code"] == "COS"]
    opex_items = [i for i in cos_items if i["category_code"] != "COS"]

    total_revenue = sum(i["amount_raw"] for i in revenue_items)
    total_cos = sum(i["amount_raw"] for i in cost_of_sales_items)
    total_opex = sum(i["amount_raw"] for i in opex_items)
    gross_profit = total_revenue - total_cos
    operating_profit = gross_profit - total_opex
    gp_margin = (gross_profit / total_revenue * 100) if total_revenue else 0.0
    op_margin = (operating_profit / total_revenue * 100) if total_revenue else 0.0

    # ─── Balance Sheet: Account-level items ───
    bs_asset_items = _account_level_balances(
        db,
        organization_id,
        [IFRSCategory.ASSETS],
        as_of_date=to_date,
    )
    bs_liability_items = _account_level_balances(
        db,
        organization_id,
        [IFRSCategory.LIABILITIES],
        as_of_date=to_date,
    )
    bs_equity_items = _account_level_balances(
        db,
        organization_id,
        [IFRSCategory.EQUITY],
        as_of_date=to_date,
    )

    # Group assets by type
    fa_items = [i for i in bs_asset_items if i["category_code"] in ("FA", "FA-AD")]
    ca_items = [i for i in bs_asset_items if i["category_code"] not in ("FA", "FA-AD")]

    # Group liabilities
    ncl_items = [i for i in bs_liability_items if i["category_code"] == "LTL"]
    cl_items = [i for i in bs_liability_items if i["category_code"] != "LTL"]

    total_nca = sum(i["amount_raw"] for i in fa_items)
    total_ca = sum(i["amount_raw"] for i in ca_items)
    total_assets = total_nca + total_ca
    total_ncl = sum(i["amount_raw"] for i in ncl_items)
    total_cl = sum(i["amount_raw"] for i in cl_items)
    total_liabilities = total_ncl + total_cl
    total_equity = sum(i["amount_raw"] for i in bs_equity_items)

    # ─── Key ratios ───
    current_ratio = total_ca / total_cl if total_cl else 0.0
    inventory_total = sum(
        i["amount_raw"] for i in ca_items if i["category_code"] == "INV"
    )
    quick_ratio = (total_ca - inventory_total) / total_cl if total_cl else 0.0
    debt_to_equity_denom = total_equity + operating_profit
    debt_to_equity = (
        total_liabilities / debt_to_equity_denom if debt_to_equity_denom else 0.0
    )
    receivables_total = sum(
        i["amount_raw"] for i in ca_items if i["category_code"] == "AR"
    )
    recv_days = receivables_total / (total_revenue / 365) if total_revenue else 0.0
    payables_total = sum(
        i["amount_raw"] for i in cl_items if i["category_code"] == "AP"
    )
    pay_days = total_cos / 365 if total_cos else 0.0
    payable_days = payables_total / pay_days if pay_days else 0.0

    # ─── Cash Flow Statement (Indirect Method — IAS 7) ───
    # Opening BS balances (as of day before start)
    opening_date = from_date - timedelta(days=1)
    ob_asset_items = _account_level_balances(
        db, organization_id, [IFRSCategory.ASSETS], as_of_date=opening_date
    )
    ob_liability_items = _account_level_balances(
        db, organization_id, [IFRSCategory.LIABILITIES], as_of_date=opening_date
    )
    ob_equity_items = _account_level_balances(
        db, organization_id, [IFRSCategory.EQUITY], as_of_date=opening_date
    )

    # Opening working capital components
    ob_ca = [i for i in ob_asset_items if i["category_code"] not in ("FA", "FA-AD")]
    ob_receivables = sum(i["amount_raw"] for i in ob_ca if i["category_code"] == "AR")
    ob_inventory = sum(i["amount_raw"] for i in ob_ca if i["category_code"] == "INV")
    ob_other_ca = sum(
        i["amount_raw"]
        for i in ob_ca
        if i["category_code"] not in ("AR", "INV", "CASH")
    )
    ob_payables = sum(
        i["amount_raw"] for i in ob_liability_items if i["category_code"] == "AP"
    )
    ob_other_cl = sum(
        i["amount_raw"]
        for i in ob_liability_items
        if i["category_code"] not in ("AP", "LTL")
    )
    ob_cash = sum(i["amount_raw"] for i in ob_ca if i["category_code"] == "CASH")
    ob_ncl = sum(
        i["amount_raw"] for i in ob_liability_items if i["category_code"] == "LTL"
    )
    ob_total_equity = sum(i["amount_raw"] for i in ob_equity_items)

    # Closing working capital components
    cash_total = sum(i["amount_raw"] for i in ca_items if i["category_code"] == "CASH")
    other_ca_total = sum(
        i["amount_raw"]
        for i in ca_items
        if i["category_code"] not in ("AR", "INV", "CASH")
    )
    other_cl_total = sum(
        i["amount_raw"] for i in cl_items if i["category_code"] != "AP"
    )

    # Depreciation charge = closing accum depn - opening accum depn
    ob_accum_depn = sum(
        i["amount_raw"] for i in ob_asset_items if i["category_code"] == "FA-AD"
    )
    cl_accum_depn = sum(
        i["amount_raw"] for i in fa_items if i["category_code"] == "FA-AD"
    )
    depreciation_charge = abs(cl_accum_depn) - abs(ob_accum_depn)

    # Working capital changes (increase in asset = cash outflow, increase in liability = cash inflow)
    chg_receivables = -(receivables_total - ob_receivables)
    chg_inventory = -(inventory_total - ob_inventory)
    chg_other_ca = -(other_ca_total - ob_other_ca)
    chg_payables = payables_total - ob_payables
    chg_other_cl = other_cl_total - ob_other_cl
    total_wc_changes = (
        chg_receivables + chg_inventory + chg_other_ca + chg_payables + chg_other_cl
    )

    cash_from_operations = operating_profit + depreciation_charge + total_wc_changes

    # Investing: change in non-current assets (excluding accum depn)
    ob_fa_cost = sum(
        i["amount_raw"] for i in ob_asset_items if i["category_code"] == "FA"
    )
    cl_fa_cost = sum(i["amount_raw"] for i in fa_items if i["category_code"] == "FA")
    capex = -(cl_fa_cost - ob_fa_cost)
    cash_from_investing = capex

    # Financing: change in long-term borrowings + equity
    chg_ncl = total_ncl - ob_ncl
    chg_equity = total_equity - ob_total_equity
    cash_from_financing = chg_ncl + chg_equity

    net_cash_change = cash_from_operations + cash_from_investing + cash_from_financing
    closing_cash = ob_cash + net_cash_change

    # ─── Changes in Equity (IAS 1) ───
    opening_equity_total = ob_total_equity
    closing_equity_total = total_equity + operating_profit

    # ─── Monthly trend ───
    monthly_data = _monthly_pl_breakdown(
        db,
        organization_id,
        from_date,
        to_date,
    )

    fmt = _format_currency

    return {
        "org_name": org_name,
        "currency_code": currency_code,
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "period_name": period.period_name if period else "No Period",
        "prepared_date": _format_date(today),
        # P&L items
        "revenue_items": revenue_items,
        "cost_of_sales_items": cost_of_sales_items,
        "opex_items": opex_items,
        "total_revenue": fmt(Decimal(str(total_revenue))),
        "total_revenue_raw": total_revenue,
        "total_cos": fmt(Decimal(str(total_cos))),
        "total_cos_raw": total_cos,
        "total_opex": fmt(Decimal(str(total_opex))),
        "total_opex_raw": total_opex,
        "gross_profit": fmt(Decimal(str(gross_profit))),
        "gross_profit_raw": gross_profit,
        "operating_profit": fmt(Decimal(str(operating_profit))),
        "operating_profit_raw": operating_profit,
        "gp_margin": round(gp_margin, 1),
        "op_margin": round(op_margin, 1),
        "is_profit": operating_profit >= 0,
        # Balance sheet items
        "fa_items": fa_items,
        "ca_items": ca_items,
        "ncl_items": ncl_items,
        "cl_items": cl_items,
        "equity_items": bs_equity_items,
        "total_nca": fmt(Decimal(str(total_nca))),
        "total_nca_raw": total_nca,
        "total_ca": fmt(Decimal(str(total_ca))),
        "total_ca_raw": total_ca,
        "total_assets": fmt(Decimal(str(total_assets))),
        "total_assets_raw": total_assets,
        "total_ncl": fmt(Decimal(str(total_ncl))),
        "total_ncl_raw": total_ncl,
        "total_cl": fmt(Decimal(str(total_cl))),
        "total_cl_raw": total_cl,
        "total_liabilities": fmt(Decimal(str(total_liabilities))),
        "total_liabilities_raw": total_liabilities,
        "total_equity": fmt(Decimal(str(total_equity))),
        "total_equity_raw": total_equity,
        "current_year_profit_raw": operating_profit,
        "current_year_profit": fmt(Decimal(str(operating_profit))),
        "total_equity_and_liabilities_raw": total_liabilities
        + total_equity
        + operating_profit,
        "total_equity_and_liabilities": fmt(
            Decimal(str(total_liabilities + total_equity + operating_profit))
        ),
        # Ratios
        "current_ratio": round(current_ratio, 2),
        "quick_ratio": round(quick_ratio, 2),
        "debt_to_equity": round(debt_to_equity, 2),
        "receivable_days": round(recv_days),
        "payable_days": round(payable_days),
        # Monthly data
        "monthly_data": monthly_data,
        # Cash Flow Statement
        "depreciation_charge": fmt(Decimal(str(depreciation_charge))),
        "depreciation_charge_raw": depreciation_charge,
        "chg_receivables": fmt(Decimal(str(chg_receivables))),
        "chg_receivables_raw": chg_receivables,
        "chg_inventory": fmt(Decimal(str(chg_inventory))),
        "chg_inventory_raw": chg_inventory,
        "chg_other_ca": fmt(Decimal(str(chg_other_ca))),
        "chg_other_ca_raw": chg_other_ca,
        "chg_payables": fmt(Decimal(str(chg_payables))),
        "chg_payables_raw": chg_payables,
        "chg_other_cl": fmt(Decimal(str(chg_other_cl))),
        "chg_other_cl_raw": chg_other_cl,
        "total_wc_changes": fmt(Decimal(str(total_wc_changes))),
        "total_wc_changes_raw": total_wc_changes,
        "cash_from_operations": fmt(Decimal(str(cash_from_operations))),
        "cash_from_operations_raw": cash_from_operations,
        "capex": fmt(Decimal(str(capex))),
        "capex_raw": capex,
        "cash_from_investing": fmt(Decimal(str(cash_from_investing))),
        "cash_from_investing_raw": cash_from_investing,
        "chg_ncl": fmt(Decimal(str(chg_ncl))),
        "chg_ncl_raw": chg_ncl,
        "chg_equity_movement": fmt(Decimal(str(chg_equity))),
        "chg_equity_movement_raw": chg_equity,
        "cash_from_financing": fmt(Decimal(str(cash_from_financing))),
        "cash_from_financing_raw": cash_from_financing,
        "net_cash_change": fmt(Decimal(str(net_cash_change))),
        "net_cash_change_raw": net_cash_change,
        "ob_cash": fmt(Decimal(str(ob_cash))),
        "ob_cash_raw": ob_cash,
        "closing_cash": fmt(Decimal(str(closing_cash))),
        "closing_cash_raw": closing_cash,
        "cash_total": fmt(Decimal(str(cash_total))),
        "cash_total_raw": cash_total,
        # Changes in Equity
        "opening_equity_total": fmt(Decimal(str(opening_equity_total))),
        "opening_equity_total_raw": opening_equity_total,
        "closing_equity_total": fmt(Decimal(str(closing_equity_total))),
        "closing_equity_total_raw": closing_equity_total,
        "ob_equity_items": ob_equity_items,
    }


def export_management_accounts_csv(
    organization_id: str,
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Export management accounts as CSV."""
    ctx = management_accounts_context(db, organization_id, start_date, end_date)

    headers = ["Section", "Account Code", "Account Name", "Amount"]
    rows: list[list[str]] = []

    # Revenue
    for item in ctx["revenue_items"]:
        rows.append(
            [
                "Revenue",
                item["account_code"],
                item["account_name"],
                str(item["amount_raw"]),
            ]
        )
    rows.append(["Revenue", "", "Total Revenue", str(ctx["total_revenue_raw"])])
    rows.append(["", "", "", ""])

    # Cost of Sales
    for item in ctx["cost_of_sales_items"]:
        rows.append(
            [
                "Cost of Sales",
                item["account_code"],
                item["account_name"],
                str(item["amount_raw"]),
            ]
        )
    rows.append(["Cost of Sales", "", "Total Cost of Sales", str(ctx["total_cos_raw"])])
    rows.append(["", "", "Gross Profit", str(ctx["gross_profit_raw"])])
    rows.append(["", "", "", ""])

    # Operating Expenses
    for item in ctx["opex_items"]:
        rows.append(
            [
                "Operating Expenses",
                item["account_code"],
                item["account_name"],
                str(item["amount_raw"]),
            ]
        )
    rows.append(
        [
            "Operating Expenses",
            "",
            "Total Operating Expenses",
            str(ctx["total_opex_raw"]),
        ]
    )
    rows.append(["", "", "Operating Profit", str(ctx["operating_profit_raw"])])
    rows.append(["", "", "", ""])

    # Balance Sheet
    for item in ctx["fa_items"]:
        rows.append(
            [
                "Non-Current Assets",
                item["account_code"],
                item["account_name"],
                str(item["amount_raw"]),
            ]
        )
    rows.append(["Non-Current Assets", "", "Total", str(ctx["total_nca_raw"])])
    for item in ctx["ca_items"]:
        rows.append(
            [
                "Current Assets",
                item["account_code"],
                item["account_name"],
                str(item["amount_raw"]),
            ]
        )
    rows.append(["Current Assets", "", "Total", str(ctx["total_ca_raw"])])
    rows.append(["", "", "Total Assets", str(ctx["total_assets_raw"])])

    return _build_csv(headers, rows)
