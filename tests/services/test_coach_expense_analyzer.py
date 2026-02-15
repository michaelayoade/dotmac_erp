from __future__ import annotations

from app.services.coach.analyzers.expense import _severity_for_pending_approvals


def test_severity_for_pending_approvals():
    assert _severity_for_pending_approvals(0, 0) == "INFO"
    assert _severity_for_pending_approvals(1, 1) == "ATTENTION"
    assert _severity_for_pending_approvals(1, 14) == "WARNING"
