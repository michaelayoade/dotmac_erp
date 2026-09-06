"""The CLOSED column disposition for the customer-import shadow comparison.

The retiring ``CustomerImporter`` and the durable ``dotmac-imports`` path both
end at an ``ar.customer`` row.  Until now the two were compared only by a
tri-state ``RowStatus``, so two rows failing for entirely different reasons
compared equal.  This module makes the FIELD VECTOR comparable.

It is a disposition, not an allow-list.  An allow-list says "ignore these";
a disposition says every field is one of a fixed set of kinds, and a field
that is none of them is a defect.  ``assert_disposition_is_closed`` refuses
a model carrying a column the disposition does not name, and refuses a
disposition naming a column the model no longer has -- added, removed and
unclassified all fail.

What the classes mean
---------------------
``Disposition.EXACT``
    A business field.  Both constructors must produce the same effective
    value.  "Effective" folds a column's scalar Python default in, because
    the two paths reach the same persisted value by different routes: the
    retiring path leaves ``credit_hold`` unset and lets the default apply,
    while ``CustomerService.create_customer`` writes ``False`` explicitly.
    Comparing raw instance state would report fourteen such columns as
    divergent when nothing about the outcome differs.

``Disposition.GENERATED_CODE``
    ``customer_code`` only, and deliberately NOT an equality column.  It is an
    ERP-owned generated identifier rather than source identity, and requiring
    equality would fossilise a legacy defect: ``CustomerImporter._code_counter``
    is per-instance state that starts at zero, so the parity loop -- which
    builds a fresh importer per row -- makes every row ``CUST00001``.  Pinning
    the durable path to that would be pinning it to the bug.  The invariants
    that DO hold are checked instead (see ``check_generated_code``).

``Disposition.SURROGATE_KEY``
    ``customer_id``.  Differs by construction, so equality is not merely
    unnecessary, it is a symptom: two equal surrogate keys mean the comparator
    was handed one object twice.  The permitted difference is asserted against
    its stated reason -- each side must be a well-formed UUID or explicitly
    database-assigned, and if both are present they must be UNEQUAL.

``Disposition.RUN_TIMESTAMP``
    ``created_at``/``updated_at``.  Also permitted to differ, also asserted
    against the reason rather than skipped: a value that is present must fall
    inside the run window.  Both paths normally leave these to the database,
    which is itself the expected shape and is recorded as such.

What this does NOT establish
----------------------------
Parity proves EQUIVALENCE, never correctness.  A field both constructors get
wrong in the same way is silent here by design, and one such field is known:
``_primary_contact`` reads ``row.get("email")`` on both sides while
``customer_source_mappings()`` declares no ``Email`` column at all, so both
paths drop the address and agree perfectly.  A reader must not mistake a clean
comparison for a correct import.

This module is also NOT Gate 4.  Gate 4 needs real customer files; this is the
contract those files will later be compared under.
"""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Iterable, Mapping, MutableSet, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import inspect as sa_inspect

from app.models.finance.ar.customer import Customer


class ColumnDispositionError(RuntimeError):
    """The shadow comparison found a field the disposition does not permit."""


class Disposition(str, enum.Enum):
    """The four kinds a customer field may be.  There is no fifth."""

    EXACT = "exact"
    GENERATED_CODE = "generated_code"
    SURROGATE_KEY = "surrogate_key"
    RUN_TIMESTAMP = "run_timestamp"


class FindingKind(str, enum.Enum):
    """Why a field was refused.  Every one of these fails the comparison."""

    FIELD_UNCLASSIFIED = "field_unclassified"
    FIELD_MISSING = "field_missing"
    EXACT_MISMATCH = "exact_mismatch"
    GENERATED_CODE_INVALID = "generated_code_invalid"
    GENERATED_CODE_NOT_UNIQUE = "generated_code_not_unique"
    DUPLICATE_CODE_CHANGED = "duplicate_code_changed"
    DUPLICATE_IDENTITY_CHANGED = "duplicate_identity_changed"
    SURROGATE_KEY_MALFORMED = "surrogate_key_malformed"
    SURROGATE_KEY_COLLIDED = "surrogate_key_collided"
    SURROGATE_KEY_INDETERMINATE = "surrogate_key_indeterminate"
    TIMESTAMP_OUTSIDE_RUN_WINDOW = "timestamp_outside_run_window"
    TIMESTAMP_PROVENANCE_DIVERGED = "timestamp_provenance_diverged"
    ALLOCATION_NOT_RECORDABLE = "allocation_not_recordable"
    STAGE_MISMATCH = "stage_mismatch"


