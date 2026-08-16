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
  * ``Makefile`` / every workflow under ``.github/workflows/`` invoke the
    binary, as does the ``entry:`` of any ``repo: local`` pre-commit hook.

The failure this file exists to prevent already happened: the declaration was
``^0.15.0`` (a caret — any 0.x >= 0.15 satisfies it) while the lock and the
hook said 0.15.0, and work landed that had been formatted by a *system* ruff
0.13.0 that never consulted any of them. Nothing in the gate noticed, because
`make check` ran no format check at all.

Two independent invariants are asserted here:

  1. **Coherence.** The declaration is an exact version, and the lock and the
     pre-commit ``rev`` name that same version.
  2. **Reachability.** Every ruff invocation goes through ``poetry run``, so it
     is the LOCKED ruff that runs. A bare ``ruff`` picks up whatever is on
     ``PATH`` — which is precisely how a 0.13.0 got in — and would make
     invariant 1 decorative. "Every" means the entry-point FAMILY, not one
     remembered filename: `make check`, every workflow under
     `.github/workflows/`, and the `entry:` of every `repo: local` pre-commit
     hook, which is unpinned by construction.
  3. **Non-disablement.** The gate cannot be turned off by a one-token edit.
     pre-commit's ``SKIP`` env var is an off switch with no reader, so the
     workflow scan refuses a SKIP list naming a formatter hook, and the format
     check is required to be a parsed `run:` step that can actually fail — not
     a substring that a comment, a `|| true`, or a `continue-on-error: true`
     on the step or on its whole job would satisfy. That last switch is not
     hypothetical here: ci.yml's `integration-test` job carries a comment
     recording that it once silenced every PostgreSQL-only check at once.

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
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "poetry.lock"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"
MAKEFILE = ROOT / "Makefile"
WORKFLOW_DIR = ROOT / ".github/workflows"

# Pre-commit hook ids whose whole job is to fail on formatter drift. Skipping
# one is indistinguishable from deleting the gate, so `SKIP:` may not name them.
GUARDED_HOOK_IDS = frozenset({"ruff", "ruff-format"})

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


def workflow_files() -> list[Path]:
    """Every workflow in the family, not one remembered filename."""
    return sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


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


_RUN_KEY = re.compile(r"^run:\s*(.*)$")
_BLOCK_SCALAR = frozenset({"|", ">", "|-", ">-", "|+", ">+"})

# One YAML line, split into its leading indent, any `- ` sequence marker, and
# the mapping key that follows. A sequence item's keys start PAST the marker,
# which is what makes step-level and job-level indentation comparable.
_YAML_LINE = re.compile(r"^([ \t]*)(-[ \t]+)?(.*)$")

# GitHub's own swallow-the-exit-status switch. Valid on a step and on a whole
# job, and it neuters a check exactly as `|| true` does — from outside the
# shell rather than inside it.
_CONTINUE_ON_ERROR = re.compile(r"^continue-on-error:\s*(.+?)\s*$")

# Only a literal false disarms that switch. `continue-on-error: ${{ ... }}` is
# legal and cannot be evaluated from the file, so the guard refuses to assume
# it is false — an expression that might be true is treated as the off switch
# it might be.
_FALSE_LITERALS = frozenset({"false", "'false'", '"false"', "no", "off"})

# A command whose failure cannot fail the step. `|| true`, `|| :` and
# `|| exit 0` all report success whatever ruff said.
_NEUTERED = re.compile(r"\|\|\s*(?:true\b|:|exit\s+0\b)")


class RunStep(NamedTuple):
    """One command a workflow runs, and whether its job swallowed the result."""

    command: str
    continue_on_error: bool


def _yaml_line(raw: str) -> tuple[int, int, str]:
    """``(block column, key column, key text)`` for one YAML line."""
    match = _YAML_LINE.match(raw)
    if match is None:  # pragma: no cover - a trailing `.*` cannot fail
        return 0, 0, raw
    lead, marker, content = match.group(1), match.group(2), match.group(3)
    block = len(lead)
    return block, block + (len(marker) if marker else 0), content


def _is_blank_or_comment(content: str) -> bool:
    stripped = content.strip()
    return not stripped or stripped.startswith("#")


