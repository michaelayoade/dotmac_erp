"""The customer-import shadow comparison compares FIELD VECTORS, not verdicts.

Before this, ``assert_legacy_customer_parity`` compared a tri-state
``RowStatus`` and nothing else, so two rows failing for entirely different
reasons compared equal and two rows succeeding with different data did too.
``customer_column_disposition`` closes that: every mapped column of
``ar.customer`` is classified, and a column that is classified as nothing is a
defect rather than a pass.

Four plants are required to hold, each with a near-miss that must stay silent
and each with an admit control, because a rule only ever observed refusing is
indistinguishable from a rule that refuses everything:

1. an exact-field divergence between the two constructors;
2. an invalid generated ``customer_code``;
3. an existing duplicate whose code did not survive;
4. a field on the entity the disposition does not name.

Both constructors are REAL wherever a plant claims a divergence.  The
retiring path runs through ``CustomerImporter`` under ``construct_only``
against a session that raises on any access; the durable path runs through
``CustomerImportPort.apply`` into the real ``CustomerService.create_customer``
against a session stub that answers only the two reads the writer makes.  The
DATABASE is stubbed; neither implementation under comparison is.

Fixtures only.  No real customer file, no external target and no personal
data appear here -- the "no synthetic data" rule governs Gate 4's real-corpus
evidence, not these unit tests, and this change is explicitly not Gate 4.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from dotmac_imports import ColumnMapping, apply_mapping, auto_map
from sqlalchemy import Column, String
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Session

from app.models.finance.ar.customer import Customer
from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    SequenceType,
)
from app.services.finance.import_export.base import FieldMapping, ImportConfig
from app.services.finance.import_export.contacts import CustomerImporter
from app.services.finance.import_export.customer_column_disposition import (
    CUSTOMER_FIELD_DISPOSITION,
    CodeAllocation,
    CodeRegistry,
    ColumnDispositionError,
    ConstructedRow,
    Disposition,
    DuplicateRow,
    ExistingCustomer,
    FindingKind,
    RowCorrelation,
    RunWindow,
    allocation_findings,
    assert_disposition_is_closed,
    assert_partition_agrees,
    check_generated_code,
    compare_constructed_row,
    compare_duplicate_row,
    compare_partition,
    correlate_row,
    generated_code_max_length,
    identity_digest,
    missing_fields,
    model_field_names,
    record_allocation,
    unclassified_fields,
)
from app.services.finance.import_export.durable_customers import (
    CustomerImportPort,
    customer_field_set,
    customer_source_mappings,
)

ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER = uuid.UUID("00000000-0000-0000-0000-000000000002")
AR = uuid.UUID("00000000-0000-0000-0000-000000000003")
OTHER_ORG = uuid.UUID("00000000-0000-0000-0000-0000000000ff")

SOURCE_SHA = "a" * 64

# Captured at import, before any monkeypatch can reach the module attribute
# the retiring importer resolves at call time.  Same discipline as
# ``test_durable_customer_import``: the perturbation tests below replace that
# attribute, and this binding stays the real, unperturbed vocabulary.
_REAL_SOURCE_MAPPINGS = customer_source_mappings
_REAL_HEADERS = tuple(item.source_field for item in _REAL_SOURCE_MAPPINGS())


# ---------------------------------------------------------------------------
# Fixture plumbing.  A failure in this section is a fixture defect and says so
# by name; it must never be mistakable for a disposition verdict.
# ---------------------------------------------------------------------------


def _mapping_pairs(mapping: object) -> list[tuple[str, str]]:
    """Serialize a REAL ``ColumnMapping`` the way the ledger stores it.

    The pair order is taken from ``auto_map`` -- the function production
    itself calls -- rather than hand-written, so this stays correct under
    whichever order ``dotmac_imports`` actually uses.  ``dotmac_imports`` is
    not installable in this repo's dev environment, so the accessor is
    discovered rather than assumed, and an unknown shape fails HERE, named, at
    fixture setup.  Deliberately no shim, mock or permissive fallback: an
    unavailable dependency contract must not be able to masquerade as
    agreement.  (The same derivation appears in
    ``test_durable_customer_import``; both DERIVE it, so neither can propagate
    an assumed literal.)
    """
    for attribute in ("pairs", "columns", "entries"):
        value = getattr(mapping, attribute, None)
        if value:
            return [(str(left), str(right)) for left, right in value]
    try:
        return [(str(left), str(right)) for left, right in mapping]  # type: ignore[misc]
    except TypeError as exc:  # pragma: no cover - fixture diagnostics
        raise AssertionError(
            f"fixture cannot serialize ColumnMapping {mapping!r}: {exc}"
        ) from exc


def _column_mapping() -> ColumnMapping:
    return ColumnMapping(
        tuple(_mapping_pairs(auto_map(_REAL_HEADERS, customer_field_set())))
    )


def _raw_row(**values: str) -> dict[str, str]:
    """One full-width CSV row: every real header, blank unless named."""
    row = dict.fromkeys(_REAL_HEADERS, "")
    row.update(values)
    return row


ROW = _raw_row(
    **{
        "Display Name": "Northwind Trading",
        "Company Name": "Northwind Ltd",
        "Billing City": "Abuja",
        "Payment Terms": "45",
    }
)
SECOND_ROW = _raw_row(**{"Display Name": "Ada Example", "First Name": "Ada"})


class _ForbiddenSession:
    """A session that fails loudly on ANY use.

    With ``skip_duplicates=False`` the retiring path has no legitimate reason
    to touch the session at all under ``construct_only``, so a session that
    cannot be read from is the strongest available proof that the legacy side
    of every comparison below is a pure construction.
    """

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"a construct-only import touched the session: {name!r}")


class _EmptyResult:
    def first(self) -> None:
        return None


class _DurableSession:
    """The database, stubbed -- the durable implementation is not.

    ``CustomerService.create_customer`` makes exactly two reads: the numbering
    sequence (``db.scalar``) and the customer-code uniqueness check
    (``db.scalars(...).first()``).  Answering those two is the smallest
    substitution that lets the REAL writer run without a database.  The
    sequence is kept between calls, as a real row would be, so consecutive
    rows get consecutive codes instead of both restarting at one.
    """

    def __init__(self) -> None:
        self.sequence: NumberingSequence | None = None
        self.added: list[object] = []
        self.numbering_reads = 0
        self.uniqueness_reads = 0

    def scalar(self, statement: object) -> NumberingSequence | None:
        del statement
        self.numbering_reads += 1
        return self.sequence

    def scalars(self, statement: object) -> _EmptyResult:
        del statement
        self.uniqueness_reads += 1
        return _EmptyResult()

    def add(self, instance: object) -> None:
        if isinstance(instance, NumberingSequence):
            self.sequence = instance
        self.added.append(instance)

    def flush(self) -> None:
        return None

    def refresh(self, instance: object) -> None:
        del instance


def _legacy_entity(raw: dict[str, str], org: uuid.UUID = ORG) -> Customer:
    """What the retiring importer decides, kept instead of thrown away."""
    importer = CustomerImporter(
        _ForbiddenSession(),
        ImportConfig(
            organization_id=org,
            user_id=USER,
            skip_duplicates=False,
            dry_run=True,
            construct_only=True,
        ),
        AR,
    )
    result = importer.import_rows([raw])
    if result.error_count or len(importer.constructed) != 1:
        raise AssertionError(
            "fixture defect, not a disposition verdict: the retiring importer "
            f"constructed {len(importer.constructed)} entities with "
            f"{result.error_count} errors: {result.errors}"
        )
    return importer.constructed[0]


def _durable_entity(
    raw: dict[str, str], db: _DurableSession, org: uuid.UUID = ORG
) -> Customer:
    """What the durable path decides, through the one canonical writer."""
    mapped = apply_mapping(raw, _column_mapping())
    if not str(mapped.get("display_name", "") or "") and raw.get("Display Name"):
        raise AssertionError(
            "fixture defect, not a disposition verdict: the column mapping did "
            "not deliver a populated 'display_name' to the durable port. "
            f"mapped keys={sorted(mapped)}"
        )
    port = CustomerImportPort(db, org, USER, AR, skip_duplicates=False)
    port.apply(mapped)
    customers = [item for item in db.added if isinstance(item, Customer)]
    if not customers:
        raise AssertionError(
            "fixture defect, not a disposition verdict: the durable writer "
            "added no customer to the session"
        )
    return customers[-1]


def _correlation(
    index: int = 0,
    display_name: str = "Northwind Trading",
    *,
    partition_ordinal: int = 0,
) -> RowCorrelation:
    return correlate_row(
        source_file_sha256=SOURCE_SHA,
        partition_ordinal=partition_ordinal,
        start_row=0,
        index=index,
        display_name=display_name,
    )


def _window() -> RunWindow:
    now = datetime(2026, 9, 6, 12, 0, 0)
    return RunWindow(started_at=now - timedelta(minutes=5), finished_at=now)


def _row_under_comparison(
    raw: dict[str, str] = ROW,
    index: int = 0,
    *,
    org: uuid.UUID = ORG,
    partition_ordinal: int = 0,
) -> ConstructedRow:
    return ConstructedRow(
        correlation=_correlation(
            index, str(raw["Display Name"]), partition_ordinal=partition_ordinal
        ),
        legacy=_legacy_entity(raw, org),
        durable=_durable_entity(raw, _DurableSession(), org),
    )


def _kinds(findings: tuple[Any, ...]) -> list[FindingKind]:
    return [item.kind for item in findings]


def _fields(findings: tuple[Any, ...]) -> list[str]:
    return [item.field for item in findings]


# ---------------------------------------------------------------------------
# The admit control for the whole comparison
# ---------------------------------------------------------------------------


def test_two_real_constructors_agree_on_every_exact_field() -> None:
    """The admit control.

    Both implementations are real and unperturbed.  Every EXACT field must
    agree, and the permitted differences must hold to their stated reasons:
    the surrogate keys are unequal, the generated code came from
    ``SyncNumberingService`` rather than from the retiring counter, and no
    timestamp was stamped outside the window.

    This proves EQUIVALENCE and not correctness.  ``_primary_contact`` reads
    ``row.get("email")`` on both sides while ``customer_source_mappings()``
    declares no ``Email`` column, so both paths silently drop the address and
    agree perfectly about a value neither of them has.  A clean comparison
    here is not a claim that the import is right.
    """
    db = _DurableSession()
    legacy = _legacy_entity(ROW)
    durable = _durable_entity(ROW, db)
    row = ConstructedRow(correlation=_correlation(), legacy=legacy, durable=durable)

    assert compare_constructed_row(row, window=_window(), registry=CodeRegistry()) == ()

    # The comparison ran against two distinct objects, not one object twice.
    assert legacy is not durable
    assert legacy.customer_id != durable.customer_id
    # Code provenance: the numbering service really allocated, and the durable
    # entity did not inherit the retiring counter's value.
    assert db.numbering_reads > 0
    assert db.uniqueness_reads > 0
    assert legacy.customer_code == "CUST00001"
    assert durable.customer_code != legacy.customer_code
    assert durable.customer_code.startswith("CUST")
    # And the decisions the two paths make really were exercised.
    assert legacy.trading_name == "Northwind Trading"
    assert legacy.credit_terms_days == 45
    assert legacy.billing_address == {"city": "Abuja"}


def test_the_second_name_branch_also_agrees() -> None:
    """Second admit direction: the individual branch of the name split."""
    row = _row_under_comparison(SECOND_ROW, index=1)

    assert compare_constructed_row(row, window=_window(), registry=CodeRegistry()) == ()
    assert row.legacy.legal_name == "Ada Example"
    assert row.legacy.trading_name is None


# ---------------------------------------------------------------------------
# Plant 1 -- an exact-field divergence
# ---------------------------------------------------------------------------


def _perturb_legacy_default(monkeypatch, source_field: str, value: Any) -> None:
    """Change ONE data-driven rule on the retiring side only.

    ``CustomerImporter.get_field_mappings`` resolves ``customer_source_mappings``
    through the ``contacts`` module namespace; ``durable_customers`` holds its
    own imported binding.  Patching the ``contacts`` name therefore perturbs a
    real rule on exactly one of the two implementations, leaving both real.
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


