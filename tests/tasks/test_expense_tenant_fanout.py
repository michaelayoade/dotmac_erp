"""The expense sweeps fan out over tenants instead of scanning across them.

Both jobs had the same shape: one ``cross_org_session`` read every tenant's rows
at once, a dict grouped the ids by organization, and a second SELECT re-fetched
each group inside a tenant session. ``cross_org_session`` lifts only the
SQLAlchemy listener and never PostgreSQL RLS, so under ``app_user`` the first
read returns **zero rows** — no approval reminder is ever sent, no stale payout
intent ever expires, no stuck transfer is ever polled, and both jobs report a
clean run every time they fire.

The grouping dicts and the id re-fetches are deleted rather than adapted: the
same predicates evaluated inside a tenant session already return only that
tenant's rows, which is the point of the session.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.tasks import expense

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")


@pytest.fixture
def tenant_sessions(monkeypatch):
    """Two organizations. Each session open is recorded and scripted.

    ``rows`` is consumed one list per session open, in order — which is how a
    test says "this tenant's query returns these rows" for a job that opens more
    than one session per tenant.
    """
    opened: list[uuid.UUID] = []
    sessions: list[MagicMock] = []
    scripted: list[list[object]] = []

    @contextmanager
    def session_for_org(organization_id):
        db = MagicMock(name=f"db:{organization_id}")
        db.scalars.return_value.all.return_value = scripted.pop(0) if scripted else []
        opened.append(organization_id)
        sessions.append(db)
        yield db

    def enumerate_ids(**kwargs):
        enumerate_ids.kwargs = kwargs
        return [ORG_A, ORG_B]

    enumerate_ids.kwargs = {}

    monkeypatch.setattr(expense, "session_for_org", session_for_org)
    monkeypatch.setattr(expense, "organization_ids", enumerate_ids)
    return SimpleNamespace(
        opened=opened, sessions=sessions, script=scripted, enumerate_ids=enumerate_ids
    )


def _claim(number: str, *, days_pending: int = 0) -> SimpleNamespace:
    """A submitted claim, pending for ``days_pending`` days.

    Default 0: too young for any reminder, so the job walks the tenants and
    stops at the age guard — which is all most of these tests need.
    """
    today = date.today()
    pending_since = today - timedelta(days=days_pending)
    return SimpleNamespace(
        claim_id=uuid.uuid4(),
        claim_number=number,
        organization_id=uuid.uuid4(),
        claim_date=pending_since,
        updated_at=datetime.combine(pending_since, datetime.min.time()),
        approver_id=None,
        employee=SimpleNamespace(
            employee_id=uuid.uuid4(),
            expense_approver_id=uuid.uuid4(),
        ),
    )


# ── process_expense_approval_reminders ───────────────────────────────


def test_pending_claims_are_read_inside_each_tenants_session(tenant_sessions) -> None:
    """The notification service is built from the tenant session, per tenant."""
    built_from: list[object] = []

    def service_factory(db):
        built_from.append(db)
        return MagicMock()

    with patch(
        "app.services.expense.expense_notifications.ExpenseNotificationService",
        side_effect=service_factory,
    ):
        expense.process_expense_approval_reminders()

    assert tenant_sessions.opened == [ORG_A, ORG_B]
    assert built_from == tenant_sessions.sessions, (
        "the service must be built from the tenant-scoped session; building it "
        "from anything else is how the cross-tenant scan came back"
    )


def test_a_tenants_claims_are_only_seen_in_that_tenants_session(
    tenant_sessions,
) -> None:
    """Isolation: org A's claim never reaches org B's session.

    The old code fetched both tenants' claims in one bypassed session and relied
    on correct grouping to keep them apart. Here the session keeps them apart,
    and this asserts the code does not undo that.
    """
    claim_a = _claim("EXP-A-1", days_pending=7)
    claim_b = _claim("EXP-B-1", days_pending=7)
    tenant_sessions.script.extend([[claim_a], [claim_b]])
    reminded: list[tuple[object, str]] = []

    def service_factory(db):
        service = MagicMock()
        service.send_pending_approval_reminder.side_effect = (
            lambda claim, approver, **_: reminded.append((db, claim.claim_number))
            or True
        )
        return service

    with (
        patch(
            "app.services.expense.expense_notifications.ExpenseNotificationService",
            side_effect=service_factory,
        ),
        patch.object(expense, "NotificationService") as notifications,
    ):
        notifications.return_value.was_sent_since.return_value = False
        expense.process_expense_approval_reminders()

    db_a, db_b = tenant_sessions.sessions
    assert tenant_sessions.opened == [ORG_A, ORG_B]
    assert reminded == [(db_a, "EXP-A-1"), (db_b, "EXP-B-1")], (
        "each claim must be reminded through the session opened for its own "
        "tenant — the isolation the grouping dict used to maintain by hand"
    )


def test_approval_reminders_reach_deactivated_tenants(tenant_sessions) -> None:
    """The scan this replaced had no ``Organization`` predicate."""
    with patch(
        "app.services.expense.expense_notifications.ExpenseNotificationService",
        return_value=MagicMock(),
    ):
        expense.process_expense_approval_reminders()

    assert tenant_sessions.enumerate_ids.kwargs == {"include_inactive": True}


# ── poll_stuck_expense_transfers ─────────────────────────────────────


def _intent(status) -> SimpleNamespace:
    return SimpleNamespace(
        intent_id=uuid.uuid4(),
        status=status,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        poll_count=0,
        last_poll_error=None,
        gateway_response=None,
    )


def test_stale_payout_intents_expire_in_their_own_tenants_session(
    tenant_sessions,
) -> None:
    """Phase one visits every tenant, expiring only that tenant's intents."""
    from app.models.finance.payments.payment_intent import PaymentIntentStatus

    stale_a = _intent(PaymentIntentStatus.PENDING)
    # Phase 1 opens a session per tenant, then phase 2 opens another per tenant.
    tenant_sessions.script.extend([[stale_a], [], [], []])

    result = expense.poll_stuck_expense_transfers()

    assert tenant_sessions.opened == [ORG_A, ORG_B, ORG_A, ORG_B]
    assert stale_a.status == PaymentIntentStatus.EXPIRED
    assert result["expired"] == 1
    # The stale sweep commits per tenant; the tenant with nothing still commits
    # its empty transaction, and neither phase-2 session did any work.
    assert tenant_sessions.sessions[0].commit.call_count == 1
    assert tenant_sessions.sessions[2].commit.call_count == 0


