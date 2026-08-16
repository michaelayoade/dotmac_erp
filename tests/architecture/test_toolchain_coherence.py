"""The formatter is a fixed point of ONE ruff version, so all four pins agree.

`ruff format` is not a stable function of the source: two ruff versions
disagree about the same file, and each rewrites the other's output. That makes
the committed formatting a fixed point of the exact version that produced it,
and it makes a floating declaration dangerous in a way a floating linter is
not — a linter that drifts adds findings, a formatter that drifts rewrites
files nobody edited and buries the real diff.

Four places name ruff and every one of them can drift on its own:

  * ``pyproject.toml`` declares the dev dependency,
  * ``poetry.lock`` records what actually resolved,
  * ``.pre-commit-config.yaml`` pins the hook that rewrites files on commit,
  * ``Makefile`` / ``.github/workflows/ci.yml`` invoke the binary.

The failure this file exists to prevent already happened: the declaration was
``^0.15.0`` (a caret — any 0.x >= 0.15 satisfies it) while the lock and the
hook said 0.15.0, and work landed that had been formatted by a *system* ruff
0.13.0 that never consulted any of them. Nothing in the gate noticed, because
`make check` ran no format check at all.

Two independent invariants are asserted here:

  1. **Coherence.** The declaration is an exact version, and the lock and the
     pre-commit ``rev`` name that same version.
  2. **Reachability.** Every ruff invocation in `make check` and in the CI lint
     job goes through ``poetry run``, so it is the LOCKED ruff that runs. A
     bare ``ruff`` picks up whatever is on ``PATH`` — which is precisely how a
     0.13.0 got in — and would make invariant 1 decorative.

Everything below is a pure function over supplied text, with the real-file
tests as thin callers. That is what lets the sensitivity proof at the bottom
feed each checker a deliberately broken input and assert it bites; a coherence
check that has only ever seen a coherent repo has not been shown to detect
anything.

PyYAML is deliberately not used to read ``.pre-commit-config.yaml``: it is not
a declared dev dependency of this repo (it is present only transitively, via
pre-commit and bandit), and a toolchain guard should not be the thing that
makes a transitive package load-bearing. A narrow two-key line scan is enough
for ``repo:``/``rev:`` and cannot fail open on a schema it does not
understand — an unrecognised file yields no rev, which is itself a failure.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "poetry.lock"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"
MAKEFILE = ROOT / "Makefile"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"

RUFF_PRE_COMMIT_REPO = "astral-sh/ruff-pre-commit"

# An exact pin: three numeric components and nothing else. Anything carrying a
# caret, tilde, inequality, wildcard or comma is a RANGE, and a range is what
# lets the formatter float.
_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")

# A ruff invocation in command position, i.e. `ruff check` / `ruff format`.
# The lookbehind keeps `.ruff_cache` and `ruff-format` (a pre-commit hook id,
# not a command) from matching.
_RUFF_COMMAND = re.compile(r"(?<![\w.\-])ruff\s+(?:check|format)\b")


# ─── pure checkers ────────────────────────────────────────────────────────


def declared_ruff_pin(pyproject_text: str) -> str | None:
    """The ruff version constraint declared in the dev dependency group."""
    data = tomllib.loads(pyproject_text)
    groups = data.get("tool", {}).get("poetry", {}).get("group", {})
    dep = groups.get("dev", {}).get("dependencies", {}).get("ruff")
    if isinstance(dep, dict):
        dep = dep.get("version")
    return dep if isinstance(dep, str) else None


def locked_ruff_version(lock_text: str) -> str | None:
    """The ruff version poetry actually resolved. poetry.lock is TOML."""
    data = tomllib.loads(lock_text)
    for package in data.get("package", []):
        if package.get("name") == "ruff":
            version = package.get("version")
            return version if isinstance(version, str) else None
    return None


def pre_commit_ruff_rev(pre_commit_text: str) -> str | None:
    """The ``rev`` of the ruff-pre-commit hook repo, with any ``v`` stripped.

    A two-key scan rather than a YAML parse: track the most recent ``repo:``
    and return the first ``rev:`` that follows the ruff one. Unknown shapes
    return None, which callers treat as a failure rather than a pass.
    """
    current_repo: str | None = None
    for raw in pre_commit_text.splitlines():
        line = raw.strip().lstrip("-").strip()
        if line.startswith("repo:"):
            current_repo = line.split(":", 1)[1].strip().strip("\"'")
        elif line.startswith("rev:") and current_repo is not None:
            if current_repo.rstrip("/").endswith(RUFF_PRE_COMMIT_REPO):
                rev = line.split(":", 1)[1].strip().strip("\"'")
                return rev[1:] if rev.startswith("v") else rev
    return None


def coherence_failures(
    pyproject_text: str,
    lock_text: str,
    pre_commit_text: str,
) -> list[str]:
    """Every way the three declared pins can disagree. Empty means coherent."""
    failures: list[str] = []

    pin = declared_ruff_pin(pyproject_text)
    locked = locked_ruff_version(lock_text)
    rev = pre_commit_ruff_rev(pre_commit_text)

    if pin is None:
        failures.append("pyproject.toml declares no ruff dev dependency")
    elif not _EXACT_VERSION.fullmatch(pin):
        failures.append(
            f"pyproject.toml pins ruff as {pin!r}, which is a range, not an "
            "exact version; a range lets the formatter float underneath the "
            "committed formatting"
        )

    if locked is None:
        failures.append("poetry.lock records no ruff package")
    if rev is None:
        failures.append(
            f"no rev found for the {RUFF_PRE_COMMIT_REPO} hook in "
            ".pre-commit-config.yaml"
        )

    if pin is not None and locked is not None and pin != locked:
        failures.append(
            f"pyproject.toml pins ruff {pin} but poetry.lock resolved {locked}"
        )
    if pin is not None and rev is not None and pin != rev:
        failures.append(
            f"pyproject.toml pins ruff {pin} but .pre-commit-config.yaml "
            f"pins rev v{rev}"
        )

    return failures


def bare_ruff_invocations(text: str) -> list[str]:
    """Lines that run ruff without ``poetry run``, i.e. off the lock."""
    offenders: list[str] = []
    for raw in text.splitlines():
        for match in _RUFF_COMMAND.finditer(raw):
            prefix = raw[: match.start()]
            if not re.search(r"poetry\s+run\s+$", prefix):
                offenders.append(raw.strip())
                break
    return offenders


def make_target_prerequisites(makefile_text: str, target: str) -> list[str]:
    """The prerequisite list of a Make target, ignoring any ``##`` comment."""
    pattern = re.compile(rf"^{re.escape(target)}:([^\n]*)$", re.MULTILINE)
    match = pattern.search(makefile_text)
    if match is None:
        return []
    body = match.group(1).split("##", 1)[0]
    return body.split()


