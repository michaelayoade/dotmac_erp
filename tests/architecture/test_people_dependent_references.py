"""Exact static ratchet for dependencies on ERP's two People hub tables.

The static inventory records model-intent identities, not Python declaration
count.  Two ORM declarations of the same intended constraint therefore collapse
to one row, while their declaration paths remain as evidence.  Foreign keys
in reusable mixins expand to each concrete mapped table because that is the
model's intended table-level dependency.

This is a retirement ledger, not an allowlist.  A new dependency and a
retired dependency both fail until the checked-in inventory changes in the
same reviewed slice.  The PostgreSQL catalog gate consumes the same six-part
identity and separately freezes referential actions and deferrability; this
static gate additionally proves where each constraint came from before
migrations are run.
"""

from __future__ import annotations

import ast
import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = REPO_ROOT / "app" / "models"
INVENTORY = REPO_ROOT / "docs" / "inventories" / "people-dependent-references.tsv"
PHYSICAL_INVENTORY = REPO_ROOT / "tests" / "integration" / "people_hub_fk_catalog.tsv"
INVENTORY_FIELDS = (
    "source_schema",
    "source_table",
    "source_column",
    "target_schema",
    "target_table",
    "target_column",
    "declaration_kind",
    "declaration_paths",
)
PHYSICAL_INVENTORY_FIELDS = INVENTORY_FIELDS[:6] + (
    "on_update",
    "on_delete",
    "match_type",
    "deferrable",
    "initially_deferred",
)
MODEL_ONLY_DEBT_BASELINE = 55
PHYSICAL_ONLY_DEBT_BASELINE = 7
TARGETS = {
    ("hr", "employee", "employee_id"),
    ("public", "people", "id"),
}
DECLARATION_KINDS = {"explicit", "mixin_expansion"}


@dataclass(frozen=True, order=True)
class ForeignKeyIdentity:
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str


@dataclass(frozen=True)
class Evidence:
    kind: str
    path: str


@dataclass(frozen=True)
class ClassDeclaration:
    path: str
    name: str
    line: int
    bases: tuple[BaseReference, ...]
    table_name: str | None
    schema: str
    declared_names: frozenset[str]
    foreign_keys: tuple[tuple[str, tuple[str, str, str], int], ...]


@dataclass(frozen=True)
class BaseReference:
    name: str
    source_path: str | None


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return node.func.id if isinstance(node.func, ast.Name) else None


def _base_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    while isinstance(node, ast.Subscript):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _assigned_value(node: ast.stmt, name: str) -> ast.expr | None:
    if isinstance(node, ast.Assign):
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if node.target.id == name:
            return node.value
    return None


def _schema_from_table_args(node: ast.expr | None) -> str:
    if node is None:
        return "public"
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Dict):
            continue
        for key, value in zip(candidate.keys, candidate.values, strict=True):
            if key is not None and _literal_string(key) == "schema":
                schema = _literal_string(value)
                if schema:
                    return schema
    return "public"


def _normalize_reference(reference: str) -> tuple[str, str, str] | None:
    parts = reference.split(".")
    if len(parts) == 2:
        parts.insert(0, "public")
    if len(parts) != 3:
        return None
    normalized = tuple(parts)
    return normalized if normalized in TARGETS else None


def _column_foreign_keys(
    node: ast.ClassDef,
) -> list[tuple[str, tuple[str, str, str], int]]:
    found: list[tuple[str, tuple[str, str, str], int]] = []
    for statement in node.body:
        column_name: str | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            column_name = statement.target.id
            value = statement.value
        elif isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                column_name = target.id
                value = statement.value
        if column_name is None or value is None:
            continue
        for candidate in ast.walk(value):
            if (
                not isinstance(candidate, ast.Call)
                or _call_name(candidate) != "ForeignKey"
            ):
                continue
            if (
                not candidate.args
                or (reference := _literal_string(candidate.args[0])) is None
            ):
                continue
            if (target := _normalize_reference(reference)) is not None:
                found.append((column_name, target, candidate.lineno))
    return found


