"""Keep every ERP tenant file stage behind the cleanup-key reservation.

This AST guard covers direct dotmac_files.stage_file calls under app, scripts
and tools, including aliases and the public/submodule attribute form. It does
not resolve dynamic getattr or direct SQL writes to mod_files.stored_files;
those are prohibited by the module ownership contract and need separate review.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNER = "app/services/file_object_cleanup.py"


def stage_calls(tree: ast.AST) -> int:
    direct: set[str] = set()
    modules: set[str] = set()
    submodules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "dotmac_files",
            "dotmac_files.service",
        }:
            direct.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "stage_file"
            )
            if node.module == "dotmac_files":
                submodules.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "service"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "dotmac_files":
                    modules.add(alias.asname or "dotmac_files")
                elif alias.name == "dotmac_files.service":
                    if alias.asname:
                        submodules.add(alias.asname)
                    else:
                        modules.add("dotmac_files")
    return sum(
        isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id in direct
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == "stage_file"
            and (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in modules | submodules
                or isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "service"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id in modules
            )
        )
        for node in ast.walk(tree)
    )


def test_only_reservation_owner_calls_stage_file() -> None:
    hits = {}
    for root in ("app", "scripts", "tools"):
        for path in (ROOT / root).rglob("*.py"):
            relative = str(path.relative_to(ROOT))
            count = stage_calls(ast.parse(path.read_text(encoding="utf-8")))
            if count:
                hits[relative] = count
    assert hits == {OWNER: 1}


def test_direct_stage_bypass_is_detected() -> None:
    assert (
        stage_calls(
            ast.parse(
                "from dotmac_files import stage_file as save\nsave(db, prepared=p)"
            )
        )
        == 1
    )
    assert (
        stage_calls(
            ast.parse("import dotmac_files as files\nfiles.stage_file(db, prepared=p)")
        )
        == 1
    )
    assert (
        stage_calls(
            ast.parse(
                "import dotmac_files.service as svc\nsvc.stage_file(db, prepared=p)"
            )
        )
        == 1
    )
    assert (
        stage_calls(
            ast.parse(
                "from dotmac_files import service as svc\nsvc.stage_file(db, prepared=p)"
            )
        )
        == 1
    )
    assert (
        stage_calls(
            ast.parse(
                "import dotmac_files.service\ndotmac_files.service.stage_file(db, prepared=p)"
            )
        )
        == 1
    )
    assert stage_calls(ast.parse("def stage_file(): pass\nstage_file()")) == 0


def test_stage_owner_checks_reservation_under_key_lock() -> None:
    tree = ast.parse((ROOT / OWNER).read_text(encoding="utf-8"))
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "stage_tenant_file_if_unreserved"
    )
    calls = [
        node.func.id
        for node in ast.walk(owner)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "_lock_file_key" in calls
    assert "stage_file" in calls
    assert calls.index("_lock_file_key") < calls.index("stage_file")


def test_apply_reserves_before_planned_rows_and_delete() -> None:
    path = ROOT / "app/tasks/file_object_reconciliation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    apply = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "clean_tenant_file_objects"
    )
    calls = {
        node.func.id: node.lineno
        for node in ast.walk(apply)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id
        in {
            "reserve_cleanup_keys",
            "record_cleanup_keys_planned",
            "_recheck_and_delete",
        }
    }
    assert calls["reserve_cleanup_keys"] < calls["record_cleanup_keys_planned"]
    assert calls["record_cleanup_keys_planned"] < calls["_recheck_and_delete"]
