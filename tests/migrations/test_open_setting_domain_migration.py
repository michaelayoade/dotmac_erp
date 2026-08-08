"""Predecessor-to-candidate upgrade for the open-setting-domain migration.

ERP's other migration tests read the migration source. That is worth having, but
it cannot answer the questions that matter here — whether `USING domain::text`
actually preserves the stored values, and whether the `settingdomain` type is
really gone afterwards. Those are properties of PostgreSQL, not of the source.

So this builds a real predecessor database: the enum type and the
`domain_settings` shape as they existed BEFORE this change, seeded with a row
under the enum, stamped at the previous head, and then upgraded one revision.

Constructing the predecessor by hand rather than by running ~100 prior
migrations is why the stamp exists: the candidate revision only touches this one
table and this one type, so the rest of the schema is irrelevant to what is
being proved, and reproducing it would make the test slow and fragile for no
extra signal.

Requires PostgreSQL: `pytest -m integration`.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

PREVIOUS_HEAD = "20260802_add_outbox_claim_lease_columns"
CANDIDATE = "20260808_open_setting_domain"

# The predecessor shape: the enum type, and only the columns this migration
# touches or the writes below need.
_LEGACY_MEMBERS = (
    "auth",
    "audit",
    "scheduler",
    "automation",
    "email",
    "features",
    "reporting",
    "payments",
    "operations",
    "support",
    "inventory",
    "projects",
    "fleet",
    "procurement",
    "settings",
    "payroll",
    "banking",
    "coach",
    "notifications",
    "expense",
    "gl",
)


def _admin_url() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL/DATABASE_URL not set")
    return url


@pytest.fixture()
def predecessor_engine() -> Iterator[Engine]:
    """An ISOLATED database carrying the pre-migration shape.

    Its own database, not a schema in the shared one: the migration issues
    `DROP TYPE public.settingdomain`, and doing that anywhere near another
    test's data would be reckless.
    """
    admin = sa.create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    name = f"erp_setting_domain_{uuid.uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))

    target_url = admin.url.set(database=name)
    engine = sa.create_engine(target_url)
    members = ", ".join(f"'{m}'" for m in _LEGACY_MEMBERS)
    with engine.begin() as conn:
        conn.execute(sa.text(f"CREATE TYPE public.settingdomain AS ENUM ({members})"))
        conn.execute(
            sa.text(
                """
                CREATE TABLE public.domain_settings (
                    id UUID PRIMARY KEY,
                    domain public.settingdomain NOT NULL,
                    key VARCHAR(120) NOT NULL,
                    organization_id UUID NULL,
                    value_type VARCHAR(20) NOT NULL DEFAULT 'string',
                    value_text TEXT NULL,
                    value_json JSON NULL,
                    is_secret BOOLEAN NOT NULL DEFAULT FALSE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE
                )
                """
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE TABLE public.domain_setting_history (
                    id UUID PRIMARY KEY,
                    setting_id UUID NULL,
                    domain VARCHAR(50) NOT NULL,
                    key VARCHAR(120) NOT NULL,
                    organization_id UUID NULL,
                    action VARCHAR(20) NOT NULL DEFAULT 'UPDATE'
                )
                """
            )
        )
        # VARCHAR(64), matching `alembic/versions/extend_alembic_version.py`,
        # which widened this column from Alembic's default 32. The predecessor
        # built here descends from that revision, so it carries that width.
        conn.execute(
            sa.text(
                "CREATE TABLE public.alembic_version ("
                "  version_num VARCHAR(64) NOT NULL,"
                "  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _seed_enum_backed_row(
    engine: Engine, domain: str, key: str, value: str
) -> uuid.UUID:
    row_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO public.domain_settings "
                "(id, domain, key, value_type, value_text) "
                "VALUES (:id, CAST(:domain AS public.settingdomain), :key, "
                "'string', :value)"
            ),
            {"id": row_id, "domain": domain, "key": key, "value": value},
        )
    return row_id


@contextmanager
def _alembic_against(engine: Engine) -> Iterator[Config]:
    """Alembic config pointed at `engine`.

    Setting `sqlalchemy.url` on the Config is NOT enough: `alembic/env.py:44`
    overwrites it with `settings.database_url` on every run, so a test that only
    sets the Config silently migrates whatever the ambient settings point at —
    SQLite under the test conftest. Patching the settings object is what env.py
    actually reads.
    """
    from app.config import settings

    url = engine.url.render_as_string(hide_password=False)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    with mock.patch.object(settings, "database_url", url):
        yield config


def _upgrade(engine: Engine) -> None:
    with _alembic_against(engine) as config:
        command.stamp(config, PREVIOUS_HEAD, purge=True)
        command.upgrade(config, CANDIDATE)


