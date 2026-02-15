from __future__ import annotations

from datetime import date, timedelta

from app.services.coach.analyzers.banking import _days_stale, _severity_for_bank_recon


def test_days_stale_never_reconciled_sorts_high():
    today = date.today()
    assert _days_stale(None, today) > 365


def test_days_stale_non_negative():
    today = date.today()
    assert _days_stale(today + timedelta(days=1), today) == 0


def test_severity_for_bank_recon():
    assert _severity_for_bank_recon(0, 0) == "INFO"
    assert _severity_for_bank_recon(1, 20) == "ATTENTION"
    assert _severity_for_bank_recon(1, 30) == "WARNING"