class _DatabaseAssigned:
    """A value the database will supply, distinct from ``None``.

    ``None`` means "this column will be NULL"; this means "this column has a
    server default or a callable default and nobody has decided its value
    yet".  Collapsing the two would let a genuinely absent value pass as an
    intentional NULL.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "DATABASE_ASSIGNED"


DATABASE_ASSIGNED = _DatabaseAssigned()


# Every mapped column of ``ar.customer``, classified.  Adding a column to the
# model without adding it here fails ``assert_disposition_is_closed`` -- that
# refusal is the whole point of the contract, so resist the urge to reach for
# a default classification.
CUSTOMER_FIELD_DISPOSITION: Mapping[str, Disposition] = {
    "customer_id": Disposition.SURROGATE_KEY,
    "customer_code": Disposition.GENERATED_CODE,
    "created_at": Disposition.RUN_TIMESTAMP,
    "updated_at": Disposition.RUN_TIMESTAMP,
    "organization_id": Disposition.EXACT,
    "customer_type": Disposition.EXACT,
    "legal_name": Disposition.EXACT,
    "trading_name": Disposition.EXACT,
    "tax_identification_number": Disposition.EXACT,
    "vat_category": Disposition.EXACT,
    "registration_number": Disposition.EXACT,
    "credit_limit": Disposition.EXACT,
    "credit_terms_days": Disposition.EXACT,
    "credit_hold": Disposition.EXACT,
    "payment_terms_id": Disposition.EXACT,
    "currency_code": Disposition.EXACT,
    "price_list_id": Disposition.EXACT,
    "ar_control_account_id": Disposition.EXACT,
    "default_revenue_account_id": Disposition.EXACT,
    "default_tax_code_id": Disposition.EXACT,
    "sales_rep_user_id": Disposition.EXACT,
    "customer_group_id": Disposition.EXACT,
    "risk_category": Disposition.EXACT,
    "is_related_party": Disposition.EXACT,
    "related_party_type": Disposition.EXACT,
    "related_party_relationship": Disposition.EXACT,
    "is_wht_applicable": Disposition.EXACT,
    "default_wht_code_id": Disposition.EXACT,
    "wht_exemption_certificate": Disposition.EXACT,
    "wht_exemption_expiry": Disposition.EXACT,
    "is_vat_exempt": Disposition.EXACT,
    "billing_address": Disposition.EXACT,
    "shipping_address": Disposition.EXACT,
    "primary_contact": Disposition.EXACT,
    "bank_details": Disposition.EXACT,
    "is_active": Disposition.EXACT,
    "erpnext_id": Disposition.EXACT,
    "splynx_id": Disposition.EXACT,
    "dotmac_sub_id": Disposition.EXACT,
    "parent_customer_id": Disposition.EXACT,
    "splynx_partner_id": Disposition.EXACT,
    "dotmac_sub_reseller_id": Disposition.EXACT,
    "dotmac_sub_metrics": Disposition.EXACT,
    "created_by_user_id": Disposition.EXACT,
    "updated_by_user_id": Disposition.EXACT,
}


# --------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RowCorrelation:
    """Which source row a finding is about.

    Anchored on the source row and its checkpoint -- the file's SHA-256, the
    partition ordinal and the absolute row ordinal (``start_row + index``) --
    plus the legal/display-name duplicate identity, carried as a digest.

    There is deliberately no ``customer_code`` field and no raw name field.
    Joining on the generated code would be joining on a value the legacy path
    repeats (every row can be ``CUST00001``), and the raw name is personal
    data that has no business appearing in a diagnostic.  Display name alone
    is not an anchor either: it is itself a decision input under test.
    """

    source_file_sha256: str
    partition_ordinal: int
    row_ordinal: int
    identity_digest: str


def identity_digest(display_name: str) -> str:
    """Digest the duplicate identity exactly as the duplicate rule sees it.

    Both real implementations compare ``legal_name`` to a ``.strip()``-ed
    display name with no case folding, so this normalizes by stripping and
    nothing more.  Case-folding here would make the correlation claim two
    rows share an identity that the system itself treats as distinct.

    This is a correlation key, not a security control.
    """
    return hashlib.sha256(display_name.strip().encode("utf-8")).hexdigest()[:32]


def correlate_row(
    *,
    source_file_sha256: str,
    partition_ordinal: int,
    start_row: int,
    index: int,
    display_name: str,
) -> RowCorrelation:
    """Anchor one row of one partition of one source file."""
    return RowCorrelation(
        source_file_sha256=source_file_sha256,
        partition_ordinal=partition_ordinal,
        row_ordinal=start_row + index,
        identity_digest=identity_digest(display_name),
    )


# --------------------------------------------------------------------------
# Findings and rows
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DispositionFinding:
    """One refusal.

    ``reason`` describes the SHAPE of the disagreement and never carries a
    field value: a customer's legal name, address block or contact block is
    personal data, and a comparison report is not a place to copy it to.  The
    correlation is how an operator with database access finds the row.
    """

    kind: FindingKind
    field: str
    reason: str
    correlation: RowCorrelation | None = None


@dataclass(frozen=True, slots=True)
class ExistingCustomer:
    """The persisted identity of a customer that already existed."""

    customer_id: UUID
    customer_code: str


@dataclass(frozen=True, slots=True, repr=False)
class ConstructedRow:
    """A row both paths turned into an entity.

    ``legacy`` is what ``CustomerImporter`` built under ``construct_only``;
    ``durable`` is what the ``dotmac-imports`` path built through
    ``CustomerService.create_customer``.  Both are ``ar.customer`` instances.
    ``repr`` is suppressed because they carry customer data.
    """

    correlation: RowCorrelation
    legacy: Any
    durable: Any


@dataclass(frozen=True, slots=True)
class DuplicateRow:
    """A row both paths skipped because the customer already existed.

    Neither path constructs anything for a duplicate, so there is no field
    vector to compare.  What must hold instead is that the import did not
    disturb what was already there: same ``customer_id``, same
    ``customer_code``, before and after.
    """

    correlation: RowCorrelation
    before: ExistingCustomer
    after: ExistingCustomer


@dataclass(frozen=True, slots=True)
class RunWindow:
    """The wall-clock interval a shadow run occupied, inclusive at both ends."""

    started_at: datetime
    finished_at: datetime

    def contains(self, moment: datetime) -> bool:
        return self.started_at <= moment <= self.finished_at


# --------------------------------------------------------------------------
# Closure: added, removed or unclassified fields fail
# --------------------------------------------------------------------------


def model_field_names(model: type[Any] = Customer) -> tuple[str, ...]:
    """Every mapped COLUMN attribute of the model.

    Relationships are excluded on purpose: ``parent_customer`` and
    ``child_customers`` are navigation, not fields a constructor produces, and
    nothing about them survives into a persisted column.
    """
    return tuple(sa_inspect(model).mapper.columns.keys())


def unclassified_fields(model: type[Any] = Customer) -> tuple[str, ...]:
    """Columns the model has and the disposition does not name."""
    return tuple(
        name
        for name in model_field_names(model)
        if name not in CUSTOMER_FIELD_DISPOSITION
    )


def missing_fields(model: type[Any] = Customer) -> tuple[str, ...]:
    """Columns the disposition names and the model no longer has."""
    present = set(model_field_names(model))
    return tuple(name for name in CUSTOMER_FIELD_DISPOSITION if name not in present)


def closure_findings(model: type[Any] = Customer) -> tuple[DispositionFinding, ...]:
    findings = [
        DispositionFinding(
            kind=FindingKind.FIELD_UNCLASSIFIED,
            field=name,
            reason=(
                "the entity carries a column the disposition does not classify; "
                "classify it in CUSTOMER_FIELD_DISPOSITION rather than ignoring it"
            ),
        )
        for name in unclassified_fields(model)
    ]
    findings.extend(
        DispositionFinding(
            kind=FindingKind.FIELD_MISSING,
            field=name,
            reason="the disposition classifies a column the entity no longer has",
        )
        for name in missing_fields(model)
    )
    return tuple(findings)


def assert_disposition_is_closed(model: type[Any] = Customer) -> None:
    """Refuse a model the disposition does not completely describe."""
    findings = closure_findings(model)
    if findings:
        raise ColumnDispositionError(_describe(findings))


# --------------------------------------------------------------------------
# Per-field comparison
# --------------------------------------------------------------------------


def _stage(entity: Any) -> str:
    state = sa_inspect(entity)
    for name in ("transient", "pending", "persistent", "detached"):
        if getattr(state, name):
            return name
    return "unknown"  # pragma: no cover - SQLAlchemy states are exhaustive


def effective_value(entity: Any, name: str) -> Any:
    """What this column WOULD hold, given what the constructor decided.

    An assigned value wins.  Otherwise a scalar Python default is folded in,
    because the constructor that omitted the column and the constructor that
    wrote the default reach the same row.  A callable default or a server
    default yields ``DATABASE_ASSIGNED``; anything else yields ``None``,
    which is what an untouched nullable column persists as.
    """
    state = sa_inspect(entity)
    if name in state.dict:
        return state.dict[name]
    column = sa_inspect(type(entity)).mapper.columns[name]
    default = column.default
    if default is not None:
        if default.is_scalar:
            return default.arg
        return DATABASE_ASSIGNED
    if column.server_default is not None:
        return DATABASE_ASSIGNED
    return None


def generated_code_max_length(model: type[Any] = Customer) -> int:
    """Read the code length limit off the column rather than restating it."""
    length = sa_inspect(model).mapper.columns["customer_code"].type.length
    if not isinstance(length, int):  # pragma: no cover - schema diagnostics
        raise ColumnDispositionError(
            "customer_code has no declared length; the generated-code invariant "
            "cannot be derived from the schema"
        )
    return length


def check_generated_code(
    code: Any,
    *,
    organization_id: Any,
    seen: MutableSet[tuple[Any, str]],
    correlation: RowCorrelation | None = None,
    model: type[Any] = Customer,
) -> tuple[DispositionFinding, ...]:
    """The approved ``customer_code`` invariants.

    Not equality with the legacy code -- see this module's docstring.  What is
    required is that the value is non-empty, fits the column, and is unique
    within ``(organization_id, customer_code)`` across the run.
    """
    findings: list[DispositionFinding] = []
    if not isinstance(code, str) or not code.strip():
        findings.append(
            DispositionFinding(
                kind=FindingKind.GENERATED_CODE_INVALID,
                field="customer_code",
                reason="the generated code is empty or is not a string",
                correlation=correlation,
            )
        )
        return tuple(findings)
    limit = generated_code_max_length(model)
    if len(code) > limit:
        findings.append(
            DispositionFinding(
                kind=FindingKind.GENERATED_CODE_INVALID,
                field="customer_code",
                reason=(
                    f"the generated code is {len(code)} characters, past the "
                    f"{limit} the column declares"
                ),
                correlation=correlation,
            )
        )
    key = (organization_id, code)
    if key in seen:
        findings.append(
            DispositionFinding(
                kind=FindingKind.GENERATED_CODE_NOT_UNIQUE,
                field="customer_code",
                reason="a second row in this organization got the same generated code",
                correlation=correlation,
            )
        )
    seen.add(key)
    return tuple(findings)


def _compare_exact(row: ConstructedRow, name: str) -> tuple[DispositionFinding, ...]:
    # ``DATABASE_ASSIGNED`` compares unequal to every real value, so a column
    # one side decides and the other leaves to the database is reported here
    # rather than through a separate added/removed kind.  No EXACT column can
    # reach that state today -- every one of them has a scalar default or no
    # default at all -- and a distinct finding kind nobody can currently plant
    # would be an unproven guard.
    legacy = effective_value(row.legacy, name)
    durable = effective_value(row.durable, name)
    if legacy != durable:
        return (
            DispositionFinding(
                kind=FindingKind.EXACT_MISMATCH,
                field=name,
                reason="the two constructors decided different values",
                correlation=row.correlation,
            ),
        )
    return ()


def _compare_surrogate_key(
    row: ConstructedRow, name: str
) -> tuple[DispositionFinding, ...]:
    """Well-formed, and UNEQUAL -- never merely skipped.

    The two paths differ here structurally and permanently: the retiring
    importer assigns ``uuid4()`` in ``create_entity`` while
    ``CustomerService.create_customer`` names no ``customer_id`` at all and
    lets the column's default supply one.  So "unequal" is only assertable
    when both sides actually carry a key, and the interesting failure is the
    one where NEITHER does -- at that point nothing distinguishes the two
    entities and a comparator handed one object twice would report perfect
    agreement.  That case is named rather than passed over.
    """
    findings: list[DispositionFinding] = []
    values: list[Any] = []
    deferred = 0
    for side, entity in (("retiring", row.legacy), ("durable", row.durable)):
        value = effective_value(entity, name)
        if value is DATABASE_ASSIGNED:
            deferred += 1
            continue
        if not isinstance(value, UUID):
            findings.append(
                DispositionFinding(
                    kind=FindingKind.SURROGATE_KEY_MALFORMED,
                    field=name,
                    reason=(
                        f"the {side} path's surrogate key is neither a UUID nor "
                        "left to the database"
                    ),
                    correlation=row.correlation,
                )
            )
            continue
        values.append(value)
    if row.legacy is row.durable or (len(values) == 2 and values[0] == values[1]):
        findings.append(
            DispositionFinding(
                kind=FindingKind.SURROGATE_KEY_COLLIDED,
                field=name,
                reason=(
                    "both sides are the same entity, so the comparison is "
                    "comparing an object with itself"
                ),
                correlation=row.correlation,
            )
        )
    elif deferred == 2:
        findings.append(
            DispositionFinding(
                kind=FindingKind.SURROGATE_KEY_INDETERMINATE,
                field=name,
                reason=(
                    "neither side carries a surrogate key, so nothing here can "
                    "tell two entities apart from one entity twice"
                ),
                correlation=row.correlation,
            )
        )
    return tuple(findings)


def _compare_run_timestamp(
    row: ConstructedRow, name: str, window: RunWindow
) -> tuple[DispositionFinding, ...]:
    """A permitted difference is asserted against its reason, never skipped.

    The reason a timestamp may differ is that the DATABASE stamps it, and both
    constructors leave it alone.  So the assertion is not "ignore this column"
    -- it is that the two sides agree about WHO decides it, and that any value
    either of them did decide falls inside the run window.

    Demanding that a value be present would be wrong at the stage the two
    entities are comparable: ``created_at`` carries a server default and
    nothing has been flushed, so a present timestamp is the exceptional case
    rather than the expected one.  Requiring presence would fail every honest
    comparison, which is why the rule is agreement plus window rather than
    presence plus window.
    """
    findings: list[DispositionFinding] = []
    stamped: dict[str, datetime | None] = {}
    for side, entity in (("retiring", row.legacy), ("durable", row.durable)):
        value = effective_value(entity, name)
        if value is DATABASE_ASSIGNED or value is None:
            stamped[side] = None
            continue
        if not isinstance(value, datetime):
            stamped[side] = None
            findings.append(
                DispositionFinding(
                    kind=FindingKind.TIMESTAMP_OUTSIDE_RUN_WINDOW,
                    field=name,
                    reason=f"the {side} path put a non-datetime in a run timestamp",
                    correlation=row.correlation,
                )
            )
            continue
        stamped[side] = value
        if not window.contains(value):
            findings.append(
                DispositionFinding(
                    kind=FindingKind.TIMESTAMP_OUTSIDE_RUN_WINDOW,
                    field=name,
                    reason=(
                        f"the {side} path stamped a time outside the run window; "
                        "a permitted difference still has to hold to its reason"
                    ),
                    correlation=row.correlation,
                )
            )
    if (stamped["retiring"] is None) != (stamped["durable"] is None):
        findings.append(
            DispositionFinding(
                kind=FindingKind.TIMESTAMP_PROVENANCE_DIVERGED,
                field=name,
                reason=(
                    "one path decided this timestamp and the other left it to "
                    "the database; the permitted difference is WHO stamps it, "
                    "and the two no longer agree"
                ),
                correlation=row.correlation,
            )
        )
    return tuple(findings)


def compare_constructed_row(
    row: ConstructedRow,
    *,
    window: RunWindow,
    seen_codes: MutableSet[tuple[Any, str]],
) -> tuple[DispositionFinding, ...]:
    """Compare one row's field vector under the disposition."""
    legacy_stage = _stage(row.legacy)
    durable_stage = _stage(row.durable)
    if legacy_stage != durable_stage:
        # A transient entity reads unset columns as unset; a flushed one has
        # had its defaults applied.  Comparing across the two would report
        # differences that are lifecycle, not decision -- so refuse rather
        # than produce a field report nobody can trust.
        return (
            DispositionFinding(
                kind=FindingKind.STAGE_MISMATCH,
                field="*",
                reason=(
                    f"the retiring entity is {legacy_stage} and the durable "
                    f"entity is {durable_stage}; capture both at the same "
                    "persistence stage before comparing"
                ),
                correlation=row.correlation,
            ),
        )
    findings: list[DispositionFinding] = []
    for name, disposition in CUSTOMER_FIELD_DISPOSITION.items():
        if disposition is Disposition.EXACT:
            findings.extend(_compare_exact(row, name))
        elif disposition is Disposition.SURROGATE_KEY:
            findings.extend(_compare_surrogate_key(row, name))
        elif disposition is Disposition.RUN_TIMESTAMP:
            findings.extend(_compare_run_timestamp(row, name, window))
        else:
            findings.extend(
                check_generated_code(
                    effective_value(row.durable, name),
                    organization_id=effective_value(row.durable, "organization_id"),
                    seen=seen_codes,
                    correlation=row.correlation,
                )
            )
    return tuple(findings)


