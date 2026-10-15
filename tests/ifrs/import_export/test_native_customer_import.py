"""Installed-wheel canary for customer source preparation's native decoder.

Preparation decodes only the first partition for preview and mapping inference.
This does not exercise the apply worker or establish import throughput.
"""

from __future__ import annotations

import io
import site
from contextlib import ExitStack
from importlib.machinery import ExtensionFileLoader
from importlib.metadata import distribution
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from dotmac_files import FilePolicy, prepare_upload
from dotmac_imports import _native
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.services.finance.import_export import durable_customers
from app.tenancy import OrganizationTenantContext

ORG = UUID("00000000-0000-0000-0000-000000000001")


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


def test_customer_source_preparation_uses_installed_native_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _native.__spec__
    assert spec is not None
    assert isinstance(spec.loader, ExtensionFileLoader)
    assert spec.origin is not None
    origin = Path(spec.origin).resolve()
    installed_roots = (
        Path(directory).resolve()
        for directory in (*site.getsitepackages(), site.getusersitepackages())
    )
    assert any(origin.is_relative_to(root) for root in installed_roots), origin
    assert "site-packages" in origin.parts, origin
    # CI's virtualenv may live inside the checkout. Bind the compiled module
    # to the installed wheel rather than rejecting that valid environment.
    installed = distribution("dotmac-imports")
    assert installed.version == "0.2.0a1"
    assert installed.files is not None
    assert any(
        Path(installed.locate_file(member)).resolve() == origin
        for member in installed.files
        if member.parts[0] == "dotmac_imports" and member.name == origin.name
    ), origin

    provider = _MemoryProvider()
    source = prepare_upload(
        provider,
        scope=OrganizationTenantContext.for_organization(ORG).tenant_scope,
        policy=FilePolicy(
            max_bytes=4096,
            allowed_extensions=frozenset({".csv"}),
            allowed_media_types=frozenset({"text/csv"}),
        ),
        original_filename="customers.csv",
        declared_media_type="text/csv",
        chunks=(
            (
                "\ufeff\u2003Display Name\u00a0, Company Name , Notes ,Unmapped\n"
                "\u00a0Café Lagos\u2003,\u3000Café Ltd\u202f,\u0085first\u001f,ignored\n"
                "\u2003Beta\u00a0, Beta Ltd , second ,ignored too\n"
            ).encode(),
        ),
    )
    monkeypatch.setattr(durable_customers.settings, "import_partition_rows", 1)
    monkeypatch.setattr(durable_customers.settings, "import_partition_max_bytes", 4096)
    previews = []
    real_preview = durable_customers.preview

    def observe_preview(*args, **kwargs):
        result = real_preview(*args, **kwargs)
        previews.append(result)
        return result

    monkeypatch.setattr(durable_customers, "preview", observe_preview)
    # Keep the actual Rust function in the call path. A stubbed return value
    # would prove only wiring and would let a missing/broken wheel pass.
    with ExitStack() as stack:
        normalize = stack.enter_context(
            patch.object(_native, "normalize_rows", wraps=_native.normalize_rows)
        )
        database_calls = [
            stack.enter_context(patch.object(owner, method))
            for owner, method in (
                (Session, "execute"),
                (Session, "add"),
                (Session, "add_all"),
                (Session, "flush"),
                (Session, "commit"),
                (Connection, "execute"),
                (Connection, "exec_driver_sql"),
                (durable_customers, "record_customer_dry_run"),
                (durable_customers.customer_service, "create_customer"),
            )
        ]
        prepared = durable_customers.prepare_customer_import(
            provider, tenant_id=ORG, source_file=source
        )
        normalize.assert_called_once()
        assert normalize.call_args.args[0] == 4
        assert normalize.call_args.args[1] == [
            (2, [b"Caf\xc3\xa9 Lagos", b"Caf\xc3\xa9 Ltd", b"first", b"ignored"])
        ]
        assert normalize.call_args.args[2:] == (1, [0, 1, 2, 3])
        for database_call in database_calls:
            database_call.assert_not_called()

    assert [descriptor.row_count for descriptor in prepared.descriptors] == [1, 1]
    assert [descriptor.start_row for descriptor in prepared.descriptors] == [0, 1]
    assert len(provider.objects) == 3  # Original source and two stored partitions.
    assert prepared.mapping.as_dict() == {
        "display_name": "Display Name",
        "company_name": "Company Name",
        "notes": "Notes",
    }
    assert len(previews) == 1
    proposed = previews[0]
    assert proposed.is_mappable
    assert proposed.mapping == prepared.mapping
    assert proposed.columns == ("Display Name", "Company Name", "Notes", "Unmapped")
    assert proposed.unmapped_columns == ("Unmapped",)
    assert proposed.missing_required_fields == ()
    assert tuple(dict(row) for row in proposed.sample) == (
        {"display_name": "Café Lagos", "company_name": "Café Ltd", "notes": "first"},
    )