def test_a_business_field_that_diverges_is_named(monkeypatch) -> None:
    """PLANT 1.

    ``Credit Limit`` is blank in the fixture row, so its mapping default is
    what lands on the entity.  Giving the retiring side a different default
    makes the two REAL constructors decide different values for a real
    business column, and the disposition must say which column.
    """
    _perturb_legacy_default(monkeypatch, "Credit Limit", Decimal("999.00"))
    row = _row_under_comparison()

    findings = compare_constructed_row(row, window=_window(), registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.EXACT_MISMATCH]
    assert _fields(findings) == ["credit_limit"]
    assert findings[0].correlation == row.correlation


def test_the_same_perturbation_on_a_field_no_constructor_uses_is_silent(
    monkeypatch,
) -> None:
    """NEAR-MISS for plant 1.

    Identical perturbation, identical mechanism, different landing site.
    ``Notes`` is in the shared CSV vocabulary but ``ar.customer`` has no
    ``notes`` column and neither ``create_entity`` nor ``apply`` reads one, so
    the transformed row differs while the field vector does not.  If this
    failed, the comparator would be reporting any perturbation at all rather
    than a field-vector divergence.
    """
    _perturb_legacy_default(monkeypatch, "Notes", "perturbed")
    row = _row_under_comparison()

    assert compare_constructed_row(row, window=_window(), registry=CodeRegistry()) == ()
    # The perturbation really did reach the retiring transform -- otherwise
    # this near-miss would be silent for the trivial reason that nothing
    # changed at all.
    assert "notes" not in set(model_field_names())
    assert any(
        item.source_field == "Notes" and item.default == "perturbed"
        for item in CustomerImporter(
            _ForbiddenSession(),
            ImportConfig(organization_id=ORG, user_id=USER, skip_duplicates=False),
            AR,
        ).get_field_mappings()
    )


