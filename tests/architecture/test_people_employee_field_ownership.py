"""Exact ownership coverage for the wide legacy Employee aggregate.

The ownership TSV is a cutover gate, not an illustrative inventory.  Every
mapped column on ``hr.employee`` must have one disposition, including
columns contributed by its mapped mixins.  The six models in
``employee_extended.py`` are tracked as whole business records because their
internal fields must move together under one eventual owner.

The checks are deliberately static.  They answer whether the checked-in ORM
surface and the checked-in ownership decisions agree; the integration catalog
test separately proves that the same ledger covers migrated PostgreSQL.
"""

from __future__ import annotations

import ast
import csv
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = REPO_ROOT / "docs" / "inventories" / "people-employee-field-ownership.tsv"
EMPLOYEE_MODEL = REPO_ROOT / "app" / "models" / "people" / "hr" / "employee.py"
PEOPLE_BASE = REPO_ROOT / "app" / "models" / "people" / "base.py"
EMPLOYEE_EXTENDED = (
    REPO_ROOT / "app" / "models" / "people" / "hr" / "employee_extended.py"
)

INVENTORY_FIELDS = (
    "source_entity",
    "source_field",
    "current_authority",
    "target_disposition",
    "intended_owner",
    "decision_status",
    "notes",
)
ALLOWED_DECISION_STATUSES = {
    "resolved",
    "resolved_retire",
    "unresolved",
    "blocked_unreleased",
}
ALLOWED_INTENDED_OWNERS = {
    "UNRESOLVED",
    "none",
    "asm-dotmac-erp/dotmac-people",
    "asm-dotmac-erp/dotmac-workforce",
}
EXPECTED_EXTENDED_ENTITIES = {
    "hr.employee_document",
    "hr.employee_qualification",
    "hr.employee_certification",
    "hr.employee_dependent",
    "hr.skill",
    "hr.employee_skill",
}
REQUIRED_EMPLOYEE_MIXINS = {
    "AuditMixin",
    "ERPNextSyncMixin",
    "VersionMixin",
}
TERMINAL_BASES = {"Base", "object"}


@dataclass(frozen=True)
class OwnershipRow:
    source_entity: str
    source_field: str
    current_authority: str
    target_disposition: str
    intended_owner: str
    decision_status: str
    notes: str


def _base_name(node: ast.expr) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if isinstance(node, ast.Attribute):
            return node.attr
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _call_name(node: ast.Call) -> str | None:
    target: ast.expr = node.func
    while isinstance(target, ast.Attribute):
        return target.attr
    return target.id if isinstance(target, ast.Name) else None


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _assigned_value(statement: ast.stmt, name: str) -> ast.expr | None:
    if isinstance(statement, ast.Assign):
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            return statement.value
    if isinstance(statement, ast.AnnAssign):
        if isinstance(statement.target, ast.Name) and statement.target.id == name:
            return statement.value
    return None


def _class_catalog(sources: dict[str, str]) -> dict[str, ast.ClassDef]:
    classes: dict[str, ast.ClassDef] = {}
    duplicates: set[str] = set()
    for path, source in sorted(sources.items()):
        for node in ast.parse(source, filename=path).body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name in classes:
                duplicates.add(node.name)
            classes[node.name] = node
    assert not duplicates, f"duplicate model/mixin class names: {sorted(duplicates)}"
    return classes


def _direct_mapped_columns(node: ast.ClassDef) -> set[str]:
    columns: set[str] = set()
    for statement in node.body:
        assigned_name: str | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            assigned_name = statement.target.id
            value = statement.value
        elif isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                assigned_name = target.id
                value = statement.value
        if (
            assigned_name is None
            or not isinstance(value, ast.Call)
            or _call_name(value) != "mapped_column"
        ):
            continue
        explicit_name = _literal_string(value.args[0]) if value.args else None
        columns.add(explicit_name or assigned_name)
    return columns