def _mapping_region(lines: list[str], index: int, key_column: int) -> tuple[int, int]:
    """Half-open line range of the mapping whose keys sit at ``key_column``.

    Walked in BOTH directions, because ``continue-on-error:`` is a sibling key
    that may be written before or after the ``run:`` it neuters — and after is
    the commoner order.
    """
    start = index
    while start > 0:
        block, key, content = _yaml_line(lines[start - 1])
        if _is_blank_or_comment(content):
            start -= 1
            continue
        if key == key_column and block < key_column:
            start -= 1  # the `- ` opening this sequence item: the mapping starts
            break
        if block < key_column:
            break
        start -= 1
    end = index + 1
    while end < len(lines):
        block, _key, content = _yaml_line(lines[end])
        if not _is_blank_or_comment(content) and block < key_column:
            break
        end += 1
    return start, end


def _swallowed_lines(lines: list[str]) -> set[int]:
    """Indices of lines inside a mapping carrying ``continue-on-error: true``."""
    swallowed: set[int] = set()
    for index, raw in enumerate(lines):
        _block, key, content = _yaml_line(raw)
        match = _CONTINUE_ON_ERROR.match(content)
        if match is None or match.group(1).lower() in _FALSE_LITERALS:
            continue
        start, end = _mapping_region(lines, index, key)
        swallowed.update(range(start, end))
    return swallowed


# `ruff format` carrying --check, i.e. a verification rather than a rewrite.
_FORMAT_CHECK = re.compile(r"(?<![\w.\-])ruff\s+format\b[^\n]*--check\b")

# `make format-check`, the delegated form of the same question.
_MAKE_FORMAT_CHECK = re.compile(r"(?<![\w.\-])make\b[^\n]*\bformat-check\b")

_SKIP_ASSIGNMENT = re.compile(r"^\s*SKIP:\s*(.+?)\s*$")


def workflow_run_steps(workflow_text: str) -> list[RunStep]:
    """Every shell command a workflow's ``run:`` steps execute, plus the one
    thing a command's own text cannot say: whether the job kept its result.

    A line scan, not a YAML parse, for the reason in the module docstring:
    PyYAML is not a declared dev dependency and a toolchain guard must not be
    what makes a transitive package load-bearing.

    Both `run:` forms are handled — inline (``run: cmd``) and block scalar
    (``run: |`` plus the more-indented body). Comment lines are DROPPED, which
    is the point: a commented-out step executes nothing, and the substring
    check this replaces passed on exactly that.
    """
    lines = workflow_text.splitlines()
    swallowed = _swallowed_lines(lines)
    steps: list[RunStep] = []
    index = 0
    while index < len(lines):
        _block, key, content = _yaml_line(lines[index])
        match = None if _is_blank_or_comment(content) else _RUN_KEY.match(content)
        if match is None:
            index += 1
            continue
        neutered = index in swallowed
        value = match.group(1).strip()
        index += 1
        if value in _BLOCK_SCALAR:
            while index < len(lines):
                body = lines[index]
                stripped = body.strip()
                if stripped and (len(body) - len(body.lstrip())) <= key:
                    break
                if stripped and not stripped.startswith("#"):
                    steps.append(RunStep(stripped, neutered))
                index += 1
        elif value:
            steps.append(RunStep(value, neutered))
    return steps


def workflow_run_commands(workflow_text: str) -> list[str]:
    """Just the command text of every run step. See ``workflow_run_steps``."""
    return [step.command for step in workflow_run_steps(workflow_text)]


def is_enforcing(step: RunStep) -> bool:
    """False when the step reports success whatever the tool decided.

    Two switches, one meaning. `|| true` swallows the exit status inside the
    shell; `continue-on-error: true` swallows it outside, on the step or on
    the whole job. A check behind either one reports and does not gate.
    """
    return not step.continue_on_error and _NEUTERED.search(step.command) is None


def local_hook_entries(pre_commit_text: str) -> list[str]:
    """``entry:`` commands of ``repo: local`` hooks.

    A hosted hook is version-pinned by its ``rev``; a local ``language: system``
    hook is a bare shell command against whatever the runner has on PATH, which
    is the same escape a bare ``ruff`` in a Makefile is. Unknown shapes yield
    nothing, and the sensitivity proof asserts a planted entry IS found, so
    "yields nothing" cannot silently mean "parsed nothing".
    """
    entries: list[str] = []
    current_repo: str | None = None
    for raw in pre_commit_text.splitlines():
        line = raw.strip().lstrip("-").strip()
        if line.startswith("repo:"):
            current_repo = line.split(":", 1)[1].strip().strip("\"'")
        elif line.startswith("entry:") and current_repo == "local":
            entries.append(line.split(":", 1)[1].strip().strip("\"'"))
    return entries