# ---------------------------------------------------------------------------
# Plant 2 -- an invalid generated code
# ---------------------------------------------------------------------------


def _fixed_generated_code(monkeypatch, code: str) -> None:
    """Make the one approved allocator emit a chosen code.

    ``customer_code`` invariants are about what the allocator produces, so the
    only way to plant a violation is to make it produce one.  Everything
    downstream -- ``create_customer``, the entity, the comparator -- stays
    real.
    """

    def _generate(
        self: Any, organization_id: Any, sequence_type: Any, *args: Any, **kwargs: Any
    ) -> str:
        del self, organization_id, sequence_type, args, kwargs
        return code

    monkeypatch.setattr(
        "app.services.finance.common.numbering.SyncNumberingService.generate_next_number",
        _generate,
    )


def test_a_generated_code_past_the_column_length_is_named(monkeypatch) -> None:
    """PLANT 2.

    The limit is read off ``ar.customer.customer_code`` rather than restated,
    so the invariant tracks the schema instead of drifting from it.
    """
    limit = generated_code_max_length()
    _fixed_generated_code(monkeypatch, "CUST-" + "9" * (limit - 4))
    row = _row_under_comparison()

    findings = compare_constructed_row(row, window=_window(), registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.GENERATED_CODE_INVALID]
    assert _fields(findings) == ["customer_code"]
    assert len(row.durable.customer_code) == limit + 1


def test_a_generated_code_exactly_at_the_limit_is_silent(monkeypatch) -> None:
    """NEAR-MISS for plant 2: one character shorter must pass."""
    limit = generated_code_max_length()
    _fixed_generated_code(monkeypatch, "CUST-" + "9" * (limit - 5))
    row = _row_under_comparison()

    assert compare_constructed_row(row, window=_window(), registry=CodeRegistry()) == ()
    assert len(row.durable.customer_code) == limit


def test_an_empty_generated_code_is_named() -> None:
    """PLANT 2b, at the other end of the same invariant."""
    findings = check_generated_code("   ", organization_id=ORG, registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.GENERATED_CODE_INVALID]


def test_a_short_generated_code_is_silent() -> None:
    """NEAR-MISS for plant 2b."""
    assert check_generated_code("C", organization_id=ORG, registry=CodeRegistry()) == ()


def test_a_repeated_code_in_one_organization_is_named() -> None:
    """PLANT 2c: uniqueness is within ``(organization_id, customer_code)``."""
    registry = CodeRegistry()

    assert (
        check_generated_code("CUST-00001", organization_id=ORG, registry=registry) == ()
    )
    findings = check_generated_code(
        "CUST-00001", organization_id=ORG, registry=registry
    )

    assert _kinds(findings) == [FindingKind.GENERATED_CODE_NOT_UNIQUE]


def test_the_same_code_in_another_organization_is_silent() -> None:
    """NEAR-MISS for plant 2c.

    The uniqueness scope really is the pair.  If this failed, the check would
    be enforcing a fleet-wide unique code, which is not what
    ``uq_customer_code`` says.
    """
    registry = CodeRegistry()

    assert (
        check_generated_code("CUST-00001", organization_id=ORG, registry=registry) == ()
    )
    assert (
        check_generated_code("CUST-00001", organization_id=OTHER_ORG, registry=registry)
        == ()
    )