def test_stuck_transfers_are_polled_with_their_own_tenants_config(
    tenant_sessions,
) -> None:
    """Phase two reads the Paystack keys and the intents from one session.

    Keys are ``SettingDomain.payments`` values resolved through the session, so
    a tenant's transfer can only ever be polled with that tenant's credentials.
    """
    from app.models.finance.payments.payment_intent import PaymentIntentStatus

    stuck_a = _intent(PaymentIntentStatus.PROCESSING)
    tenant_sessions.script.extend([[], [], [stuck_a], []])
    polled: list[tuple[object, object]] = []

    def service_factory(db, org_id):
        service = MagicMock()
        service.poll_transfer_status.side_effect = lambda intent, config: polled.append(
            (db, org_id)
        )
        return service

    with (
        patch(
            "app.services.settings_spec.resolve_value",
            side_effect=lambda db, domain, key: f"value-for-{key}",
        ),
        patch(
            "app.services.finance.payments.payment_service.PaymentService",
            side_effect=service_factory,
        ),
    ):
        result = expense.poll_stuck_expense_transfers()

    # Session index 2 is org A's phase-two session.
    assert polled == [(tenant_sessions.sessions[2], ORG_A)]
    assert result["intents_checked"] == 1
    assert result["still_pending"] == 1
    assert stuck_a.poll_count == 1


def test_a_tenant_without_stuck_transfers_is_not_asked_for_paystack_keys(
    tenant_sessions,
) -> None:
    """Skipping before the config read keeps the warning meaningful.

    The old grouping only ever built a session for a tenant that had a stuck
    intent, so "No Paystack keys for org X" meant a transfer was actually
    waiting on those keys. Enumerating every tenant would turn that into noise
    for every unconfigured tenant on the fleet if the order were reversed.
    """
    with patch("app.services.settings_spec.resolve_value") as resolve:
        result = expense.poll_stuck_expense_transfers()

    resolve.assert_not_called()
    assert result["intents_checked"] == 0


def test_transfer_polling_reaches_deactivated_tenants(tenant_sessions) -> None:
    """Neither scan had an ``Organization`` predicate, and an in-flight payout
    for a deactivated tenant still has to settle."""
    expense.poll_stuck_expense_transfers()

    assert tenant_sessions.enumerate_ids.kwargs == {"include_inactive": True}


# ── The retired seam ─────────────────────────────────────────────────


def test_the_module_no_longer_reaches_across_tenants() -> None:
    """The bypass is gone from this module, not merely unused.

    ``cross_org_session`` and the two id-grouping dicts it fed were the whole
    cross-tenant path here, and the architecture guard only sees the catalogue
    enumeration shape, not this one.
    """
    source = (expense.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    assert "cross_org_session(" not in text
    assert "import cross_org_session" not in text
    assert "claims_by_org" not in text
    assert "stale_by_org" not in text
    assert "stuck_intent_meta" not in text
    assert "organization_ids(include_inactive=True)" in text
