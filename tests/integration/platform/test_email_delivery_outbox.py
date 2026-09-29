"""PostgreSQL proof that email content remains tenant-private and atomic.

The CI integration job migrates its disposable PostgreSQL database before
collecting this module. A missing table or privilege is a failure, not a skip.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.services.email import enqueue_email

pytestmark = pytest.mark.integration

_CONFLICT_DELIVERY_ID = "canary:same-business-email"


def _insert_organization(connection, suffix: str) -> UUID:
    organization_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO core_org.organization (
                organization_id, organization_code, legal_name,
                functional_currency_code, presentation_currency_code,
                fiscal_year_end_month, fiscal_year_end_day, is_active
            ) VALUES (
                :organization_id, :organization_code, :legal_name,
                'NGN', 'NGN', 12, 31, true
            )
            """
        ),
        {
            "organization_id": organization_id,
            "organization_code": f"EM-{suffix}-{uuid4().hex[:8].upper()}",
            "legal_name": f"Email outbox canary {suffix}",
        },
    )
    return organization_id


def _scope_app_user(connection, organization_id: UUID) -> None:
    connection.execute(text("SET LOCAL ROLE app_user"))
    connection.execute(
        text("SELECT set_config('app.current_organization_id', :org, true)"),
        {"org": str(organization_id)},
    )


def _age_terminal_event_as_test_admin(connection, event_id: UUID) -> None:
    """The test owner alone may move the DB clock for a disposable event.

    DDL and the clock update share one transaction; rollback re-enables the
    trigger automatically if the canary fails mid-step.
    """
    connection.execute(
        text(
            "ALTER TABLE platform.event_outbox "
            "DISABLE TRIGGER email_outbox_retention_guard"
        )
    )
    connection.execute(
        text(
            "UPDATE platform.event_outbox "
            "SET terminal_at = now() - interval '31 days' WHERE event_id = :id"
        ),
        {"id": event_id},
    )
    connection.execute(
        text(
            "ALTER TABLE platform.event_outbox "
            "ENABLE TRIGGER email_outbox_retention_guard"
        )
    )


def _create_terminal_email(
    engine, suffix: str, *, status: str = "PUBLISHED"
) -> tuple[UUID, UUID, UUID]:
    with engine.begin() as setup:
        organization_id = _insert_organization(setup, suffix)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _scope_app_user(connection, organization_id)
            with Session(bind=connection) as db:
                event = enqueue_email(
                    db,
                    delivery_id=f"canary:cleanup:{suffix}:{uuid4()}",
                    organization_id=organization_id,
                    to_email="supplier@example.com",
                    subject="Approved",
                    body_html="<p>Private approval</p>",
                )
                event_id = event.event_id
                private_id = UUID(event.payload["delivery_id"])
            transaction.commit()
        finally:
            if transaction.is_active:
                transaction.rollback()
    with engine.begin() as settle:
        settle.execute(
            text(
                "UPDATE platform.event_outbox "
                "SET status = CAST(:status AS event_status) WHERE event_id = :id"
            ),
            {"id": event_id, "status": status},
        )
        _age_terminal_event_as_test_admin(settle, event_id)
    return organization_id, event_id, private_id


def test_replay_then_permanent_failure_restarts_retention_clock(engine) -> None:
    from app.services.finance.platform.outbox_publisher import OutboxPublisher

    organization_id, event_id, private_id = _create_terminal_email(
        engine, "REPLAY", status="DEAD"
    )
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                _scope_app_user(connection, organization_id)
                with Session(bind=connection) as db:
                    OutboxPublisher.requeue_dead_event(db, event_id)
                    OutboxPublisher.mark_dead(db, event_id, "permanent failure")
                assert connection.scalar(
                    text(
                        "SELECT terminal_at > now() - interval '1 day' "
                        "FROM platform.event_outbox WHERE event_id = :id"
                    ),
                    {"id": event_id},
                )
                assert (
                    connection.execute(
                        text(
                            "DELETE FROM public.email_delivery WHERE delivery_id = :id"
                        ),
                        {"id": private_id},
                    ).rowcount
                    == 0
                )
                transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()
    finally:
        _cleanup_canary_rows(engine, organization_id, event_id)