# ─── the real repository ──────────────────────────────────────────────────


def test_the_four_ruff_pins_agree() -> None:
    failures = coherence_failures(
        PYPROJECT.read_text(),
        LOCK.read_text(),
        PRE_COMMIT.read_text(),
    )
    assert failures == [], "ruff pins disagree:\n  " + "\n  ".join(failures)


def test_make_check_runs_the_format_check() -> None:
    """A format gate that `check` does not depend on is not a gate."""
    makefile = MAKEFILE.read_text()
    prerequisites = make_target_prerequisites(makefile, "check")
    assert "format-check" in prerequisites, (
        "`make check` must depend on format-check, or formatter drift stays "
        f"invisible locally until CI review; got {prerequisites}"
    )
    assert "format-check:" in makefile, "no format-check target defined"


def test_no_makefile_ruff_escapes_the_lock() -> None:
    offenders = bare_ruff_invocations(MAKEFILE.read_text())
    assert offenders == [], (
        "Makefile invokes ruff without `poetry run`, so it would run whatever "
        f"is on PATH instead of the locked version: {offenders}"
    )


def test_no_ci_ruff_escapes_the_lock() -> None:
    offenders = bare_ruff_invocations(CI_WORKFLOW.read_text())
    assert offenders == [], (
        f"the CI workflow invokes ruff without `poetry run`: {offenders}"
    )


def test_ci_lint_job_checks_formatting() -> None:
    """CI and `make check` must ask the same question of the same roots."""
    ci = CI_WORKFLOW.read_text()
    assert "ruff format --check" in ci, (
        "the CI lint job must run `ruff format --check`; without it a "
        "differently-versioned formatter's output reaches main unnoticed"
    )


