from __future__ import annotations

from decimal import Decimal

from app.services.coach.analyzers.ar_overdue import _severity_for_overdue_receivables


def test_severity_for_overdue_receivables():
    assert _severity_for_overdue_receivables(0, Decimal("0"), 0) == "INFO"
    assert _severity_for_overdue_receivables(1, Decimal("1"), 10) == "ATTENTION"
    assert _severity_for_overdue_receivables(1, Decimal("1"), 60) == "WARNING"
    assert _severity_for_overdue_receivables(1, Decimal("10000000"), 10) == "WARNING"