def test_two_rows_that_receive_the_same_code_are_named_end_to_end(monkeypatch) -> None:
    """PLANT 2c through the real constructors.

    A stuck allocator hands two rows one code.  This is precisely the retiring
    importer's own defect -- a fresh instance per row makes every row
    ``CUST00001`` -- and it must be refused rather than adopted.
    """
    _fixed_generated_code(monkeypatch, "CUST-00001")
    rows = [_row_under_comparison(ROW, 0), _row_under_comparison(SECOND_ROW, 1)]

    findings = compare_partition(rows, window=_window(), registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.GENERATED_CODE_NOT_UNIQUE]
    assert findings[0].correlation == rows[1].correlation


def test_two_rows_with_distinct_codes_are_silent() -> None:
    """NEAR-MISS: the same two rows with the real allocator running."""
    db = _DurableSession()
    rows = [
        ConstructedRow(
            correlation=_correlation(0, "Northwind Trading"),
            legacy=_legacy_entity(ROW),
            durable=_durable_entity(ROW, db),
        ),
        ConstructedRow(
            correlation=_correlation(1, "Ada Example"),
            legacy=_legacy_entity(SECOND_ROW),
            durable=_durable_entity(SECOND_ROW, db),
        ),
    ]

    assert compare_partition(rows, window=_window(), registry=CodeRegistry()) == ()
    assert rows[0].durable.customer_code != rows[1].durable.customer_code


# ---------------------------------------------------------------------------
# Plant 3 -- an existing duplicate whose code did not survive
# ---------------------------------------------------------------------------


def _duplicate(before: ExistingCustomer, after: ExistingCustomer) -> DuplicateRow:
    return DuplicateRow(
        correlation=_correlation(3, "Existing Customer"),
        before=before,
        after=after,
    )


def test_an_existing_duplicate_that_lost_its_code_is_named() -> None:
    """PLANT 3.

    Neither path constructs anything for a duplicate, so there is no field
    vector -- what must hold is that the run left the persisted customer
    exactly as it found it.
    """
    persisted = uuid.uuid4()
    findings = compare_duplicate_row(
        _duplicate(
            ExistingCustomer(customer_id=persisted, customer_code="CUST-00007"),
            ExistingCustomer(customer_id=persisted, customer_code="CUST-00042"),
        )
    )

    assert _kinds(findings) == [FindingKind.DUPLICATE_CODE_CHANGED]


def test_an_existing_duplicate_that_lost_its_identity_is_named() -> None:
    """PLANT 3b: the persisted surrogate key must survive too."""
    findings = compare_duplicate_row(
        _duplicate(
            ExistingCustomer(customer_id=uuid.uuid4(), customer_code="CUST-00007"),
            ExistingCustomer(customer_id=uuid.uuid4(), customer_code="CUST-00007"),
        )
    )

    assert _kinds(findings) == [FindingKind.DUPLICATE_IDENTITY_CHANGED]


def test_an_untouched_duplicate_is_silent() -> None:
    """NEAR-MISS and admit control for plant 3.

    ``before`` and ``after`` are DISTINCT objects carrying equal values, so a
    check written against object identity rather than value would fail here.
    """
    persisted = uuid.uuid4()
    before = ExistingCustomer(customer_id=persisted, customer_code="CUST-00007")
    after = ExistingCustomer(customer_id=persisted, customer_code="CUST-00007")

    assert before is not after
    assert compare_duplicate_row(_duplicate(before, after)) == ()


# ---------------------------------------------------------------------------
# Plant 4 -- a field the disposition does not name
# ---------------------------------------------------------------------------


def _mirror_of_customer(
    name: str, *, extra: dict[str, Any] | None = None, drop: tuple[str, ...] = ()
) -> type[Any]:
    """A mapped class whose columns are copied from the REAL customer table.

    Derived from the model rather than from ``CUSTOMER_FIELD_DISPOSITION``: a
    mirror built out of the thing under test would agree with it by
    construction and prove nothing.  Each mirror gets its own registry, so
    nothing here touches the application's metadata.
    """

    class _Base(DeclarativeBase):
        pass

    namespace: dict[str, Any] = {"__tablename__": name}
    for column in Customer.__table__.columns:
        if column.key in drop:
            continue
        namespace[column.key] = Column(
            column.name, column.type, primary_key=column.primary_key
        )
    namespace.update(extra or {})
    return type(name, (_Base,), namespace)


def test_a_new_column_the_disposition_does_not_name_fails() -> None:
    """PLANT 4 -- the one that makes this a closed contract.

    An allow-list would let ``loyalty_tier`` through: it is not on the list of
    things to compare, so it would be silently ignored, and a column nobody
    classified would ship into a shadow comparison that claims completeness.
    """
    mirror = _mirror_of_customer(
        "mirror_with_extra", extra={"loyalty_tier": Column(String(20))}
    )

    assert unclassified_fields(mirror) == ("loyalty_tier",)
    assert missing_fields(mirror) == ()
    with pytest.raises(ColumnDispositionError, match="loyalty_tier"):
        assert_disposition_is_closed(mirror)


def test_a_non_mapped_attribute_of_the_same_name_is_silent() -> None:
    """NEAR-MISS for plant 4.

    The same name appears on the class, but as an ordinary Python attribute
    rather than a mapped column, so nothing about it is ever persisted or
    decided.  The disposition governs the field vector, not the namespace.
    """
    mirror = _mirror_of_customer(
        "mirror_with_attribute", extra={"loyalty_tier": "gold"}
    )

    assert unclassified_fields(mirror) == ()
    assert missing_fields(mirror) == ()
    assert_disposition_is_closed(mirror)


