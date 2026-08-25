"""ERP-owned customer ports over the reusable durable import ledger.

``dotmac-imports`` owns runs, partitions, claims and outcomes.  This adapter is
where CSV fields acquire ERP meaning and where a valid row reaches the one
customer writer.  Storage preparation is session-free; every function that
accepts a session only mutates and flushes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import BinaryIO, Protocol, cast
from uuid import UUID

from dotmac_files import (
    FilePolicy,
    PreparedFile,
    StorageProvider,
    StoredObjectRef,
    download_target,
    prepare_upload,
    stage_file,
)
from dotmac_imports import (
    ColumnMapping,
    FieldSet,
    FieldSpec,
    ImportIssue,
    ImportRun,
    ImportRowOutcome,
    PartitionClaim,
    PartitionDescriptor,
    PreparedPartition,
    RowSkipped,
    RowStatus,
    RunStatus,
    SourceDocument,
    apply_claimed_partition,
    apply_mapping,
    auto_map,
    create_dry_run,
    get_run,
    get_run_outcomes,
    iter_csv_partitions,
    preview,
    promote,
    read_claimed_partition,
    register_partition_plan,
    validate_claimed_partition,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.ar.customer import Customer, CustomerType, RiskCategory
from app.services.file_upload import AsyncUpload, prepare_tenant_import_csv
from app.services.finance.ar import CustomerInput, customer_service
from app.services.finance.import_export.base import ImportConfig
from app.services.finance.import_export.contacts import (
    CustomerImporter,
    customer_source_mappings,
    get_ar_control_account,
)
from app.tenancy import OrganizationTenantContext

CUSTOMER_IMPORT_KIND = "finance.customer_master.v1"


class ImportStorageProvider(StorageProvider, Protocol):
    """The dotmac-files seam narrowed to the binary stream CSV needs."""

    def open(self, key: str) -> BinaryIO: ...


class CustomerImportParityError(RuntimeError):
    """The durable verdict differs from ERP's retiring importer."""


class CustomerImportConfigurationError(RuntimeError):
    """ERP cannot apply or validate a customer import safely."""


@dataclass(frozen=True, slots=True, repr=False)
class PreparedCustomerImport:
    source_file: PreparedFile
    partition_files: tuple[PreparedFile, ...]
    source: SourceDocument
    mapping: ColumnMapping
    descriptors: tuple[PartitionDescriptor, ...]


@dataclass(frozen=True, slots=True)
class CustomerImportRunSnapshot:
    run_id: UUID
    status: RunStatus
    dry_run: bool
    processed: int
    ok: int
    failed: int
    skipped: int

    @property
    def complete(self) -> bool:
        return self.status in {RunStatus.DRY_RUN_READY, RunStatus.COMPLETED}


@dataclass(frozen=True, slots=True, repr=False)
class ClaimedCustomerPartition:
    claim: PartitionClaim
    target: StoredObjectRef
    created_by: UUID
    ar_control_account_id: UUID
    dry_run: bool


