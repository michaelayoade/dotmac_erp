"""``assert_legacy_customer_parity`` compares FIELD VECTORS, not verdicts.

Before this repair, ``assert_legacy_customer_parity`` (1) ran the retiring
importer under plain ``dry_run=True``, which
``tests/ifrs/import_export/test_legacy_construct_only.py`` already pins as
never actually calling ``create_entity`` at all -- the legacy side built
nothing to compare -- and (2) compared only the tri-state ``RowStatus``. So
two rows landing on the same status with different field values compared
equal. The repair (a) switches to ``ImportConfig.construct_only``, which
does construct and keep the entity, and (b) runs every row both paths would
actually build through ``customer_column_disposition``'s already-merged,
closed field-vector contract (``CUSTOMER_FIELD_DISPOSITION`` /
``compare_partition``) instead of restating a second, driftable comparison.

The durable side is built through ``CustomerImportPort.preview``, which
delegates to ``CustomerService.prepare_customer`` -- the ONE real
preparation step ``create_customer`` also consumes (Michael's ruled shape,
2026-09-09), sharing its exact field mapping (``_build_customer_entity``)
and parent-customer validation. Nothing is added, flushed, begun-nested,
committed or rolled back, so this adapter never owns a transaction to
discard (``tests/architecture/test_imports_adoption.py::
test_customer_adapter_owns_no_transaction_or_session_factory`` forbids
exactly that shape -- an earlier version of this repair built the durable
entity inside a rolled-back SAVEPOINT and tripped it; a second attempt
moved the SAVEPOINT into ``CustomerService`` instead of removing it, which
was explicitly rejected).

UNMEASURABLE FIELD: ``customer_code`` cannot be observed from a preview --
see ``CustomerService.prepare_customer``'s and
``assert_legacy_customer_parity``'s docstrings. This is named there, not
tested here: nothing in this file plants a ``customer_code`` divergence,
because there is no real value to diverge from.

Runs under the Integration Tests (PostgreSQL) job, matching this directory's
existing pattern (``test_reseller_promotion_match.py``), even though these
particular rows never reach the database: a real ``org_id``/``ar_control_
account`` fixture pair keeps the comparison honest about the shape a real
caller passes, and a future row exercising the parent-customer read would
need the real ``ar.customer`` table this tier provides. No real customer
file, external target or personal data appears here: every row is a
synthetic fixture, matching ``test_customer_column_disposition.py``'s own
rule.
"""

from __future__ import annotations

import dataclasses
import uuid
from decimal import Decimal
from typing import Any

import pytest
from dotmac_imports import PartitionClaim, PreparedPartition, SourceLayout, auto_map

from app.services.finance.import_export import (
    durable_customers as durable_customers_module,
)
from app.services.finance.import_export.base import FieldMapping
from app.services.finance.import_export.durable_customers import (
    CustomerImportParityError,
    assert_legacy_customer_parity,
    customer_field_set,
    customer_source_mappings,
)

USER = uuid.uuid4()

# Captured before any monkeypatch can reach it -- the real, unperturbed
# vocabulary both the plant and the near-miss perturb a copy of.
_REAL_SOURCE_MAPPINGS = customer_source_mappings
_REAL_HEADERS = tuple(item.source_field for item in _REAL_SOURCE_MAPPINGS())


def _raw_row(**values: str) -> dict[str, str]:
    """One full-width CSV row: every real header, blank unless named."""
    row = dict.fromkeys(_REAL_HEADERS, "")
    row.update(values)
    return row


COMPANY_ROW = _raw_row(
    **{
        "Display Name": "Northwind Trading",
        "Company Name": "Northwind Ltd",
        "Billing City": "Abuja",
        "Payment Terms": "45",
    }
)


def _mapping_pairs() -> tuple[tuple[str, str], ...]:
    """The real, auto-detected mapping -- the same shape a persisted
    ``ImportRun.column_mapping`` row holds, derived rather than hand-written
    so this stays correct under whatever pair order ``dotmac_imports`` uses
    (same discipline as ``test_customer_column_disposition.py``'s
    ``_mapping_pairs``)."""
    return tuple(auto_map(_REAL_HEADERS, customer_field_set()).pairs)


class _StubbedRun:
    """Stands in for the ``ImportRun`` row ``get_run`` would normally fetch.

    ``assert_legacy_customer_parity`` reads exactly one thing off the run:
    ``column_mapping``. Standing up a real, persisted ``dotmac_imports`` run
    ledger row is a separate, unrelated migration/fixture dependency this
    test does not take on -- ``get_run`` is monkeypatched to hand back this
    instead. Nothing about the comparison under test is faked: the legacy
    importer, the durable writer and the disposition comparator all run for
    real, against the real database.
    """

    def __init__(self, column_mapping: tuple[tuple[str, str], ...]) -> None:
        self.column_mapping = column_mapping


