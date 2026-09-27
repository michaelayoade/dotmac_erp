"""PostgreSQL runtime-role canary for the managed tenant file report."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_files import ObjectInfo
from dotmac_kernel.cache import TenantScope
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.file_object_reconciliation import report_file_objects

pytestmark = pytest.mark.integration


def test_app_user_reads_only_the_selected_tenants_file_rows(engine) -> None:
    first_id = uuid4()
    second_id = uuid4()
    first_key = f"tenants/{first_id}/files/{uuid4()}"
    second_key = f"tenants/{second_id}/files/{uuid4()}"
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text(
                "INSERT INTO public.tenants (id, slug, name, is_active) "
                "VALUES (:id, :slug, :name, true)"
            ),
            [
                {"id": first_id, "slug": f"file-test-{first_id}", "name": "Files A"},
                {"id": second_id, "slug": f"file-test-{second_id}", "name": "Files B"},
            ],
        )
        connection.execute(
            text(
                "INSERT INTO mod_files.stored_files "
                "(id, tenant_id, provider_code, storage_key, original_filename, "
                "size_bytes, declared_media_type, detected_media_type, "
                "checksum_sha256, state) VALUES "
                "(:id, :tenant_id, 'erp_s3', :key, 'opaque.csv', 3, "
                "'text/csv', 'text/csv', :checksum, 'available')"
            ),
            [
                {
                    "id": uuid4(),
                    "tenant_id": first_id,
                    "key": first_key,
                    "checksum": "sha256:" + "a" * 64,
                },
                {
                    "id": uuid4(),
                    "tenant_id": second_id,
                    "key": second_key,
                    "checksum": "sha256:" + "b" * 64,
                },
            ],
        )
        connection.execute(text("SET LOCAL ROLE app_user"))
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(first_id)},
        )
        assert connection.scalar(text("SELECT current_user")) == "app_user"
        visible = (
            connection.execute(text("SELECT storage_key FROM mod_files.stored_files"))
            .scalars()
            .all()
        )
        assert visible == [first_key]

        with Session(bind=connection) as db:
            report = report_file_objects(
                db,
                scope=TenantScope(first_id),
                provider_code="erp_s3",
                observations=(
                    ObjectInfo(
                        first_key,
                        3,
                        datetime.now(UTC) - timedelta(days=3),
                    ),
                ),
                observed_at=datetime.now(UTC),
            )
        assert report.safe_summary()["referenced_objects"] == 1
        assert report.candidate_keys == ()
        assert report.missing_references == ()
        assert report.boundary_drift == ()
    finally:
        transaction.rollback()
        connection.close()
