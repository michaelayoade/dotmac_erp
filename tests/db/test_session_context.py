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
        assert session.info["tenant_id"] == org_id

    def test_overwrites_existing_organization_id(self):
        from app.db.session_context import prime_session

        session = MagicMock()
        session.info = {"organization_id": uuid4()}
        new_org_id = uuid4()

        prime_session(session, new_org_id)

        assert session.info["organization_id"] == new_org_id
        assert session.info["tenant_id"] == new_org_id


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


class TestPrimeTenantContext:
    """Contract for the in-place dual-layer primer used by public portals
    after slug/token → org_id resolution. See
    :func:`app.db.session_context.prime_tenant_context`.
    """

    def test_calls_both_layers(self):
        """The whole point of this helper: callers can't forget one layer
        because the helper composes both. Future refactors must not drop
        the prime_session OR the set_current_organization_sync call.
        """
        from unittest.mock import patch

        from app.db.session_context import prime_tenant_context

        org_id = uuid4()
        session = MagicMock()
        session.info = {}
        session.get_bind.return_value.dialect.name = "postgresql"
        with (
            patch("app.db.session_context.prime_session") as mock_prime,
            patch(
                "app.db.session_context.set_current_organization_sync"
            ) as mock_set_guc,
        ):
            prime_tenant_context(session, org_id)
            mock_prime.assert_called_once_with(session, org_id)
            mock_set_guc.assert_called_once_with(session, org_id)

    def test_skips_postgres_guc_for_sqlite(self):
        """SQLite tests still need ORM scoping, but cannot set Postgres GUCs."""
        from unittest.mock import patch

        from app.db.session_context import prime_tenant_context

        org_id = uuid4()
        session = MagicMock()
        session.info = {}
        session.get_bind.return_value.dialect.name = "sqlite"
        with (
            patch("app.db.session_context.prime_session") as mock_prime,
            patch(
                "app.db.session_context.set_current_organization_sync"
            ) as mock_set_guc,
        ):
            prime_tenant_context(session, org_id)
            mock_prime.assert_called_once_with(session, org_id)
            mock_set_guc.assert_not_called()


class TestSessionForOrg:
    def test_yields_primed_session(self):
        from app.db.session_context import session_for_org

        org_id = uuid4()
        with session_for_org(org_id) as db:
            assert db.info["organization_id"] == org_id
            assert db.info["tenant_id"] == org_id

    def test_sets_both_layers(self):
        """Contract: session_for_org composes BOTH tenant layers. Future
        refactors must not drop one — both are required for full isolation.

        The contract is unchanged; the mechanism for layer 2 is not. It used
        to be one eager `set_current_organization_sync` at entry, which
        `SET LOCAL` then discarded at the first commit. Layer 2 is now armed
        by an `after_begin` listener, so it is present for the first statement
        and re-armed after every commit.

        So this asserts the layer is set *when a transaction begins* — the
        property that matters — rather than that a particular function was
        called at a particular moment.
        """
        from unittest.mock import patch

        from sqlalchemy import text

        from app.db.session_context import session_for_org

        org_id = uuid4()
        with (
            patch("app.db.session_context.prime_session") as mock_prime,
            patch(
                "app.db.session_context.set_current_organization_on_connection"
            ) as mock_arm,
        ):
            with session_for_org(org_id) as db:
                # Layer 1 is still eager — session.info needs no transaction.
                mock_prime.assert_called_once_with(db, org_id)
                # Layer 2 is armed lazily, as the first transaction opens.
                mock_arm.assert_not_called()
                db.execute(text("SELECT 1"))
                assert mock_arm.call_count == 1
                assert mock_arm.call_args[0][1] == org_id

    def test_layer_two_is_re_armed_after_a_commit(self):
        """The defect this replaced: `SET LOCAL` is transaction-scoped, so a
        commit inside the block dropped the GUC while `session.info` kept
        layer 1 looking primed — it read as scoped and behaved as unscoped."""
        from unittest.mock import patch

        from sqlalchemy import text

        from app.db.session_context import session_for_org

        org_id = uuid4()
        with patch(
            "app.db.session_context.set_current_organization_on_connection"
        ) as mock_arm:
            with session_for_org(org_id) as db:
                db.execute(text("SELECT 1"))
                db.commit()
                db.execute(text("SELECT 1"))

        assert mock_arm.call_count == 2, (
            "layer 2 must be re-armed for the transaction that follows a commit"
        )

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


class TestCrossOrgSession:
    """Contract for the canonical cross-tenant session entry point.

    cross_org_session() exists for batch jobs that must list/operate
    across every organization (e.g. "find every org with pending work").
    It bypasses ORM-listener filtering but never PostgreSQL native RLS, so
    callers must use it deliberately and only for legitimate application-layer
    cross-tenant work — not as a substitute for forgetting to prime.
    """

    def test_orm_listener_bypass_active_inside_block(self):
        from app.db.session_context import cross_org_session

        with cross_org_session() as db:
            assert db.info["allow_cross_org"] is True

    def test_no_org_context_leaked(self):
        """cross_org_session must NOT set an org_id on the session —
        otherwise a caller could mistake it for an org-scoped session
        and the ORM listener might filter to that (likely-stale) org's
        rows instead of bypassing."""
        from app.db.session_context import cross_org_session

        with cross_org_session() as db:
            assert "organization_id" not in db.info
            assert "tenant_id" not in db.info

    def test_closes_session_on_exit(self):
        from app.db.session_context import cross_org_session

        captured = {}
        try:
            with cross_org_session() as db:
                captured["db"] = db
                original_close = db.close
                captured["close_calls"] = 0

                def _spy_close(*args, **kwargs):
                    captured["close_calls"] += 1
                    return original_close(*args, **kwargs)

                db.close = _spy_close
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        assert captured["close_calls"] == 1
        assert captured["db"].in_transaction() is False
