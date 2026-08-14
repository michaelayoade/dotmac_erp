"""PostgreSQL canary for concurrent startup settings seeding."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.db.session_context import allow_cross_org
from app.models.domain_settings import (
    DomainSetting,
    DomainSettingHistory,
    SettingDomain,
    SettingValueType,
)
from app.services.domain_settings import DomainSettings

pytestmark = pytest.mark.integration


def test_concurrent_workers_seed_one_global_setting(engine) -> None:
    """Every worker may seed at startup, but only one row may be created."""
    key = f"concurrent_seed_{uuid.uuid4().hex}"
    workers = 4
    ready = Barrier(workers)
    sessions = sessionmaker(bind=engine)

    def seed() -> uuid.UUID:
        with sessions() as db:
            ready.wait(timeout=10)
            with allow_cross_org(db):
                setting = DomainSettings(SettingDomain.auth).ensure_by_key(
                    db,
                    key=key,
                    value_type=SettingValueType.string,
                    value_text="canary",
                    organization_id=None,
                )
            return setting.id

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            setting_ids = list(pool.map(lambda _: seed(), range(workers)))

        assert len(set(setting_ids)) == 1
        with sessions() as db, allow_cross_org(db):
            rows = list(
                db.scalars(
                    select(DomainSetting).where(
                        DomainSetting.domain == SettingDomain.auth,
                        DomainSetting.key == key,
                        DomainSetting.organization_id.is_(None),
                    )
                )
            )
            assert len(rows) == 1
    finally:
        with sessions() as db, allow_cross_org(db):
            db.execute(
                delete(DomainSettingHistory).where(
                    DomainSettingHistory.domain == SettingDomain.auth,
                    DomainSettingHistory.key == key,
                    DomainSettingHistory.organization_id.is_(None),
                )
            )
            db.execute(
                delete(DomainSetting).where(
                    DomainSetting.domain == SettingDomain.auth,
                    DomainSetting.key == key,
                    DomainSetting.organization_id.is_(None),
                )
            )
            db.commit()
