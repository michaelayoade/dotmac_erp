"""Every door onto a PLATFORM row asks the grant tables, and refuses cleanly.

Making the four webhook SSRF keys platform-owned closes the tenant-facing
settings form. It does not, by itself, close the other ways a tenant-side
actor reaches a row with ``organization_id IS NULL``:

* ``app/api/settings.py::restore_setting`` — ``restore_from_history`` picks
  its target scope from the history row, so a NULL-organization entry rewrites
  the PLATFORM row. Its admin half read ``auth["roles"]``, a login-time claim;
* ``AdminWebService._require_admin_web_auth`` — the portal guard in front of
  ``settings_create_response``/``settings_update_response``, which write
  platform rows. It read ``auth.roles``, the same kind of claim taken when the
  web session was established.

Both are now ``has_live_admin_grant``, the function ``require_admin_bypass``
already used. These tests are about the REVOCATION: an actor whose token or
session still asserts ``admin`` while the grant is gone. A test that only
showed an admin succeeding would pass against either version.

The last class is the other half of the same door: restoring an entry whose
key is now platform-owned must be a handled refusal on a clean session, not a
listener exception escaping as a 500 with the session left mid-flush.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.models.domain_settings import (
    DomainSetting,
    DomainSettingHistory,
    SettingChangeAction,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.models.rbac import PersonRole
from app.services.auth_dependencies import has_live_admin_grant
from app.services.settings_cache import settings_cache


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    settings_cache.clear_inmemory()
    yield
    settings_cache.clear_inmemory()


@pytest.fixture()
def revoke_admin(db_session, admin_person, admin_role):
    """Remove the live grant, leaving every issued claim intact."""

    def _revoke() -> None:
        db_session.execute(
            PersonRole.__table__.delete().where(
                PersonRole.person_id == admin_person.id,
                PersonRole.role_id == admin_role.id,
            )
        )
        db_session.commit()

    return _revoke


# ---------------------------------------------------------------------------
# The shared check
# ---------------------------------------------------------------------------


class TestTheLiveGrantIsTheAnswer:
    def test_a_granted_person_holds_admin(self, db_session, admin_person):
        assert has_live_admin_grant(db_session, admin_person.id) is True

    def test_a_revoked_person_does_not(self, db_session, admin_person, revoke_admin):
        revoke_admin()
        assert has_live_admin_grant(db_session, admin_person.id) is False

    @pytest.mark.parametrize("person_id", [None, "", "not-a-uuid", uuid.uuid4()])
    def test_an_unresolvable_actor_holds_nothing(self, db_session, person_id):
        """It fails CLOSED. A check that raised here would be a 500, not a 403."""
        assert has_live_admin_grant(db_session, person_id) is False


# ---------------------------------------------------------------------------
# Door 1 — the history restore predicate
# ---------------------------------------------------------------------------


class TestTheHistoryRestoreDoor:
    """A platform-row history entry needs a LIVE admin grant, not a claim."""

    @staticmethod
    def _platform_entry():
        entry = DomainSettingHistory(
            domain=str(SettingDomain.automation),
            key="webhook_allowed_hosts",
            organization_id=None,
            action=SettingChangeAction.UPDATE,
        )
        return entry

    @staticmethod
    def _auth(person_id, organization_id=None):
        # `roles` deliberately still says admin. That is the stale claim, and
        # the point of the test is that it is no longer what answers.
        return {
            "person_id": str(person_id),
            "organization_id": str(organization_id) if organization_id else None,
            "roles": ["admin"],
        }

    def test_a_live_admin_may_restore_a_platform_entry(self, db_session, admin_person):
        from app.api.settings import _can_restore_history_entry

        assert (
            _can_restore_history_entry(
                self._platform_entry(), self._auth(admin_person.id), db_session
            )
            is True
        )

    def test_a_revoked_admin_may_not(self, db_session, admin_person, revoke_admin):
        from app.api.settings import _can_restore_history_entry

        revoke_admin()
        assert (
            _can_restore_history_entry(
                self._platform_entry(), self._auth(admin_person.id), db_session
            )
            is False
        ), (
            "a revoked administrator restored a platform row on the strength of "
            "a token claim; the predicate is reading auth['roles'] again"
        )

    def test_the_organization_scoped_entry_is_untouched_by_this(
        self, db_session, admin_person, revoke_admin
    ):
        """Sensitivity: the change must not turn every restore into an admin op.

        An organization's own history entry is still answered by ownership, so
        a non-admin tenant operator keeps restoring its own settings.
        """
        from app.api.settings import _can_restore_history_entry

        revoke_admin()
        org_id = uuid.uuid4()
        entry = DomainSettingHistory(
            domain=str(SettingDomain.automation),
            key="webhook_tenant_allowed_hosts",
            organization_id=org_id,
            action=SettingChangeAction.UPDATE,
        )
        assert (
            _can_restore_history_entry(
                entry, self._auth(admin_person.id, org_id), db_session
            )
            is True
        )

    def test_reading_is_still_the_looser_question(self, db_session, admin_person):
        """`_can_read_history_entry` is deliberately NOT tightened.

        Readability of a global row was the justification for the original
        predicate; applying it to a WRITE was the mistake. Tightening the read
        as well would be a second, unasked-for behaviour change.
        """
        from app.api.settings import _can_read_history_entry

        assert (
            _can_read_history_entry(self._platform_entry(), self._auth(admin_person.id))
            is True
        )


# ---------------------------------------------------------------------------
# Door 2 — the admin portal guard
# ---------------------------------------------------------------------------


class _Auth:
    """The shape `_require_admin_web_auth` reads off `WebAuthContext`."""

    def __init__(self, person_id, roles, is_authenticated=True):
        self.person_id = person_id
        self.roles = roles
        self.is_authenticated = is_authenticated


class TestTheAdminPortalDoor:
    @staticmethod
    def _request():
        from unittest.mock import MagicMock

        request = MagicMock()
        request.url.path = "/admin/settings/new"
        request.url.query = ""
        return request

    @pytest.fixture(params=["legacy", "mixin"])
    def guard(self, request):
        """Both copies of the guard.

        `app/services/admin/web.py` is the one that actually serves the routes
        today (`AdminWebService.__getattr__` falls back to it); the mixin in
        `app/services/admin/web/common.py` is where they are being migrated
        to. Fixing only the live one would leave the stale claim waiting in the
        destination.
        """
        if request.param == "legacy":
            from app.services.admin.web._legacy import LegacyAdminWebService

            return LegacyAdminWebService()._require_admin_web_auth
        from app.services.admin.web.common import AdminWebCommonMixin

        return AdminWebCommonMixin()._require_admin_web_auth

    def test_a_live_admin_passes(self, db_session, admin_person, guard):
        auth = _Auth(admin_person.id, ["admin"])
        assert guard(self._request(), db_session, auth) is auth

    def test_a_revoked_admin_is_refused(
        self, db_session, admin_person, revoke_admin, guard
    ):
        revoke_admin()
        with pytest.raises(HTTPException) as raised:
            guard(self._request(), db_session, _Auth(admin_person.id, ["admin"]))
        assert raised.value.status_code == 403

    def test_a_claim_without_the_role_is_still_refused(
        self, db_session, admin_person, guard
    ):
        """The claim check stays: it is cheap and necessary, just not sufficient."""
        with pytest.raises(HTTPException) as raised:
            guard(self._request(), db_session, _Auth(admin_person.id, ["viewer"]))
        assert raised.value.status_code == 403

    def test_an_anonymous_caller_still_gets_the_login_redirect(self, db_session, guard):
        from fastapi.responses import RedirectResponse

        result = guard(
            self._request(), db_session, _Auth(None, [], is_authenticated=False)
        )
        assert isinstance(result, RedirectResponse)


# ---------------------------------------------------------------------------
# Restoring a platform-owned key is a refusal, not a 500
# ---------------------------------------------------------------------------


class TestRestoringAPlatformOwnedKeyIntoAnOrganization:
    """The migration renamed organization rows onto the narrowing keys.

    It also wrote a history entry naming the OLD key with the organization's
    id, which is precisely the entry an operator would reach for. Restoring it
    builds an organization-scoped `DomainSetting` for a platform-owned key, so
    `_require_platform_scope` fires at flush. It failed CLOSED, but as an
    unhandled `PlatformOwnedSettingError` — a 500 on a session left mid-flush,
    which a later request on the same session would inherit.
    """

    @pytest.fixture()
    def demoted_entry(self, db_session):
        org_id = uuid.uuid4()
        entry = DomainSettingHistory(
            domain=str(SettingDomain.automation),
            key="webhook_allowed_hosts",
            organization_id=org_id,
            action=SettingChangeAction.UPDATE,
            old_value_type=SettingValueType.string.value,
            old_value_text="internal.example.com",
            old_is_secret=False,
            old_is_active=True,
            change_reason="Webhook SSRF policy is platform-owned; ...",
        )
        db_session.add(entry)
        db_session.commit()
        db_session.refresh(entry)
        yield entry
        db_session.rollback()
        db_session.execute(
            DomainSettingHistory.__table__.delete().where(
                DomainSettingHistory.id == entry.id
            )
        )
        db_session.commit()

    def test_it_is_a_handled_refusal(self, db_session, demoted_entry):
        from app.services.domain_settings import restore_from_history

        with pytest.raises(HTTPException) as raised:
            restore_from_history(db_session, history_id=str(demoted_entry.id))

        assert raised.value.status_code == 409
        assert "platform-owned" in str(raised.value.detail)

    def test_the_session_is_usable_afterwards(self, db_session, demoted_entry):
        """The rollback is the half a bare `raise` would have missed.

        Without it the caller inherits a session holding a pending, rejected
        INSERT, and the next statement on it fails for a reason that has
        nothing to do with the request that failed.
        """
        from app.services.domain_settings import restore_from_history

        with pytest.raises(HTTPException):
            restore_from_history(db_session, history_id=str(demoted_entry.id))

        # The session answers a fresh query rather than re-raising the pending
        # flush, and the refused row is not there.
        leftover = db_session.execute(
            DomainSetting.__table__.select()
            .where(DomainSetting.key == "webhook_allowed_hosts")
            .where(DomainSetting.organization_id == demoted_entry.organization_id)
        ).first()
        assert leftover is None, "the refused row was written anyway"

    def test_the_narrowing_key_still_restores(self, db_session):
        """Sensitivity. A blanket refusal would pass both tests above."""
        from app.services.domain_settings import restore_from_history

        org_id = uuid.uuid4()
        entry = DomainSettingHistory(
            domain=str(SettingDomain.automation),
            key="webhook_tenant_allowed_hosts",
            organization_id=org_id,
            action=SettingChangeAction.UPDATE,
            old_value_type=SettingValueType.string.value,
            old_value_text="internal.example.com",
            old_is_secret=False,
            old_is_active=True,
        )
        db_session.add(entry)
        db_session.commit()

        restored = restore_from_history(db_session, history_id=str(entry.id))
        try:
            assert restored.organization_id == org_id
            assert restored.value_text == "internal.example.com"
            assert restored.scope is SettingScope.ORG_SPECIFIC
        finally:
            db_session.rollback()
            db_session.execute(
                DomainSetting.__table__.delete().where(DomainSetting.id == restored.id)
            )
            db_session.execute(
                DomainSettingHistory.__table__.delete().where(
                    DomainSettingHistory.organization_id == org_id
                )
            )
            db_session.commit()