def _cleanup_canary_rows(engine, organization_id: UUID, event_id: UUID) -> None:
    with engine.begin() as cleanup:
        cleanup.execute(
            text("SELECT set_config('app.current_organization_id', :org, true)"),
            {"org": str(organization_id)},
        )
        cleanup.execute(
            text(
                "DELETE FROM public.email_delivery "
                "WHERE organization_id = :organization_id"
            ),
            {"organization_id": organization_id},
        )
        cleanup.execute(
            text(
                "ALTER TABLE platform.event_outbox "
                "DISABLE TRIGGER email_outbox_retention_guard"
            )
        )
        cleanup.execute(
            text("DELETE FROM platform.event_outbox WHERE event_id = :id"),
            {"id": event_id},
        )
        cleanup.execute(
            text(
                "ALTER TABLE platform.event_outbox "
                "ENABLE TRIGGER email_outbox_retention_guard"
            )
        )
        cleanup.execute(
            text("DELETE FROM core_org.organization WHERE organization_id = :id"),
            {"id": organization_id},
        )


def _enqueue_competitor(engine, organization_id: UUID, marker: str, ready: Event):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _scope_app_user(connection, organization_id)
            connection.execute(
                text("SELECT set_config('application_name', :name, true)"),
                {"name": marker},
            )
            with Session(bind=connection) as db:
                ready.set()
                event = enqueue_email(
                    db,
                    delivery_id=_CONFLICT_DELIVERY_ID,
                    organization_id=organization_id,
                    to_email="supplier@example.com",
                    subject="Approved",
                    body_html="<p>Same business email</p>",
                )
                result = (event.event_id, UUID(event.payload["delivery_id"]))
            transaction.commit()
            return result
        finally:
            if transaction.is_active:
                transaction.rollback()


def _wait_for_unique_conflict(engine, marker: str, future) -> bool:
    """Observe the loser waiting on the winner's uncommitted unique key."""
    deadline = time.monotonic() + 10
    with engine.connect() as connection:
        while time.monotonic() < deadline and not future.done():
            wait_type = connection.scalar(
                text(
                    "SELECT wait_event_type FROM pg_stat_activity "
                    "WHERE application_name = :name"
                ),
                {"name": marker},
            )
            if wait_type == "Lock":
                return True
            connection.commit()  # Refresh pg_stat_activity's transaction snapshot.
            time.sleep(0.05)
    return False