def skipped_pre_commit_hooks(workflow_text: str) -> set[str]:
    """Hook ids a workflow disables through pre-commit's ``SKIP`` env var."""
    skipped: set[str] = set()
    for raw in workflow_text.splitlines():
        match = _SKIP_ASSIGNMENT.match(raw)
        if match is None:
            continue
        value = match.group(1).split("#", 1)[0].strip().strip("\"'")
        skipped.update(part.strip() for part in value.split(",") if part.strip())
    return skipped


def format_roots(makefile_text: str) -> list[str]:
    """The value of ``FORMAT_ROOTS``, however it is assigned."""
    match = re.search(r"^FORMAT_ROOTS\s*[:?]?=\s*(.+)$", makefile_text, re.MULTILINE)
    return match.group(1).split("#", 1)[0].split() if match else []


# Directories that hold .py files nobody formats: vendored, generated, or not
# ours. Everything else that contains a .py is a root CI format-checks.
_NOT_OUR_SOURCE = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "build",
        "dist",
        ".seabone",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


def python_roots(root: Path) -> set[str]:
    """The first path component of every .py file in the tree."""
    return {
        path.relative_to(root).parts[0]
        for path in root.rglob("*.py")
        if not (set(path.relative_to(root).parts) & _NOT_OUR_SOURCE)
    }


def uncovered_python_roots(roots: list[str], present: set[str]) -> list[str]:
    """Python roots ``ruff format`` would never be pointed at.

    ``.`` is the whole tree and therefore covers everything; any other value
    covers exactly the roots it names.
    """
    covered = {root.rstrip("/") for root in roots}
    if "." in covered:
        return []
    return sorted(present - covered)


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


def test_the_workflow_family_is_not_empty() -> None:
    """A glob that matches nothing passes every check below for free."""
    assert workflow_files(), f"no workflows found under {WORKFLOW_DIR}"


def test_no_workflow_ruff_escapes_the_lock() -> None:
    """Every workflow, not one remembered filename.

    The guard used to name .github/workflows/ci.yml. Four other workflows were
    never read, so a bare `ruff format` added to any of them was invisible.
    """
    offenders: dict[str, list[str]] = {}
    for path in workflow_files():
        commands = "\n".join(workflow_run_commands(path.read_text()))
        found = bare_ruff_invocations(commands)
        if found:
            offenders[path.name] = found
    assert offenders == {}, f"a workflow invokes ruff without `poetry run`: {offenders}"


def test_no_pre_commit_local_hook_escapes_the_lock() -> None:
    """A `language: system` hook's `entry:` is a bare PATH command.

    The hosted ruff hook is pinned by its `rev`. A local hook is not pinned by
    anything, so `entry: ruff format` there would reintroduce exactly the
    system-ruff-0.13.0 escape the rev pin exists to close.
    """
    offenders = bare_ruff_invocations(
        "\n".join(local_hook_entries(PRE_COMMIT.read_text()))
    )
    assert offenders == [], (
        f"a `repo: local` pre-commit hook invokes ruff off the lock: {offenders}"
    )


def test_no_workflow_skips_a_formatter_hook() -> None:
    """`SKIP:` is an off switch with no reader. Give it one.

    ci.yml already sets `SKIP: semgrep`. Appending `,ruff-format` is a
    one-token edit that disables the only formatter check covering tests/ and
    scripts/, and nothing anywhere would have noticed.
    """
    skipped = {
        path.name: sorted(skipped_pre_commit_hooks(path.read_text()) & GUARDED_HOOK_IDS)
        for path in workflow_files()
    }
    skipped = {name: hooks for name, hooks in skipped.items() if hooks}
    assert skipped == {}, (
        "a workflow's pre-commit SKIP list disables a formatter hook, which is "
        f"indistinguishable from deleting the gate: {skipped}"
    )


