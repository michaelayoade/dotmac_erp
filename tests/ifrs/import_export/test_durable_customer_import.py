"""Behaviour canaries for ERP's customer-import ports."""

from __future__ import annotations

import io
import uuid
from unittest.mock import MagicMock, patch

import pytest

from dotmac_files import (
    FilePolicy,
    FileState,
    ProviderMismatch,
    StoredObjectRef,
    prepare_upload,
)
from dotmac_imports import ImportIssue, PreparedPartition, RowSkipped

from app.services.finance.import_export.base import ImportConfig
from app.services.finance.import_export.contacts import CustomerImporter
from app.services.finance.import_export.durable_customers import (
    ClaimedCustomerPartition,
    CustomerImportParityError,
    CustomerImportPort,
    assert_legacy_customer_parity,
    customer_field_set,
    customer_source_mappings,
    prepare_customer_import,
    read_customer_partition,
)
from app.tenancy import OrganizationTenantContext
from app.tasks.imports import enqueue_customer_import_run

ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER = uuid.UUID("00000000-0000-0000-0000-000000000002")
AR = uuid.UUID("00000000-0000-0000-0000-000000000003")


class _MemoryProvider:
    code = "memory"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(
        self,
        key,
        content,
        *,
        content_type,
        size_bytes,
        checksum_sha256,
    ) -> None:
        del content_type, checksum_sha256
        value = content.read()
        assert len(value) == size_bytes
        self.objects[key] = value

    def open(self, key):
        return io.BytesIO(self.objects[key])

    def exists(self, key) -> bool:
        return key in self.objects

    def delete(self, key) -> None:
        self.objects.pop(key, None)

    def list(self, prefix):
        return ()


def test_legacy_and_durable_paths_share_one_customer_vocabulary() -> None:
    legacy = CustomerImporter(
        MagicMock(), ImportConfig(organization_id=ORG, user_id=USER), AR
    )
    assert [
        (item.source_field, item.target_field, item.required, item.default)
        for item in legacy.get_field_mappings()
    ] == [
        (item.source_field, item.target_field, item.required, item.default)
        for item in customer_source_mappings()
    ]
    assert customer_field_set().names == frozenset(
        mapping.target_field for mapping in legacy.get_field_mappings()
    )


def test_required_display_name_is_a_typed_safe_issue() -> None:
    port = CustomerImportPort(MagicMock(), ORG, USER, AR, skip_duplicates=True)
    assert tuple(port.validate({"display_name": ""})) == (port.REQUIRED_DISPLAY_NAME,)


def test_duplicate_policy_is_a_skip_and_never_reaches_the_writer() -> None:
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = object()
    port = CustomerImportPort(db, ORG, USER, AR, skip_duplicates=True)

    with pytest.raises(RowSkipped) as skipped:
        port.validate({"display_name": "Existing customer"})

    assert skipped.value.issue.code == "customer_duplicate"


@patch(
    "app.services.finance.import_export.durable_customers.customer_service.create_customer"
)
def test_apply_delegates_to_the_canonical_customer_owner(create_customer) -> None:
    created = MagicMock(customer_id=uuid.uuid4(), created_by_user_id=None)
    create_customer.return_value = created
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    port = CustomerImportPort(db, ORG, USER, AR, skip_duplicates=True)

    result = port.apply(
        {
            "display_name": "Acme",
            "company_name": "Acme Limited",
            "currency_code": "NGN",
            "payment_terms_days": "30",
            "is_active": "Active",
        }
    )

    create_customer.assert_called_once()
    assert create_customer.call_args.args[:2] == (db, ORG)
    assert created.created_by_user_id == USER
    assert result == {"customer_id": str(created.customer_id)}


