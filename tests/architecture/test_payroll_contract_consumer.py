"""Contract-only payroll adoption cannot grant access to its stateful owner."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app import bill_of_materials as bom

ROOT = Path(__file__).resolve().parents[2]


def runtime_sources(root: Path) -> list[Path]:
    """Discover existing runtime entrypoints, excluding tests and documentation."""
    paths = set(root.glob("*.py"))
    for family in (
        "app",
        "scripts",
        "tasks",
        "workers",
        "jobs",
        "cli",
        "cron",
        "alembic",
        "migrations",
        "bin",
        "src",
        "packages",
    ):
        directory = root / family
        if directory.is_dir():
            paths.update(directory.rglob("*.py"))
    return sorted(paths)


def violations(source: str, path: str) -> list[str]:
    consumer = bom.CALCULATION_CONTRACT_CONSUMERS[0]
    tree = ast.parse(source)
    findings = []
    aliases = {"__import__": "builtins.__import__"}

    def qualified(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{qualified(node.value)}.{node.attr}"
        return ""

    # Resolve import aliases before inspecting calls. Also resolve ordinary
    # assignment aliases, e.g. load = importlib.import_module.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            resolved = qualified(node.value)
            if resolved in {"importlib.import_module", "builtins.__import__"}:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = resolved

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.split(".")[0] == "dotmac_payroll" for alias in node.names
            ):
                findings.append("module import bypasses public symbol allowlist")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] != "dotmac_payroll":
                continue
            if (
                path not in consumer.consumer_paths
                or module != "dotmac_payroll"
                or any(
                    alias.name not in consumer.public_imports for alias in node.names
                )
            ):
                findings.append("payroll import outside pure public contract")
        elif isinstance(node, ast.Call) and qualified(node.func) in {
            "importlib.import_module",
            "builtins.__import__",
        }:
            name = (
                node.args[0]
                if node.args
                else next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "name"
                    ),
                    None,
                )
            )
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                if name.value.split(".")[0] == "dotmac_payroll":
                    findings.append("dynamic payroll import")
    return findings


def scan(root: Path) -> dict[str, list[str]]:
    return {
        str(path.relative_to(root)): errors
        for path in runtime_sources(root)
        if (errors := violations(path.read_text(), str(path.relative_to(root))))
    }


def test_product_imports_only_the_declared_pure_payroll_contract() -> None:
    assert not scan(ROOT), scan(ROOT)


@pytest.mark.parametrize(
    "source",
    [
        "from dotmac_payroll.models import PayrollRun",
        "from dotmac_payroll.service import PayrollService",
        "from dotmac_payroll import PayrollService",
        "from dotmac_payroll import *",
        "import dotmac_payroll",
        "import dotmac_payroll.models as models",
        "__import__('dotmac_payroll.models')",
        "importlib.import_module('dotmac_payroll.service')",
        "from importlib import import_module as load; load('dotmac_payroll.service')",
        "import importlib as loader; loader.import_module('dotmac_payroll.models')",
        "import builtins; builtins.__import__('dotmac_payroll.service')",
        "import builtins as b; b.__import__('dotmac_payroll.models')",
        "from builtins import __import__ as load; load('dotmac_payroll.models')",
        "import importlib; load = importlib.import_module; load(name='dotmac_payroll.service')",
    ],
)
def test_guard_refuses_stateful_and_allowlist_bypass_imports(source: str) -> None:
    assert violations(source, "app/services/people/payroll/run_totals.py")


def test_guard_restricts_even_public_imports_to_named_consumers() -> None:
    assert violations(
        "from dotmac_payroll import PayrollAmountSnapshot",
        "app/unreviewed.py",
    )
    assert not violations(
        "from dotmac_payroll import PayrollAmountSnapshot",
        "app/services/people/payroll/run_totals.py",
    )


@pytest.mark.parametrize(
    "family",
    [
        "",
        "app",
        "scripts",
        "tasks",
        "workers",
        "jobs",
        "cli",
        "cron",
        "alembic",
        "migrations",
        "bin",
        "src",
        "packages",
    ],
)
def test_each_runtime_entrypoint_family_is_scanned(tmp_path, family: str) -> None:
    directory = tmp_path / family
    directory.mkdir(parents=True, exist_ok=True)
    planted = directory / "entrypoint.py"
    planted.write_text("from dotmac_payroll.service import PayrollService\n")
    assert str(planted.relative_to(tmp_path)) in scan(tmp_path)


def test_guard_does_not_scan_documentation_strings(tmp_path) -> None:
    (tmp_path / "entrypoint.py").write_text('"import dotmac_payroll.service"\n')
    assert not scan(tmp_path)