@dataclass(frozen=True, slots=True)
class CodeAllocation:
    """Evidence binding raw-row identity to an identity and an assigned code.

    One of the ruled ``customer_code`` invariants is that the run records
    raw-row identity -> ``customer_id`` -> assigned ``customer_code``.  The
    correlation IS the raw-row identity, so a record is exactly these three.

    Note what this exposes about timing.  ``CustomerService.create_customer``
    allocates the code but leaves ``customer_id`` to the database, so at the
    stage the two constructors are comparable the durable customer HAS a code
    and does NOT yet have an identity.  Evidence is therefore recordable only
    once the durable side has an identity -- and ``record_allocation`` refuses
    rather than inventing one or quietly recording two of the three.
    """

    correlation: RowCorrelation
    customer_id: UUID
    customer_code: str


def record_allocation(row: ConstructedRow) -> CodeAllocation:
    """Bind raw-row identity to the identity and code the durable path assigned."""
    findings = allocation_findings(row)
    if findings:
        raise ColumnDispositionError(_describe(findings))
    return CodeAllocation(
        correlation=row.correlation,
        customer_id=effective_value(row.durable, "customer_id"),
        customer_code=effective_value(row.durable, "customer_code"),
    )


def allocation_findings(row: ConstructedRow) -> tuple[DispositionFinding, ...]:
    """Why this row cannot yet yield an allocation record, if it cannot."""
    identity = effective_value(row.durable, "customer_id")
    code = effective_value(row.durable, "customer_code")
    findings: list[DispositionFinding] = []
    if not isinstance(identity, UUID):
        findings.append(
            DispositionFinding(
                kind=FindingKind.ALLOCATION_NOT_RECORDABLE,
                field="customer_id",
                reason=(
                    "the durable customer has no identity yet, so evidence "
                    "would bind a code to nothing"
                ),
                correlation=row.correlation,
            )
        )
    if not isinstance(code, str) or not code.strip():
        findings.append(
            DispositionFinding(
                kind=FindingKind.ALLOCATION_NOT_RECORDABLE,
                field="customer_code",
                reason="the durable customer carries no assigned code to record",
                correlation=row.correlation,
            )
        )
    return tuple(findings)


