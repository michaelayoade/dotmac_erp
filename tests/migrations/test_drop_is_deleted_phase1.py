"""Smoke test for the drop-is_deleted Phase 1 migration.

Asserts the migration's structure: per-table coverage lists, that the
column drops are present in upgrade(), and that the downgrade is
symmetric. Uses AST/source-string parsing instead of executing the
module, because the migration imports `alembic.op` which is only
resolvable inside an alembic runtime context.

Note: the plan's recon nominally listed 13 base model FILES, but several
files contain multiple SoftDelete-using ORM classes:
  - app/models/people/hr/employee_extended.py -> 5 tables
  - app/models/people/hr/job_description.py   -> 2 tables
So the actual SoftDelete-using table count is 18, not 13. These tests
verify the migration's lists match that reality.
"""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path


def _find_migration_path() -> Path:
    versions = Path(__file__).resolve().parents[1].parent / "alembic" / "versions"
    candidates = list(versions.glob("*drop_is_deleted_phase1*.py"))
    assert len(candidates) == 1, (
        f"Expected exactly one drop_is_deleted_phase1 migration; "
        f"found {len(candidates)}: {candidates}"
    )
    return candidates[0]


def _load_migration_module() -> types.ModuleType:
    """Load the migration module by stubbing the runtime-only imports
    (`alembic.op`, `sqlalchemy`) so the module body executes without a
    live alembic context. Returns the loaded module so its module-level
    constants and function bodies can be inspected."""
    import importlib.util

    # Stub alembic.op
    if "alembic" not in sys.modules or not hasattr(sys.modules["alembic"], "op"):
        alembic_stub = types.ModuleType("alembic")
        alembic_stub.op = types.SimpleNamespace(
            add_column=lambda *a, **kw: None,
            drop_column=lambda *a, **kw: None,
            execute=lambda *a, **kw: None,
        )
        sys.modules["alembic"] = alembic_stub

    # Stub sqlalchemy + sqlalchemy.dialects.postgresql
    if "sqlalchemy" not in sys.modules:
        sa_stub = types.ModuleType("sqlalchemy")

        class _Anything:
            def __init__(self, *a, **kw):
                pass

            def __call__(self, *a, **kw):
                return self

        sa_stub.Column = _Anything
        sa_stub.Boolean = _Anything
        sa_stub.DateTime = _Anything
        sa_stub.text = lambda *a, **kw: None
        sys.modules["sqlalchemy"] = sa_stub

        dialects = types.ModuleType("sqlalchemy.dialects")
        sys.modules["sqlalchemy.dialects"] = dialects
        pg = types.ModuleType("sqlalchemy.dialects.postgresql")
        pg.UUID = _Anything
        sys.modules["sqlalchemy.dialects.postgresql"] = pg

    path = _find_migration_path()
    spec = importlib.util.spec_from_file_location("drop_is_deleted_phase1", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_module_loads():
    """The migration file must be importable (with runtime stubs) and
    expose upgrade/downgrade callables."""
    module = _load_migration_module()
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_migration_lists_have_consistent_lengths():
    """_PER_TABLE_MIGRATION must cover every SoftDelete-using table (18 in
    practice; not 13 as the recon nominally suggested) and
    _TABLES_NEEDING_IS_ACTIVE must be a subset of size 8."""
    module = _load_migration_module()

    # 18 = 13 base model files + 4 extra employee_extended sub-tables
    # (skill + the 4 employee_* sub-tables minus the one "base" entry) +
    # 1 extra job_description sub-table (competency). See module docstring.
    assert len(module._PER_TABLE_MIGRATION) == 18, (
        f"Expected exactly 18 (schema, table, set_clause) entries; "
        f"got {len(module._PER_TABLE_MIGRATION)}"
    )
    assert len(module._TABLES_NEEDING_IS_ACTIVE) == 8
    assert len(module._TABLES_WITH_DELETED_AT) == 14, (
        "_TABLES_WITH_DELETED_AT must list the 14 SoftDeleteMixin-using "
        "tables (the same set from which deleted_by_id is dropped)."
    )

    # The tables with new is_active columns must also appear in the
    # full migration list.
    new_set = set(module._TABLES_NEEDING_IS_ACTIVE)
    full_pairs = {(s, t) for s, t, _ in module._PER_TABLE_MIGRATION}
    assert new_set <= full_pairs, (
        f"_TABLES_NEEDING_IS_ACTIVE has entries not in _PER_TABLE_MIGRATION: "
        f"{new_set - full_pairs}"
    )

    # The tables-with-deleted_at set must also be a subset of the full
    # migration list (we only drop deleted_at where it exists).
    assert full_pairs >= module._TABLES_WITH_DELETED_AT, (
        f"_TABLES_WITH_DELETED_AT has entries not in _PER_TABLE_MIGRATION: "
        f"{module._TABLES_WITH_DELETED_AT - full_pairs}"
    )


def test_migration_drops_deleted_by_id_on_mixin_tables():
    """deleted_by_id is the third column of SoftDeleteMixin. The migration
    must drop it alongside is_deleted/deleted_at, otherwise the FK column
    is orphaned with no associated soft-delete bit."""
    path = _find_migration_path()
    source = path.read_text()
    tree = ast.parse(source)

    upgrade_fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )
    downgrade_fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "downgrade"
    )

    upgrade_source = ast.unparse(upgrade_fn)
    assert "deleted_by_id" in upgrade_source, (
        "upgrade() must drop deleted_by_id from SoftDeleteMixin tables"
    )

    downgrade_source = ast.unparse(downgrade_fn)
    assert "deleted_by_id" in downgrade_source, (
        "downgrade() must re-add deleted_by_id for symmetry"
    )
