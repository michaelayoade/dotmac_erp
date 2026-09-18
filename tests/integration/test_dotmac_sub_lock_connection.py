"""Real PostgreSQL proof that an ORM commit cannot release the lock connection."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.tasks.dotmac_sub import (
    _release_incremental_sync_lock,
    _try_acquire_incremental_sync_lock,
)


@pytest.mark.integration
def test_incremental_lock_survives_commit_and_releases_same_backend():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip(
            "TEST_DATABASE_URL must point to a disposable PostgreSQL test database"
        )
    # The CI URL uses the generic dialect; this project installs psycopg 3.
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    # ORM work, the dedicated lock owner and the competing backend each need
    # a connection. Keep overflow disabled so this remains a bounded proof.
    engine = create_engine(url, pool_size=3, max_overflow=0, pool_timeout=5)
    org = uuid4()
    identity = {"key": f"dotmac_sub:incremental:{org}"}
    acquire = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
    release = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")
    try:
        with Session(engine) as db, engine.connect() as competitor:
            assert _try_acquire_incremental_sync_lock(db, org)
            try:
                db.execute(text("SELECT 1"))
                assert competitor.scalar(acquire, identity) is False
                db.commit()
                assert competitor.scalar(acquire, identity) is False
                db.execute(text("SELECT 1"))
                db.rollback()
                assert competitor.scalar(acquire, identity) is False
            finally:
                assert _release_incremental_sync_lock(db, org)
            assert competitor.scalar(acquire, identity) is True
            assert competitor.scalar(release, identity) is True
    finally:
        engine.dispose()
