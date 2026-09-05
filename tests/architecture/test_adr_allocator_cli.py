"""The allocator's ENTRY POINT is run, not read.

``test_adr_allocator.py`` proves the pure functions.  Reading source proves
intention; it does not prove that ``python tools/adr/allocate.py`` does the
thing when a person types it.  This module runs the real entry point as a
subprocess, in a throwaway git repository, against the five situations that
decide whether the register is an allocator or a decoration:

* **absent register** — refuses, permanently, and offers no way to proceed;
* **valid single allocation** — takes ``next_free``, appends one row, raises
  the scalar by one, and leaves the doctrine intact;
* **duplicate claim** — the same slug twice is refused the second time;
* **stale base** — a ``next_free`` pointing at a row that already exists is
  refused rather than overwriting it;
* **competing allocation** — two branches allocating from one base produce a
  real ``git merge-file`` CONFLICT.  That conflict is the entire mechanism.  A
  test that only checks both sides picked the same number would pass even if
  git merged them silently, which is exactly how ADR-0008 came to name two
  decisions.

No network, no database, no service.  ``git init`` in a temporary directory is
the only external command, plus ``git merge-file``, which needs no repository.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

GIT = shutil.which("git") or "git"
REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOCATOR = REPO_ROOT / "tools" / "adr" / "allocate.py"

BASE = """# doctrine that must survive every rewrite
next_free = 3

[[reservation]]
number = 1
slug = "alpha"
status = "authored"
claimed = "2026-01-01"
landed_at = "2026-01-01"

[[reservation]]
number = 2
slug = "beta"
status = "authored"
claimed = "2026-01-02"
landed_at = "2026-01-02"

# ---------------------------------------------------------------------------
# collisions
# ---------------------------------------------------------------------------