def test_a_column_the_disposition_names_but_the_model_lost_fails() -> None:
    """PLANT 4b: the other direction of closure -- a removed field."""
    mirror = _mirror_of_customer("mirror_without_column", drop=("vat_category",))

    assert unclassified_fields(mirror) == ()
    assert missing_fields(mirror) == ("vat_category",)
    with pytest.raises(ColumnDispositionError, match="vat_category"):
        assert_disposition_is_closed(mirror)


def test_the_real_customer_model_is_completely_classified() -> None:
    """Admit control for plant 4, on the real model.

    Also the reason the two ``Customer`` relationships never need an
    exemption: ``parent_customer`` and ``child_customers`` exist on the real
    class and are not flagged, because navigation is not a field.
    """
    # Non-vacuity first: two empty sets are also equal, and a `model_field_names`
    # that had quietly started returning nothing would satisfy every assertion
    # below while classifying nothing at all.
    assert len(model_field_names()) > 20
    assert "legal_name" in model_field_names()
    assert "customer_code" in model_field_names()

    assert unclassified_fields() == ()
    assert missing_fields() == ()
    assert set(CUSTOMER_FIELD_DISPOSITION) == set(model_field_names())
    assert_disposition_is_closed()
    assert hasattr(Customer, "parent_customer")
    assert hasattr(Customer, "child_customers")


def test_the_generated_identity_columns_are_not_exact_columns() -> None:
    """The ruled classification, pinned so a later edit has to argue with it.

    ``customer_code`` is an ERP-owned generated identifier, not source
    identity.  Demanding equality would fossilise the retiring importer's
    defect, where a fresh instance per row makes every row ``CUST00001``.
    """
    assert CUSTOMER_FIELD_DISPOSITION["customer_code"] is Disposition.GENERATED_CODE
    assert CUSTOMER_FIELD_DISPOSITION["customer_id"] is Disposition.SURROGATE_KEY
    assert CUSTOMER_FIELD_DISPOSITION["created_at"] is Disposition.RUN_TIMESTAMP
    assert CUSTOMER_FIELD_DISPOSITION["updated_at"] is Disposition.RUN_TIMESTAMP
    assert CUSTOMER_FIELD_DISPOSITION["legal_name"] is Disposition.EXACT


# ---------------------------------------------------------------------------
# The permitted differences are asserted against their reasons, not skipped
# ---------------------------------------------------------------------------


def test_two_sides_sharing_a_surrogate_key_are_named() -> None:
    """A permitted difference that stopped differing is a defect.

    Equal surrogate keys mean the harness handed the comparator one entity
    twice, under which every EXACT field would agree for the wrong reason.
    """
    row = _row_under_comparison()
    row.durable.customer_id = row.legacy.customer_id

    findings = compare_constructed_row(row, window=_window(), registry=CodeRegistry())

    assert FindingKind.SURROGATE_KEY_COLLIDED in _kinds(findings)


def test_distinct_surrogate_keys_are_silent() -> None:
    """NEAR-MISS: the durable side carrying its own key must pass."""
    row = _row_under_comparison()
    row.durable.customer_id = uuid.uuid4()

    assert compare_constructed_row(row, window=_window(), registry=CodeRegistry()) == ()


def test_one_entity_handed_in_twice_is_named() -> None:
    """The failure the surrogate rule exists to catch.

    A harness bug that passes the same entity as both sides would make every
    EXACT field agree perfectly.  Under the real pre-flush shapes the durable
    side has no key yet, so a value comparison alone would not notice -- the
    rule checks the entities, not only their keys.
    """
    only = _legacy_entity(ROW)
    row = ConstructedRow(correlation=_correlation(), legacy=only, durable=only)

    findings = compare_constructed_row(row, window=_window(), registry=CodeRegistry())

    assert FindingKind.SURROGATE_KEY_COLLIDED in _kinds(findings)


def test_two_entities_built_from_the_same_row_are_not_a_collision() -> None:
    """NEAR-MISS: identical DATA is not identical IDENTITY.

    Two separate constructions of the same source row agree on every business
    field, which is precisely what a clean shadow comparison looks like.  If
    this failed, the collision rule would be refusing agreement itself.
    """
    row = ConstructedRow(
        correlation=_correlation(),
        legacy=_legacy_entity(ROW),
        durable=_durable_entity(ROW, _DurableSession()),
    )

    assert row.legacy is not row.durable
    assert compare_constructed_row(row, window=_window(), registry=CodeRegistry()) == ()


def test_neither_side_carrying_a_surrogate_key_is_named() -> None:
    """A permitted difference that has become unassertable is not silence.

    With both keys deferred to the database nothing in the field vector can
    distinguish two entities from one, so the comparator says so instead of
    reporting agreement it cannot justify.  ``del`` removes the attribute from
    instance state, which is exactly the shape ``create_customer`` leaves the
    durable side in.
    """
    row = _row_under_comparison()
    del row.legacy.customer_id

    findings = compare_constructed_row(row, window=_window(), registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.SURROGATE_KEY_INDETERMINATE]
    assert _fields(findings) == ["customer_id"]


def test_one_side_carrying_a_surrogate_key_is_silent() -> None:
    """NEAR-MISS for the indeterminate rule -- and the REAL shape.

    The retiring path assigns ``uuid4()``; ``create_customer`` names no
    ``customer_id`` and lets the column default supply one.  One discriminating
    key is enough, and this is what every honest comparison looks like today.
    """
    row = _row_under_comparison()

    assert isinstance(row.legacy.customer_id, uuid.UUID)
    assert "customer_id" not in sa_inspect(row.durable).dict
    assert compare_constructed_row(row, window=_window(), registry=CodeRegistry()) == ()


def test_a_surrogate_key_that_is_not_a_uuid_is_named() -> None:
    """Well-formedness, not merely difference."""
    row = _row_under_comparison()
    row.durable.customer_id = "not-a-uuid"

    findings = compare_constructed_row(row, window=_window(), registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.SURROGATE_KEY_MALFORMED]


