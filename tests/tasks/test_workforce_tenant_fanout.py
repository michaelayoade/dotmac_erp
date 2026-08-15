"""The workforce batch jobs fan out over tenants instead of scanning across them.

Same failure mode as the discipline slice (see
``test_discipline_tenant_fanout.py``): each of these jobs opened one
``cross_org_session``, read a domain table across every tenant to learn which
organizations had due work, and only then opened a tenant session per
organization. ``cross_org_session`` bypasses the SQLAlchemy listener and nothing
else, so under ``app_user`` that first read returns **zero rows** — no
organizations, no work, exit 0.

Three things are asserted here, per job:

1. discovery goes through :mod:`app.tenant_catalog`, not a cross-tenant read;
2. every domain query runs inside a tenant-scoped session; and
3. one tenant's rows are never handled in another tenant's session.

Plus one property that is easy to get wrong in exactly this conversion: the
retired scans enumerated *domain rows*, which carry no organization-active
predicate, so they included deactivated organizations. Every converted job
therefore enumerates with ``include_inactive=True``. Swapping in
``active_organization_ids()`` would look tidier and would silently stop
processing a deactivated tenant's work — a product decision wearing a
refactor's clothes. ``test_every_converted_job_still_includes_inactive_tenants``
is what stops that.

Deliberately NOT covered: ``automation._resolve_workflow_rule_org`` and
``performance``'s four ``cycle_id``-driven tasks. Those resolve which tenant
owns one named row rather than enumerating tenants, the catalogue definer
returns identifiers only and cannot answer that, and they are held back for
reclassification. ``test_the_resolution_seams_are_held_back_not_converted``
pins that they were left alone on purpose rather than missed.
"""

from __future__ import annotations

import ast
import inspect
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from app.tasks import automation, fleet
from app.tasks import license as license_task
from app.tasks import performance, project_sla

ORG_A = UUID("00000000-0000-0000-0000-0000000000a1")
ORG_B = UUID("00000000-0000-0000-0000-0000000000b2")

#: Every module in this slice that now enumerates tenants for itself.
CONVERTED_MODULES = (automation, fleet, license_task, performance, project_sla)


@pytest.fixture
def tenant_sessions(monkeypatch):
    """Patch one module's enumeration + session seams; record what was opened.

    Returns a factory so a single test can arm whichever module it drives. The
    sessions are built up front, not lazily on first open, so a test can name
    "org A's session" before the job runs and then assert what reached it.
    """

    def arm(module):
        opened: list[UUID] = []
        sessions: dict[UUID, MagicMock] = {
            ORG_A: MagicMock(name="db:org-a"),
            ORG_B: MagicMock(name="db:org-b"),
        }

        @contextmanager
        def session_for_org(organization_id: UUID):
            opened.append(organization_id)
            yield sessions[organization_id]

        monkeypatch.setattr(module, "session_for_org", session_for_org)
        monkeypatch.setattr(module, "_list_organization_ids", lambda: [ORG_A, ORG_B])
        return opened, sessions

    return arm


def _empty_scalars(db: MagicMock) -> None:
    """Make ``db.scalars(...).all()`` return an empty list rather than a mock."""
    db.scalars.return_value.all.return_value = []


