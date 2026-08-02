"""Architecture guard: the dotmac-kernel import boundary under ``app/``.

Enforces the kernel-adoption ledger (``docs/PLATFORM_ADOPTION_LEDGER.md``,
"Kernel public-module classification"): only the documented *consume-pure*
kernel modules may ever be imported by application code. Everything else —
kernel persistence (``db``, ``models``, ``messaging`` storage, ``audit``,
``entitlements``, ``settings_models``, ``migrations``), kernel identity/auth
(``models``, ``security``, ``deps``, ``web_deps``, ``platform_auth``,
``identity``), kernel web/app surface (``app_factory``, ``middleware``,
``crud``, ``templating``, ``branding``, ``display``), bare ``import
dotmac_kernel``, and any ``_``-private kernel path — fails this test.

The scan is a static AST walk: ``dotmac_kernel`` is NOT installed at the E1
pin, and this test must never import it. When a later slice adopts an
``adapt-existing`` or ``defer-db`` module, it extends ``ALLOWED_KERNEL_MODULES``
in the same change that updates the ledger's classification table (the
ledger-sync test below keeps the two from drifting).

Red-sensitivity is proven, not assumed: the negative-control tests run the
same checker over a synthetic tree containing forbidden imports and assert
the violations are reported.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "app"
LEDGER = PROJECT_ROOT / "docs" / "PLATFORM_ADOPTION_LEDGER.md"

KERNEL_PACKAGE = "dotmac_kernel"

# The documented consume-pure surface (ledger classification table). Every
# entry must appear in the ledger as ``dotmac_kernel.<module>`` classified
# consume-pure; the sync test below enforces that.
ALLOWED_KERNEL_MODULES: frozenset[str] = frozenset(
    {
        "assembly",
        "capabilities",
        "features",
        "licensing",
        "money",
        "profiles",
        "providers",
        "testing",
    }
)


def _classify(module: str) -> str | None:
    """Return a violation reason for a kernel import, or None if allowed.

    ``module`` is a dotted import target such as ``dotmac_kernel.db`` or
    ``dotmac_kernel.money.Money`` (ImportFrom names are appended by the
    caller so ``from dotmac_kernel import X`` is judged as
    ``dotmac_kernel.X``).
    """
    parts = module.split(".")
    if parts[0] != KERNEL_PACKAGE:
        return None
    if len(parts) == 1:
        return (
            "bare 'import dotmac_kernel' is not allowed — import a "
            "documented consume-pure submodule explicitly"
        )
    if any(part.startswith("_") for part in parts[1:]):
        return f"kernel-internal (private) import '{module}'"
    if parts[1] not in ALLOWED_KERNEL_MODULES:
        return (
            f"'{module}' is outside the documented consume-pure allowlist "
            f"(see docs/PLATFORM_ADOPTION_LEDGER.md)"
        )
    return None


def _imported_kernel_targets(tree: ast.AST) -> list[tuple[int, str]]:
    """Yield (lineno, dotted-target) for every dotmac_kernel import."""
    targets: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == KERNEL_PACKAGE:
                    targets.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] != KERNEL_PACKAGE:
                continue
            if node.module == KERNEL_PACKAGE:
                # ``from dotmac_kernel import X`` — judge each name as a
                # submodule/attribute of the top-level package.
                targets.extend(
                    (node.lineno, f"{KERNEL_PACKAGE}.{alias.name}")
                    for alias in node.names
                )
            else:
                targets.append((node.lineno, node.module))
    return targets


def kernel_import_violations(root: Path) -> list[str]:
    """Scan every .py file under ``root`` for disallowed kernel imports."""
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — syntax is checked elsewhere
            continue
        rel = path.relative_to(root)
        for lineno, target in _imported_kernel_targets(tree):
            reason = _classify(target)
            if reason is not None:
                violations.append(f"{rel}:{lineno}: {reason}")
    return violations


# ---------------------------------------------------------------------------
# The real guard
# ---------------------------------------------------------------------------


def test_app_imports_only_documented_kernel_modules() -> None:
    """No file under app/ imports outside the consume-pure kernel surface.

    At the E1 pin this holds trivially (the dependency is not installed and
    nothing imports it); the guard exists so that E2+ cannot add an
    undocumented, internal, or prohibited kernel import without failing CI.
    """
    violations = kernel_import_violations(APP_DIR)
    assert violations == [], "undocumented dotmac_kernel imports under app/:\n" + (
        "\n".join(violations)
    )


def test_allowlist_matches_ledger_classification() -> None:
    """Every allowlisted module is documented consume-pure in the ledger.

    Keeps this test and docs/PLATFORM_ADOPTION_LEDGER.md from drifting: a
    module cannot join the allowlist without the ledger documenting it, and
    the ledger's consume-pure rows are exactly the enforced surface.
    """
    text = LEDGER.read_text(encoding="utf-8")
    missing = sorted(
        module
        for module in ALLOWED_KERNEL_MODULES
        if f"`dotmac_kernel.{module}`" not in text
    )
    assert missing == [], (
        "allowlisted kernel modules missing from the ledger classification "
        f"table: {missing}"
    )
    # And the ledger must still classify each one consume-pure somewhere in
    # its classification table (coarse but drift-catching).
    assert "consume-pure" in text


# ---------------------------------------------------------------------------
# Negative controls: prove the checker is red-sensitive
# ---------------------------------------------------------------------------


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def test_guard_flags_prohibited_and_internal_kernel_imports(tmp_path: Path) -> None:
    """A synthetic tree with forbidden imports must produce violations."""
    _write_tree(
        tmp_path,
        {
            "svc/bad_db.py": "from dotmac_kernel.db import get_db\n",
            "svc/bad_models.py": "import dotmac_kernel.messaging.models\n",
            "svc/bad_toplevel.py": "import dotmac_kernel\n",
            "svc/bad_name.py": "from dotmac_kernel import Party\n",
            "svc/bad_private.py": (
                "from dotmac_kernel.testing._internal import fake\n"
            ),
        },
    )
    violations = kernel_import_violations(tmp_path)
    flagged = "\n".join(violations)
    assert len(violations) == 5, flagged
    assert "bad_db.py:1" in flagged and "dotmac_kernel.db" in flagged
    assert "bad_models.py:1" in flagged and "messaging.models" in flagged
    assert "bad_toplevel.py:1" in flagged and "bare 'import dotmac_kernel'" in flagged
    assert "bad_name.py:1" in flagged and "dotmac_kernel.Party" in flagged
    assert "bad_private.py:1" in flagged and "kernel-internal" in flagged


def test_guard_accepts_documented_consume_pure_imports(tmp_path: Path) -> None:
    """The same checker passes a tree using only the documented surface —
    proving the negative controls fail for the right reason (the import,
    not the mere presence of the kernel package name)."""
    _write_tree(
        tmp_path,
        {
            "svc/good_money.py": "from dotmac_kernel.money import Money\n",
            "svc/good_caps.py": "import dotmac_kernel.capabilities\n",
            "svc/good_profiles.py": "from dotmac_kernel import profiles\n",
            "svc/good_testing.py": (
                "from dotmac_kernel.testing.fakes import FakeClock\n"
            ),
            "svc/unrelated.py": "import app\n",
        },
    )
    assert kernel_import_violations(tmp_path) == []