def test_source_is_verified_and_split_into_bounded_immutable_partitions(
    monkeypatch,
) -> None:
    provider = _MemoryProvider()
    scope = OrganizationTenantContext.for_organization(ORG).tenant_scope
    source = prepare_upload(
        provider,
        scope=scope,
        policy=FilePolicy(
            max_bytes=1024,
            allowed_extensions=frozenset({".csv"}),
            allowed_media_types=frozenset({"text/csv"}),
        ),
        original_filename="customers.csv",
        declared_media_type="text/csv",
        chunks=(b"Display Name,Company Name\nAcme,Acme Ltd\nBeta,Beta Ltd\n",),
    )
    monkeypatch.setattr(
        "app.services.finance.import_export.durable_customers.settings.import_partition_rows",
        1,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.finance.import_export.durable_customers.settings.import_partition_max_bytes",
        1024,
        raising=False,
    )

    prepared = prepare_customer_import(
        provider,
        tenant_id=ORG,
        source_file=source,
    )

    assert [item.row_count for item in prepared.descriptors] == [1, 1]
    assert [item.start_row for item in prepared.descriptors] == [0, 1]
    assert len(prepared.partition_files) == 2
    assert "Acme" not in repr(prepared)


def test_source_from_another_tenant_is_refused_before_read() -> None:
    provider = _MemoryProvider()
    other = uuid.uuid4()
    source = prepare_upload(
        provider,
        scope=OrganizationTenantContext.for_organization(other).tenant_scope,
        policy=FilePolicy(
            max_bytes=1024,
            allowed_extensions=frozenset({".csv"}),
            allowed_media_types=frozenset({"text/csv"}),
        ),
        original_filename="customers.csv",
        declared_media_type="text/csv",
        chunks=(b"Display Name,Company Name\nAcme,Acme Ltd\n",),
    )

    with pytest.raises(TypeError, match="tenant-scoped"):
        prepare_customer_import(provider, tenant_id=ORG, source_file=source)


def test_partition_read_preserves_the_files_provider_authorization() -> None:
    target = StoredObjectRef(
        id=uuid.uuid4(),
        scope=OrganizationTenantContext.for_organization(ORG).tenant_scope,
        provider_code="another_provider",
        storage_key="tenant/object",
        state=FileState.AVAILABLE,
        original_filename="partition.csv",
        size_bytes=10,
        detected_media_type="text/csv",
        checksum_sha256=f"sha256:{'0' * 64}",
    )
    authorized = ClaimedCustomerPartition(
        claim=MagicMock(file_id=target.id),
        target=target,
        created_by=USER,
        ar_control_account_id=AR,
        dry_run=True,
    )

    with pytest.raises(ProviderMismatch):
        read_customer_partition(_MemoryProvider(), authorized)


def test_shadow_parity_mismatch_refuses_partition_settlement() -> None:
    prepared = PreparedPartition(
        claim=MagicMock(run_id=uuid.uuid4()),
        rows=((("Display Name", "Acme"),),),
    )
    legacy_result = MagicMock(error_count=0, skipped_count=0)
    durable_port = MagicMock()
    durable_port.validate.return_value = (
        ImportIssue("customer_refused", "Customer row was refused"),
    )
    run = MagicMock(column_mapping=[["Display Name", "display_name"]])

    with (
        patch(
            "app.services.finance.import_export.durable_customers.get_run",
            return_value=run,
        ),
        patch(
            "app.services.finance.import_export.durable_customers.CustomerImporter"
        ) as legacy,
        patch(
            "app.services.finance.import_export.durable_customers.CustomerImportPort",
            return_value=durable_port,
        ),
        pytest.raises(CustomerImportParityError),
    ):
        legacy.return_value.import_rows.return_value = legacy_result
        assert_legacy_customer_parity(
            MagicMock(),
            prepared,
            tenant_id=ORG,
            created_by=USER,
            ar_control_account_id=AR,
            skip_duplicates=True,
        )


@patch("app.tasks.imports.process_customer_import_partitions.delay")
def test_queue_fans_out_validation_but_serializes_application(
    delay, monkeypatch
) -> None:
    run_id = uuid.uuid4()
    monkeypatch.setattr(
        "app.tasks.imports.settings.import_validation_workers",
        3,
        raising=False,
    )

    enqueue_customer_import_run(ORG, run_id, dry_run=True)
    assert delay.call_count == 3
    assert all(call.args == (str(ORG), str(run_id)) for call in delay.call_args_list)

    delay.reset_mock()
    enqueue_customer_import_run(ORG, run_id, dry_run=False)
    delay.assert_called_once_with(str(ORG), str(run_id))
