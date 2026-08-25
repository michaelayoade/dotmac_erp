"""Architecture guardrails for the SOT relationship registry.

The registry (`app/services/sot_relationships.py`) is an operational map, not
documentation: every module it names as an owner must import cleanly and be
reachable from application code. First governance suite for this repo's SOT
map — mirrors the dotmac_sub registry-liveness pattern; the undeclared-writer
baseline (sub's second gate) is deliberately deferred until the registry's
coverage grows past this Phase-0 seed, so this file starts with existence,
importability, uniqueness, and reachability only.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from app.services.sot_relationships import (
    DOMAIN_SOT_RELATIONSHIPS,
    all_services,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "app"


def _imported_modules(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover — syntax is checked elsewhere
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _all_app_imports() -> set[str]:
    imports: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        imports |= _imported_modules(path)
    return imports


def test_registry_names_are_unique() -> None:
    names = [service.name for service in all_services()]
    assert len(names) == len(set(names)), "duplicate SOTService names"
    domains = [d.domain for d in DOMAIN_SOT_RELATIONSHIPS]
    assert len(domains) == len(set(domains)), "duplicate domain names"


def test_registry_modules_import() -> None:
    broken: list[str] = []
    for service in all_services():
        try:
            importlib.import_module(service.module)
        except Exception as exc:  # noqa: BLE001 — reported below
            broken.append(f"{service.name}: {service.module} ({exc!r})")
    for domain in DOMAIN_SOT_RELATIONSHIPS:
        for entrypoint in domain.entrypoints:
            try:
                importlib.import_module(entrypoint)
            except Exception as exc:  # noqa: BLE001
                broken.append(f"{domain.domain} entrypoint: {entrypoint} ({exc!r})")
    assert not broken, "registry names unimportable modules:\n  " + "\n  ".join(broken)


def test_registry_dependencies_resolve() -> None:
    known = {service.name for service in all_services()}
    dangling = [
        f"{service.name} -> {dep}"
        for service in all_services()
        for dep in service.depends_on
        if dep not in known
    ]
    assert not dangling, f"depends_on names unknown services: {dangling}"


def test_registry_owners_are_live() -> None:
    """A module only tests import is not a live owner — it must be imported
    by application code (an entrypoint or any other app module)."""
    app_imports = _all_app_imports()
    dead: list[str] = []
    for service in all_services():
        module = service.module
        if any(
            imported == module or imported.startswith(module + ".")
            for imported in app_imports
        ):
            continue
        # A package owner counts as live when any submodule is imported.
        if any(imported.startswith(module) for imported in app_imports):
            continue
        dead.append(f"{service.name}: {module}")
    assert not dead, (
        "registry owners not imported anywhere under app/ — wire a consumer "
        "or strike the entry:\n  " + "\n  ".join(dead)
    )


def test_the_refund_owners_are_registered_and_reachable() -> None:
    """ADR-0008 named two owners for a decision that previously had none.

    The generic tests above would keep passing if the `customer_refund` domain
    were quietly dropped or renamed, because they only check whatever the
    registry happens to contain. This pins what it must contain: before this
    slice, `grep -n "refund\\|reversal\\|credit"` over the registry returned
    nothing at all, and that silence is exactly what the map's expansion rule
    exists to prevent recurring.
    """
    by_name = {service.name: service for service in all_services()}

    assert "refunds.customer_money" in by_name, (
        "the customer refund owner is not registered — a decision with no "
        "registry entry is how refund came to have eleven writers"
    )
    assert (
        by_name["refunds.customer_money"].module
        == "app.services.finance.ar.customer_payment"
    )

    # The GL mechanism is registered as a MECHANISM, and must stay a
    # dependency of the refund owner rather than becoming a second one.
    assert "refunds.gl_mechanism" in by_name
    assert "refunds.gl_mechanism" in by_name["refunds.customer_money"].depends_on

    # Company money out stays where ADR-0005 put it, and the refund owner
    # depends on it rather than writing PaymentIntent.status itself.
    assert "payments.intent_lifecycle" in by_name
    assert "payments.intent_lifecycle" in by_name["refunds.customer_money"].depends_on

    domain = next(d for d in DOMAIN_SOT_RELATIONSHIPS if d.domain == "customer_refund")
    # The adapters that used to decide are named as entry points, which is what
    # keeps them importable and visibly subordinate to the owner.
    assert "app.services.dotmac_sub.sync._payments" in domain.entrypoints
    assert "app.services.finance.payments.webhook_service" in domain.entrypoints
