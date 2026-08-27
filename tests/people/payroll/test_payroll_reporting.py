from __future__ import annotations

from decimal import Decimal

from app.services.people.payroll_reporting import calculate_active_monthly_net_average


def test_active_monthly_net_average_excludes_zero_slip_months() -> None:
    months = [
        {"month_label": "Sep 2025", "slip_count": 0, "total_net": Decimal("0")},
        {"month_label": "Oct 2025", "slip_count": 0, "total_net": Decimal("0")},
        {"month_label": "Nov 2025", "slip_count": 0, "total_net": Decimal("0")},
        {"month_label": "Dec 2025", "slip_count": 0, "total_net": Decimal("0")},
        {
            "month_label": "Jan 2026",
            "slip_count": 135,
            "total_net": Decimal("16607638.00"),
        },
        {
            "month_label": "Feb 2026",
            "slip_count": 138,
            "total_net": Decimal("16858067.05"),
        },
        {
            "month_label": "Mar 2026",
            "slip_count": 142,
            "total_net": Decimal("17930052.05"),
        },
        {
            "month_label": "Apr 2026",
            "slip_count": 153,
            "total_net": Decimal("19030783.79"),
        },
        {
            "month_label": "May 2026",
            "slip_count": 152,
            "total_net": Decimal("19886827.15"),
        },
        {
            "month_label": "Jun 2026",
            "slip_count": 169,
            "total_net": Decimal("20621225.00"),
        },
        {
            "month_label": "Jul 2026",
            "slip_count": 171,
            "total_net": Decimal("21122891.05"),
        },
        {"month_label": "Aug 2026", "slip_count": 0, "total_net": Decimal("0")},
    ]

    assert calculate_active_monthly_net_average(months) == Decimal("18865354.87")


def test_active_monthly_net_average_returns_zero_when_no_payroll_ran() -> None:
    months = [
        {"month_label": "Sep 2025", "slip_count": 0, "total_net": Decimal("0")},
        {"month_label": "Oct 2025", "slip_count": 0, "total_net": Decimal("0")},
    ]

    assert calculate_active_monthly_net_average(months) == Decimal("0")
