from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETTINGS_CALLERS = (
    "app/services/finance/banking/mono_sync.py",
    "app/services/fixed_assets/depreciation.py",
    "app/web/help.py",
    "app/web_home.py",
)
MONO_CONSTRUCTORS = (
    "app/api/finance/banking.py",
    "app/tasks/finance.py",
)


def _calls(path: str, function_name: str) -> list[ast.Call]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == function_name
    ]


def test_observed_settings_reads_state_their_scope() -> None:
    for path in SETTINGS_CALLERS:
        calls = _calls(path, "resolve_value")
        assert calls, f"expected settings reads in {path}"
        assert all(
            any(keyword.arg == "organization_id" for keyword in call.keywords)
            for call in calls
        ), f"every settings read in {path} must state its scope"


def test_production_mono_services_receive_an_explicit_scope() -> None:
    for path in MONO_CONSTRUCTORS:
        calls = _calls(path, "MonoSyncService")
        assert calls, f"expected MonoSyncService construction in {path}"
        assert all(
            len(call.args) >= 2
            or any(keyword.arg == "organization_id" for keyword in call.keywords)
            for call in calls
        ), f"every MonoSyncService in {path} must receive its settings scope"
