"""Shared machinery for the /api/v1 OpenAPI contract-surface pin.

The full OpenAPI document spans 2,800+ paths because every API router is
mounted twice: once at a bare legacy alias (``/customers/...``,
``/crm/webhook``, ...) and once under the canonical ``/api/v1`` prefix (see
``_include_api_router`` in ``app/main.py``). The pinned *contract surface*
is the canonical ``/api/v1`` prefix only — the bare legacy duplicates are
deliberately not pinned: they mirror the same routers route-for-route and
would double every diff without adding contract information. ``/api/v2`` is
also in scope: it carries no routes today, but it is the documented home for
the next breaking API generation, so any route mounted there enters the
manifest automatically.

Per route the manifest records method, params, and request/response schema
fingerprints, plus a fingerprint per transitively referenced component
schema. A fingerprint change names the exact route/schema that moved, and
the committed manifest diffs at route granularity in review.

``app/main.py`` mounts several routers conditionally behind module flags
(``ENABLED_MODULES``: unset -> all modules) and dev-mode licensing
(``DOTMAC_DEV_MODE=true`` explicitly -> every module licensed). The manifest
therefore pins the surface UNDER THE TEST ENVIRONMENT'S FLAG DEFAULTS —
every module enabled. One known test-env exclusion: ``app/api/people``
skips mounting its sub-routers when ``PYTEST_CURRENT_TEST`` is set (pytest
always sets it), so those ~97 operations are not part of the pinned
surface. ``scripts/update_openapi_contract.py`` sets the same env and
prints the route count on regeneration, so a flag-driven shrink or growth
of the surface is immediately visible.

Consumers:
- ``tests/architecture/test_openapi_contract_surface.py`` — fails on drift.
  Like every other test that imports ``app.main``, it relies on
  ``tests/conftest.py``'s import-time env and ``app.db``/``app.rls`` test
  doubles; no lifespan is run and no network is touched.
- ``scripts/update_openapi_contract.py`` — regenerates the manifest after an
  intentional contract change (it imports ``tests/conftest.py`` first so the
  surface is computed under exactly the same import environment).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).with_name("openapi_contract_surface.json")
API_PREFIXES = ("/api/v1", "/api/v2")
_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


def build_full_app():
    """Return the fully mounted application from ``app.main``.

    erp builds a single app at import time (no router-spec registry), so the
    module-level singleton *is* the full surface under the current flag
    defaults. Importing it never runs the lifespan.
    """
    from app.main import app

    return app


def _fingerprint(node: Any) -> str:
    return hashlib.sha256(
        json.dumps(node, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]


def _collect_refs(node: Any, acc: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            acc.add(ref.rsplit("/", 1)[1])
        for value in node.values():
            _collect_refs(value, acc)
    elif isinstance(node, list):
        for value in node:
            _collect_refs(value, acc)


def compute_surface(app) -> dict[str, Any]:
    doc = app.openapi()
    schemas = doc.get("components", {}).get("schemas", {})

    routes: dict[str, Any] = {}
    referenced: set[str] = set()
    for path, item in doc.get("paths", {}).items():
        if not path.startswith(API_PREFIXES):
            continue
        for method in _METHODS:
            op = item.get(method)
            if op is None:
                continue
            _collect_refs(op, referenced)
            params = sorted(
                f"{p.get('in')}:{p.get('name')}{'!' if p.get('required') else ''}"
                for p in op.get("parameters", [])
            )
            request = op.get("requestBody")
            responses = {
                code: _fingerprint(body)
                for code, body in sorted(op.get("responses", {}).items())
            }
            routes[f"{method.upper()} {path}"] = {
                "params": params,
                "request": _fingerprint(request) if request is not None else None,
                "responses": responses,
            }

    # Transitive closure: a referenced schema's own $refs are part of the
    # contract too (a nested field change must move some fingerprint).
    frontier = set(referenced)
    while frontier:
        name = frontier.pop()
        node = schemas.get(name)
        if node is None:
            continue
        nested: set[str] = set()
        _collect_refs(node, nested)
        frontier |= nested - referenced
        referenced |= nested

    return {
        "api_prefixes": list(API_PREFIXES),
        "routes": routes,
        "schemas": {
            name: _fingerprint(schemas[name])
            for name in sorted(referenced)
            if name in schemas
        },
    }


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def write_manifest(surface: dict[str, Any]) -> None:
    MANIFEST_PATH.write_text(
        json.dumps(surface, sort_keys=True, indent=1) + "\n", encoding="utf-8"
    )


def diff_surfaces(pinned: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Human-actionable drift lines, bounded by the caller."""
    lines: list[str] = []
    for kind in ("routes", "schemas"):
        old, new = pinned.get(kind, {}), current.get(kind, {})
        for key in sorted(set(old) - set(new)):
            lines.append(f"removed {kind[:-1]}: {key}")
        for key in sorted(set(new) - set(old)):
            lines.append(f"added {kind[:-1]}: {key}")
        for key in sorted(set(old) & set(new)):
            if old[key] != new[key]:
                lines.append(f"changed {kind[:-1]}: {key}")
    return lines
