"""Architecture pin: webhook org attribution derives from the verifying binding.

Audit D2: per-org ``IntegrationConfig(DOTMAC_SUB)`` rows are the single
definition authority for inbound-webhook organization attribution. The
strategic resolver, ``_resolve_org_by_binding``, must be structurally
incapable of default-org attribution: it may never reference the retiring
env-path settings (``default_organization_id``, ``dotmac_sub_webhook_secret``).
Those may appear only in the legacy resolver, the mode composition, and the
zero-authority 503 gate — all of which the retirement PR deletes or shrinks.

AST-based (repo standard, mirrors tests/architecture/test_sot_registry_liveness.py):
the pin inspects the source, so no runtime monkeypatching can satisfy it.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "app" / "api" / "dotmac_sub.py"

# The retiring legacy-authority knobs: referencing either inside the binding
# resolver would reintroduce a second attribution authority.
FORBIDDEN_NAMES = {"default_organization_id", "dotmac_sub_webhook_secret"}


def _function_node(tree: ast.Module, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name == name:
                return node
    raise AssertionError(
        f"{name} not found in {MODULE_PATH} — the binding resolver is the "
        "pinned attribution authority; renaming it must update this pin"
    )


def _referenced_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            names.add(child.value)
    return names


def test_binding_resolver_is_structurally_unable_to_attribute_default_org() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    resolver = _function_node(tree, "_resolve_org_by_binding")
    hits = _referenced_names(resolver) & FORBIDDEN_NAMES
    assert not hits, (
        f"_resolve_org_by_binding references {sorted(hits)}: the strategic "
        "attribution authority must derive the org ONLY from the credential "
        "that verified the signature (per-org IntegrationConfig bindings), "
        "never from the retiring env/default-org settings (audit D2)"
    )


def test_binding_helper_is_also_clean() -> None:
    """The row loader the resolver depends on is held to the same bar."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    helper = _function_node(tree, "_active_binding_rows")
    hits = _referenced_names(helper) & FORBIDDEN_NAMES
    assert not hits, (
        f"_active_binding_rows references {sorted(hits)} — the binding row "
        "query must not consult the retiring env/default-org settings"
    )