def _constraint_foreign_keys(
    node: ast.ClassDef,
) -> list[tuple[str, tuple[str, str, str], int]]:
    found: list[tuple[str, tuple[str, str, str], int]] = []
    table_args = next(
        (
            value
            for statement in node.body
            if (value := _assigned_value(statement, "__table_args__")) is not None
        ),
        None,
    )
    if table_args is None:
        return found
    for candidate in ast.walk(table_args):
        if (
            not isinstance(candidate, ast.Call)
            or _call_name(candidate) != "ForeignKeyConstraint"
        ):
            continue
        if len(candidate.args) < 2:
            continue
        local_nodes = candidate.args[0]
        remote_nodes = candidate.args[1]
        if not isinstance(local_nodes, (ast.List, ast.Tuple)) or not isinstance(
            remote_nodes, (ast.List, ast.Tuple)
        ):
            continue
        local_names = [_literal_string(item) for item in local_nodes.elts]
        remote_names = [_literal_string(item) for item in remote_nodes.elts]
        if any(name is None for name in (*local_names, *remote_names)):
            continue
        for column_name, reference in zip(local_names, remote_names, strict=True):
            assert column_name is not None and reference is not None
            if (target := _normalize_reference(reference)) is not None:
                found.append((column_name, target, candidate.lineno))
    return found


def _import_path(path: str, module: str | None, level: int) -> str | None:
    if level:
        parent = Path(path).parent
        for _ in range(level - 1):
            parent = parent.parent
        module_path = parent / Path(*(module or "").split("."))
    elif module:
        module_path = Path(*module.split("."))
    else:
        return None
    return module_path.with_suffix(".py").as_posix()


def _parse_classes(path: str, source: str) -> list[ClassDeclaration]:
    tree = ast.parse(source, filename=path)
    imports: dict[str, tuple[str, str]] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.ImportFrom):
            continue
        source_path = _import_path(path, statement.module, statement.level)
        if source_path is None:
            continue
        for name in statement.names:
            imports[name.asname or name.name] = (name.name, source_path)
    declarations: list[ClassDeclaration] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        table_name: str | None = None
        table_args: ast.expr | None = None
        for statement in node.body:
            if (value := _assigned_value(statement, "__tablename__")) is not None:
                table_name = _literal_string(value)
            if (value := _assigned_value(statement, "__table_args__")) is not None:
                table_args = value
        declarations.append(
            ClassDeclaration(
                path=path,
                name=node.name,
                line=node.lineno,
                bases=tuple(
                    BaseReference(
                        imports.get(base_name, (base_name, None))[0],
                        imports.get(base_name, (base_name, None))[1],
                    )
                    for base in node.bases
                    if (base_name := _base_name(base)) is not None
                ),
                table_name=table_name,
                schema=_schema_from_table_args(table_args),
                declared_names=frozenset(
                    target.id
                    for statement in node.body
                    for target in (
                        statement.targets
                        if isinstance(statement, ast.Assign)
                        else (statement.target,)
                        if isinstance(statement, ast.AnnAssign)
                        else ()
                    )
                    if isinstance(target, ast.Name)
                ),
                foreign_keys=tuple(
                    _column_foreign_keys(node) + _constraint_foreign_keys(node)
                ),
            )
        )
    return declarations


def _inherited_foreign_keys(
    declaration: ClassDeclaration,
    classes_by_name: dict[str, tuple[ClassDeclaration, ...]],
    classes_by_path_and_name: dict[tuple[str, str], ClassDeclaration],
) -> tuple[tuple[str, tuple[str, str, str], int, ClassDeclaration], ...]:
    found: list[tuple[str, tuple[str, str, str], int, ClassDeclaration]] = []
    visited: set[tuple[str, str, int]] = set()

    def visit(reference: BaseReference, importing_path: str) -> None:
        imported = (
            classes_by_path_and_name.get((reference.source_path, reference.name))
            if reference.source_path is not None
            else None
        )
        local = classes_by_path_and_name.get((importing_path, reference.name))
        candidates = (
            (imported,)
            if imported is not None
            else (local,)
            if local is not None
            else classes_by_name.get(reference.name, ())
            if len(classes_by_name.get(reference.name, ())) == 1
            else ()
        )
        for base in candidates:
            assert base is not None
            key = (base.path, base.name, base.line)
            if key in visited:
                continue
            visited.add(key)
            found.extend((*foreign_key, base) for foreign_key in base.foreign_keys)
            for ancestor in base.bases:
                visit(ancestor, base.path)

    for base in declaration.bases:
        visit(base, declaration.path)
    return tuple(found)