def _prepared_partition(
    rows: list[dict[str, str]], *, tenant_id: uuid.UUID
) -> PreparedPartition:
    claim = PartitionClaim(
        partition_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        tenant_id=tenant_id,
        lease_token=uuid.uuid4(),
        file_id=uuid.uuid4(),
        ordinal=0,
        start_row=0,
        row_count=len(rows),
        checksum_sha256="a" * 64,
        byte_size=1,
        source_file_id=uuid.uuid4(),
        source_checksum_sha256="a" * 64,
        source_layout=SourceLayout.CSV,
        source_delimiter=",",
        source_encoding="utf-8",
    )
    return PreparedPartition(
        claim=claim, rows=tuple(tuple(row.items()) for row in rows)
    )


def _assert_parity(
    monkeypatch: pytest.MonkeyPatch,
    db: Any,
    rows: list[dict[str, str]],
    *,
    org_id: uuid.UUID,
    ar_control_account_id: uuid.UUID,
) -> None:
    monkeypatch.setattr(
        durable_customers_module,
        "get_run",
        lambda db, *, tenant_id, run_id: _StubbedRun(_mapping_pairs()),
    )
    assert_legacy_customer_parity(
        db,
        _prepared_partition(rows, tenant_id=org_id),
        tenant_id=org_id,
        created_by=USER,
        ar_control_account_id=ar_control_account_id,
        skip_duplicates=False,
    )


def _perturb_legacy_default(
    monkeypatch: pytest.MonkeyPatch, source_field: str, value: Any
) -> None:
    """Change ONE data-driven rule on the retiring side only.

    ``CustomerImporter.get_field_mappings`` (defined in ``contacts.py``)
    resolves ``customer_source_mappings`` through the ``contacts`` module
    namespace at call time; ``durable_customers`` holds its own binding,
    captured at import time. Patching the ``contacts`` name therefore
    perturbs a real rule on exactly one of the two REAL constructors,
    leaving both real -- the identical technique
    ``test_customer_column_disposition.py::_perturb_legacy_default`` uses
    for the same purpose, against the same shared production mapping.
    """

    def _perturbed() -> list[FieldMapping]:
        return [
            dataclasses.replace(item, default=value)
            if item.source_field == source_field
            else item
            for item in _REAL_SOURCE_MAPPINGS()
        ]

    monkeypatch.setattr(
        "app.services.finance.import_export.contacts.customer_source_mappings",
        _perturbed,
    )


# ---------------------------------------------------------------------------
# Admit control: two real, unperturbed constructors must agree
# ---------------------------------------------------------------------------


def test_a_clean_row_passes_field_vector_comparison(
    monkeypatch, db, org_id, ar_control_account
):
    """The baseline a real run must pass silently.

    This also proves ``construct_only`` and ``CustomerService.construct_
    customer_preview`` really run end to end: if ``construct_only`` were not
    wired in, the legacy importer would build nothing and this function
    would refuse every call with "reported OK without constructing a row"
    rather than passing.
    """
    result = _assert_parity(
        monkeypatch,
        db,
        [COMPANY_ROW],
        org_id=org_id,
        ar_control_account_id=ar_control_account.account_id,
    )

    assert result is None


# ---------------------------------------------------------------------------
# The plant this repair exists for: same status, different field value
# ---------------------------------------------------------------------------


def test_a_same_status_different_value_divergence_is_caught(
    monkeypatch, db, org_id, ar_control_account
):
    """PLANT.

    ``Credit Limit`` is blank in the fixture row, so its mapping default is
    what lands on the entity. Perturbing the LEGACY side's default alone
    makes the two real constructors decide different ``credit_limit``
    values while BOTH still land at ``RowStatus.OK`` -- a divergence a bare
    status comparison cannot see. Before this repair (plain ``dry_run=True``
    plus a status-only comparison) this row compared clean: the legacy side
    never even built an entity to disagree with.
    """
    _perturb_legacy_default(monkeypatch, "Credit Limit", Decimal("999.00"))

    with pytest.raises(CustomerImportParityError, match="credit_limit"):
        _assert_parity(
            monkeypatch,
            db,
            [COMPANY_ROW],
            org_id=org_id,
            ar_control_account_id=ar_control_account.account_id,
        )


def test_the_same_perturbation_on_a_field_no_constructor_uses_is_silent(
    monkeypatch, db, org_id, ar_control_account
):
    """NEAR-MISS for the plant above.

    Identical perturbation, identical mechanism, different landing site:
    ``Notes`` is in the shared CSV vocabulary but ``ar.customer`` has no
    ``notes`` column and neither constructor reads one, so the transformed
    row differs while the field vector does not. If this failed, the
    wiring would be refusing on ANY perturbation at all rather than on a
    genuine field-vector divergence -- the detector would not be
    discriminating, only breaking.
    """
    _perturb_legacy_default(monkeypatch, "Notes", "perturbed")

    result = _assert_parity(
        monkeypatch,
        db,
        [COMPANY_ROW],
        org_id=org_id,
        ar_control_account_id=ar_control_account.account_id,
    )

    assert result is None
