"""The platform and operations jobs fan out over tenants instead of scanning across them.

Three entry points shared one broken shape: open a single ``cross_org_session``,
read an RLS-protected table for the whole fleet, and process what came back.

``cross_org_session`` bypasses only ERP's SQLAlchemy listener, never PostgreSQL
RLS. Once the runtime login stops being the ``postgres`` superuser, each of
those scans returns **zero rows** — and each job then reports success having
done nothing: no infrastructure alert reaches an operator, no stuck sync row is
unstuck, no exited employee is offboarded.

What is asserted here:

1. discovery goes through the tenant catalogue, not a cross-org scan;
2. each tenant's work happens inside that tenant's own session; and
3. the fleet-wide properties the single-session version got for free — one
   shared ``limit`` budget, and one tenant's failure not taking the others
   down — still hold now that there are many sessions.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.db import session_context
from app.services import infrastructure_health as infra
from app.tasks import dotmac_sub
from scripts import backfill_mailcow_offboarding as mailcow

ORG_A = UUID("00000000-0000-0000-0000-0000000000a1")
ORG_B = UUID("00000000-0000-0000-0000-0000000000b2")


@pytest.fixture
def tenant_sessions():
    """Build a fake ``for_each_organization`` plus the sessions it hands out.

    Returns ``(install, opened, sessions, kwargs_seen)``:
    ``install(monkeypatch, module)`` patches the module's imported name,
    ``opened`` records the organizations yielded in order, ``sessions`` is the
    per-organization session a test can assert against by name, and
    ``kwargs_seen`` records how each caller asked for the enumeration.
    """
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

    def install(monkeypatch, module):
        monkeypatch.setattr(module, "for_each_organization", fake_for_each_organization)

    return install, opened, sessions, kwargs_seen


def _stale_row(started_at) -> MagicMock:
    row = MagicMock()
    row.started_at = started_at
    return row


# ── dotmac_sub stale sync-history sweep ──────────────────────


def test_the_stale_sweep_visits_every_tenant_in_its_own_session(
    tenant_sessions, monkeypatch
):
    """Every organization is swept, and each query runs on that org's session."""
    install, opened, sessions, kwargs_seen = tenant_sessions
    install(monkeypatch, dotmac_sub)

    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for session in sessions.values():
        session.scalars.return_value.all.return_value = [_stale_row(started)]

    result = dotmac_sub.cleanup_stale_dotmac_sub_sync_history(limit=10)

    assert opened == [ORG_A, ORG_B], "every organization gets its own session"
    assert result == {"success": True, "checked": 2, "marked_failed": 2}
    for session in sessions.values():
        session.scalars.assert_called_once()
        session.commit.assert_called_once()


def test_the_stale_sweep_enumerates_inactive_organizations_too(
    tenant_sessions, monkeypatch
):
    """A deactivated tenant's stuck RUNNING rows still have to be closed out.

    The scan this replaced never looked at organization status, so narrowing
    the sweep to active tenants would quietly leave rows stuck forever.
    """
    install, _opened, sessions, kwargs_seen = tenant_sessions
    install(monkeypatch, dotmac_sub)
    for session in sessions.values():
        session.scalars.return_value.all.return_value = []

    dotmac_sub.cleanup_stale_dotmac_sub_sync_history()

    assert kwargs_seen == [{"include_inactive": True}]


def test_the_stale_sweep_limit_stays_a_fleet_wide_cap(tenant_sessions, monkeypatch):
    """``limit`` bounds the whole run, not each tenant.

    A per-tenant limit would silently multiply the batch by the number of
    organizations — the opposite of what a maintenance cap is for.
    """
    install, _opened, sessions, _kwargs = tenant_sessions
    install(monkeypatch, dotmac_sub)

    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sessions[ORG_A].scalars.return_value.all.return_value = [
        _stale_row(started) for _ in range(3)
    ]

    result = dotmac_sub.cleanup_stale_dotmac_sub_sync_history(limit=3)

    assert result["marked_failed"] == 3
    # The budget was spent on the first organization, so the second must not be
    # queried at all — a per-tenant limit would have marked three more rows.
    sessions[ORG_B].scalars.assert_not_called()


def test_the_stale_sweep_dry_run_commits_nothing(tenant_sessions, monkeypatch):
    """Dry run reports what it would mark and commits no tenant session.

    The old code called ``db.rollback()``; now each tenant session is simply
    closed without a commit, which discards the same work.
    """
    install, _opened, sessions, _kwargs = tenant_sessions
    install(monkeypatch, dotmac_sub)

    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sessions[ORG_A].scalars.return_value.all.return_value = [_stale_row(started)]
    sessions[ORG_B].scalars.return_value.all.return_value = []

    result = dotmac_sub.cleanup_stale_dotmac_sub_sync_history(dry_run=True)

    assert result == {"success": True, "checked": 1, "would_mark": 1}
    for session in sessions.values():
        session.commit.assert_not_called()