def _scan_sources(sources: dict[str, str]) -> dict[ForeignKeyIdentity, set[Evidence]]:
    classes = [
        declaration
        for path, source in sorted(sources.items())
        for declaration in _parse_classes(path, source)
    ]
    classes_by_name_lists: dict[str, list[ClassDeclaration]] = defaultdict(list)
    for declaration in classes:
        classes_by_name_lists[declaration.name].append(declaration)
    classes_by_name = {
        name: tuple(declarations)
        for name, declarations in classes_by_name_lists.items()
    }
    classes_by_path_and_name = {
        (declaration.path, declaration.name): declaration for declaration in classes
    }

    found: dict[ForeignKeyIdentity, set[Evidence]] = defaultdict(set)
    for declaration in classes:
        if declaration.table_name is None:
            continue
        for column, target, line in declaration.foreign_keys:
            identity = ForeignKeyIdentity(
                declaration.schema,
                declaration.table_name,
                column,
                *target,
            )
            found[identity].add(Evidence("explicit", f"{declaration.path}:{line}"))
        for column, target, line, mixin in _inherited_foreign_keys(
            declaration, classes_by_name, classes_by_path_and_name
        ):
            # A concrete declaration overrides the same-named mixin column;
            # SQLAlchemy does not install both foreign keys in that case.
            if column in declaration.declared_names:
                continue
            identity = ForeignKeyIdentity(
                declaration.schema,
                declaration.table_name,
                column,
                *target,
            )
            found[identity].add(
                Evidence(
                    "mixin_expansion",
                    f"{declaration.path}:{declaration.line} via {mixin.path}:{line}",
                )
            )
    return dict(found)


def _scan_models() -> dict[ForeignKeyIdentity, set[Evidence]]:
    return _scan_sources(
        {
            path.relative_to(REPO_ROOT).as_posix(): path.read_text()
            for path in sorted(MODEL_ROOT.rglob("*.py"))
        }
    )