def test_email_enqueue_is_atomic_and_tenant_private(engine) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        event_id = None
        private_id = None
        try:
            first = _insert_organization(connection, "A")
            second = _insert_organization(connection, "B")
            connection.execute(text("SET LOCAL ROLE app_user"))
            connection.execute(
                text("SELECT set_config('app.current_organization_id', :org, true)"),
                {"org": str(first)},
            )

            with Session(bind=connection) as db:
                event = enqueue_email(
                    db,
                    delivery_id="canary:purchase-order-approved",
                    organization_id=first,
                    to_email="supplier@example.com",
                    subject="Approved",
                    body_html="<p>Private approval</p>",
                    attachments=[("po.pdf", b"%PDF", "application/pdf")],
                )
                event_id = event.event_id
                private_id = UUID(event.payload["delivery_id"])
                assert event.payload == {"delivery_id": str(private_id)}
                assert event.headers == {
                    "organization_id": str(first),
                    "source": "email",
                }

            row = connection.execute(
                text(
                    "SELECT organization_id, body_html, attachments "
                    "FROM public.email_delivery WHERE delivery_id = :id"
                ),
                {"id": private_id},
            ).one()
            assert row.organization_id == first
            assert row.body_html == "<p>Private approval</p>"
            assert row.attachments[0]["data_b64"] == "JVBERg=="

            early_delete = connection.execute(
                text("DELETE FROM public.email_delivery WHERE delivery_id = :id"),
                {"id": private_id},
            )
            assert early_delete.rowcount == 0

            connection.execute(
                text("SELECT set_config('app.current_organization_id', :org, true)"),
                {"org": str(second)},
            )
            assert (
                connection.execute(
                    text(
                        "SELECT delivery_id FROM public.email_delivery "
                        "WHERE delivery_id = :id"
                    ),
                    {"id": private_id},
                ).first()
                is None
            )

            savepoint = connection.begin_nested()
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        """
                        INSERT INTO public.email_delivery (
                            delivery_id, organization_id, content_digest, to_email,
                            subject, body_html
                        ) VALUES (
                            :id, :organization_id, :digest, 'other@example.com',
                            'Subject', '<p>Private</p>'
                        )
                        """
                    ),
                    {"id": uuid4(), "organization_id": first, "digest": "a" * 64},
                )
            savepoint.rollback()
        finally:
            transaction.rollback()

    assert event_id is not None and private_id is not None
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT 1 FROM platform.event_outbox WHERE event_id = :id"),
                {"id": event_id},
            ).first()
            is None
        )
        assert (
            connection.execute(
                text("SELECT 1 FROM public.email_delivery WHERE delivery_id = :id"),
                {"id": private_id},
            ).first()
            is None
        )


def test_email_delivery_table_has_forced_rls_and_minimal_app_grants(engine) -> None:
    with engine.connect() as connection:
        protected = connection.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE oid = 'public.email_delivery'::regclass"
            )
        ).one()
        assert protected == (True, True)
        grants = connection.execute(
            text(
                """
                SELECT
                    has_table_privilege('app_user', 'public.email_delivery', 'SELECT'),
                    has_table_privilege('app_user', 'public.email_delivery', 'INSERT'),
                    has_table_privilege('app_user', 'public.email_delivery', 'UPDATE'),
                    has_table_privilege('app_user', 'public.email_delivery', 'DELETE')
                """
            )
        ).one()
        assert grants == (True, True, False, True)