def test_a_timestamp_outside_the_run_window_is_named() -> None:
    """A permitted difference still has to hold to its stated reason.

    Both sides stamp, so the provenance rule is satisfied and the only thing
    left to fail is the window itself.
    """
    window = _window()
    row = _row_under_comparison()
    row.legacy.created_at = window.started_at + timedelta(seconds=1)
    row.durable.created_at = window.started_at - timedelta(days=1)

    findings = compare_constructed_row(row, window=window, registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.TIMESTAMP_OUTSIDE_RUN_WINDOW]
    assert _fields(findings) == ["created_at"]


def test_two_timestamps_inside_the_run_window_are_silent() -> None:
    """NEAR-MISS for the window rule: different instants, both permitted."""
    window = _window()
    row = _row_under_comparison()
    row.legacy.created_at = window.started_at + timedelta(seconds=1)
    row.durable.created_at = window.started_at + timedelta(seconds=2)

    assert row.legacy.created_at != row.durable.created_at
    assert compare_constructed_row(row, window=window, registry=CodeRegistry()) == ()


def test_a_timestamp_only_one_path_decides_is_named() -> None:
    """The permitted difference is WHO stamps, so disagreeing about that fails.

    This is the arm that stops "permitted to differ" from decaying into
    "skipped".  The durable path stamps a perfectly valid, in-window time and
    the retiring path still leaves the column to the database; nothing about
    the VALUE is wrong, and it is still a divergence.
    """
    window = _window()
    row = _row_under_comparison()
    row.durable.created_at = window.started_at + timedelta(seconds=1)

    findings = compare_constructed_row(row, window=window, registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.TIMESTAMP_PROVENANCE_DIVERGED]
    assert _fields(findings) == ["created_at"]


def test_both_paths_leaving_the_timestamp_to_the_database_is_silent() -> None:
    """NEAR-MISS and admit control for the provenance rule.

    Neither constructor touches ``created_at``: it carries a server default
    and nothing has been flushed.  That agreement is the expected shape, and
    it is asserted here rather than assumed.
    """
    row = _row_under_comparison()

    assert "created_at" not in sa_inspect(row.legacy).dict
    assert "created_at" not in sa_inspect(row.durable).dict
    assert compare_constructed_row(row, window=_window(), registry=CodeRegistry()) == ()


def test_entities_captured_at_different_persistence_stages_are_refused() -> None:
    """Comparing a transient entity with a pending one compares lifecycles.

    A transient entity reads an untouched column as untouched; one that has
    been through a session has had its defaults applied.  Reporting that as a
    field divergence -- or worse, silently normalizing it away -- would make
    every later verdict untrustworthy, so the comparator refuses instead.
    ``Session()`` here is unbound: no engine, no connection, ORM state only.
    """
    row = _row_under_comparison()
    # Held in a local: a garbage-collected session would drop the entity back
    # out of the pending state and the guard would be tested against nothing.
    session = Session()
    session.add(row.durable)

    findings = compare_constructed_row(row, window=_window(), registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.STAGE_MISMATCH]
    assert _fields(findings) == ["*"]


def test_entities_captured_at_the_same_stage_are_compared(monkeypatch) -> None:
    """NEAR-MISS for the stage guard: it must refuse the mismatch, not the run.

    Both entities are transient AND a real divergence is present, so a guard
    that had simply stopped comparing would show nothing here.
    """
    _perturb_legacy_default(monkeypatch, "Credit Limit", Decimal("999.00"))
    row = _row_under_comparison()

    findings = compare_constructed_row(row, window=_window(), registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.EXACT_MISMATCH]


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


def test_correlation_never_carries_the_generated_code() -> None:
    """The anchor is the source row, never the ERP-generated identifier.

    Joining on ``customer_code`` would join on a value the retiring path
    repeats: a fresh importer per row makes every row ``CUST00001``, so a
    code-based join would collapse an entire partition onto one row.
    """
    names = {field.name for field in dataclasses.fields(RowCorrelation)}

    assert names == {
        "source_file_sha256",
        "partition_ordinal",
        "row_ordinal",
        "identity_digest",
    }
    assert "customer_code" not in names
    assert "customer_id" not in names


def test_rows_sharing_a_code_still_correlate_distinctly() -> None:
    """The legacy defect, made harmless by the choice of anchor."""
    first = _legacy_entity(ROW)
    second = _legacy_entity(SECOND_ROW)

    assert first.customer_code == second.customer_code == "CUST00001"
    assert _correlation(0, "Northwind Trading") != _correlation(1, "Ada Example")


def test_rows_sharing_a_display_name_still_correlate_distinctly() -> None:
    """Display name alone is not an anchor: it is a decision input under test."""
    assert _correlation(0, "Northwind Trading") != _correlation(1, "Northwind Trading")


def test_the_same_row_in_another_source_file_correlates_distinctly() -> None:
    one = correlate_row(
        source_file_sha256="a" * 64,
        partition_ordinal=0,
        start_row=0,
        index=0,
        display_name="Northwind Trading",
    )
    other = correlate_row(
        source_file_sha256="b" * 64,
        partition_ordinal=0,
        start_row=0,
        index=0,
        display_name="Northwind Trading",
    )

    assert one != other


def test_the_row_ordinal_is_absolute_across_partitions() -> None:
    """``start_row + index`` -- the checkpoint, not the offset within it."""
    second_partition = correlate_row(
        source_file_sha256=SOURCE_SHA,
        partition_ordinal=1,
        start_row=500,
        index=3,
        display_name="Northwind Trading",
    )

    assert second_partition.row_ordinal == 503