def test_an_empty_fleet_sweep_keeps_its_original_response_shape(
    tenant_sessions, monkeypatch
):
    """Nothing stale anywhere still answers ``marked_failed``, even in dry run."""
    install, _opened, sessions, _kwargs = tenant_sessions
    install(monkeypatch, dotmac_sub)
    for session in sessions.values():
        session.scalars.return_value.all.return_value = []

    assert dotmac_sub.cleanup_stale_dotmac_sub_sync_history(dry_run=True) == {
        "success": True,
        "checked": 0,
        "marked_failed": 0,
    }


# ── infrastructure alert delivery ────────────────────────────


def test_infrastructure_alerts_are_delivered_once_per_tenant(
    tenant_sessions, monkeypatch
):
    """The checks run fleet-wide; the notifications are written per tenant.

    ``infrastructure_alert`` and ``infrastructure_health_status`` carry no
    ``organization_id``, but their recipients (``Person``) and the
    notifications themselves are tenant rows — so delivery, and only delivery,
    fans out.
    """
    install, opened, sessions, kwargs_seen = tenant_sessions
    install(monkeypatch, infra)
    delivered_on: list[object] = []

    def deliver(db, events):
        delivered_on.append(db)
        return 1

    monkeypatch.setattr(
        infra.infrastructure_health_service, "deliver_notifications", deliver
    )

    total = infra._deliver_alerts_to_every_tenant([object()])

    assert total == 2
    assert opened == [ORG_A, ORG_B]
    assert delivered_on == [sessions[ORG_A], sessions[ORG_B]], (
        "each tenant's notifications must be written through that tenant's "
        "session — writing them all through one session is what RLS rejects"
    )
    assert kwargs_seen == [{"include_inactive": True}]


def test_one_tenants_delivery_failure_does_not_stop_the_others(
    tenant_sessions, monkeypatch
):
    """A failing tenant is rolled back and skipped; the rest are still notified.

    The single-session version turned one failure into zero notifications for
    the entire fleet.
    """
    install, _opened, sessions, _kwargs = tenant_sessions
    install(monkeypatch, infra)

    def deliver(db, events):
        if db is sessions[ORG_A]:
            raise RuntimeError("notification store unavailable")
        return 2

    monkeypatch.setattr(
        infra.infrastructure_health_service, "deliver_notifications", deliver
    )

    total = infra._deliver_alerts_to_every_tenant([object()])

    assert total == 2
    sessions[ORG_A].rollback.assert_called_once()
    sessions[ORG_B].commit.assert_called_once()


# ── mailcow offboarding backfill ─────────────────────────────


def test_the_mailcow_backfill_enumerates_through_the_catalogue(monkeypatch):
    """Org discovery is a catalogue call, not a DISTINCT over an RLS table."""
    calls: list[dict] = []

    def fake_organization_ids(**kwargs):
        calls.append(kwargs)
        return [ORG_A, ORG_B]

    monkeypatch.setattr(mailcow, "organization_ids", fake_organization_ids)

    assert mailcow._list_org_ids(None) == [ORG_A, ORG_B]
    assert calls == [{"include_inactive": True, "only": None}]


def test_the_mailcow_backfill_pins_one_organization_through_the_catalogue(monkeypatch):
    """``--organization-id`` narrows the catalogue rather than skipping it.

    Taking the argument on trust would let a typo run the backfill against an
    organization that does not exist; ``only=`` makes that an empty run.
    """
    calls: list[dict] = []

    def fake_organization_ids(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(mailcow, "organization_ids", fake_organization_ids)

    assert mailcow._list_org_ids(str(ORG_A)) == []
    assert calls == [{"include_inactive": True, "only": ORG_A}]


# ── the retired seam is gone ─────────────────────────────────


RETIRED_SEAMS = frozenset({"cross_org_session", "allow_cross_org"})


def _code_names(source_path: str) -> set[str]:
    """Names the module's CODE binds or references — defs, imports, attributes.

    Deliberately an AST walk rather than a substring search: these files now
    explain in their docstrings what they used to do and why it was wrong, and
    a substring check would make that explanation unwritable.
    """
    with open(source_path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[-1])
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
    return names


@pytest.mark.parametrize(
    "module",
    [dotmac_sub, infra, mailcow],
    ids=["dotmac_sub", "infrastructure_health", "backfill_mailcow_offboarding"],
)
def test_these_modules_no_longer_reach_across_tenants(module) -> None:
    """Re-importing a retired seam is the regression this slice prevents.

    The architecture guard reads a dispositioned inventory; it does not know
    that these three files have been converted. This does.
    """
    referenced = _code_names((module.__file__ or "").replace(".pyc", ".py"))

    assert not (referenced & RETIRED_SEAMS)


def test_the_retired_seam_check_still_bites() -> None:
    """A check over an empty set passes for the wrong reason.

    ``app/db/session_context.py`` defines both seams, so it must fail the same
    detector the three converted modules pass.
    """
    referenced = _code_names(session_context.__file__ or "")

    assert referenced & RETIRED_SEAMS == RETIRED_SEAMS
