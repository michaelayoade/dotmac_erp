"""Tests for session context primitives (prime, cross-org bypass, factory)."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest


class TestPrimeSession:
    def test_sets_organization_id_on_session_info(self):
        from app.db.session_context import prime_session

        session = MagicMock()
        session.info = {}
        org_id = uuid4()

        prime_session(session, org_id)

        assert session.info["organization_id"] == org_id

    def test_overwrites_existing_organization_id(self):
        from app.db.session_context import prime_session

        session = MagicMock()
        session.info = {"organization_id": uuid4()}
        new_org_id = uuid4()

        prime_session(session, new_org_id)

        assert session.info["organization_id"] == new_org_id


class TestAllowCrossOrg:
    def test_sets_flag_inside_block_unsets_after(self):
        from app.db.session_context import allow_cross_org

        session = MagicMock()
        session.info = {}

        with allow_cross_org(session):
            assert session.info["allow_cross_org"] is True

        # After exit: flag must be False (or removed) so subsequent queries
        # are NOT bypassed.
        assert (
            session.info.get("allow_cross_org") is False
            or "allow_cross_org" not in session.info
        )

    def test_restores_state_after_exception(self):
        from app.db.session_context import allow_cross_org

        session = MagicMock()
        session.info = {}

        with pytest.raises(RuntimeError):
            with allow_cross_org(session):
                raise RuntimeError("boom")

        assert (
            session.info.get("allow_cross_org") is False
            or "allow_cross_org" not in session.info
        )

    def test_nested_preserves_outer_state(self):
        """Nested context managers must restore the OUTER state on inner exit,
        not unconditionally clear the flag."""
        from app.db.session_context import allow_cross_org

        session = MagicMock()
        session.info = {}

        with allow_cross_org(session):
            assert session.info["allow_cross_org"] is True
            with allow_cross_org(session):
                assert session.info["allow_cross_org"] is True
            # Inner exit: outer must still be True
            assert session.info["allow_cross_org"] is True

        # Outer exit: now False
        assert (
            session.info.get("allow_cross_org") is False
            or "allow_cross_org" not in session.info
        )


class TestSessionForOrg:
    def test_yields_primed_session(self):
        from app.db.session_context import session_for_org

        org_id = uuid4()
        with session_for_org(org_id) as db:
            assert db.info["organization_id"] == org_id

    def test_closes_session_on_exit(self):
        """The factory must close the session even on exception so DB
        connections aren't leaked under task failures."""
        from app.db.session_context import session_for_org

        org_id = uuid4()
        captured = {}
        try:
            with session_for_org(org_id) as db:
                captured["db"] = db
                # Spy: wrap close() to record invocation. SQLAlchemy 2.0's
                # ``is_active`` stays True after close() because of autobegin
                # on next use, so we verify closure by call count instead.
                original_close = db.close
                captured["close_calls"] = 0

                def _spy_close(*args, **kwargs):
                    captured["close_calls"] += 1
                    return original_close(*args, **kwargs)

                db.close = _spy_close
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        # finally: must have called close() exactly once even though the
        # block raised. After close(), the session has no active transaction.
        assert captured["close_calls"] == 1
        assert captured["db"].in_transaction() is False
