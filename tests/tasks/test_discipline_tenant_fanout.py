"""The discipline reminder jobs fan out over tenants instead of scanning across them.

These three jobs are the proving slice for the domain-scan shape: the previous
code opened one ``cross_org_session``, asked ``DisciplineService`` for every
tenant's due cases at once, grouped the ids by organization, and then re-fetched
each group inside a tenant session.

That shape has a specific failure mode under ``app_user``. ``cross_org_session``
bypasses only the SQLAlchemy listener, never PostgreSQL RLS, so the cross-tenant
scan returns **zero rows** once the runtime is no longer the ``postgres``
superuser — and a reminder job that finds no due work looks exactly like a
reminder job with no due work. It reports success and sends nothing.

What is asserted here:

1. discovery goes through the tenant catalogue, not a cross-org scan;
2. every service query happens inside a tenant-scoped session; and
3. one tenant's cases are never processed in another tenant's session — the
   isolation property the old grouping code had to maintain by hand.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from app.tasks import discipline

ORG_A = UUID("00000000-0000-0000-0000-0000000000a1")
ORG_B = UUID("00000000-0000-0000-0000-0000000000b2")


class _Case:
    """Minimal stand-in: the jobs only touch these attributes before skipping."""

    def __init__(self, case_number: str) -> None:
        self.case_number = case_number
        self.employee = None  # every job's first guard is `if not case.employee`
        self.response_due_date = None
        self.hearing_date = None
        self.appeal_deadline = None


@pytest.fixture
def tenant_sessions(monkeypatch):
    """Record which organization each session was opened for, in order."""
    opened: list[UUID] = []
    # Built up front, not lazily on first open: a test that needs to say "this
    # case belongs to org A's session" has to name that session before the job
    # runs.
    sessions: dict[UUID, MagicMock] = {
        ORG_A: MagicMock(name="db:org-a"),
        ORG_B: MagicMock(name="db:org-b"),
    }

    @contextmanager
    def session_for_org(organization_id: UUID):
        opened.append(organization_id)
        yield sessions[organization_id]

    monkeypatch.setattr(discipline, "session_for_org", session_for_org)
    monkeypatch.setattr(
        discipline, "active_organization_ids", lambda **_: [ORG_A, ORG_B]
    )
    return opened, sessions


@pytest.mark.parametrize(
    ("job", "service_method"),
    [
        (
            discipline.process_discipline_hearing_reminders,
            "get_cases_with_upcoming_hearings",
        ),
        (
            discipline.process_discipline_appeal_deadline_reminders,
            "get_cases_with_expiring_appeals",
        ),
    ],
)
def test_each_organization_is_queried_inside_its_own_session(
    tenant_sessions, job, service_method
):
    """The service is constructed with the tenant session, once per tenant."""
    opened, sessions = tenant_sessions
    constructed_with: list[object] = []

    def service_factory(db):
        constructed_with.append(db)
        service = MagicMock()
        getattr(service, service_method).return_value = []
        return service

    with patch(
        "app.services.people.discipline.DisciplineService", side_effect=service_factory
    ):
        job()

    assert opened == [ORG_A, ORG_B], "every active organization gets its own session"
    assert constructed_with == [sessions[ORG_A], sessions[ORG_B]], (
        "the service must be built from the tenant-scoped session; building it "
        "from anything else is how the cross-tenant scan came back"
    )


def test_a_tenants_cases_are_only_handled_in_that_tenants_session(tenant_sessions):
    """Isolation: org A's case never reaches org B's session.

    The old code fetched both tenants' cases in one cross-org session and relied
    on correct grouping to keep them apart. Here the database keeps them apart,
    and this asserts the code does not undo that.
    """
    opened, sessions = tenant_sessions
    case_a, case_b = _Case("DISC-A-1"), _Case("DISC-B-1")
    by_org = {sessions[ORG_A]: [case_a], sessions[ORG_B]: [case_b]}
    seen: list[tuple[object, str]] = []

    def service_factory(db):
        service = MagicMock()

        def cases(**_):
            found = by_org[db]
            seen.extend((db, case.case_number) for case in found)
            return found

        service.get_cases_with_upcoming_hearings.side_effect = cases
        return service

    with patch(
        "app.services.people.discipline.DisciplineService", side_effect=service_factory
    ):
        discipline.process_discipline_hearing_reminders()

    assert seen == [
        (sessions[ORG_A], "DISC-A-1"),
        (sessions[ORG_B], "DISC-B-1"),
    ], "each case is seen exactly once, in its own organization's session"


def test_the_response_job_reuses_one_service_per_tenant(tenant_sessions):
    """Pending and overdue are two queries against ONE tenant-scoped service.

    Both must run inside the same organization's session. Splitting them across
    sessions, or hoisting either back out of the loop, reintroduces a
    cross-tenant read.
    """
    opened, sessions = tenant_sessions
    services: list[MagicMock] = []

    def service_factory(db):
        service = MagicMock(name=f"svc:{db}")
        service.get_cases_with_pending_responses.return_value = []
        service.get_cases_with_overdue_responses.return_value = []
        services.append(service)
        return service

    with patch(
        "app.services.people.discipline.DisciplineService", side_effect=service_factory
    ):
        discipline.process_discipline_response_reminders()

    assert len(services) == 2, "one service per organization, not one shared service"
    for service in services:
        service.get_cases_with_pending_responses.assert_called_once_with(days_before=3)
        service.get_cases_with_overdue_responses.assert_called_once_with()


def test_the_module_no_longer_reaches_across_tenants() -> None:
    """The retired seam is gone from this module, not merely unused.

    ``cross_org_session`` and the id-grouping helper it fed were the whole
    cross-tenant path here. A re-import of either is the regression this slice
    exists to prevent, and the architecture guard only sees the catalogue
    enumeration shape, not this one.
    """
    source = (discipline.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    assert "cross_org_session" not in text
    assert "_group_case_ids_by_org" not in text
    assert "active_organization_ids" in text