def test_app_user_can_delete_email_content_only_after_terminal_retention(
    engine,
) -> None:
    with engine.begin() as setup:
        organization_id = _insert_organization(setup, "RETENTION")
    event_id = None
    private_id = None
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                _scope_app_user(connection, organization_id)
                with Session(bind=connection) as db:
                    event = enqueue_email(
                        db,
                        delivery_id="canary:retention",
                        organization_id=organization_id,
                        to_email="supplier@example.com",
                        subject="Approved",
                        body_html="<p>Private approval</p>",
                    )
                    event_id = event.event_id
                    private_id = UUID(event.payload["delivery_id"])
                assert (
                    connection.execute(
                        text(
                            "DELETE FROM public.email_delivery WHERE delivery_id = :id"
                        ),
                        {"id": private_id},
                    ).rowcount
                    == 0
                )
                transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()

        with engine.begin() as settle:
            settle.execute(
                text(
                    "UPDATE platform.event_outbox "
                    "SET status = 'DEAD', terminal_at = now() - interval '31 days' "
                    "WHERE event_id = :id"
                ),
                {"id": event_id},
            )

        with engine.connect() as attacker:
            transaction = attacker.begin()
            try:
                _scope_app_user(attacker, organization_id)
                attacker.execute(
                    text(
                        "UPDATE platform.event_outbox "
                        "SET terminal_at = now() - interval '31 days' "
                        "WHERE event_id = :id"
                    ),
                    {"id": event_id},
                )
                assert attacker.scalar(
                    text(
                        "SELECT terminal_at > now() - interval '1 day' "
                        "FROM platform.event_outbox WHERE event_id = :id"
                    ),
                    {"id": event_id},
                )
                assert (
                    attacker.execute(
                        text(
                            "DELETE FROM public.email_delivery WHERE delivery_id = :id"
                        ),
                        {"id": private_id},
                    ).rowcount
                    == 0
                )
                transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()

        with engine.begin() as settle:
            _age_terminal_event_as_test_admin(settle, event_id)

        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                _scope_app_user(connection, organization_id)
                assert (
                    connection.execute(
                        text(
                            "DELETE FROM public.email_delivery WHERE delivery_id = :id"
                        ),
                        {"id": private_id},
                    ).rowcount
                    == 1
                )
                transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()

        with engine.connect() as attacker:
            transaction = attacker.begin()
            try:
                _scope_app_user(attacker, organization_id)
                with pytest.raises(DBAPIError, match="mature published status"):
                    attacker.execute(
                        text("DELETE FROM platform.event_outbox WHERE event_id = :id"),
                        {"id": event_id},
                    )
            finally:
                transaction.rollback()

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT 1 FROM public.email_delivery WHERE delivery_id = :id"),
                    {"id": private_id},
                ).first()
                is None
            )
            assert (
                connection.execute(
                    text(
                        "SELECT status FROM platform.event_outbox WHERE event_id = :id"
                    ),
                    {"id": event_id},
                ).scalar_one()
                == "DEAD"
            )
    finally:
        with engine.begin() as cleanup:
            cleanup.execute(
                text("SELECT set_config('app.current_organization_id', :org, true)"),
                {"org": str(organization_id)},
            )
            cleanup.execute(
                text(
                    "DELETE FROM public.email_delivery "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
            if event_id is not None:
                cleanup.execute(
                    text(
                        "ALTER TABLE platform.event_outbox "
                        "DISABLE TRIGGER email_outbox_retention_guard"
                    )
                )
                cleanup.execute(
                    text("DELETE FROM platform.event_outbox WHERE event_id = :id"),
                    {"id": event_id},
                )
                cleanup.execute(
                    text(
                        "ALTER TABLE platform.event_outbox "
                        "ENABLE TRIGGER email_outbox_retention_guard"
                    )
                )
            cleanup.execute(
                text(
                    "DELETE FROM core_org.organization "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )


def test_concurrent_same_key_enqueue_keeps_one_event_and_one_private_row(
    engine,
) -> None:
    """A loser's savepoint discards its payload and reloads the winner."""
    with engine.begin() as setup:
        organization_id = _insert_organization(setup, "CONFLICT")

    marker = f"email-conflict-{uuid4().hex}"
    key = (
        "email:"
        + hashlib.sha256(
            f"{organization_id}:{_CONFLICT_DELIVERY_ID}".encode()
        ).hexdigest()
    )
    try:
        with engine.connect() as winner_connection:
            winner_transaction = winner_connection.begin()
            try:
                _scope_app_user(winner_connection, organization_id)
                with Session(bind=winner_connection) as db:
                    winner = enqueue_email(
                        db,
                        delivery_id=_CONFLICT_DELIVERY_ID,
                        organization_id=organization_id,
                        to_email="supplier@example.com",
                        subject="Approved",
                        body_html="<p>Same business email</p>",
                    )
                    winner_result = (
                        winner.event_id,
                        UUID(winner.payload["delivery_id"]),
                    )

                ready = Event()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        _enqueue_competitor, engine, organization_id, marker, ready
                    )
                    started = ready.wait(timeout=5)
                    blocked = (
                        _wait_for_unique_conflict(engine, marker, future)
                        if started
                        else False
                    )
                    # Release the unique-key wait even if observation failed.
                    winner_transaction.commit()
                    loser_result = future.result(timeout=15)
                assert started and blocked, "loser never waited on the unique key"
            finally:
                if winner_transaction.is_active:
                    winner_transaction.rollback()

        assert loser_result == winner_result
        with engine.connect() as inspect_connection:
            assert (
                inspect_connection.scalar(
                    text(
                        "SELECT count(*) FROM platform.event_outbox "
                        "WHERE idempotency_key = :key"
                    ),
                    {"key": key},
                )
                == 1
            )
            assert (
                inspect_connection.scalar(
                    text(
                        "SELECT count(*) FROM public.email_delivery "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
                == 1
            )
    finally:
        with engine.begin() as cleanup:
            cleanup.execute(
                text("SELECT set_config('app.current_organization_id', :org, true)"),
                {"org": str(organization_id)},
            )
            cleanup.execute(
                text(
                    "DELETE FROM public.email_delivery "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
            cleanup.execute(
                text(
                    "ALTER TABLE platform.event_outbox "
                    "DISABLE TRIGGER email_outbox_retention_guard"
                )
            )
            cleanup.execute(
                text("DELETE FROM platform.event_outbox WHERE idempotency_key = :key"),
                {"key": key},
            )
            cleanup.execute(
                text(
                    "ALTER TABLE platform.event_outbox "
                    "ENABLE TRIGGER email_outbox_retention_guard"
                )
            )
            cleanup.execute(
                text(
                    "DELETE FROM core_org.organization "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )


def test_cleanup_task_deletes_published_pair_and_rolls_back_failed_pair(
    engine, monkeypatch
) -> None:
    """Run discovery, tenant lock, recheck and atomic delete against PostgreSQL."""
    from app.tasks.outbox_relay import cleanup_terminal_email_deliveries

    monkeypatch.setattr(
        "app.db.SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False),
    )
    organization_id, event_id, private_id = _create_terminal_email(engine, "CLEAN")
    try:
        result = cleanup_terminal_email_deliveries(batch_size=10)
        assert result["purged"] == 1
        assert result["published_deleted"] == 1
        with engine.connect() as inspect:
            assert (
                inspect.scalar(
                    text(
                        "SELECT count(*) FROM public.email_delivery WHERE delivery_id = :id"
                    ),
                    {"id": private_id},
                )
                == 0
            )
            assert (
                inspect.scalar(
                    text(
                        "SELECT count(*) FROM platform.event_outbox WHERE event_id = :id"
                    ),
                    {"id": event_id},
                )
                == 0
            )
    finally:
        _cleanup_canary_rows(engine, organization_id, event_id)

    organization_id, event_id, private_id = _create_terminal_email(engine, "ROLL")
    child_id = uuid4()
    try:
        # A dependent event makes the published-event DELETE fail after the
        # private DELETE has already flushed. Both mutations must roll back.
        with engine.begin() as setup:
            setup.execute(
                text(
                    """
                    INSERT INTO platform.event_outbox (
                        event_id, event_name, event_version, producer_module,
                        aggregate_type, aggregate_id, correlation_id,
                        causation_id, idempotency_key, payload, headers,
                        status, retry_count
                    ) VALUES (
                        :child_id, 'canary.email.cleanup.blocker', 1, 'test',
                        'Canary', :child_id_text, :child_id_text,
                        :parent_id, :child_id_text, '{}'::jsonb, '{}'::jsonb,
                        'PENDING', 0
                    )
                    """
                ),
                {
                    "child_id": child_id,
                    "child_id_text": str(child_id),
                    "parent_id": event_id,
                },
            )
        with pytest.raises(DBAPIError):
            cleanup_terminal_email_deliveries(batch_size=10)
        with engine.connect() as inspect:
            assert (
                inspect.scalar(
                    text(
                        "SELECT count(*) FROM public.email_delivery WHERE delivery_id = :id"
                    ),
                    {"id": private_id},
                )
                == 1
            )
            assert inspect.execute(
                text(
                    "SELECT status, email_payload_purged_at "
                    "FROM platform.event_outbox WHERE event_id = :id"
                ),
                {"id": event_id},
            ).one() == ("PUBLISHED", None)
    finally:
        with engine.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM platform.event_outbox WHERE event_id = :id"),
                {"id": child_id},
            )
        _cleanup_canary_rows(engine, organization_id, event_id)