def _load_inventory() -> dict[ForeignKeyIdentity, set[Evidence]]:
    with INVENTORY.open(newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == INVENTORY_FIELDS
        loaded: dict[ForeignKeyIdentity, set[Evidence]] = {}
        previous: ForeignKeyIdentity | None = None
        for row in reader:
            identity = ForeignKeyIdentity(
                *(row[field] for field in INVENTORY_FIELDS[:6])
            )
            assert identity not in loaded, f"duplicate inventory identity: {identity}"
            assert previous is None or previous < identity, "inventory must stay sorted"
            assert (
                identity.target_schema,
                identity.target_table,
                identity.target_column,
            ) in TARGETS
            kinds = set(row["declaration_kind"].split("|"))
            assert kinds <= DECLARATION_KINDS and kinds
            paths = row["declaration_paths"].split("|")
            assert paths == sorted(paths) and all(paths)
            loaded[identity] = {
                Evidence(kind, path)
                for kind in kinds
                for path in paths
                if (kind == "mixin_expansion") == (" via " in path)
            }
            assert {evidence.kind for evidence in loaded[identity]} == kinds
            previous = identity
    return loaded


def _load_physical_inventory() -> set[ForeignKeyIdentity]:
    with PHYSICAL_INVENTORY.open(newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == PHYSICAL_INVENTORY_FIELDS
        loaded: list[ForeignKeyIdentity] = []
        for row in reader:
            identity = ForeignKeyIdentity(
                *(row[field] for field in INVENTORY_FIELDS[:6])
            )
            assert (
                identity.target_schema,
                identity.target_table,
                identity.target_column,
            ) in TARGETS
            loaded.append(identity)
    assert loaded == sorted(loaded), "physical People FK inventory must stay sorted"
    assert len(loaded) == len(set(loaded)), (
        "physical People FK inventory contains duplicate identities"
    )
    return set(loaded)


def _debt_deltas(
    static: set[ForeignKeyIdentity],
    physical: set[ForeignKeyIdentity],
    model_only_baseline: int,
    physical_only_baseline: int,
) -> tuple[int, int]:
    return (
        len(static - physical) - model_only_baseline,
        len(physical - static) - physical_only_baseline,
    )


def _format_identities(identities: set[ForeignKeyIdentity]) -> str:
    return "\n".join(
        f"  {item.source_schema}.{item.source_table}.{item.source_column} -> "
        f"{item.target_schema}.{item.target_table}.{item.target_column}"
        for item in sorted(identities)
    )


def test_people_hub_dependency_inventory_matches_models_exactly() -> None:
    actual = _scan_models()
    expected = _load_inventory()
    new = set(actual) - set(expected)
    retired = set(expected) - set(actual)
    assert not new and not retired, (
        "People dependency inventory drifted. This is a two-directional retirement "
        "ratchet: update the inventory in the same reviewed domain slice.\n"
        f"New dependencies:\n{_format_identities(new) or '  (none)'}\n"
        f"Retired dependencies:\n{_format_identities(retired) or '  (none)'}"
    )
    assert actual == expected, "declaration kind/path evidence drifted"


def test_people_hub_model_catalog_drift_matches_baselines() -> None:
    static = set(_load_inventory())
    physical = _load_physical_inventory()
    model_only = static - physical
    physical_only = physical - static
    model_delta, physical_delta = _debt_deltas(
        static,
        physical,
        MODEL_ONLY_DEBT_BASELINE,
        PHYSICAL_ONLY_DEBT_BASELINE,
    )
    assert model_delta == 0 and physical_delta == 0, (
        "People model/catalog FK debt changed. This two-directional ratchet must be "
        "updated in the same reviewed slice even when either kind of debt falls, "
        "so the reduction remains visible.\n"
        f"Model-only baseline: {MODEL_ONLY_DEBT_BASELINE}\n"
        f"Model-only actual: {len(model_only)}\n"
        f"Model-only delta: {model_delta:+d}\n"
        f"Physical-only baseline: {PHYSICAL_ONLY_DEBT_BASELINE}\n"
        f"Physical-only actual: {len(physical_only)}\n"
        f"Physical-only delta: {physical_delta:+d}\n"
        f"Model-only identities:\n{_format_identities(model_only)}\n"
        f"Physical-only identities:\n{_format_identities(physical_only)}"
    )


def test_people_hub_dependency_detector_sensitivity() -> None:
    sample = """
from sqlalchemy import ForeignKey, ForeignKeyConstraint
from app.models.example_mixins import AuditMixin

class Base:
    pass

class Expense(Base, AuditMixin):
    __tablename__ = "expense"
    __table_args__ = (
        ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        {"schema": "expense"},
    )
    owner_id = mapped_column(ForeignKey("people.id"))

class OverrideExpense(Base, AuditMixin):
    __tablename__ = "override_expense"
    __table_args__ = {"schema": "expense"}
    created_by_id = mapped_column(ForeignKey("hr.employee.employee_id"))
"""
    imported_mixin = """
class AuditMixin:
    created_by_id = mapped_column(ForeignKey("people.id"))
"""
    same_named_but_unused_mixin = """
class AuditMixin:
    misleading_employee_id = mapped_column(ForeignKey("hr.employee.employee_id"))
"""
    duplicate = """
class ExpenseShadow(Base):
    __tablename__ = "expense"
    __table_args__ = {"schema": "expense"}
    owner_id = mapped_column(ForeignKey("people.id"))
"""
    actual = _scan_sources(
        {
            "app/models/example.py": sample,
            "app/models/example_mixins.py": imported_mixin,
            "app/models/example_shadow.py": duplicate,
            "app/models/unused_mixins.py": same_named_but_unused_mixin,
        }
    )
    assert set(actual) == {
        ForeignKeyIdentity(
            "expense", "expense", "employee_id", "hr", "employee", "employee_id"
        ),
        ForeignKeyIdentity("expense", "expense", "owner_id", "public", "people", "id"),
        ForeignKeyIdentity(
            "expense", "expense", "created_by_id", "public", "people", "id"
        ),
        ForeignKeyIdentity(
            "expense",
            "override_expense",
            "created_by_id",
            "hr",
            "employee",
            "employee_id",
        ),
    }
    owner_evidence = actual[
        ForeignKeyIdentity("expense", "expense", "owner_id", "public", "people", "id")
    ]
    assert len(owner_evidence) == 2, "duplicate model declarations must collapse"
    assert {
        evidence.kind
        for evidence in actual[
            ForeignKeyIdentity(
                "expense", "expense", "created_by_id", "public", "people", "id"
            )
        ]
    } == {"mixin_expansion"}


def test_people_hub_model_catalog_debt_detector_is_two_directional() -> None:
    shared_identity = ForeignKeyIdentity(
        "expense", "claim", "employee_id", "hr", "employee", "employee_id"
    )
    model_only_identity = ForeignKeyIdentity(
        "support", "ticket", "employee_id", "hr", "employee", "employee_id"
    )
    added_model_identity = ForeignKeyIdentity(
        "workflow", "task", "assignee_id", "public", "people", "id"
    )
    physical_only_identity = ForeignKeyIdentity(
        "support", "notification", "actor_id", "public", "people", "id"
    )
    added_physical_identity = ForeignKeyIdentity(
        "inventory", "request", "creator_id", "public", "people", "id"
    )
    static = {shared_identity, model_only_identity}
    physical = {shared_identity, physical_only_identity}

    assert _debt_deltas(static, physical, 1, 1) == (0, 0)
    assert _debt_deltas(static | {added_model_identity}, physical, 1, 1) == (1, 0)
    assert _debt_deltas({shared_identity}, physical, 1, 1) == (-1, 0)
    assert _debt_deltas(static, physical | {added_physical_identity}, 1, 1) == (
        0,
        1,
    )
    assert _debt_deltas(static, {shared_identity}, 1, 1) == (0, -1)
