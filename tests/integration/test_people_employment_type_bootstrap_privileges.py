"""PostgreSQL privilege proof for the pre-activation legacy source reader."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.people.hr import EmploymentType
from app.services.people.hr.employment_type_bootstrap import (
    BootstrapMode,
    EmploymentTypeBootstrapService,
)

pytestmark = pytest.mark.integration


def test_app_user_can_only_read_the_legacy_employment_type_source(
    engine: Engine,
) -> None:
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT has_schema_privilege('app_user', 'hr', 'USAGE')")
        )
        assert connection.scalar(
            text(
                "SELECT has_table_privilege('app_user', 'hr.employment_type', 'SELECT')"
            )
        )
        assert connection.scalar(
            text(
                "SELECT has_function_privilege("
                "'app_user', 'hr.lock_employment_type_bootstrap()', 'EXECUTE')"
            )
        )
        function = connection.execute(
            text(
                "SELECT p.prosecdef, p.proconfig "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'hr' "
                "AND p.proname = 'lock_employment_type_bootstrap' "
                "AND p.pronargs = 0"
            )
        ).one()
        assert function == (True, ["search_path=pg_catalog"])
        for privilege in (
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        ):
            assert not connection.scalar(
                text(
                    "SELECT has_table_privilege("
                    "'app_user', 'hr.employment_type', :privilege)"
                ),
                {"privilege": privilege},
            ), f"app_user unexpectedly has {privilege} on hr.employment_type"
        for privilege in ("INSERT", "UPDATE", "REFERENCES"):
            assert not connection.scalar(
                text(
                    "SELECT has_any_column_privilege("
                    "'app_user', 'hr.employment_type', :privilege)"
                ),
                {"privilege": privilege},
            ), f"app_user unexpectedly has column {privilege} on hr.employment_type"


def test_real_a2_bootstrap_preserves_identity_and_replays_to_unchanged(
    db: Session, engine: Engine, organization: object
) -> None:
    organization_id = organization.organization_id
    source_id = uuid4()
    created_at = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)
    updated_at = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
    db.execute(
        text(
            "INSERT INTO public.tenants "
            "(id, slug, name, is_active, created_at, updated_at) "
            "VALUES (:id, :slug, :name, true, :created_at, :updated_at)"
        ),
        {
            "id": organization_id,
            "slug": f"erp-{organization_id}",
            "name": "Employment Type Bootstrap Test",
            "created_at": created_at,
            "updated_at": updated_at,
        },
    )
    db.add(
        EmploymentType(
            employment_type_id=source_id,
            organization_id=organization_id,
            type_code="contract",
            type_name="Contract",
            description="Fixed-term engagement",
            is_active=True,
            created_at=created_at,
            updated_at=updated_at,
        )
    )
    db.flush()
    db.info["organization_id"] = organization_id
    db.info["tenant_id"] = organization_id
    db.execute(
        text("SELECT set_config('app.current_organization_id', :value, true)"),
        {"value": str(organization_id)},
    )
    db.execute(
        text("SELECT set_config('app.current_tenant', :value, true)"),
        {"value": str(organization_id)},
    )
    # Exercise the real online role after the fixture has installed source
    # facts. This proves the migration's SELECT grant and the module lineage's
    # target DML grants work together; a superuser-only rehearsal would not.
    db.execute(text("SET LOCAL ROLE app_user"))

    first = EmploymentTypeBootstrapService(db, organization_id=organization_id).execute(
        mode=BootstrapMode.COMMIT
    )

    # The SECURITY DEFINER helper acquired SHARE as app_user and PostgreSQL
    # keeps it until this bootstrap transaction commits or rolls back. A real
    # second connection therefore cannot obtain the ROW EXCLUSIVE table lock an
    # ordinary legacy writer needs, even with a different row predicate.
    with engine.connect() as writer:
        with pytest.raises(DBAPIError) as refused:
            with writer.begin():
                writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
                writer.execute(
                    text(
                        "UPDATE hr.employment_type SET type_name = type_name "
                        "WHERE employment_type_id = :source_id"
                    ),
                    {"source_id": source_id},
                )
        assert getattr(refused.value.orig, "sqlstate", None) == "55P03"

    replay = EmploymentTypeBootstrapService(
        db, organization_id=organization_id
    ).execute(mode=BootstrapMode.REPLAY)

    target = db.execute(
        text(
            "SELECT id, tenant_id, code, name, description, is_active, "
            "created_at, updated_at FROM mod_people.employment_types "
            "WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": organization_id},
    ).one()
    assert target == (
        source_id,
        organization_id,
        "CONTRACT",
        "Contract",
        "Fixed-term engagement",
        True,
        created_at,
        updated_at,
    )
    assert (first.created, first.updated, first.unchanged) == (1, 0, 0)
    assert (replay.created, replay.updated, replay.unchanged) == (0, 0, 1)
    assert first.source_fingerprint_set_digest == replay.source_fingerprint_set_digest
    assert first.target_after_fingerprint_set_digest == (
        replay.target_after_fingerprint_set_digest
    )