class DurableCustomerImportService:
    """Application workflow presented to ERP's HTTP adapter.

    The service owns sequencing but never the transaction boundary: callers
    commit the staged run before enqueuing work, and commit a promotion before
    an apply worker may claim it.
    """

    def __init__(
        self,
        db: Session,
        provider: ImportStorageProvider | None = None,
    ) -> None:
        self.db = db
        self.provider = provider

    async def create_dry_run(
        self,
        upload: AsyncUpload,
        *,
        tenant_id: UUID,
        created_by: UUID,
    ) -> CustomerImportRunSnapshot:
        if self.provider is None:
            raise CustomerImportConfigurationError(
                "customer import storage is not configured"
            )
        source_file = await prepare_tenant_import_csv(
            upload,
            tenant_id=tenant_id,
            provider=self.provider,
        )
        prepared = prepare_customer_import(
            self.provider,
            tenant_id=tenant_id,
            source_file=source_file,
        )
        return record_customer_dry_run(
            self.db,
            tenant_id=tenant_id,
            created_by=created_by,
            prepared=prepared,
        )

    def get(self, *, tenant_id: UUID, run_id: UUID) -> CustomerImportRunSnapshot:
        return get_customer_import(self.db, tenant_id=tenant_id, run_id=run_id)

    def outcomes(
        self, *, tenant_id: UUID, run_id: UUID
    ) -> tuple[ImportRowOutcome, ...]:
        return customer_import_outcomes(
            self.db,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    def promote(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        created_by: UUID,
    ) -> CustomerImportRunSnapshot:
        return promote_customer_import(
            self.db,
            tenant_id=tenant_id,
            run_id=run_id,
            created_by=created_by,
        )


def customer_field_set() -> FieldSet:
    """Project ERP's canonical legacy mappings into the shared mechanism."""
    return FieldSet(
        tuple(
            FieldSpec(
                mapping.target_field,
                required=mapping.required,
                aliases=frozenset({mapping.source_field}),
            )
            for mapping in customer_source_mappings()
        )
    )


def _source(prepared: PreparedFile) -> SourceDocument:
    prefix = "sha256:"
    if not prepared.checksum_sha256.startswith(prefix):
        raise ValueError("stored source does not carry a SHA-256 identity")
    return SourceDocument(
        file_id=prepared.id,
        checksum_sha256=prepared.checksum_sha256.removeprefix(prefix),
    )


def prepare_customer_import(
    provider: ImportStorageProvider,
    *,
    tenant_id: UUID,
    source_file: PreparedFile,
) -> PreparedCustomerImport:
    """Stream, partition and store a customer CSV without a DB session."""
    source = _source(source_file)
    scope = OrganizationTenantContext.for_organization(tenant_id).tenant_scope
    if source_file.scope != scope:
        raise TypeError("customer imports require tenant-scoped files")
    files: list[PreparedFile] = []
    descriptors: list[PartitionDescriptor] = []
    mapping: ColumnMapping | None = None
    partition_policy = FilePolicy(
        max_bytes=settings.import_partition_max_bytes,
        allowed_extensions=frozenset({".csv"}),
        allowed_media_types=frozenset({"text/csv"}),
    )
    for payload in iter_csv_partitions(
        source,
        open_source=lambda: provider.open(source_file.storage_key),
        partition_rows=settings.import_partition_rows,
        max_partition_bytes=settings.import_partition_max_bytes,
    ):
        prepared = prepare_upload(
            provider,
            scope=scope,
            policy=partition_policy,
            original_filename=f"customer-partition-{payload.ordinal:06d}.csv",
            declared_media_type="text/csv",
            chunks=(payload.data,),
        )
        checksum = prepared.checksum_sha256.removeprefix("sha256:")
        if checksum != payload.checksum_sha256:
            raise RuntimeError("stored partition checksum differs from its payload")
        files.append(prepared)
        descriptors.append(
            PartitionDescriptor(
                ordinal=payload.ordinal,
                start_row=payload.start_row,
                row_count=payload.row_count,
                file_id=prepared.id,
                checksum_sha256=checksum,
                byte_size=prepared.size_bytes,
            )
        )
        if mapping is None:
            from dotmac_imports import decode

            columns, rows = decode(payload.data, source=_source(prepared))
            proposed = preview(columns, rows, customer_field_set())
            if not proposed.is_mappable:
                raise ValueError("customer CSV is missing required columns")
            mapping = auto_map(columns, customer_field_set())
    if mapping is None:
        raise ValueError("customer CSV contains no data rows")
    return PreparedCustomerImport(
        source_file=source_file,
        partition_files=tuple(files),
        source=source,
        mapping=mapping,
        descriptors=tuple(descriptors),
    )


def record_customer_dry_run(
    db: Session,
    *,
    tenant_id: UUID,
    created_by: UUID,
    prepared: PreparedCustomerImport,
) -> CustomerImportRunSnapshot:
    """Stage file metadata and one immutable dry-run plan."""
    stage_file(db, prepared=prepared.source_file)
    for item in prepared.partition_files:
        stage_file(db, prepared=item)
    run = create_dry_run(
        db,
        tenant_id=tenant_id,
        kind=CUSTOMER_IMPORT_KIND,
        source=prepared.source,
        mapping=prepared.mapping,
        created_by=str(created_by),
    )
    register_partition_plan(
        db,
        tenant_id=tenant_id,
        run_id=run.id,
        source=prepared.source,
        descriptors=prepared.descriptors,
    )
    return _snapshot(run)


def get_customer_import(
    db: Session, *, tenant_id: UUID, run_id: UUID
) -> CustomerImportRunSnapshot:
    run = get_run(db, tenant_id=tenant_id, run_id=run_id)
    if run.kind != CUSTOMER_IMPORT_KIND:
        raise ValueError("import run is not a customer-master run")
    return _snapshot(run)


def customer_import_outcomes(
    db: Session, *, tenant_id: UUID, run_id: UUID
) -> tuple[ImportRowOutcome, ...]:
    get_customer_import(db, tenant_id=tenant_id, run_id=run_id)
    return get_run_outcomes(db, tenant_id=tenant_id, run_id=run_id)


def promote_customer_import(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    created_by: UUID,
) -> CustomerImportRunSnapshot:
    validated = get_run(db, tenant_id=tenant_id, run_id=run_id)
    if validated.kind != CUSTOMER_IMPORT_KIND:
        raise ValueError("import run is not a customer-master run")
    applied = promote(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        source=SourceDocument(
            file_id=validated.source_file_id,
            checksum_sha256=validated.source_checksum_sha256,
        ),
        created_by=str(created_by),
    )
    return _snapshot(applied)


def authorize_customer_partition(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
) -> ClaimedCustomerPartition | None:
    from dotmac_imports import claim_partition

    run = get_run(db, tenant_id=tenant_id, run_id=run_id)
    if run.kind != CUSTOMER_IMPORT_KIND:
        raise ValueError("import run is not a customer-master run")
    try:
        created_by = UUID(str(run.created_by))
    except (TypeError, ValueError):
        raise CustomerImportConfigurationError(
            "customer import has no valid operator identity"
        ) from None
    ar_control_account_id = get_ar_control_account(db, tenant_id)
    if ar_control_account_id is None:
        raise CustomerImportConfigurationError(
            "customer import requires an AR control account"
        )
    claim = claim_partition(db, tenant_id=tenant_id, run_id=run_id)
    if claim is None:
        return None
    target = download_target(
        db,
        scope=OrganizationTenantContext.for_organization(tenant_id).tenant_scope,
        file_id=claim.file_id,
    )
    return ClaimedCustomerPartition(
        claim=claim,
        target=target,
        created_by=created_by,
        ar_control_account_id=ar_control_account_id,
        dry_run=bool(run.dry_run),
    )


def read_customer_partition(
    provider: ImportStorageProvider,
    authorized: ClaimedCustomerPartition,
) -> PreparedPartition:
    """Read and verify one authorized object with no database session."""
    from dotmac_files import open_object

    def open_partition(file_id: UUID) -> BinaryIO:
        if file_id != authorized.target.id:
            raise ValueError("worker was offered an unauthorized partition")
        # ``open_object`` preserves provider/state authorization but publishes
        # the intentionally minimal ReadableObject protocol.  This adapter's
        # narrower provider contract guarantees the BinaryIO required by the
        # streaming CSV reader.
        return cast(BinaryIO, open_object(provider, target=authorized.target))

    return read_claimed_partition(
        authorized.claim,
        open_partition=open_partition,
    )


def settle_customer_partition(
    db: Session,
    prepared: PreparedPartition,
    authorized: ClaimedCustomerPartition,
    *,
    tenant_id: UUID,
) -> None:
    """Settle validation or application inside the caller's transaction."""
    port = CustomerImportPort(
        db,
        tenant_id,
        authorized.created_by,
        authorized.ar_control_account_id,
        skip_duplicates=True,
    )
    if authorized.dry_run:
        assert_legacy_customer_parity(
            db,
            prepared,
            tenant_id=tenant_id,
            created_by=authorized.created_by,
            ar_control_account_id=authorized.ar_control_account_id,
            skip_duplicates=True,
        )
        validate_claimed_partition(
            db,
            prepared,
            fields=customer_field_set(),
            validator=port,
        )
    else:
        apply_claimed_partition(
            db,
            prepared,
            fields=customer_field_set(),
            validator=port,
            applier=port,
        )


def assert_legacy_customer_parity(
    db: Session,
    prepared: PreparedPartition,
    *,
    tenant_id: UUID,
    created_by: UUID,
    ar_control_account_id: UUID,
    skip_duplicates: bool,
) -> None:
    """Refuse settlement if any row differs from the retiring dry-run path."""
    run = get_run(db, tenant_id=tenant_id, run_id=prepared.claim.run_id)
    mapping = ColumnMapping(
        tuple((str(pair[0]), str(pair[1])) for pair in (run.column_mapping or []))
    )
    port = CustomerImportPort(
        db,
        tenant_id,
        created_by,
        ar_control_account_id,
        skip_duplicates=skip_duplicates,
    )
    for raw_pairs in prepared.rows:
        raw = dict(raw_pairs)
        legacy = CustomerImporter(
            db,
            ImportConfig(
                organization_id=tenant_id,
                user_id=created_by,
                skip_duplicates=skip_duplicates,
                dry_run=True,
            ),
            ar_control_account_id,
        ).import_rows([raw])
        if legacy.error_count:
            old = RowStatus.ERROR
        elif legacy.skipped_count:
            old = RowStatus.SKIPPED
        else:
            old = RowStatus.OK
        try:
            issues = tuple(port.validate(apply_mapping(raw, mapping)))
        except RowSkipped:
            new = RowStatus.SKIPPED
        else:
            new = RowStatus.ERROR if issues else RowStatus.OK
        if old is not new:
            raise CustomerImportParityError(
                "customer dry-run verdict differs from the retiring importer"
            )


class CustomerImportPort:
    """One object implementing ERP's validator and applier ports."""

    REQUIRED_DISPLAY_NAME = ImportIssue(
        "customer_display_name_required",
        "Customer display name is required",
    )

    def __init__(
        self,
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        ar_control_account_id: UUID,
        *,
        skip_duplicates: bool,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.user_id = user_id
        self.ar_control_account_id = ar_control_account_id
        self.skip_duplicates = skip_duplicates

    def validate(self, row: Mapping[str, str]) -> Sequence[ImportIssue]:
        display_name = str(row.get("display_name", "") or "").strip()
        if not display_name:
            return (self.REQUIRED_DISPLAY_NAME,)
        if self.skip_duplicates and self._duplicate(display_name):
            raise RowSkipped(
                "customer_duplicate",
                "Customer already exists in this organization",
            )
        return ()

    def apply(self, row: Mapping[str, str]) -> Mapping[str, object]:
        transformed = _transform_customer(row)
        display_name = str(transformed.get("display_name", "") or "").strip()
        company_name = str(transformed.get("company_name", "") or "").strip()
        first_name = str(transformed.get("first_name", "") or "").strip()
        last_name = str(transformed.get("last_name", "") or "").strip()
        if company_name:
            customer_type = CustomerType.COMPANY
            legal_name = company_name
            trading_name = display_name if display_name != company_name else None
        elif first_name or last_name:
            customer_type = CustomerType.INDIVIDUAL
            legal_name = display_name or f"{first_name} {last_name}".strip()
            trading_name = None
        else:
            customer_type = CustomerType.COMPANY
            legal_name = display_name
            trading_name = None
        input_data = CustomerInput(
            customer_type=customer_type,
            customer_name=legal_name[:255],
            trading_name=trading_name[:255] if trading_name else None,
            default_receivable_account_id=self.ar_control_account_id,
            credit_limit=cast(Decimal | None, transformed.get("credit_limit")),
            payment_terms_days=_payment_terms(transformed.get("payment_terms_days")),
            currency_code=str(
                transformed.get("currency_code")
                or settings.default_functional_currency_code
            ),
            risk_category=RiskCategory.MEDIUM,
            billing_address=_address(transformed, "billing"),
            shipping_address=_address(transformed, "shipping"),
            primary_contact=_primary_contact(transformed, display_name),
            is_active=bool(transformed.get("is_active", True)),
        )
        try:
            customer = customer_service.create_customer(
                self.db, self.organization_id, input_data
            )
        except ValueError:
            from dotmac_imports import RowRejected

            raise RowRejected(
                "customer_create_refused",
                "Customer could not be created from this row",
            ) from None
        customer.created_by_user_id = self.user_id
        self.db.flush()
        return {"customer_id": str(customer.customer_id)}

    def _duplicate(self, display_name: str) -> bool:
        return (
            self.db.execute(
                select(Customer.customer_id).where(
                    Customer.organization_id == self.organization_id,
                    Customer.legal_name == display_name,
                )
            ).scalar_one_or_none()
            is not None
        )


def _transform_customer(row: Mapping[str, str]) -> dict[str, object]:
    transformed: dict[str, object] = {}
    for mapping in customer_source_mappings():
        value = row.get(mapping.target_field)
        try:
            candidate = mapping.transform(value)
        except (TypeError, ValueError):
            candidate = mapping.default
        prior = transformed.get(mapping.target_field)
        if prior not in (None, "") and candidate in (None, ""):
            continue
        transformed[mapping.target_field] = candidate
    return transformed


def _address(row: Mapping[str, object], prefix: str) -> dict[str, object] | None:
    values = {
        "attention": row.get(f"{prefix}_attention"),
        "street": row.get(f"{prefix}_street"),
        "street2": row.get(f"{prefix}_street2"),
        "city": row.get(f"{prefix}_city"),
        "state": row.get(f"{prefix}_state"),
        "country": row.get(f"{prefix}_country"),
        "postal_code": row.get(f"{prefix}_postal_code"),
        "phone": row.get(f"{prefix}_phone"),
    }
    present = {key: value for key, value in values.items() if value}
    return present or None


def _payment_terms(value: object) -> int:
    if isinstance(value, (str, int)) and value not in ("", 0):
        return int(value)
    return 30


def _primary_contact(
    row: Mapping[str, object], display_name: str
) -> dict[str, object] | None:
    values = {
        "name": display_name,
        "phone": row.get("phone"),
        "email": row.get("email"),
    }
    present = {key: value for key, value in values.items() if value}
    return present or None


def _snapshot(run: ImportRun) -> CustomerImportRunSnapshot:
    return CustomerImportRunSnapshot(
        run_id=run.id,
        status=RunStatus(run.status),
        dry_run=bool(run.dry_run),
        processed=int(run.total_rows),
        ok=int(run.ok_rows),
        failed=int(run.failed_rows),
        skipped=int(run.skipped_rows),
    )


__all__ = [
    "CUSTOMER_IMPORT_KIND",
    "ClaimedCustomerPartition",
    "CustomerImportParityError",
    "CustomerImportConfigurationError",
    "CustomerImportPort",
    "CustomerImportRunSnapshot",
    "DurableCustomerImportService",
    "PreparedCustomerImport",
    "assert_legacy_customer_parity",
    "authorize_customer_partition",
    "customer_field_set",
    "customer_import_outcomes",
    "customer_source_mappings",
    "get_customer_import",
    "prepare_customer_import",
    "promote_customer_import",
    "read_customer_partition",
    "record_customer_dry_run",
    "settle_customer_partition",
]