def test_the_identity_digest_follows_the_real_duplicate_rule() -> None:
    """Stripped, never case-folded.

    Both real implementations compare ``legal_name`` against a ``.strip()``-ed
    display name with no case folding.  Folding case here would make the
    correlation claim two rows share an identity the system itself treats as
    distinct.
    """
    assert identity_digest("  Northwind Trading  ") == identity_digest(
        "Northwind Trading"
    )
    assert identity_digest("northwind trading") != identity_digest("Northwind Trading")


def test_a_finding_never_carries_a_customer_value() -> None:
    """A comparison report is not a place to copy personal data to.

    The plant is a divergence on a column that would otherwise put a legal
    name straight into the message.
    """
    row = ConstructedRow(
        correlation=_correlation(),
        legacy=_legacy_entity(ROW),
        durable=_durable_entity(ROW, _DurableSession()),
    )
    row.durable.legal_name = "Someone Private Limited"

    findings = compare_constructed_row(row, window=_window(), registry=CodeRegistry())

    assert _kinds(findings) == [FindingKind.EXACT_MISMATCH]
    rendered = str(findings)
    assert "Someone Private" not in rendered
    assert "Northwind" not in rendered


def test_the_guard_form_raises_with_the_correlation_of_the_offending_row(
    monkeypatch,
) -> None:
    """``assert_partition_agrees`` is the shape Gate 4 will call."""
    _perturb_legacy_default(monkeypatch, "Credit Limit", Decimal("999.00"))
    row = _row_under_comparison()

    with pytest.raises(ColumnDispositionError) as refused:
        assert_partition_agrees([row], window=_window(), registry=CodeRegistry())

    message = str(refused.value)
    assert "exact_mismatch on credit_limit" in message
    assert f"row={row.correlation.row_ordinal}" in message


def test_the_guard_form_admits_a_partition_that_agrees() -> None:
    """Admit control for the guard form itself."""
    db = _DurableSession()
    rows = [
        ConstructedRow(
            correlation=_correlation(0, "Northwind Trading"),
            legacy=_legacy_entity(ROW),
            durable=_durable_entity(ROW, db),
        ),
        DuplicateRow(
            correlation=_correlation(1, "Existing Customer"),
            before=ExistingCustomer(
                customer_id=uuid.UUID("00000000-0000-0000-0000-00000000aaaa"),
                customer_code="CUST-00007",
            ),
            after=ExistingCustomer(
                customer_id=uuid.UUID("00000000-0000-0000-0000-00000000aaaa"),
                customer_code="CUST-00007",
            ),
        ),
    ]

    assert_partition_agrees(rows, window=_window(), registry=CodeRegistry())


def test_the_sequence_type_the_allocator_uses_is_the_customer_one() -> None:
    """Provenance, pinned: codes come from the CUSTOMER numbering sequence."""
    db = _DurableSession()
    _durable_entity(ROW, db)

    sequences = [item for item in db.added if isinstance(item, NumberingSequence)]

    assert [item.sequence_type for item in sequences] == [SequenceType.CUSTOMER]


# ---------------------------------------------------------------------------
# Evidence: raw-row identity -> customer_id -> assigned customer_code
# ---------------------------------------------------------------------------


def test_evidence_is_refused_while_the_durable_customer_has_no_identity() -> None:
    """A code bound to nothing is not evidence.

    ``CustomerService.create_customer`` allocates the code but leaves
    ``customer_id`` to the database, so at the stage the two constructors are
    comparable the durable customer has a code and no identity yet.  Recording
    two of the three required links and calling it evidence would be worse
    than recording none, so this refuses by name.
    """
    row = _row_under_comparison()

    findings = allocation_findings(row)

    assert _kinds(findings) == [FindingKind.ALLOCATION_NOT_RECORDABLE]
    assert _fields(findings) == ["customer_id"]
    # The code half really was available -- the refusal is about the identity.
    assert row.durable.customer_code
    with pytest.raises(ColumnDispositionError, match="customer_id"):
        record_allocation(row)


def test_evidence_binds_the_row_the_identity_and_the_code() -> None:
    """NEAR-MISS and admit control: with an identity, all three links record."""
    row = _row_under_comparison()
    identity = uuid.uuid4()
    row.durable.customer_id = identity

    assert allocation_findings(row) == ()
    evidence = record_allocation(row)

    assert evidence == CodeAllocation(
        correlation=row.correlation,
        customer_id=identity,
        customer_code=row.durable.customer_code,
    )
    # Raw-row identity, not the generated code, is what the evidence is keyed on.
    assert evidence.correlation.source_file_sha256 == SOURCE_SHA
    assert evidence.correlation.row_ordinal == 0
    assert evidence.customer_code != row.legacy.customer_code


def test_evidence_is_refused_when_no_code_was_assigned(monkeypatch) -> None:
    """The other half of the same refusal."""
    _fixed_generated_code(monkeypatch, "   ")
    row = _row_under_comparison()
    row.durable.customer_id = uuid.uuid4()

    findings = allocation_findings(row)

    assert _kinds(findings) == [FindingKind.ALLOCATION_NOT_RECORDABLE]
    assert _fields(findings) == ["customer_code"]


# ---------------------------------------------------------------------------
# Uniqueness is scoped to the RUN, not to the partition
# ---------------------------------------------------------------------------
#
# The first version of this contract created the registry inside
# `compare_partition`, which enforced uniqueness within
# `(organization_id, customer_code, partition)`.  That is a different and
# weaker claim than the ruled one, and a dishonest one: partitioning is a
# function of `IMPORT_PARTITION_ROWS` and a byte ceiling, so the same corpus
# split differently would have produced a different verdict.  These tests are
# the acceptance evidence that the scope is now the run.


def _partition_rows(
    ordinal: int, *, org: uuid.UUID = ORG
) -> list[ConstructedRow | DuplicateRow]:
    """One partition holding one row, correlated to that partition."""
    return [
        _row_under_comparison(
            ROW if ordinal == 0 else SECOND_ROW,
            index=ordinal,
            org=org,
            partition_ordinal=ordinal,
        )
    ]


