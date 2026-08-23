"""Three-phase workers for ERP's durable customer imports."""

from __future__ import annotations

from uuid import UUID

from celery import shared_task

from app.config import settings
from app.db.session_context import session_for_org


def enqueue_customer_import_run(
    organization_id: UUID,
    run_id: UUID,
    *,
    dry_run: bool,
) -> None:
    """Enqueue a recoverable run after its current transaction commits."""
    worker_count = settings.import_validation_workers if dry_run else 1
    for _ in range(worker_count):
        process_customer_import_partitions.delay(str(organization_id), str(run_id))


@shared_task
def process_customer_import_partitions(
    organization_id: str,
    run_id: str,
) -> dict[str, object]:
    """Claim, read and settle partitions until this worker finds none."""
    from app.services.finance.import_export.durable_customers import (
        authorize_customer_partition,
        get_customer_import,
        read_customer_partition,
        settle_customer_partition,
    )
    from app.services.storage import get_dotmac_files_provider

    tenant_id = UUID(organization_id)
    import_run_id = UUID(run_id)
    settled = 0
    while True:
        # Phase 1: authorize one bounded object and commit the lease.
        with session_for_org(tenant_id) as db:
            authorized = authorize_customer_partition(
                db,
                tenant_id=tenant_id,
                run_id=import_run_id,
            )
            db.commit()
        if authorized is None:
            break

        # Phase 2: provider I/O and checksum verification, with no session.
        prepared = read_customer_partition(
            get_dotmac_files_provider(),
            authorized,
        )

        # Phase 3: parity + domain effect + ledger checkpoint, one transaction.
        with session_for_org(tenant_id) as db:
            settle_customer_partition(
                db,
                prepared,
                authorized,
                tenant_id=tenant_id,
            )
            db.commit()
        settled += 1

    with session_for_org(tenant_id) as db:
        final = get_customer_import(db, tenant_id=tenant_id, run_id=import_run_id)

    return {
        "run_id": str(import_run_id),
        "partitions_settled": settled,
        "complete": final.complete,
    }


__all__ = [
    "enqueue_customer_import_run",
    "process_customer_import_partitions",
]