def test_a_workflow_actually_enforces_the_format_check() -> None:
    """Parsed, not substring-matched, and required to be able to fail.

    The predecessor was `assert "ruff format --check" in ci`, which passes on a
    commented-out step, on a mention inside a `name:`, and on a step suffixed
    with `|| true`. This reads the `run:` steps and requires at least one that
    asks the question AND can fail the job.
    """
    enforcing: dict[str, list[str]] = {}
    for path in workflow_files():
        for step in workflow_run_steps(path.read_text()):
            command = step.command
            asks = _FORMAT_CHECK.search(command) or _MAKE_FORMAT_CHECK.search(command)
            if asks and is_enforcing(step):
                enforcing.setdefault(path.name, []).append(command)
    assert enforcing, (
        "no workflow runs a format check that can fail; without one a "
        "differently-versioned formatter's output reaches main unnoticed"
    )


def test_format_roots_cover_every_python_root() -> None:
    """`make check` must not ask a narrower question than CI's pre-commit job.

    The ruff-format hook declares no `files:` and no `exclude:`, and
    pre-commit/action runs `--all-files`, so CI format-checks every tracked
    .py. FORMAT_ROOTS read `app/`, so `make check` passed on trees CI rejects —
    which is how eight blocks under tests/ reached a push.

    The comparison runs whatever FORMAT_ROOTS says. An earlier version of this
    test returned early on the `.` that is live today, so neither the walk nor
    the coverage check ever executed: it was a check that passed by not
    running. `.` covering everything is now a fact `uncovered_python_roots`
    states and the sensitivity proof exercises, not a reason to skip.
    """
    roots = format_roots(MAKEFILE.read_text())
    assert roots, "Makefile defines no FORMAT_ROOTS"
    present = python_roots(ROOT)
    assert present, f"no .py files found under {ROOT}, so this proved nothing"
    missing = uncovered_python_roots(roots, present)
    assert not missing, (
        "CI's pre-commit job format-checks these roots and FORMAT_ROOTS does "
        f"not, so `make check` is weaker than CI: {missing}"
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
        failures = coherence_failures(perturbed, _COHERENT_LOCK, _COHERENT_PRE_COMMIT)
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


_WORKFLOW_WITH_A_COMMENTED_OUT_CHECK = """
jobs:
  lint:
    steps:
      # - name: Ruff format check
      #   run: poetry run ruff format --check app/
      - name: Tests
        run: pytest -q
"""

_WORKFLOW_WITH_A_NEUTERED_CHECK = """
jobs:
  lint:
    steps:
      - name: Ruff format check
        run: poetry run ruff format --check app/ || true
"""

_WORKFLOW_WITH_A_BLOCK_SCALAR_CHECK = """
jobs:
  lint:
    steps:
      - name: Ruff format check
        run: |
          poetry run ruff format --check app/
          poetry run ruff check app/
"""

_WORKFLOW_WITH_A_CONTINUE_ON_ERROR_STEP = """
jobs:
  lint:
    steps:
      - name: Ruff format check
        run: poetry run ruff format --check app/
        continue-on-error: true
      - name: Tests
        run: pytest -q
"""

_WORKFLOW_WITH_A_CONTINUE_ON_ERROR_JOB = """
jobs:
  lint:
    continue-on-error: true
    steps:
      - name: Ruff format check
        run: poetry run ruff format --check app/
  test:
    steps:
      - name: Tests
        run: pytest -q
"""


def test_sensitivity_a_commented_out_step_runs_nothing() -> None:
    assert workflow_run_commands(_WORKFLOW_WITH_A_COMMENTED_OUT_CHECK) == ["pytest -q"]


def test_sensitivity_a_neutered_step_is_not_an_enforcing_one() -> None:
    steps = workflow_run_steps(_WORKFLOW_WITH_A_NEUTERED_CHECK)
    assert [step.command for step in steps] == [
        "poetry run ruff format --check app/ || true"
    ]
    assert _FORMAT_CHECK.search(steps[0].command) is not None
    assert is_enforcing(steps[0]) is False


def test_sensitivity_a_block_scalar_body_is_read() -> None:
    assert workflow_run_commands(_WORKFLOW_WITH_A_BLOCK_SCALAR_CHECK) == [
        "poetry run ruff format --check app/",
        "poetry run ruff check app/",
    ]


def test_sensitivity_a_continue_on_error_step_is_not_an_enforcing_one() -> None:
    """Written AFTER the `run:` it neuters, which is the commoner order."""
    steps = workflow_run_steps(_WORKFLOW_WITH_A_CONTINUE_ON_ERROR_STEP)
    assert [step.command for step in steps] == [
        "poetry run ruff format --check app/",
        "pytest -q",
    ]
    assert _FORMAT_CHECK.search(steps[0].command) is not None
    assert is_enforcing(steps[0]) is False
    assert is_enforcing(steps[1]) is True, "the sibling step must stay enforcing"


def test_sensitivity_a_continue_on_error_job_neuters_every_step_under_it() -> None:
    """Job level is the switch ci.yml's comment records; it must reach steps."""
    steps = workflow_run_steps(_WORKFLOW_WITH_A_CONTINUE_ON_ERROR_JOB)
    assert [(step.command, is_enforcing(step)) for step in steps] == [
        ("poetry run ruff format --check app/", False),
        ("pytest -q", True),
    ]


def test_sensitivity_continue_on_error_false_leaves_the_step_enforcing() -> None:
    disarmed = _WORKFLOW_WITH_A_CONTINUE_ON_ERROR_STEP.replace(
        "continue-on-error: true", "continue-on-error: false"
    )
    assert is_enforcing(workflow_run_steps(disarmed)[0]) is True


def test_sensitivity_an_unreadable_continue_on_error_counts_as_on() -> None:
    """`${{ ... }}` cannot be evaluated from the file, so it may well be true."""
    expression = _WORKFLOW_WITH_A_CONTINUE_ON_ERROR_STEP.replace(
        "continue-on-error: true",
        "continue-on-error: ${{ github.event_name == 'schedule' }}",
    )
    assert is_enforcing(workflow_run_steps(expression)[0]) is False


def test_sensitivity_an_enforcing_check_is_recognised_in_both_forms() -> None:
    assert is_enforcing(RunStep("poetry run ruff format --check app/", False)) is True
    assert _MAKE_FORMAT_CHECK.search("make format-check") is not None
    assert _MAKE_FORMAT_CHECK.search("make check") is None


def test_sensitivity_a_local_hook_entry_is_found_and_a_hosted_one_is_not() -> None:
    planted = """
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.0
    hooks:
      - id: ruff-format
  - repo: local
    hooks:
      - id: rogue
        language: system
        entry: ruff format --check app/
"""
    assert local_hook_entries(planted) == ["ruff format --check app/"]
    assert bare_ruff_invocations("\n".join(local_hook_entries(planted))) == [
        "ruff format --check app/"
    ]


def test_sensitivity_a_skip_list_naming_a_formatter_hook_is_reported() -> None:
    assert skipped_pre_commit_hooks("        SKIP: semgrep") == {"semgrep"}
    assert skipped_pre_commit_hooks("        SKIP: semgrep,ruff-format") == {
        "semgrep",
        "ruff-format",
    }
    assert skipped_pre_commit_hooks("        SKIP: semgrep  # comment") == {"semgrep"}
    planted = skipped_pre_commit_hooks("        SKIP: semgrep,ruff-format")
    assert planted & GUARDED_HOOK_IDS == {"ruff-format"}


def test_sensitivity_narrow_format_roots_are_reported() -> None:
    assert format_roots("FORMAT_ROOTS ?= app/") == ["app/"]
    assert format_roots("FORMAT_ROOTS ?= .") == ["."]
    assert format_roots("FORMAT_ROOTS ?= app/ tests/  # roots") == ["app/", "tests/"]
    assert format_roots("nothing here") == []


def test_sensitivity_a_root_no_format_root_names_is_reported() -> None:
    """The comparison the real test used to skip, driven against a narrow set."""
    present = {"app", "tests", "scripts"}
    assert uncovered_python_roots(["app/"], present) == ["scripts", "tests"]
    assert uncovered_python_roots(["app/", "tests/"], present) == ["scripts"]
    assert uncovered_python_roots(["app/", "tests/", "scripts/"], present) == []
    assert uncovered_python_roots(["."], present) == []


def test_sensitivity_the_python_root_walk_finds_this_very_file() -> None:
    """A walk that matched nothing would make the coverage check vacuous."""
    assert "tests" in python_roots(ROOT)