def test_upgrade_preserves_values_drops_the_type_and_opens_the_column(
    predecessor_engine: Engine,
) -> None:
    """One upgrade, four properties — they are asserted together because they
    are only meaningful together: a migration that dropped the type but lost the
    rows would satisfy any one of them alone."""
    kept = _seed_enum_backed_row(predecessor_engine, "payments", "provider", "paystack")
    # `operations` is declared by nothing after this change. Its rows must still
    # survive the conversion — becoming unwritable is not the same as being
    # deleted, and a migration that quietly removed them would be data loss.
    orphaned = _seed_enum_backed_row(
        predecessor_engine, "operations", "legacy_flag", "true"
    )

    _upgrade(predecessor_engine)

    with predecessor_engine.connect() as conn:
        rows = dict(
            conn.execute(
                sa.text("SELECT id, domain FROM public.domain_settings")
            ).fetchall()
        )
        assert rows[kept] == "payments"
        assert rows[orphaned] == "operations", "an undeclared domain's rows were lost"

        value = conn.execute(
            sa.text("SELECT value_text FROM public.domain_settings WHERE id = :id"),
            {"id": kept},
        ).scalar_one()
        assert value == "paystack"

        column = conn.execute(
            sa.text(
                "SELECT data_type, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_name = 'domain_settings' AND column_name = 'domain'"
            )
        ).one()
        assert column.data_type == "character varying"
        assert column.character_maximum_length == 120

        assert (
            conn.execute(sa.text("SELECT to_regtype('public.settingdomain')")).scalar()
            is None
        ), "the settingdomain enum type survived the migration"


def test_the_history_column_is_widened_to_match(predecessor_engine: Engine) -> None:
    """`domain_setting_history.domain` was VARCHAR(50) while the live column
    becomes 120 — a 51-120 character domain would otherwise store and then fail
    when its change was recorded."""
    _upgrade(predecessor_engine)

    with predecessor_engine.connect() as conn:
        width = conn.execute(
            sa.text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'domain_setting_history' AND column_name = 'domain'"
            )
        ).scalar_one()
    assert width == 120

    long_domain = "d" * 100
    with predecessor_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO public.domain_setting_history (id, domain, key, action) "
                "VALUES (:id, :domain, 'k', 'UPDATE')"
            ),
            {"id": uuid.uuid4(), "domain": long_domain},
        )
        stored = conn.execute(
            sa.text(
                "SELECT domain FROM public.domain_setting_history WHERE domain = :d"
            ),
            {"d": long_domain},
        ).scalar_one()
    assert stored == long_domain


def test_history_rows_survive_the_upgrade(predecessor_engine: Engine) -> None:
    """Widening a column must not disturb what is already in it."""
    row_id = uuid.uuid4()
    with predecessor_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO public.domain_setting_history (id, domain, key, action) "
                "VALUES (:id, 'payments', 'provider', 'UPDATE')"
            ),
            {"id": row_id},
        )

    _upgrade(predecessor_engine)

    with predecessor_engine.connect() as conn:
        assert (
            conn.execute(
                sa.text(
                    "SELECT domain FROM public.domain_setting_history WHERE id = :id"
                ),
                {"id": row_id},
            ).scalar_one()
            == "payments"
        )


def test_a_newly_declared_domain_stores_in_the_widened_column(
    predecessor_engine: Engine,
) -> None:
    """SCHEMA-level only: that the converted column accepts a domain the old
    enum never contained.

    That the live ORM write path accepts it is a different claim, proved where
    the listener actually runs — `tests/architecture/test_setting_domains.py::
    test_the_live_write_path_accepts_a_newly_declared_domain`. Asserting it here
    through raw SQL would look like proof and be none: raw SQL bypasses the
    listener entirely.
    """
    _upgrade(predecessor_engine)

    with predecessor_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO public.domain_settings (id, domain, key, value_type, "
                "value_text) VALUES (:id, :domain, 'sample_rate', 'string', '0.1')"
            ),
            {"id": uuid.uuid4(), "domain": "telemetry"},
        )
        stored = conn.execute(
            sa.text(
                "SELECT domain FROM public.domain_settings WHERE domain = 'telemetry'"
            )
        ).scalar_one()
    assert stored == "telemetry"


def test_an_undeclared_domain_is_refused_by_the_registry(
    predecessor_engine: Engine,
) -> None:
    """The column accepts any string now, so the refusal has to come from the
    registry and the ORM listener — this pins that it does."""
    from app.services.setting_domains import UndeclaredSettingDomainError, registry

    _upgrade(predecessor_engine)

    with pytest.raises(UndeclaredSettingDomainError):
        registry().require("not-a-real-domain")

    # ...and the database will happily take it, which is exactly why the check
    # cannot live there.
    with predecessor_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO public.domain_settings (id, domain, key, value_type, "
                "value_text) VALUES (:id, 'not-a-real-domain', 'k', 'string', 'v')"
            ),
            {"id": uuid.uuid4()},
        )


def test_downgrade_is_destructive_and_says_so(predecessor_engine: Engine) -> None:
    """Downgrade restores the enum, so a row under a domain the enum never had
    cannot survive. Asserted rather than merely documented."""
    kept = _seed_enum_backed_row(predecessor_engine, "fleet", "k", "v")
    _upgrade(predecessor_engine)

    with predecessor_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO public.domain_settings (id, domain, key, value_type, "
                "value_text) VALUES (:id, 'telemetry', 'k', 'string', 'v')"
            ),
            {"id": uuid.uuid4()},
        )

    with _alembic_against(predecessor_engine) as config:
        command.downgrade(config, PREVIOUS_HEAD)

    with predecessor_engine.connect() as conn:
        remaining = (
            conn.execute(sa.text("SELECT id FROM public.domain_settings"))
            .scalars()
            .all()
        )
        assert kept in remaining
        assert len(remaining) == 1, "the newly-declared row should have been removed"
        assert (
            conn.execute(sa.text("SELECT to_regtype('public.settingdomain')")).scalar()
            is not None
        )