def _mapped_columns(
    sources: dict[str, str], class_name: str
) -> tuple[set[str], set[str]]:
    """Return direct and recursively inherited mapped columns for a model."""

    classes = _class_catalog(sources)
    assert class_name in classes, f"model class is not statically visible: {class_name}"
    visited: set[str] = set()
    active: set[str] = set()

    def visit(name: str) -> set[str]:
        if name in TERMINAL_BASES:
            return set()
        assert name in classes, (
            f"mapped base {name!r} is not in the ownership scanner sources; "
            "add its authoritative source before changing Employee inheritance"
        )
        if name in visited:
            return set()
        assert name not in active, f"cyclic model inheritance involving {name}"
        active.add(name)
        node = classes[name]
        columns = _direct_mapped_columns(node)
        for base in filter(None, (_base_name(item) for item in node.bases)):
            columns.update(visit(base))
        active.remove(name)
        visited.add(name)
        return columns

    model = classes[class_name]
    direct = _direct_mapped_columns(model)
    return direct, visit(class_name)


def _table_schema(node: ast.ClassDef) -> str:
    table_args = next(
        (
            value
            for statement in node.body
            if (value := _assigned_value(statement, "__table_args__")) is not None
        ),
        None,
    )
    if table_args is None:
        return "public"
    for candidate in ast.walk(table_args):
        if not isinstance(candidate, ast.Dict):
            continue
        for key, value in zip(candidate.keys, candidate.values, strict=True):
            if key is not None and _literal_string(key) == "schema":
                schema = _literal_string(value)
                if schema:
                    return schema
    return "public"


def _mapped_entities(source: str, path: str) -> set[str]:
    entities: set[str] = set()
    for node in ast.parse(source, filename=path).body:
        if not isinstance(node, ast.ClassDef):
            continue
        table_name = next(
            (
                _literal_string(value)
                for statement in node.body
                if (value := _assigned_value(statement, "__tablename__")) is not None
            ),
            None,
        )
        if table_name is not None:
            entities.add(f"{_table_schema(node)}.{table_name}")
    return entities


def _read_inventory() -> list[OwnershipRow]:
    with INVENTORY.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == INVENTORY_FIELDS
        rows = [OwnershipRow(**row) for row in reader]
    assert rows, "People employee ownership inventory must not be empty"
    return rows


def _assert_exact_coverage(
    *, label: str, inventory_values: set[str], model_values: set[str]
) -> None:
    missing = model_values - inventory_values
    stale = inventory_values - model_values
    assert not missing, f"{label} missing from ownership inventory: {sorted(missing)}"
    assert not stale, (
        f"{label} no longer physical but still inventoried: {sorted(stale)}"
    )


def _assert_status_invariants(row: OwnershipRow) -> None:
    assert row.decision_status in ALLOWED_DECISION_STATUSES, (
        f"{row.source_entity}.{row.source_field}: unknown decision_status "
        f"{row.decision_status!r}"
    )
    assert row.intended_owner in ALLOWED_INTENDED_OWNERS, (
        f"{row.source_entity}.{row.source_field}: unknown intended_owner "
        f"{row.intended_owner!r}"
    )
    assert all(
        (
            row.source_entity,
            row.source_field,
            row.current_authority,
            row.target_disposition,
            row.intended_owner,
            row.decision_status,
            row.notes,
        )
    ), f"ownership row contains an empty field: {row}"
    assert row.current_authority.startswith("dotmac_erp/"), (
        f"{row.source_entity}.{row.source_field}: current authority must name "
        "the historical source explicitly"
    )

    if row.decision_status == "unresolved":
        assert row.intended_owner == "UNRESOLVED", (
            f"{row.source_entity}.{row.source_field}: unresolved rows must not "
            "imply a target owner"
        )
    else:
        assert row.intended_owner != "UNRESOLVED", (
            f"{row.source_entity}.{row.source_field}: a resolved or blocked row "
            "cannot retain the unresolved sentinel"
        )
    if row.intended_owner == "none":
        assert row.decision_status == "resolved_retire", (
            f"{row.source_entity}.{row.source_field}: owner 'none' is valid only "
            "for an explicit retirement"
        )

    if row.decision_status == "resolved":
        assert row.intended_owner != "none", (
            f"{row.source_entity}.{row.source_field}: carried fields require a "
            "named owner"
        )
    elif row.decision_status == "resolved_retire":
        assert "retire" in row.target_disposition.lower(), (
            f"{row.source_entity}.{row.source_field}: resolved_retire must state "
            "the retirement disposition"
        )
    elif row.decision_status == "blocked_unreleased":
        assert row.intended_owner not in {"none", "UNRESOLVED"}
        assert "released" in row.target_disposition.lower(), (
            f"{row.source_entity}.{row.source_field}: blocked_unreleased must name "
            "the release gate"
        )


