"""Freeze callers of ERP's retiring endpoint-response idempotency ledger.

ADR-0001 names ``dotmac_kernel.idempotency`` as ERP's sole future durable
at-most-once owner.  ``platform.idempotency_record`` may coexist while its
operations are migrated, but it may gain no new callers.  This is a
two-directional ratchet: growth is new debt, and shrinkage must lower the
checked-in inventory in the same reviewed change.

The detector matches exact Python identifiers.  In particular,
``PostingIdempotencyService`` is a natural-key journal lookup that ADR-0001
explicitly retains; a substring scan would incorrectly classify it as the
legacy response ledger.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"
BASELINE = Path(__file__).with_name("idempotency_legacy_callers.txt")
LEGACY_IDENTIFIERS = frozenset({"IdempotencyRecord", "IdempotencyService"})


def _references_legacy_owner(source: str) -> bool:
    tree = ast.parse(source)
    return any(
        (isinstance(node, ast.Name) and node.id in LEGACY_IDENTIFIERS)
        or (isinstance(node, ast.Attribute) and node.attr in LEGACY_IDENTIFIERS)
        or (isinstance(node, ast.ClassDef) and node.name in LEGACY_IDENTIFIERS)
        or (
            isinstance(node, ast.alias)
            and (
                node.name.rsplit(".", 1)[-1] in LEGACY_IDENTIFIERS
                or node.asname in LEGACY_IDENTIFIERS
            )
        )
        for node in ast.walk(tree)
    )


def _observed_callers(root: Path = APP_ROOT) -> set[str]:
    return {
        path.relative_to(REPO_ROOT).as_posix()
        for path in root.rglob("*.py")
        if _references_legacy_owner(path.read_text(encoding="utf-8"))
    }


def _recorded_callers() -> set[str]:
    lines = [
        line.strip()
        for line in BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lines == sorted(set(lines)), (
        "legacy-caller baseline must be sorted and unique"
    )
    return set(lines)


def test_legacy_idempotency_callers_are_a_two_directional_ratchet() -> None:
    observed = _observed_callers()
    recorded = _recorded_callers()
    assert observed == recorded, (
        "ERP's retiring IdempotencyService/IdempotencyRecord caller set moved: "
        f"new={sorted(observed - recorded)}, retired={sorted(recorded - observed)}. "
        "New callers are forbidden. When a caller is migrated, remove its row "
        "from idempotency_legacy_callers.txt in the same change."
    )


def test_legacy_idempotency_detector_is_red_sensitive_and_exact() -> None:
    assert _references_legacy_owner(
        "from legacy import IdempotencyService\nIdempotencyService.reserve()\n"
    )
    assert _references_legacy_owner("legacy.IdempotencyRecord\n")
    assert not _references_legacy_owner("class PostingIdempotencyService:\n    pass\n")