def _module_source(module) -> str:
    source = (module.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        return handle.read()


def _function_sources(module) -> dict[str, str]:
    """Map top-level function name to its source text.

    Read from the file through :mod:`ast` rather than ``inspect.getsource`` on
    the attribute: Celery's ``@shared_task`` replaces the module attribute with
    a task proxy, and unwrapping that correctly is not what these assertions are
    about.
    """
    text = _module_source(module)
    return {
        node.name: ast.get_source_segment(text, node) or ""
        for node in ast.parse(text).body
        if isinstance(node, ast.FunctionDef)
    }


# ── the enumeration contract ──


@pytest.mark.parametrize("module", CONVERTED_MODULES, ids=lambda m: m.__name__)
def test_every_converted_job_still_includes_inactive_tenants(module, monkeypatch):
    """``include_inactive=True``, because the retired scans had no active filter.

    Each scan this replaced selected distinct ``organization_id`` values from a
    domain table — templates, workflow rules, vehicle documents, maintenance
    records, people with a role, projects. None of them joined
    ``core_org.organization``, so none of them could have excluded a deactivated
    tenant. Preserving that is the whole reason these helpers do not call
    ``active_organization_ids``.
    """
    calls: list[dict] = []
    monkeypatch.setattr(
        module,
        "organization_ids",
        lambda **kwargs: calls.append(kwargs) or [],
    )

    assert module._list_organization_ids() == []
    assert calls == [{"include_inactive": True}], (
        f"{module.__name__} must enumerate every tenant, including deactivated "
        "ones — the scan it replaced did"
    )


@pytest.mark.parametrize("module", CONVERTED_MODULES, ids=lambda m: m.__name__)
def test_enumeration_goes_through_the_tenant_catalogue(module):
    """The helper is a call into the catalogue, not a query of its own.

    A helper that grew a ``select(...)`` back would be a cross-tenant read again,
    and no behaviour test in this file would notice: the mocks would answer it.
    """
    source = inspect.getsource(module._list_organization_ids)
    assert "organization_ids(" in source
    assert "select(" not in source


# ── automation ──


def test_recurring_templates_runs_once_per_tenant(tenant_sessions):
    """``run_due_templates`` is handed the tenant session, once per tenant.

    It takes no ``organization_id`` — it scopes through the session — so the
    pre-scan that used to narrow the loop to organizations with due templates
    was applying the same filter the per-tenant call already applies.
    """
    opened, sessions = tenant_sessions(automation)
    service = MagicMock()
    service.run_due_templates.return_value = []

    with patch("app.services.finance.automation.recurring.recurring_service", service):
        automation.process_recurring_templates()

    assert opened == [ORG_A, ORG_B]
    assert [call.args[0] for call in service.run_due_templates.call_args_list] == [
        sessions[ORG_A],
        sessions[ORG_B],
    ], "each run must use its own tenant's session, never a shared one"


def test_scheduled_workflow_rules_runs_once_per_tenant(tenant_sessions):
    """``evaluate_due_rules`` selects its own rules, through the tenant session."""
    opened, sessions = tenant_sessions(automation)
    evaluator = MagicMock()
    evaluator.evaluate_due_rules.return_value = {
        "rules_checked": 1,
        "rules_due": 0,
        "actions_fired": 0,
        "errors": [],
    }

    with patch(
        "app.services.finance.automation.scheduled_evaluator.scheduled_evaluator",
        evaluator,
    ):
        results = automation.process_scheduled_workflow_rules()

    assert opened == [ORG_A, ORG_B]
    assert [call.args[0] for call in evaluator.evaluate_due_rules.call_args_list] == [
        sessions[ORG_A],
        sessions[ORG_B],
    ]
    assert results["rules_checked"] == 2, "per-tenant counts are summed, not replaced"


# ── performance ──


def test_phase_transitions_asks_each_tenant_for_its_own_due_cycles(tenant_sessions):
    """The service is built from the tenant session and its cycles used directly.

    The retired code carried ``(cycle_id, org_id)`` pairs out of a cross-tenant
    scan and re-fetched each cycle with ``db.get`` inside the tenant session.
    Here the cycle object the service returns is already bound to the session it
    will be advanced in, so there is nothing to re-fetch.
    """
    opened, sessions = tenant_sessions(performance)
    constructed_with: list[object] = []

    def service_factory(db):
        constructed_with.append(db)
        service = MagicMock()
        service.get_cycles_ready_for_transition.return_value = []
        return service

    with patch(
        "app.services.performance_automation.PerformanceAutomationService",
        side_effect=service_factory,
    ):
        results = performance.process_cycle_phase_transitions()

    assert opened == [ORG_A, ORG_B]
    assert constructed_with == [sessions[ORG_A], sessions[ORG_B]]
    assert results["transitions"] == []
    assert results["errors"] == []


def test_a_tenants_cycle_is_only_advanced_in_that_tenants_session(tenant_sessions):
    """Isolation: org A's cycle never reaches org B's session.

    The old code held both tenants' cycles in one cross-org result and relied on
    grouping by ``organization_id`` to keep them apart. The database keeps them
    apart now; this asserts the code does not undo that.
    """
    opened, sessions = tenant_sessions(performance)
    cycle_a, cycle_b = MagicMock(name="cycle-a"), MagicMock(name="cycle-b")
    cycle_a.cycle_name, cycle_b.cycle_name = "CYCLE-A", "CYCLE-B"
    target = MagicMock()
    target.value = "REVIEW"
    by_session = {
        sessions[ORG_A]: [(cycle_a, target)],
        sessions[ORG_B]: [(cycle_b, target)],
    }
    advanced: list[tuple[object, object]] = []

    def service_factory(db):
        service = MagicMock()
        service.get_cycles_ready_for_transition.return_value = by_session[db]
        service.advance_cycle_phase.side_effect = (
            lambda cycle, _status: advanced.append((db, cycle)) or True
        )
        return service

    with patch(
        "app.services.performance_automation.PerformanceAutomationService",
        side_effect=service_factory,
    ):
        performance.process_cycle_phase_transitions()

    assert advanced == [
        (sessions[ORG_A], cycle_a),
        (sessions[ORG_B], cycle_b),
    ], "each cycle is advanced exactly once, in its own organization's session"


def test_cycle_progress_sync_queries_inside_each_tenant_session(tenant_sessions):
    """The status filter moved into the tenant session; the id round-trip is gone."""
    opened, sessions = tenant_sessions(performance)
    for db in sessions.values():
        _empty_scalars(db)
    constructed_with: list[object] = []

    with patch(
        "app.services.performance_automation.PerformanceAutomationService",
        side_effect=lambda db: constructed_with.append(db) or MagicMock(),
    ):
        results = performance.sync_all_cycle_progress()

    assert opened == [ORG_A, ORG_B]
    assert constructed_with == [sessions[ORG_A], sessions[ORG_B]]
    for db in sessions.values():
        db.scalars.assert_called_once()
    assert results["cycles_processed"] == 0
    assert results["errors"] == []


# ── fleet ──


@pytest.mark.parametrize(
    "job",
    [
        fleet.process_document_expiry_notifications,
        fleet.process_maintenance_due_notifications,
    ],
    ids=["documents", "maintenance"],
)
def test_fleet_reminders_query_every_tenant(tenant_sessions, job):
    """Both fleet jobs scan per tenant; the cutoff predicate never left the query.

    The deleted helpers applied the same expiry/schedule cutoff the per-tenant
    query still applies. They were a narrowing pass over a cross-tenant read —
    which is to say, over zero rows under ``app_user``.
    """
    opened, sessions = tenant_sessions(fleet)
    for db in sessions.values():
        _empty_scalars(db)

    results = job()

    assert opened == [ORG_A, ORG_B]
    for db in sessions.values():
        # Two queries per tenant, both inside the tenant session: the due-work
        # scan, then the recipient lookup.
        assert db.scalars.call_count == 2
    assert results["notifications_sent"] == 0
    assert results["errors"] == []


# ── license ──


def test_license_notices_resolve_recipients_inside_each_tenant_session(
    tenant_sessions,
):
    """The admin lookup runs per tenant, keyed by the session's own scope.

    It previously ran one cross-tenant join and grouped rows by
    ``Person.organization_id``. That grouping key is now the session scope, and
    the predicate in the query says the same thing to a database that enforces
    it.
    """
    from app.licensing.schema import LicenseStatus

    opened, sessions = tenant_sessions(license_task)
    for db in sessions.values():
        _empty_scalars(db)

    state = MagicMock()
    state.status = LicenseStatus.EXPIRED
    state.payload = None
    state.error = None
    results: dict = {"notifications_sent": 0, "errors": []}

    license_task._notify_admins(state, results)

    assert opened == [ORG_A, ORG_B]
    for db in sessions.values():
        db.scalars.assert_called_once()
        db.commit.assert_called_once()
    assert results["errors"] == [], "a clean run must not record an error"
    assert results["notifications_sent"] == 0


# ── project SLA ──


def test_project_sla_scans_every_tenant(tenant_sessions):
    """One breach scan per tenant, each in its own session.

    ``organizations`` now counts tenants scanned rather than tenants owning a
    project row — the old number was a by-product of the cross-tenant pre-scan.
    ``projects`` and ``breaches`` are unchanged: a tenant with no projects adds
    zero to both.
    """
    opened, sessions = tenant_sessions(project_sla)
    scanned: list[tuple[object, UUID]] = []

    def service_factory(db, organization_id):
        scanned.append((db, organization_id))
        service = MagicMock()
        service.process_breaches.return_value = {"projects": 2, "breaches": 1}
        return service

    with patch.object(project_sla, "ProjectSLAService", side_effect=service_factory):
        results = project_sla.process_project_sla_breaches()

    assert scanned == [
        (sessions[ORG_A], ORG_A),
        (sessions[ORG_B], ORG_B),
    ], "the service must be built from the matching tenant's session and id"
    assert results == {"organizations": 2, "projects": 4, "breaches": 2}


# ── what must not come back, and what was deliberately left ──


@pytest.mark.parametrize(
    "module", (fleet, license_task, project_sla), ids=lambda m: m.__name__
)
def test_the_fully_converted_modules_no_longer_reach_across_tenants(module):
    """The retired seam is gone from these three, not merely unused.

    A re-import of ``cross_org_session`` is the regression this slice exists to
    prevent, and the architecture guard sees only the catalogue enumeration
    shape, not this one.
    """
    text = _module_source(module)

    assert "cross_org_session" not in text
    assert "organization_ids" in text


def test_the_resolution_seams_are_held_back_not_converted():
    """The five ``cross_org_session`` sites left in this slice are all resolution.

    Each is handed one row's id from outside any tenant context and has to find
    the owning tenant before it can open a session. The catalogue definer
    returns identifiers and nothing else — by design, so that discovery can
    never become a general cross-tenant read — so it cannot answer "who owns
    this row". Converting them anyway would mean widening the definer or
    guessing, and both are worse than leaving a known seam visible.

    This test fails if that set changes in either direction: a new resolution
    site appearing, or one being converted without the contract landing.
    """
    resolution_sites = {
        automation: {"_resolve_workflow_rule_org"},
        performance: {
            "generate_cycle_appraisals",
            "calculate_cycle_progress",
            "activate_cycle",
            "complete_cycle",
        },
    }

    for module, expected in resolution_sites.items():
        # `cross_org_session()` call sites only — the import line, and the
        # module docstring that explains the hold-back, both name the symbol.
        assert _module_source(module).count("with cross_org_session()") == len(
            expected
        ), (
            f"{module.__name__} should hold back exactly {len(expected)} "
            "resolution lookups"
        )

        found = {
            name
            for name, body in _function_sources(module).items()
            if "cross_org_session" in body
        }
        assert found == expected, (
            f"{module.__name__}: held-back resolution seams drifted. Each of "
            "these resolves the owning tenant of one named row and must keep "
            "its lookup until a tenant-resolution contract exists."
        )