def compare_duplicate_row(row: DuplicateRow) -> tuple[DispositionFinding, ...]:
    """An existing duplicate must come out of the run exactly as it went in."""
    findings: list[DispositionFinding] = []
    if row.after.customer_code != row.before.customer_code:
        findings.append(
            DispositionFinding(
                kind=FindingKind.DUPLICATE_CODE_CHANGED,
                field="customer_code",
                reason=(
                    "an already-existing customer's generated code did not "
                    "survive the import"
                ),
                correlation=row.correlation,
            )
        )
    if row.after.customer_id != row.before.customer_id:
        findings.append(
            DispositionFinding(
                kind=FindingKind.DUPLICATE_IDENTITY_CHANGED,
                field="customer_id",
                reason=(
                    "an already-existing customer's persisted identity did not "
                    "survive the import"
                ),
                correlation=row.correlation,
            )
        )
    return tuple(findings)


def compare_partition(
    rows: Iterable[ConstructedRow | DuplicateRow],
    *,
    window: RunWindow,
    model: type[Any] = Customer,
) -> tuple[DispositionFinding, ...]:
    """Close the disposition, then compare every row against it."""
    findings: list[DispositionFinding] = list(closure_findings(model))
    seen_codes: set[tuple[Any, str]] = set()
    for row in rows:
        if isinstance(row, DuplicateRow):
            findings.extend(compare_duplicate_row(row))
        else:
            findings.extend(
                compare_constructed_row(row, window=window, seen_codes=seen_codes)
            )
    return tuple(findings)