[[collision]]
number = 1
where = "somewhere"
"""


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """The real entry point, the way a person invokes it."""
    return subprocess.run(  # noqa: S603
        [sys.executable, str(root / "tools" / "adr" / "allocate.py"), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [
            GIT,
            "-c",
            "user.email=adr@example.invalid",
            "-c",
            "user.name=adr",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repository laid out like this one, with a clean worktree."""
    (tmp_path / "tools" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    shutil.copy(ALLOCATOR, tmp_path / "tools" / "adr" / "allocate.py")
    _git(tmp_path, "init", "--quiet")
    (tmp_path / "docs" / "adr" / "reservations.toml").write_text(BASE, encoding="utf-8")
    _git(tmp_path, "add", "tools/adr/allocate.py", "docs/adr/reservations.toml")
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    return tmp_path


def _register(root: Path) -> dict:
    return tomllib.loads(
        (root / "docs" / "adr" / "reservations.toml").read_text(encoding="utf-8")
    )


# --- 1. absent register ----------------------------------------------------


def test_the_entry_point_refuses_an_absent_register(tmp_path: Path) -> None:
    """Fails before this module existed: nothing ran the entry point at all,
    so the refusal was asserted by reading the source. A permission denial on
    a local dry run is UNKNOWN, not success."""
    (tmp_path / "tools" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    shutil.copy(ALLOCATOR, tmp_path / "tools" / "adr" / "allocate.py")

    result = _run(tmp_path, "--slug", "gamma")

    assert result.returncode == 2, result
    assert "will not create it" in result.stderr
    assert "genesis" in result.stderr
    assert not (tmp_path / "docs" / "adr" / "reservations.toml").exists(), (
        "the refusal wrote a register anyway"
    )


def test_the_absent_register_refusal_survives_check_mode(tmp_path: Path) -> None:
    """`--check` writes nothing, so it is the mode most tempting to make
    lenient. It must refuse identically."""
    (tmp_path / "tools" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    shutil.copy(ALLOCATOR, tmp_path / "tools" / "adr" / "allocate.py")

    result = _run(tmp_path, "--slug", "gamma", "--check")

    assert result.returncode == 2, result
    assert "will not create it" in result.stderr


# --- 2. valid single allocation --------------------------------------------


def test_a_valid_allocation_claims_exactly_one_number(repo: Path) -> None:
    result = _run(repo, "--slug", "gamma", "--claimed", "2026-01-03")

    assert result.returncode == 0, result.stderr
    assert "claimed ADR-0003" in result.stdout

    register = _register(repo)
    assert register["next_free"] == 4
    rows = {row["number"]: row for row in register["reservation"]}
    assert set(rows) == {1, 2, 3}
    assert rows[3] == {
        "number": 3,
        "slug": "gamma",
        "status": "reserved",
        "claimed": "2026-01-03",
    }


def test_a_valid_allocation_keeps_the_doctrine_and_the_ordering(repo: Path) -> None:
    _run(repo, "--slug", "gamma", "--claimed", "2026-01-03")
    text = (repo / "docs" / "adr" / "reservations.toml").read_text(encoding="utf-8")
    assert "# doctrine that must survive every rewrite" in text
    assert text.index("gamma") < text.index("[[collision]]")


def test_check_mode_reports_the_number_and_writes_nothing(repo: Path) -> None:
    before = (repo / "docs" / "adr" / "reservations.toml").read_text(encoding="utf-8")
    result = _run(repo, "--slug", "gamma", "--check")
    assert result.returncode == 0, result.stderr
    assert "would claim ADR-0003" in result.stdout
    after = (repo / "docs" / "adr" / "reservations.toml").read_text(encoding="utf-8")
    assert after == before


def test_the_allocation_refuses_to_travel_with_another_change(repo: Path) -> None:
    """Step 3 of the protocol: a claim that ships with its ADR is a claim on a
    branch, and a claim on a branch is not a claim."""
    (repo / "docs" / "adr" / "0003-gamma.md").write_text(
        "# ADR-0003\n", encoding="utf-8"
    )
    _git(repo, "add", "docs/adr/0003-gamma.md")

    result = _run(repo, "--slug", "gamma")

    assert result.returncode == 2, result
    assert "committed alone" in result.stderr


# --- 3. duplicate claim ----------------------------------------------------


def test_the_same_slug_twice_is_refused_the_second_time(repo: Path) -> None:
    first = _run(repo, "--slug", "gamma", "--claimed", "2026-01-03")
    assert first.returncode == 0, first.stderr
    _git(repo, "add", "docs/adr/reservations.toml")
    _git(repo, "commit", "--quiet", "-m", "claim gamma")

    second = _run(repo, "--slug", "gamma", "--claimed", "2026-01-04")

    assert second.returncode == 2, second
    assert "already holds a number" in second.stderr
    assert _register(repo)["next_free"] == 4, "the refusal advanced next_free"


def test_a_different_slug_after_a_claim_is_not_refused(repo: Path) -> None:
    """Near miss: the duplicate guard must read the slug, not the fact that a
    claim already happened."""
    _run(repo, "--slug", "gamma", "--claimed", "2026-01-03")
    _git(repo, "add", "docs/adr/reservations.toml")
    _git(repo, "commit", "--quiet", "-m", "claim gamma")

    result = _run(repo, "--slug", "delta", "--claimed", "2026-01-04")

    assert result.returncode == 0, result.stderr
    assert "claimed ADR-0004" in result.stdout


# --- 4. stale base ---------------------------------------------------------


def test_a_stale_next_free_is_refused_rather_than_overwriting(repo: Path) -> None:
    """A branch that raised `next_free` and lost the row, or rebased badly,
    must not be allowed to hand the number out twice."""
    register = repo / "docs" / "adr" / "reservations.toml"
    register.write_text(
        BASE.replace("next_free = 3", "next_free = 2"), encoding="utf-8"
    )
    _git(repo, "add", "docs/adr/reservations.toml")
    _git(repo, "commit", "--quiet", "-m", "stale base")

    result = _run(repo, "--slug", "gamma")

    assert result.returncode == 2, result
    assert "already has a row" in result.stderr
    rows = [row["slug"] for row in _register(repo)["reservation"]]
    assert rows == ["alpha", "beta"], "the stale base was written over"


def test_an_existing_document_for_the_claimed_number_is_refused(repo: Path) -> None:
    (repo / "docs" / "adr" / "0003-gamma.md").write_text(
        "# ADR-0003\n", encoding="utf-8"
    )
    _git(repo, "add", "docs/adr/0003-gamma.md")
    _git(repo, "commit", "--quiet", "-m", "document first")

    result = _run(repo, "--slug", "gamma")

    assert result.returncode == 2, result
    assert "same change that authors it" in result.stderr


# --- 5. competing allocation ----------------------------------------------


def test_two_branches_allocating_from_one_base_conflict_in_git(
    repo: Path, tmp_path: Path
) -> None:
    """THE property. Two lanes that cannot see each other both take 3 and both
    rewrite the same single line, so a three-way merge FAILS. The conflict is
    the lock being contended; a clean merge here would be two decisions sharing
    one number, which is what ADR-0003, 0004, 0006 and 0008 already are."""
    base = tmp_path / "base.toml"
    base.write_text(BASE, encoding="utf-8")

    ours = tmp_path / "ours.toml"
    theirs = tmp_path / "theirs.toml"

    _run(repo, "--slug", "gamma", "--claimed", "2026-01-03")
    ours.write_text(
        (repo / "docs" / "adr" / "reservations.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    (repo / "docs" / "adr" / "reservations.toml").write_text(BASE, encoding="utf-8")
    _run(repo, "--slug", "delta", "--claimed", "2026-01-03")
    theirs.write_text(
        (repo / "docs" / "adr" / "reservations.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert tomllib.loads(ours.read_text())["next_free"] == 4
    assert tomllib.loads(theirs.read_text())["next_free"] == 4

    merge = subprocess.run(  # noqa: S603
        [GIT, "merge-file", "-p", str(ours), str(base), str(theirs)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert merge.returncode != 0, (
        "git merged two competing allocations without complaint. `next_free` "
        "has stopped being the lock, and two decisions now share one number."
    )
    assert "<<<<<<<" in merge.stdout


def test_one_allocation_against_its_own_base_merges_cleanly(
    repo: Path, tmp_path: Path
) -> None:
    """Near miss for the conflict above. If EVERY merge conflicted, the test
    would pass while proving nothing about contention. One lane allocating and
    the other lane untouched must merge clean."""
    base = tmp_path / "base.toml"
    base.write_text(BASE, encoding="utf-8")

    _run(repo, "--slug", "gamma", "--claimed", "2026-01-03")
    ours = tmp_path / "ours.toml"
    ours.write_text(
        (repo / "docs" / "adr" / "reservations.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    theirs = tmp_path / "theirs.toml"
    theirs.write_text(BASE, encoding="utf-8")

    merge = subprocess.run(  # noqa: S603
        [GIT, "merge-file", "-p", str(ours), str(base), str(theirs)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert merge.returncode == 0, merge.stdout
    assert "<<<<<<<" not in merge.stdout


# --- the real register, through the real entry point -----------------------


def test_the_entry_point_reads_this_repositorys_register(tmp_path: Path) -> None:
    """Non-vacuity: every case above runs against a synthetic register. This
    one runs `--check` against the real file, in a copy so nothing is written,
    and requires it to yield a number rather than a refusal."""
    root = tmp_path / "copy"
    (root / "tools" / "adr").mkdir(parents=True)
    (root / "docs" / "adr").mkdir(parents=True)
    shutil.copy(ALLOCATOR, root / "tools" / "adr" / "allocate.py")
    shutil.copy(
        REPO_ROOT / "docs" / "adr" / "reservations.toml",
        root / "docs" / "adr" / "reservations.toml",
    )

    result = _run(root, "--slug", "a-slug-that-is-not-claimed-anywhere", "--check")

    assert result.returncode == 0, result.stderr
    assert "would claim ADR-" in result.stdout


# ---------------------------------------------------------------------------
# The allocation-only gate.  `allocate.py` refuses to RUN beside another
# change; the register is a text file and can be hand-edited, so this is the
# gate that catches the edit rather than the tool.
# ---------------------------------------------------------------------------

_gate_spec = importlib.util.spec_from_file_location(
    "_adr_allocation_only", REPO_ROOT / "tools" / "adr" / "allocation_only.py"
)
assert _gate_spec is not None and _gate_spec.loader is not None
_gate = importlib.util.module_from_spec(_gate_spec)
_gate_spec.loader.exec_module(_gate)
is_genesis, reserved_numbers, verdict = (
    _gate.is_genesis,
    _gate.reserved_numbers,
    _gate.verdict,
)

REGISTER_PATH = "docs/adr/reservations.toml"

WITH_CLAIM = (
    BASE.replace("next_free = 3", "next_free = 4")
    + """
[[reservation]]
number = 3
slug = "gamma"
status = "reserved"
claimed = "2026-01-03"
"""
)

WITH_NOTE = BASE.replace('slug = "alpha"', 'slug = "alpha"\nnote = "corrected"')


def test_a_claim_travelling_with_another_file_is_refused() -> None:
    """Fails before this gate existed: nothing read a pull request's file set,
    so a hand-edited register could hand out a number in the same change that
    authored the ADR — which is what ADR-0003, 0004, 0006 and 0008 are."""
    problems = verdict({REGISTER_PATH, "docs/adr/0003-gamma.md"}, BASE, WITH_CLAIM)
    assert any("claims ADR-0003" in p for p in problems), problems
    assert any("0003-gamma.md" in p for p in problems), problems


def test_a_claim_travelling_alone_is_not_refused() -> None:
    """Near miss: the gate must read the OTHER files, not the fact of a claim."""
    assert verdict({REGISTER_PATH}, BASE, WITH_CLAIM) == []


def test_a_register_edit_that_claims_nothing_is_not_refused() -> None:
    """Near miss: correcting a note or recording a collision hands out no
    number. Gating every register edit would block the genesis migration with
    the rule the genesis migration introduces."""
    assert verdict({REGISTER_PATH, "AGENTS.md"}, BASE, WITH_NOTE) == []


def test_a_pull_request_that_never_touches_the_register_is_not_refused() -> None:
    assert verdict({"app/main.py"}, BASE, BASE) == []


def test_genesis_is_recognised_and_is_not_held_to_the_one_file_rule() -> None:
    """The register arrives with rows already `reserved`, alongside the
    allocator and its tests. That is the migration, not an allocation."""
    assert is_genesis("")
    assert verdict({REGISTER_PATH, "tools/adr/allocate.py"}, "", WITH_CLAIM) == []


def test_a_deleted_register_is_not_mistaken_for_genesis() -> None:
    """The near miss that matters most. Genesis and 'someone deleted the
    register' both leave one side empty; only one of them is a migration, and
    the other must not buy a free pass through this gate."""
    problems = verdict({REGISTER_PATH}, "", "")
    assert any("absent on both sides" in p for p in problems), problems


def test_reserved_numbers_reads_only_reserved_rows() -> None:
    """`authored`, `withdrawn` and `contested` rows are not fresh claims."""
    assert reserved_numbers(BASE) == set()
    assert reserved_numbers(WITH_CLAIM) == {3}
    assert reserved_numbers("") == set()