def test_the_same_code_in_two_partitions_is_named(monkeypatch) -> None:
    """PLANT: the collision a per-partition registry could not see.

    Two partitions, settled by two separate `compare_partition` calls exactly
    as a worker fanned out across partitions would, sharing ONE run registry.
    A stuck allocator hands both rows the same code, and it must be refused on
    the second partition rather than passing because each partition looked
    clean on its own.
    """
    _fixed_generated_code(monkeypatch, "CUST-00001")
    registry = CodeRegistry()
    window = _window()

    first = compare_partition(_partition_rows(0), window=window, registry=registry)
    second = compare_partition(_partition_rows(1), window=window, registry=registry)

    assert first == ()
    assert _kinds(second) == [FindingKind.GENERATED_CODE_NOT_UNIQUE]
    # Named against the row that collided, in the partition it came from.
    assert second[0].correlation is not None
    assert second[0].correlation.partition_ordinal == 1


def test_a_registry_made_per_partition_would_have_missed_it(monkeypatch) -> None:
    """Why the argument is required rather than defaulted.

    This pins the defect itself, so the repair cannot be quietly undone: given
    a FRESH registry per partition the very same collision goes unreported.
    If this test ever starts failing, someone has changed the scope again --
    in which case it is this test, not the guard, that should be revisited.
    """
    _fixed_generated_code(monkeypatch, "CUST-00001")
    window = _window()

    first = compare_partition(
        _partition_rows(0), window=window, registry=CodeRegistry()
    )
    second = compare_partition(
        _partition_rows(1), window=window, registry=CodeRegistry()
    )

    assert first == ()
    assert second == ()


def test_compare_partition_will_not_invent_a_registry() -> None:
    """A default registry is exactly how the partition scope got in."""
    with pytest.raises(TypeError, match="registry"):
        compare_partition(_partition_rows(0), window=_window())  # type: ignore[call-arg]

    with pytest.raises(TypeError, match="registry"):
        assert_partition_agrees(_partition_rows(0), window=_window())  # type: ignore[call-arg]


def test_the_same_code_in_two_partitions_of_another_organization_is_silent(
    monkeypatch,
) -> None:
    """NEAR-MISS: the scope really is the pair, across partitions too.

    Same stuck allocator, same two partitions, but the second belongs to a
    different organization.  `uq_customer_code` is scoped to
    `(organization_id, customer_code)`, so this must pass -- a run registry
    that had drifted into a fleet-wide unique code would fail here.
    """
    _fixed_generated_code(monkeypatch, "CUST-00001")
    registry = CodeRegistry()
    window = _window()

    first = compare_partition(_partition_rows(0), window=window, registry=registry)
    second = compare_partition(
        _partition_rows(1, org=OTHER_ORG), window=window, registry=registry
    )

    assert first == ()
    assert second == ()


def test_two_partitions_with_distinct_codes_are_silent() -> None:
    """ADMIT CONTROL: the run-scoped registry passes an honest two-partition run.

    The real allocator runs, so the two rows get genuinely different codes.
    Without this, a registry that refused every second partition would look
    identical to one that catches collisions.
    """
    db = _DurableSession()
    registry = CodeRegistry()
    window = _window()
    first_row = ConstructedRow(
        correlation=_correlation(0, "Northwind Trading", partition_ordinal=0),
        legacy=_legacy_entity(ROW),
        durable=_durable_entity(ROW, db),
    )
    second_row = ConstructedRow(
        correlation=_correlation(1, "Ada Example", partition_ordinal=1),
        legacy=_legacy_entity(SECOND_ROW),
        durable=_durable_entity(SECOND_ROW, db),
    )

    assert compare_partition([first_row], window=window, registry=registry) == ()
    assert compare_partition([second_row], window=window, registry=registry) == ()
    assert first_row.durable.customer_code != second_row.durable.customer_code


# ---------------------------------------------------------------------------
# Codes an EARLIER run persisted, when the caller supplies them
# ---------------------------------------------------------------------------


def test_a_code_an_earlier_run_persisted_is_named(monkeypatch) -> None:
    """PLANT: the residue a run-scoped registry cannot see unaided.

    The comparator never reaches a database -- that is what lets every plant
    here run without one -- so persisted codes arrive through the caller.
    Given them, a collision with an earlier run is refused on FIRST sighting,
    not on the second row.
    """
    _fixed_generated_code(monkeypatch, "CUST-00001")
    registry = CodeRegistry(persisted=[(ORG, "CUST-00001")])

    findings = compare_partition(
        _partition_rows(0), window=_window(), registry=registry
    )

    assert _kinds(findings) == [FindingKind.GENERATED_CODE_NOT_UNIQUE]
    assert "already persisted" in findings[0].reason
    assert registry.seeded_from_durable_state is True


def test_a_persisted_code_in_another_organization_is_silent(monkeypatch) -> None:
    """NEAR-MISS for the durable seed: same pair scope as everywhere else."""
    _fixed_generated_code(monkeypatch, "CUST-00001")
    registry = CodeRegistry(persisted=[(OTHER_ORG, "CUST-00001")])

    findings = compare_partition(
        _partition_rows(0), window=_window(), registry=registry
    )

    assert findings == ()
    assert registry.seeded_from_durable_state is True


def test_an_unseeded_registry_says_so(monkeypatch) -> None:
    """The residue is reportable, not silent.

    With no seed the same collision above is invisible.  That is an
    UNMONITORED region, and `seeded_from_durable_state` is how a caller can
    tell which of the two claims a clean run actually made.
    """
    _fixed_generated_code(monkeypatch, "CUST-00001")
    registry = CodeRegistry()

    findings = compare_partition(
        _partition_rows(0), window=_window(), registry=registry
    )

    assert findings == ()
    assert registry.seeded_from_durable_state is False
