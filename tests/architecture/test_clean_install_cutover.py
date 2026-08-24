"""Enforce ADR-0003's clean-install boundary.

The legacy Accounting extractor is useful evidence, which also makes it the
most tempting place to add a convenience loader later. These checks keep the
historical reader and the governed clean bootstrap as distinct entry-point
families.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app import accounting_adoption

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_EXTRACTOR = REPO_ROOT / "scripts" / "backfill_accounting.py"


def _command_line_options(source: str) -> set[str]:
    tree = ast.parse(source)
    options: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                if argument.value.startswith("--"):
                    options.add(argument.value)
    return options


def _imported_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


def test_clean_install_admission_vocabulary_is_closed() -> None:
    assert {
        "reconciled_master",
        "open_operational_item",
        "approved_accounting_opening",
        "continuity_identity",
    } == accounting_adoption.CLEAN_INSTALL_INPUT_CLASSES


def test_legacy_accounting_history_is_explicitly_forbidden() -> None:
    assert {
        "gl.journal_entry",
        "gl.journal_entry_line",
        "gl.posted_ledger_line",
        "gl.posting_batch",
    } == accounting_adoption.CLEAN_INSTALL_FORBIDDEN_HISTORY_RELATIONS
    assert (
        set(accounting_adoption.RELATION_OWNERSHIP)
        >= accounting_adoption.CLEAN_INSTALL_FORBIDDEN_HISTORY_RELATIONS
    )


def test_legacy_extractor_has_no_load_option_or_module_writer_import() -> None:
    source = LEGACY_EXTRACTOR.read_text(encoding="utf-8")
    assert "--load" not in _command_line_options(source)
    imported = _imported_names(source)
    assert "load_masters" not in imported
    assert not any(
        name == accounting_adoption.IMPORT_PACKAGE
        or name.startswith(f"{accounting_adoption.IMPORT_PACKAGE}.")
        for name in imported
    )


def test_load_option_detector_is_sensitive() -> None:
    source = LEGACY_EXTRACTOR.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parse_args = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_args"
    )
    parse_args.body.insert(
        -1,
        ast.parse('parser.add_argument("--load", action="store_true")').body[0],
    )
    assert "--load" in _command_line_options(ast.unparse(tree))
