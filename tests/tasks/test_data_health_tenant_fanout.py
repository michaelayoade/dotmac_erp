"""The data-health jobs fan out over tenants — and the two that must not, don't.

``app/tasks/data_health.py`` carries ONE row in the cross-org caller inventory,
for one helper (``_task_session``), and that row hid nine entry points with two
different shapes.

Seven of them repair or count rows in tenant tables — invoices, journals,
notifications, payments, balances. Their unpinned branch opened a single
``cross_org_session``, which bypasses only ERP's SQLAlchemy listener and never
PostgreSQL RLS. Under ``app_user`` those jobs see zero rows: the repair tasks
repair nothing and the health check reports a clean fleet it cannot actually
see. Both look exactly like success.

The other two read ``event_outbox``, which has no ``organization_id`` column at
all — no RLS policy, no listener filter, nothing to bypass. Fanning THOSE out
would filter on a JSONB header and silently drop every event whose header is
missing or names an unknown organization: the precise events a stuck-event
recovery job exists to find.

What is asserted here:

1. each tenant is served by its own session, and per-tenant results are SUMMED
   rather than overwritten by the last tenant;
2. a ``batch_size``/``limit`` stays a fleet-wide cap instead of quietly
   becoming per-tenant;
3. the organization filter survives on statements RLS alone would have to
   scope — a ``DELETE`` is not a SELECT, so the ORM listener never touches it;
4. one tenant's failure does not abort the remaining tenants; and
5. the two outbox entry points are still fleet-wide reads.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.models.finance.ar.invoice import InvoiceStatus
from app.tasks import data_health

ORG_A = UUID("00000000-0000-0000-0000-0000000000a1")
ORG_B = UUID("00000000-0000-0000-0000-0000000000b2")


@pytest.fixture
def tenants(monkeypatch):
    """Patch the fan-out with two tenants, each with its own session."""
    opened: list[UUID] = []
    sessions: dict[UUID, MagicMock] = {
        ORG_A: MagicMock(name="db:org-a"),
        ORG_B: MagicMock(name="db:org-b"),
    }
    kwargs_seen: list[dict] = []

    def fake_for_each_organization(**kwargs):
        kwargs_seen.append(kwargs)
        for organization_id in (ORG_A, ORG_B):
            opened.append(organization_id)
            yield organization_id, sessions[organization_id]

    monkeypatch.setattr(
        data_health, "for_each_organization", fake_for_each_organization
    )
    return opened, sessions, kwargs_seen


# ── discovery contract ───────────────────────────────────────


def test_the_fan_out_asks_the_catalogue_for_inactive_tenants_too(tenants):
    """A deactivated tenant's bad data is still bad data.

    The scan this replaced never looked at organization status, so filtering to
    active tenants would quietly stop repairing (and stop reporting) a whole
    class of rows.
    """
    _opened, sessions, kwargs_seen = tenants
    for session in sessions.values():
        session.scalars.return_value.all.return_value = []

    data_health.reconcile_invoice_statuses()

    assert kwargs_seen == [{"include_inactive": True, "only": None}]


def test_a_pinned_organization_is_narrowed_through_the_catalogue(tenants):
    """``organization_id`` goes through ``only=``, not an unchecked session.

    An id that is not in the catalogue now yields an empty run rather than an
    unscoped one.
    """
    _opened, sessions, kwargs_seen = tenants
    for session in sessions.values():
        session.scalars.return_value.all.return_value = []

    data_health.reconcile_invoice_statuses(organization_id=str(ORG_A))

    assert kwargs_seen == [{"include_inactive": True, "only": ORG_A}]


# ── per-tenant sessions and summed results ───────────────────


def _false_paid_invoice(number: str) -> MagicMock:
    """PAID on paper, half paid in fact — what the reconciler exists to fix."""
    invoice = MagicMock()
    invoice.invoice_number = number
    invoice.total_amount = Decimal("1000.00")
    invoice.amount_paid = Decimal("500.00")
    invoice.status = InvoiceStatus.PAID
    invoice.due_date = None
    return invoice


def test_each_tenants_invoices_are_repaired_in_that_tenants_session(tenants):
    """Isolation: org A's invoice is never touched through org B's session."""
    opened, sessions, _kwargs = tenants
    seen: list[tuple[object, str]] = []

    for org, number in ((ORG_A, "INV-A"), (ORG_B, "INV-B")):
        invoice = _false_paid_invoice(number)
        session = sessions[org]

        def rows(_session=session, _invoice=invoice):
            seen.append((_session, _invoice.invoice_number))
            return [_invoice]

        session.scalars.return_value.all.side_effect = rows

    result = data_health.reconcile_invoice_statuses()

    assert opened == [ORG_A, ORG_B]
    assert seen == [(sessions[ORG_A], "INV-A"), (sessions[ORG_B], "INV-B")]
    assert result["fixed_to_partially_paid"] == 2, (
        "per-tenant counts must accumulate; assigning instead of adding would "
        "report only the last tenant's repairs"
    )


def test_draft_counts_are_summed_across_tenants_not_overwritten(tenants):
    """Three counters, two tenants, one total each."""
    _opened, sessions, _kwargs = tenants
    sessions[ORG_A].scalar.side_effect = [3, 5, 2]
    sessions[ORG_B].scalar.side_effect = [1, 1, 1]

    result = data_health.cleanup_stale_drafts(dry_run=True)

    assert result["journal_drafts"] == 4
    assert result["invoice_drafts"] == 6
    assert result["ap_invoice_drafts"] == 3


def test_notification_cleanup_totals_every_tenants_deletions(tenants):
    """Deleted rowcounts add up across tenants."""
    _opened, sessions, _kwargs = tenants
    sessions[ORG_A].execute.side_effect = [MagicMock(rowcount=5), MagicMock(rowcount=2)]
    sessions[ORG_B].execute.side_effect = [MagicMock(rowcount=1), MagicMock(rowcount=0)]

    result = data_health.cleanup_old_notifications()

    assert result["read_deleted"] == 6
    assert result["unread_deleted"] == 2
    assert result["errors"] == []


def test_the_notification_delete_still_names_its_organization(tenants):
    """The org filter on the DELETE is load-bearing, not decoration.

    ERP's ORM listener injects its filter on SELECTs only — it explicitly
    passes on anything else. So inside a tenant session a ``DELETE`` is scoped
    by PostgreSQL RLS and by nothing else, and by nothing at all on SQLite.
    Dropping the explicit filter here would purge every tenant's
    notifications, once per tenant.
    """
    _opened, sessions, _kwargs = tenants
    for session in sessions.values():
        session.execute.return_value = MagicMock(rowcount=0)

    data_health.cleanup_old_notifications()

    statements = [call.args[0] for call in sessions[ORG_A].execute.call_args_list]
    assert statements, "the cleanup issued no DELETE at all"
    for statement in statements:
        compiled = statement.compile()
        assert "organization_id" in str(compiled)
        assert ORG_A in compiled.params.values(), (
            "the DELETE must be bound to the session's own organization"
        )


def test_one_tenants_failure_does_not_abort_the_others(tenants):
    """A broken tenant is rolled back, recorded, and stepped over."""
    _opened, sessions, _kwargs = tenants
    sessions[ORG_A].execute.side_effect = RuntimeError("DB error")
    sessions[ORG_B].execute.side_effect = [
        MagicMock(rowcount=4),
        MagicMock(rowcount=0),
    ]

    result = data_health.cleanup_old_notifications()

    assert result["read_deleted"] == 4, "org B is still cleaned up"
    assert len(result["errors"]) == 1
    assert "DB error" in result["errors"][0]
    sessions[ORG_A].rollback.assert_called_once()


# ── fleet-wide batch budgets ─────────────────────────────────


def test_the_unbalanced_journal_batch_is_a_fleet_wide_cap(tenants):
    """``batch_size`` bounds the run, not each tenant.

    A per-tenant limit multiplies the work by the number of organizations,
    which is the opposite of what a maintenance cap is for.
    """
    _opened, sessions, _kwargs = tenants
    row = MagicMock()
    row.imbalance = 1
    row.total_debit = 10
    row.total_credit = 9
    sessions[ORG_A].execute.return_value.all.return_value = [row, row]

    result = data_health.fix_unbalanced_posted_journals(dry_run=True, batch_size=2)

    assert result["found"] == 2
    sessions[ORG_B].execute.assert_not_called()


def test_the_payment_allocation_batch_is_a_fleet_wide_cap(tenants):
    """The same cap, on the task whose inner loop owns its own ``remaining``.

    The per-payment loop tracks the unallocated portion of ONE payment under
    that name; the fleet budget is a separate variable, and this is what fails
    if the two are ever merged.
    """
    _opened, sessions, _kwargs = tenants
    payment = MagicMock()
    payment.amount = 100
    payment.payment_date = None
    sessions[ORG_A].scalars.return_value.all.side_effect = [[payment], []]

    data_health.reconcile_payment_allocations(batch_size=1, dry_run=True)

    sessions[ORG_B].scalars.assert_not_called()


def test_auto_post_without_a_batch_size_visits_every_tenant(tenants):
    """``batch_size=None`` means no cap — and no accidental early break."""
    opened, sessions, _kwargs = tenants
    for session in sessions.values():
        session.scalars.return_value.all.return_value = []

    data_health.auto_post_approved_invoices()

    assert opened == [ORG_A, ORG_B]


# ── the outbox is deliberately NOT fanned out ────────────────


@pytest.fixture
def outbox_session(monkeypatch):
    """Stand up the fleet-wide outbox session and forbid the fan-out."""
    db = MagicMock(name="db:outbox")
    session = MagicMock()
    session.return_value.__enter__ = MagicMock(return_value=db)
    session.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(data_health, "cross_org_session", session)

    def forbidden(**_kwargs):
        raise AssertionError(
            "the outbox entry points must not fan out over tenants: "
            "event_outbox has no organization_id column, so a per-tenant "
            "filter would hide every event with a missing or unknown header"
        )
        yield  # pragma: no cover - never reached

    monkeypatch.setattr(data_health, "for_each_organization", forbidden)
    return db


def test_the_stuck_event_sweep_reads_the_whole_outbox(outbox_session):
    """One fleet-wide pass, not one pass per tenant."""
    event = MagicMock()
    event.retry_count = 0
    outbox_session.scalars.return_value.all.return_value = [event]

    result = data_health.process_stuck_outbox_events()

    assert result["recovered"] == 1


def test_the_health_check_counts_the_outbox_fleet_wide(monkeypatch):
    """Its seven tenant counters fan out; its two outbox counters do not."""
    tenant_db = MagicMock(name="db:tenant")
    tenant_db.scalar.return_value = 0

    def one_tenant(**_kwargs):
        yield ORG_A, tenant_db

    outbox_db = MagicMock(name="db:outbox")
    outbox_db.scalar.side_effect = [7, 3]
    session = MagicMock()
    session.return_value.__enter__ = MagicMock(return_value=outbox_db)
    session.return_value.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(data_health, "for_each_organization", one_tenant)
    monkeypatch.setattr(data_health, "cross_org_session", session)

    result = data_health.run_data_health_check()

    assert result["stuck_outbox_events"] == 7
    assert result["dead_outbox_events"] == 3


def test_the_health_check_answers_every_counter_for_an_empty_fleet(monkeypatch):
    """No organizations must not read as "these checks did not run".

    A dict built key-by-key inside the loop returns nothing at all when the
    catalogue is empty — indistinguishable, to a caller, from a crash.
    """

    def no_tenants(**_kwargs):
        return iter(())

    outbox_db = MagicMock(name="db:outbox")
    outbox_db.scalar.return_value = 0
    session = MagicMock()
    session.return_value.__enter__ = MagicMock(return_value=outbox_db)
    session.return_value.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(data_health, "for_each_organization", no_tenants)
    monkeypatch.setattr(data_health, "cross_org_session", session)

    result = data_health.run_data_health_check()

    assert result == {
        "unbalanced_journals": 0,
        "false_paid_invoices": 0,
        "stuck_outbox_events": 0,
        "dead_outbox_events": 0,
        "stale_journal_drafts": 0,
        "account_balance_rows": 0,
        "notification_total": 0,
        "notification_unread": 0,
        "approved_invoices_stuck": 0,
        "unallocated_payments": 0,
    }


# ── the retired seam ─────────────────────────────────────────


def test_the_retired_helper_is_gone_and_the_fan_out_is_imported() -> None:
    """``_task_session`` was the one seam; it must not survive alongside it.

    Leaving it in place is how a tenth entry point quietly gets added on the
    old contract.
    """
    assert not hasattr(data_health, "_task_session")
    assert hasattr(data_health, "_tenant_sessions")
    assert hasattr(data_health, "_outbox_session")


@pytest.mark.parametrize(
    "task",
    [
        data_health.cleanup_old_notifications,
        data_health.reconcile_invoice_statuses,
        data_health.auto_post_approved_invoices,
        data_health.cleanup_stale_drafts,
        data_health.rebuild_account_balances,
        data_health.reconcile_payment_allocations,
        data_health.fix_unbalanced_posted_journals,
    ],
    ids=lambda task: task.name.rsplit(".", 1)[-1],
)
def test_every_converted_task_goes_through_the_fan_out(task, monkeypatch):
    """Each of the seven reaches the database only via a tenant session.

    Parametrised over the whole family on purpose: the single inventory row
    for this module is exactly how six of these went unnoticed behind the
    seventh.
    """
    called = False

    def fan_out(**_kwargs):
        nonlocal called
        called = True
        return iter(())

    def forbidden():
        raise AssertionError(f"{task.name} still opens a cross-org session")

    monkeypatch.setattr(data_health, "for_each_organization", fan_out)
    monkeypatch.setattr(data_health, "cross_org_session", forbidden)

    task()

    assert called
