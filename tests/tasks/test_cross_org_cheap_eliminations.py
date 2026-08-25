"""Four cross-org bypasses that were never protecting anything.

Unlike the fan-out conversions in the preceding steps, none of these callers
needed a per-tenant loop. Each had opened a fleet-wide session for a reason
that does not survive reading the code:

1. ``DisciplineWebService._can_view_department_case`` suppressed the ORM
   listener around two statements that already pin the request's own
   organization, so the bypass removed a filter identical to the one written
   by hand.
2. ``execute_async_hook`` opened one purely to read back an organization that
   both enqueue sites already hold, then threw the session away and reopened a
   scoped one.
3. ``verify_audit_hash_chain`` discovered its tenants with a ``DISTINCT`` over
   the RLS-protected ``audit.audit_log``. ``cross_org_session`` sets no
   organization GUC, so under ``app_user`` that returns zero rows: a tamper
   check that verifies nothing and exits 0.

What is asserted here: the bypass is gone from each module, the replacement
does the same work, and the two callers that now carry an organization on the
wire actually put it there.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

ORG_A = UUID("00000000-0000-0000-0000-0000000000a1")
ORG_B = UUID("00000000-0000-0000-0000-0000000000b2")


# --------------------------------------------------------------------------
# 1. the department check that ran in its own tenant context all along
# --------------------------------------------------------------------------


def test_the_department_check_pins_the_organization_on_every_statement():
    """Both statements carry the org predicate the bypass used to suppress."""
    from app.services.people.discipline.web.discipline_web import (
        DisciplineWebService,
    )
    from app.web.deps import WebAuthContext

    org_id = uuid.uuid4()
    department_id = uuid.uuid4()
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid.uuid4(),
        organization_id=org_id,
        roles=["customer_experience_discipline_viewer"],
        scopes=["self:access", "discipline:department:read"],
    )

    seen: list[str] = []
    db = MagicMock()

    def record(stmt):
        seen.append(str(stmt))
        return department_id

    db.scalar.side_effect = record

    assert (
        DisciplineWebService._can_view_department_case(db, org_id, auth, uuid.uuid4())
        is True
    )
    assert len(seen) == 2
    assert all("organization_id" in sql for sql in seen)


def test_the_discipline_web_module_no_longer_imports_the_bypass():
    from app.services.people.discipline.web import discipline_web

    assert not hasattr(discipline_web, "allow_cross_org")


# --------------------------------------------------------------------------
# 2. the organization travels on the async hook message
# --------------------------------------------------------------------------


def test_an_async_hook_without_an_organization_is_rejected_before_any_session():
    """A message enqueued before this deploy is refused, not run unscoped."""
    from app.tasks.hooks import execute_async_hook

    with patch("app.tasks.hooks.session_for_org") as mock_session:
        result = execute_async_hook.run(
            execution_id=str(uuid.uuid4()),
            hook_id=str(uuid.uuid4()),
        )

    assert result == {"ok": False, "error": "missing organization context"}
    mock_session.assert_not_called()


def test_an_async_hook_scopes_its_session_to_the_organization_on_the_message():
    from app.tasks.hooks import execute_async_hook

    db = MagicMock()
    db.get.side_effect = [None, None]

    with patch("app.tasks.hooks.session_for_org") as mock_session:
        mock_session.return_value.__enter__ = MagicMock(return_value=db)
        mock_session.return_value.__exit__ = MagicMock(return_value=False)
        result = execute_async_hook.run(
            execution_id=str(uuid.uuid4()),
            hook_id=str(uuid.uuid4()),
            organization_id=str(ORG_A),
        )

    assert result == {"ok": False, "error": "missing entities"}
    mock_session.assert_called_once_with(ORG_A)


def test_the_registry_puts_the_event_organization_on_the_message():
    from app.models.finance.platform.service_hook import (
        HookExecutionMode,
        HookHandlerType,
        ServiceHook,
    )
    from app.services.hooks.registry import HookEvent, HookRegistry

    hook = ServiceHook(
        hook_id=uuid.uuid4(),
        organization_id=ORG_A,
        event_name="inventory.stock.reserved",
        handler_type=HookHandlerType.WEBHOOK,
        execution_mode=HookExecutionMode.ASYNC,
        name="Async Hook",
        handler_config={"url": "https://example.invalid/hook"},
        conditions={},
    )

    db = MagicMock()
    db.scalars.return_value.all.return_value = [hook]

    with (
        patch("app.services.hooks.registry.is_feature_enabled", return_value=True),
        patch("app.tasks.hooks.execute_async_hook.delay") as mock_delay,
    ):
        HookRegistry(db).emit(
            HookEvent(
                event_name="inventory.stock.reserved",
                organization_id=ORG_A,
                entity_type="StockReservation",
                entity_id=uuid.uuid4(),
                actor_user_id=None,
                payload={"status": "ok"},
            )
        )

    assert mock_delay.call_args.kwargs["organization_id"] == str(ORG_A)


# --------------------------------------------------------------------------
# 3. audit-integrity discovery comes from the tenant catalog
# --------------------------------------------------------------------------


@pytest.fixture
def audit_fanout(monkeypatch):
    """Patch catalog discovery, per-tenant sessions and the chain verifier."""
    from app.services.finance.platform import audit_log as audit_log_module
    from app.tasks import audit_integrity

    calls: dict = {"catalog": [], "sessions": [], "verified": []}

    def fake_organization_ids(*, include_inactive=False, only=None):
        calls["catalog"].append({"include_inactive": include_inactive, "only": only})
        return [only] if only is not None else [ORG_A, ORG_B]

    class _Ctx:
        def __init__(self, organization_id):
            calls["sessions"].append(organization_id)

        def __enter__(self):
            return MagicMock(name="db")

        def __exit__(self, *exc):
            return False

    def fake_verify_hash_chain(*, db, organization_id, from_date, to_date):
        calls["verified"].append(organization_id)
        return True, None

    monkeypatch.setattr(audit_integrity, "organization_ids", fake_organization_ids)
    monkeypatch.setattr(audit_integrity, "session_for_org", _Ctx)
    monkeypatch.setattr(
        audit_log_module.AuditLogService,
        "verify_hash_chain",
        staticmethod(fake_verify_hash_chain),
    )
    return calls


def test_hash_chain_verification_enumerates_the_catalog_not_the_audit_log(
    audit_fanout,
):
    from app.tasks.audit_integrity import verify_audit_hash_chain

    results = verify_audit_hash_chain(days_back=1)

    assert audit_fanout["catalog"] == [{"include_inactive": True, "only": None}]
    assert audit_fanout["sessions"] == [ORG_A, ORG_B]
    assert audit_fanout["verified"] == [ORG_A, ORG_B]
    assert results["organizations_checked"] == 2
    assert results["organizations_valid"] == 2


def test_a_pinned_organization_narrows_the_catalog_rather_than_the_loop(
    audit_fanout,
):
    """`only=` is the catalog's own narrowing, so an absent id runs nothing."""
    from app.tasks.audit_integrity import verify_audit_hash_chain

    verify_audit_hash_chain(days_back=1, organization_id=str(ORG_B))

    assert audit_fanout["catalog"] == [{"include_inactive": True, "only": ORG_B}]
    assert audit_fanout["sessions"] == [ORG_B]


def test_the_audit_integrity_module_no_longer_imports_the_bypass():
    from app.tasks import audit_integrity

    assert not hasattr(audit_integrity, "cross_org_session")
