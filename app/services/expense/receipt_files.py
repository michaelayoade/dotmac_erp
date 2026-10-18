"""Expense receipt staging on the tenant files plane.

Receipts are ``dotmac-files`` tenant records: ``prepare_upload`` writes
the object under the canonical tenant key and
``stage_tenant_file_if_unreserved`` stages the record in the claim's
transaction. ``ExpenseClaimItem.receipt_url`` entries for staged
receipts are the bare file id; pre-cutover path/key entries keep their
legacy serving branches until the receipt backfill rewrites them.
"""

from __future__ import annotations

from uuid import UUID

from dotmac_files import FileError, FilePolicy, PreparedFile, prepare_upload
from dotmac_files.providers import StorageError
from sqlalchemy.orm import Session

from app.models.finance.core_org.organization import Organization
from app.services.file_object_cleanup import (
    CleanupUnsafeReferenceView,
    stage_tenant_file_if_unreserved,
)
from app.services.file_upload import (
    FileStorageError,
    FileUploadError,
    get_expense_receipt_upload,
)
from app.services.storage import get_dotmac_files_provider
from app.services.tenant_projection import reconcile_organization_tenant
from app.tenancy import OrganizationTenantContext


def receipt_policy() -> FilePolicy:
    """The module policy equivalent of the legacy receipt upload config."""
    config = get_expense_receipt_upload().config
    return FilePolicy(
        max_bytes=config.max_size_bytes,
        allowed_extensions=frozenset(config.allowed_extensions),
        allowed_media_types=frozenset(config.allowed_content_types),
    )


def stage_receipt(
    db: Session,
    organization_id: UUID,
    *,
    file_data: bytes,
    content_type: str | None,
    original_filename: str | None,
) -> PreparedFile:
    """Upload and stage one receipt; the caller commits the transaction."""
    scope = OrganizationTenantContext.for_organization(organization_id).tenant_scope
    try:
        prepared = prepare_upload(
            get_dotmac_files_provider(),
            scope=scope,
            policy=receipt_policy(),
            original_filename=original_filename or "receipt",
            declared_media_type=content_type or "",
            chunks=iter([file_data]),
        )
    except FileError as exc:
        raise FileUploadError(str(exc)) from exc
    except StorageError as exc:
        raise FileStorageError(str(exc)) from exc
    # Module rows FK to the kernel tenant catalogue; reconcile the
    # Organization's projection in this transaction (PostgreSQL only --
    # the SQLite unit harness has no tenant catalogue).
    if db.get_bind().dialect.name == "postgresql":
        organization = db.get(Organization, organization_id)
        if organization is not None:
            reconcile_organization_tenant(db, organization)
            db.flush()
    try:
        stage_tenant_file_if_unreserved(db, prepared=prepared)
    except CleanupUnsafeReferenceView as exc:
        raise FileUploadError(str(exc)) from exc
    return prepared


def receipt_file_id(entry: str) -> UUID | None:
    """The staged file id behind a post-cutover receipt entry, else None."""
    try:
        return UUID(entry.strip())
    except (ValueError, AttributeError):
        return None