def assert_partition_agrees(
    rows: Sequence[ConstructedRow | DuplicateRow],
    *,
    window: RunWindow,
    model: type[Any] = Customer,
) -> None:
    """Guard form: raise if anything at all was refused."""
    findings = compare_partition(rows, window=window, model=model)
    if findings:
        raise ColumnDispositionError(_describe(findings))


def _describe(findings: Sequence[DispositionFinding]) -> str:
    lines = [
        f"{item.kind.value} on {item.field}: {item.reason}"
        + (
            ""
            if item.correlation is None
            else (
                f" [source={item.correlation.source_file_sha256[:12]}"
                f" partition={item.correlation.partition_ordinal}"
                f" row={item.correlation.row_ordinal}"
                f" identity={item.correlation.identity_digest[:12]}]"
            )
        )
        for item in findings
    ]
    return "customer column disposition refused this comparison:\n" + "\n".join(lines)


__all__ = [
    "CUSTOMER_FIELD_DISPOSITION",
    "DATABASE_ASSIGNED",
    "CodeAllocation",
    "ColumnDispositionError",
    "ConstructedRow",
    "Disposition",
    "DispositionFinding",
    "DuplicateRow",
    "ExistingCustomer",
    "FindingKind",
    "RowCorrelation",
    "RunWindow",
    "allocation_findings",
    "assert_disposition_is_closed",
    "assert_partition_agrees",
    "check_generated_code",
    "closure_findings",
    "compare_constructed_row",
    "compare_duplicate_row",
    "compare_partition",
    "correlate_row",
    "effective_value",
    "generated_code_max_length",
    "identity_digest",
    "missing_fields",
    "record_allocation",
    "model_field_names",
    "unclassified_fields",
]
