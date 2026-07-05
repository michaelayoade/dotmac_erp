"""P3: ApiKey.last_used_at write in require_service_auth is throttled.

Refreshing it on every CRM service call added a row UPDATE + commit to hot read
paths (per-project expense-totals) and contended on one key. It now writes at
most once per window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.api.sync.dotmac_crm import _LAST_USED_THROTTLE, _last_used_is_stale

UTC = timezone.utc


def test_unset_last_used_is_stale():
    assert _last_used_is_stale(None, datetime.now(UTC)) is True


def test_recent_last_used_is_not_stale():
    now = datetime.now(UTC)
    recent = now - (_LAST_USED_THROTTLE / 2)
    assert _last_used_is_stale(recent, now) is False


def test_old_last_used_is_stale():
    now = datetime.now(UTC)
    old = now - (_LAST_USED_THROTTLE + timedelta(seconds=1))
    assert _last_used_is_stale(old, now) is True


def test_naive_stored_value_is_treated_as_stale():
    # A naive datetime can't be subtracted from an aware `now`; refresh it.
    now = datetime.now(UTC)
    naive = datetime(2026, 1, 1, 0, 0, 0)  # no tzinfo
    assert _last_used_is_stale(naive, now) is True
