"""``app_user`` cannot mutate the frozen legacy invoice sync archive.

Requires a real PostgreSQL database migrated to head (`20260918_invoice_sync_
canonical_evidence`), which physically renames the pre-canonical evidence
tables to `ar.dotmac_sub_invoice_sync_outcome_legacy` /
`ar.dotmac_sub_invoice_sync_issue_legacy` and REVOKEs INSERT/UPDATE/DELETE/
TRUNCATE from `app_user` on both. This module executes that boundary as
`app_user`, the same style as
`tests/integration/test_app_user_cross_org_reachability.py`: connect, `SET
LOCAL ROLE app_user`, attempt the write inside its own savepoint, and assert
PostgreSQL actually refuses it (SQLSTATE 42501, insufficient_privilege) —
not that a grant is merely absent from a static ledger.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

APP_ROLE = "app_user"
INSUFFICIENT_PRIVILEGE = "42501"

LEGACY_OUTCOME = "ar.dotmac_sub_invoice_sync_outcome_legacy"
LEGACY_ISSUE = "ar.dotmac_sub_invoice_sync_issue_legacy"


def _sqlstate(error: DBAPIError) -> str | None:
    original = error.orig
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


@pytest.fixture()
def app_user_connection(engine):
    """A connection whose ``current_user`` is ``app_user``, rolled back after."""
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
        try:
            yield connection
        finally:
            transaction.rollback()


def test_the_proof_actually_runs_as_app_user(app_user_connection) -> None:
    assert app_user_connection.scalar(text("SELECT current_user")) == APP_ROLE, (
        "SET LOCAL ROLE did not take effect; nothing below proves anything "
        "about app_user."
    )


@pytest.mark.parametrize("table", [LEGACY_OUTCOME, LEGACY_ISSUE])
def test_app_user_cannot_insert_into_legacy_table(app_user_connection, table) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        with app_user_connection.begin_nested():
            app_user_connection.execute(
                text(f"INSERT INTO {table} (outcome_id) VALUES (:id)"),  # noqa: S608
                {"id": str(uuid.uuid4())},
            )
    assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE, (
        f"expected insufficient_privilege inserting into {table}, got "
        f"{_sqlstate(excinfo.value)!r}"
    )


@pytest.mark.parametrize("table", [LEGACY_OUTCOME, LEGACY_ISSUE])
def test_app_user_cannot_update_legacy_table(app_user_connection, table) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        with app_user_connection.begin_nested():
            app_user_connection.execute(
                text(
                    f"UPDATE {table} SET organization_id = organization_id "  # noqa: S608
                    "WHERE false"
                )
            )
    assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE, (
        f"expected insufficient_privilege updating {table}, got "
        f"{_sqlstate(excinfo.value)!r}"
    )


@pytest.mark.parametrize("table", [LEGACY_OUTCOME, LEGACY_ISSUE])
def test_app_user_cannot_delete_from_legacy_table(app_user_connection, table) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        with app_user_connection.begin_nested():
            app_user_connection.execute(text(f"DELETE FROM {table} WHERE false"))  # noqa: S608
    assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE, (
        f"expected insufficient_privilege deleting from {table}, got "
        f"{_sqlstate(excinfo.value)!r}"
    )


def test_app_user_can_still_select_from_the_legacy_table(app_user_connection) -> None:
    """The freeze is write-only: forensic/audit SELECT is preserved.

    Sensitivity proof (ADR-0018): the three write tests above must fail on a
    grant that also removed SELECT, and this test proves SELECT specifically
    was never revoked — a boundary that denied everything would trivially
    "pass" the write-refusal tests above for the wrong reason.
    """
    with app_user_connection.begin_nested():
        result = app_user_connection.execute(
            text(f"SELECT count(*) FROM {LEGACY_OUTCOME}")  # noqa: S608
        ).scalar_one()
    assert result is not None