# ─── sensitivity proof ────────────────────────────────────────────────────
#
# Each checker is fed a deliberately broken input. Without these, the tests
# above would still pass on a repo where the checkers were quietly gutted.

_COHERENT_PYPROJECT = """
[tool.poetry.group.dev.dependencies]
ruff = "0.15.0"
"""
_COHERENT_LOCK = """
[[package]]
name = "pytest"
version = "8.2.2"

[[package]]
name = "ruff"
version = "0.15.0"
"""
_COHERENT_PRE_COMMIT = """
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.0
    hooks:
      - id: ruff
      - id: ruff-format
"""


def test_sensitivity_the_coherent_fixture_passes() -> None:
    """The baseline the negative cases are perturbed from must itself pass."""
    assert (
        coherence_failures(
            _COHERENT_PYPROJECT,
            _COHERENT_LOCK,
            _COHERENT_PRE_COMMIT,
        )
        == []
    )


def test_sensitivity_a_caret_range_is_reported() -> None:
    perturbed = _COHERENT_PYPROJECT.replace('"0.15.0"', '"^0.15.0"')
    failures = coherence_failures(perturbed, _COHERENT_LOCK, _COHERENT_PRE_COMMIT)
    assert any("range" in f for f in failures), failures


def test_sensitivity_other_ranges_are_reported() -> None:
    for spec in ("~0.15.0", ">=0.15.0", ">=0.15,<0.16", "0.15.*", "*"):
        perturbed = _COHERENT_PYPROJECT.replace('"0.15.0"', f'"{spec}"')
        failures = coherence_failures(
            perturbed, _COHERENT_LOCK, _COHERENT_PRE_COMMIT
        )
        assert any("range" in f for f in failures), (spec, failures)


def test_sensitivity_a_lock_that_resolved_something_else_is_reported() -> None:
    perturbed = _COHERENT_LOCK.replace('version = "0.15.0"', 'version = "0.13.0"')
    failures = coherence_failures(_COHERENT_PYPROJECT, perturbed, _COHERENT_PRE_COMMIT)
    assert any("poetry.lock resolved 0.13.0" in f for f in failures), failures


def test_sensitivity_a_stale_pre_commit_rev_is_reported() -> None:
    perturbed = _COHERENT_PRE_COMMIT.replace("v0.15.0", "v0.13.0")
    failures = coherence_failures(_COHERENT_PYPROJECT, _COHERENT_LOCK, perturbed)
    assert any("v0.13.0" in f for f in failures), failures


def test_sensitivity_a_missing_ruff_hook_is_reported() -> None:
    perturbed = _COHERENT_PRE_COMMIT.replace("ruff-pre-commit", "some-other-hook")
    failures = coherence_failures(_COHERENT_PYPROJECT, _COHERENT_LOCK, perturbed)
    assert any("no rev found" in f for f in failures), failures


def test_sensitivity_the_rev_is_read_from_the_ruff_repo_only() -> None:
    """A neighbouring repo's rev must not be mistaken for ruff's."""
    two_repos = """
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: check-yaml
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.0
    hooks:
      - id: ruff
"""
    assert pre_commit_ruff_rev(two_repos) == "0.15.0"


def test_sensitivity_a_bare_ruff_invocation_is_reported() -> None:
    assert bare_ruff_invocations("\truff check app/") == ["ruff check app/"]
    assert bare_ruff_invocations("\truff format --check app/") == [
        "ruff format --check app/"
    ]
    assert bare_ruff_invocations("        run: ruff check app/") == [
        "run: ruff check app/"
    ]


def test_sensitivity_poetry_run_and_non_commands_are_not_reported() -> None:
    benign = "\n".join(
        [
            "\tpoetry run ruff check app/",
            "\tpoetry run ruff format --check app/",
            "        run: poetry run ruff check app/",
            "\tfind . -type d -name .ruff_cache -exec rm -rf {} +",
            "      - id: ruff-format",
            "[tool.ruff.lint]",
        ]
    )
    assert bare_ruff_invocations(benign) == []


def test_sensitivity_a_check_target_without_format_check_is_visible() -> None:
    without = "check: lint type-check security semgrep ## all checks"
    assert "format-check" not in make_target_prerequisites(without, "check")
    with_it = "check: lint format-check type-check ## all checks"
    assert make_target_prerequisites(with_it, "check") == [
        "lint",
        "format-check",
        "type-check",
    ]