def test_employee_inventory_covers_every_mapped_column_in_both_directions() -> None:
    rows = _read_inventory()
    employee_rows = [row for row in rows if row.source_entity == "hr.employee"]
    inventoried = {row.source_field for row in employee_rows}

    sources = {
        EMPLOYEE_MODEL.relative_to(REPO_ROOT).as_posix(): EMPLOYEE_MODEL.read_text(
            encoding="utf-8"
        ),
        PEOPLE_BASE.relative_to(REPO_ROOT).as_posix(): PEOPLE_BASE.read_text(
            encoding="utf-8"
        ),
    }
    classes = _class_catalog(sources)
    employee_bases = set(
        filter(None, (_base_name(base) for base in classes["Employee"].bases))
    )
    assert employee_bases >= REQUIRED_EMPLOYEE_MIXINS, (
        "Employee ownership scanning must continue to include AuditMixin, "
        "ERPNextSyncMixin and VersionMixin"
    )

    direct, all_mapped = _mapped_columns(sources, "Employee")
    assert all_mapped - direct, "the scanner failed to include inherited columns"
    _assert_exact_coverage(
        label="hr.employee columns",
        inventory_values=inventoried,
        model_values=all_mapped,
    )


def test_extended_inventory_covers_every_extended_entity_exactly_once() -> None:
    rows = _read_inventory()
    extended_rows = [row for row in rows if row.source_entity != "hr.employee"]
    assert all(row.source_field == "*" for row in extended_rows), (
        "extended Employee records are owned as whole entities and must use '*'"
    )

    model_entities = _mapped_entities(
        EMPLOYEE_EXTENDED.read_text(encoding="utf-8"),
        EMPLOYEE_EXTENDED.relative_to(REPO_ROOT).as_posix(),
    )
    assert model_entities == EXPECTED_EXTENDED_ENTITIES, (
        "employee_extended.py changed its six-entity boundary; classify the new "
        "or removed business record explicitly"
    )
    _assert_exact_coverage(
        label="employee extended entities",
        inventory_values={row.source_entity for row in extended_rows},
        model_values=model_entities,
    )


def test_inventory_keys_are_unique_and_decisions_are_well_formed() -> None:
    rows = _read_inventory()
    keys = [(row.source_entity, row.source_field) for row in rows]
    assert len(keys) == len(set(keys)), "duplicate People ownership inventory keys"
    for row in rows:
        _assert_status_invariants(row)


def test_coverage_ratchet_sensitivity_rejects_growth_and_stale_rows() -> None:
    with pytest.raises(AssertionError, match="missing from ownership inventory"):
        _assert_exact_coverage(
            label="planted columns",
            inventory_values={"employee_id"},
            model_values={"employee_id", "planted_new_column"},
        )
    with pytest.raises(AssertionError, match="no longer physical"):
        _assert_exact_coverage(
            label="planted columns",
            inventory_values={"employee_id", "planted_stale_column"},
            model_values={"employee_id"},
        )


def test_model_scanner_sensitivity_includes_mixin_columns_and_db_names() -> None:
    planted = """
class PlantedMixin:
    inherited_name: Mapped[str] = mapped_column("inherited_db_name", String())

class Employee(Base, PlantedMixin):
    employee_id: Mapped[str] = mapped_column(String())
"""
    direct, mapped = _mapped_columns({"planted.py": planted}, "Employee")
    assert direct == {"employee_id"}
    assert mapped == {"employee_id", "inherited_db_name"}


def test_status_sensitivity_refuses_disguised_unresolved_ownership() -> None:
    planted = OwnershipRow(
        source_entity="hr.employee",
        source_field="planted",
        current_authority="dotmac_erp/hr.Employee",
        target_disposition="retain until somebody decides",
        intended_owner="asm-dotmac-erp/dotmac-people",
        decision_status="unresolved",
        notes="Planted sensitivity row.",
    )
    with pytest.raises(AssertionError, match="must not imply a target owner"):
        _assert_status_invariants(planted)
