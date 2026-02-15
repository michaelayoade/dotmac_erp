from __future__ import annotations

from decimal import Decimal

from app.services.coach.analyzers.ap_due import _severity_for_payables


def test_severity_for_payables():
    assert _severity_for_payables(0, Decimal("0")) == "ATTENTION"
    assert _severity_for_payables(1, Decimal("1")) == "ATTENTION"
    assert _severity_for_payables(20, Decimal("1")) == "WARNING"
    assert _severity_for_payables(1, Decimal("10000000")) == "WARNING"
