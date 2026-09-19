"""Architecture guard: the invoice-accounting-sync pipeline never reaches GL/AR posting.

Mirrors ``tests/architecture/test_kernel_import_boundary.py``'s exact shape: a
static AST walk (never a text grep) over the five files that make up ERP's
two consumption paths for Sub's invoice-accounting-sync feed — the shadow
task's direct-pull path and the Integrator-delivered receiver path — proving
neither is even STRUCTURALLY capable of importing the general-ledger or
accounts-receivable posting machinery. This complements (does not replace)
the existing runtime behavior: both
``app/services/dotmac_sub/invoice_sync_outcomes.py`` and
``app/services/dotmac_sub/invoice_sync_shadow.py`` already document that they
observe and record decisions only, with no dependency on ERP's invoice
posting service.

Verified against this repository's actual module layout (2026-09-18): all
five forbidden targets below exist at the stated paths, and none of the five
scanned files currently imports any of them — this test is a permanent
guard against a FUTURE regression, not a report of a violation found today.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCANNED_FILES: tuple[Path, ...] = (
    PROJECT_ROOT / "app/services/dotmac_sub/invoice_sync_shadow.py",
    PROJECT_ROOT / "app/services/dotmac_sub/invoice_sync_outcomes.py",
    PROJECT_ROOT / "app/services/dotmac_sub/integrator_observations.py",
    PROJECT_ROOT / "app/api/integrator_observations.py",
    PROJECT_ROOT / "app/services/dotmac_sub/client.py",
)

# Confirmed real module paths in this repo (see module docstring above).
FORBIDDEN_MODULE_PREFIXES: tuple[str, ...] = (
    "app.services.finance.gl",
    "app.services.finance.ar.posting",
)
FORBIDDEN_EXACT_MODULES: frozenset[str] = frozenset(
    {
        "app.services.finance.ar.ar_posting_adapter",
        "app.services.finance.ar.ar_posting_saga",
        "app.services.finance.ar.invoice",
    }
)


def _is_forbidden(target: str) -> bool:
    if target in FORBIDDEN_EXACT_MODULES:
        return True
    return any(
        target == prefix or target.startswith(prefix + ".")
        for prefix in FORBIDDEN_MODULE_PREFIXES
    )


def _imported_targets(tree: ast.AST) -> list[tuple[int, str]]:
    """Yield (lineno, dotted-target) for every absolute import in ``tree``.

    ``ImportFrom`` targets are reconstructed from ``node.module`` PLUS each
    alias name — ``from app.services.finance.ar import invoice`` must be
    judged as ``app.services.finance.ar.invoice``, not merely as the
    (allowed-looking) package ``app.services.finance.ar``.
    """
    targets: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                # Reconstruct the FULL dotted target from `node.module` plus
                # this alias's name — `from app.services.finance.ar import
                # invoice` must be judged as `app.services.finance.ar.invoice`
                # (an exact forbidden target), not merely as the (allowed)
                # package `app.services.finance.ar`. `_is_forbidden` also
                # prefix-matches, so `from app.services.finance.gl import
                # poster` (target `...gl.poster`) is still caught under the
                # `app.services.finance.gl` prefix rule.
                targets.append((node.lineno, f"{node.module}.{alias.name}"))
    return targets


def gl_boundary_violations(paths: tuple[Path, ...]) -> list[str]:
    """Scan every path in ``paths`` for a forbidden GL/AR-posting import."""
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.name
        seen: set[tuple[int, str]] = set()
        for lineno, target in _imported_targets(tree):
            if not _is_forbidden(target):
                continue
            key = (lineno, target)
            if key in seen:
                continue
            seen.add(key)
            violations.append(f"{rel}:{lineno}: forbidden import '{target}'")
    return violations


def _write_tree(root: Path, files: dict[str, str]) -> tuple[Path, ...]:
    paths = []
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        paths.append(target)
    return tuple(paths)


# ---------------------------------------------------------------------------
# The real guard
# ---------------------------------------------------------------------------


def test_invoice_sync_pipeline_never_imports_gl_or_ar_posting() -> None:
    for path in SCANNED_FILES:
        assert path.is_file(), f"expected scanned file to exist: {path}"
    violations = gl_boundary_violations(SCANNED_FILES)
    assert violations == [], (
        "the invoice-accounting-sync observation pipeline must never import "
        "GL/AR posting code:\n" + "\n".join(violations)
    )


def test_forbidden_module_paths_are_real() -> None:
    """The forbidden targets name real modules in this repo, not stale ones.

    A guard against modules that no longer exist would pass for the wrong
    reason (nothing could ever import a module that isn't there).
    """
    gl_pkg = PROJECT_ROOT / "app/services/finance/gl"
    ar_posting_pkg = PROJECT_ROOT / "app/services/finance/ar/posting"
    assert gl_pkg.is_dir()
    assert ar_posting_pkg.is_dir()
    for exact in FORBIDDEN_EXACT_MODULES:
        rel = Path(*exact.split(".")).with_suffix(".py")
        assert (PROJECT_ROOT / rel).is_file(), f"missing {rel}"


# ---------------------------------------------------------------------------
# Negative controls: prove the checker is red-sensitive.
# ---------------------------------------------------------------------------


def test_guard_flags_a_planted_forbidden_import(tmp_path: Path) -> None:
    """A synthetic tree with forbidden imports must produce violations —
    proving the checker actually catches a planted regression, not merely
    that no import happens to trip it today."""
    paths = _write_tree(
        tmp_path,
        {
            "bad_gl_direct.py": "import app.services.finance.gl.poster\n",
            "bad_gl_submodule.py": (
                "from app.services.finance.gl.poster import post_entry\n"
            ),
            "bad_ar_posting.py": (
                "from app.services.finance.ar.posting import adapter\n"
            ),
            "bad_ar_posting_adapter.py": (
                "from app.services.finance.ar import ar_posting_adapter\n"
            ),
            "bad_ar_posting_saga.py": "import app.services.finance.ar.ar_posting_saga\n",
            "bad_ar_invoice.py": "from app.services.finance.ar import invoice\n",
        },
    )
    violations = gl_boundary_violations(paths)
    flagged = "\n".join(violations)
    assert len(violations) == 6, flagged
    assert "bad_gl_direct.py:1" in flagged
    assert "bad_gl_submodule.py:1" in flagged
    assert "bad_ar_posting.py:1" in flagged
    assert "bad_ar_posting_adapter.py:1" in flagged
    assert "bad_ar_posting_saga.py:1" in flagged
    assert "bad_ar_invoice.py:1" in flagged


def test_guard_accepts_allowed_imports_only(tmp_path: Path) -> None:
    """The same checker passes a tree using only allowed imports — proving
    the negative control above fails for the right reason (the specific
    forbidden import, not merely the presence of 'app.services.finance' in
    the tree)."""
    paths = _write_tree(
        tmp_path,
        {
            "good_ar_other.py": (
                "from app.services.finance.ar.dotmac_sub_invoice_sync_outcome "
                "import DotmacSubInvoiceSyncOutcome\n"
            ),
            "good_unrelated.py": "import app.services.dotmac_sub.client\n",
            "good_stdlib.py": "import hashlib\nfrom uuid import UUID\n",
        },
    )
    assert gl_boundary_violations(paths) == []
