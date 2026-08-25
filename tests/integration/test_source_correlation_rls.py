"""PostgreSQL proof for the source-correlation tenant boundary."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def _organization_values(suffix: str) -> dict[str, object]:
    return {
        "organization_id": uuid4(),
        "organization_code": f"RLS-{suffix}-{uuid4().hex[:8].upper()}",
        "legal_name": f"Source correlation RLS {suffix}",
    }


def test_app_user_cannot_read_another_organizations_source_correlation(engine) -> None:
    first = _organization_values("A")
    second = _organization_values("B")

    connection = engine.connect()
    transaction = connection.begin()
    try:
        for organization in (first, second):
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
                organization,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO sync.source_correlation (
                        mapping_id, organization_id, source_application,
                        source_entity_type, source_reference,
                        local_entity_type, local_entity_id, source_status,
                        display_name
                    ) VALUES (
                        :mapping_id, :organization_id, 'sub', 'PROJECT',
                        :source_reference, 'project', :local_entity_id,
                        'ACTIVE', :display_name
                    )
                    """
                ),
                {
                    "mapping_id": uuid4(),
                    "organization_id": organization["organization_id"],
                    "source_reference": str(uuid4()),
                    "local_entity_id": uuid4(),
                    "display_name": organization["legal_name"],
                },
            )

        connection.execute(text("SET LOCAL ROLE app_user"))
        connection.execute(
            text("SELECT set_config('app.current_organization_id', :org, true)"),
            {"org": str(first["organization_id"])},
        )
        visible = list(connection.execute(
            text(
                "SELECT organization_id FROM sync.source_correlation "
                "WHERE source_application = 'sub'"
            )
        ).scalars())

        assert visible == [first["organization_id"]]
        assert second["organization_id"] not in visible

        attempted_mapping_id = uuid4()
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    """
                    INSERT INTO sync.source_correlation (
                        mapping_id, organization_id, source_application,
                        source_entity_type, source_reference,
                        local_entity_type, local_entity_id, source_status,
                        display_name
                    ) VALUES (
                        :mapping_id, :organization_id, 'sub', 'PROJECT',
                        :source_reference, 'project', :local_entity_id,
                        'ACTIVE', 'Rejected cross-org insert'
                    )
                    """
                ),
                {
                    "mapping_id": attempted_mapping_id,
                    "organization_id": second["organization_id"],
                    "source_reference": str(uuid4()),
                    "local_entity_id": uuid4(),
                },
            )
        savepoint.rollback()

        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE sync.source_correlation "
                    "SET organization_id = :other "
                    "WHERE organization_id = :current"
                ),
                {
                    "other": second["organization_id"],
                    "current": first["organization_id"],
                },
            )
        savepoint.rollback()

        unchanged = connection.execute(
            text(
                "SELECT organization_id FROM sync.source_correlation "
                "WHERE source_application = 'sub'"
            )
        ).scalars().all()
        assert unchanged == [first["organization_id"]]
    finally:
        transaction.rollback()
        connection.close()
