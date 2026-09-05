"""The allocator claims one number, alone, and refuses an absent register.

``tests/architecture/test_adr_number_allocation.py`` is the CHECKER: it reads a
worktree and reports whether the register and the directory agree.  This module
holds the WRITER — ``tools/adr/allocate.py`` — to the three properties that make
it safe to point at the register.

The one that needs saying out loud is the refusal.  A tool that creates a
missing register on first run is indistinguishable, from inside a branch, from
a tool that re-allocates 0001 because the branch lost the file.  Genesis is a
one-time migration performed against ``main``'s history with each row resolved
to the pull request that landed it; it is not something a helper reproduces by
accident.  So the absent-register path is a permanent, unconditional refusal,
and ``test_a_planted_absent_register_is_refused`` is the proof that it bites
while ``test_a_present_register_is_not_refused_for_absence`` is the proof that
it bites for the right reason.

The module is loaded by path.  ``tools/`` carries no ``__init__.py``, nothing
under ``app/`` imports it, and ``test_the_allocator_is_not_reachable_from_the_
application`` keeps that true.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOCATOR = REPO_ROOT / "tools" / "adr" / "allocate.py"
REGISTER = REPO_ROOT / "docs" / "adr" / "reservations.toml"


def _load():
    spec = importlib.util.spec_from_file_location("_adr_allocate", ALLOCATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


allocate = _load()


MINIMAL = """# doctrine that must survive a rewrite
next_free = 3

[[reservation]]
number = 1
slug = "first-decision"
status = "authored"
claimed = "2026-08-15"

[[reservation]]
number = 2
slug = "second-decision"
status = "withdrawn"
claimed = "2026-08-16"
reason = "superseded"

# ---------------------------------------------------------------------------
# collisions
# ---------------------------------------------------------------------------

[[collision]]
number = 1
claimants = ["first-decision", "a-rival"]
where = "some/branch"
"""


# --- the absent-register refusal, and its near miss -------------------------


def test_a_planted_absent_register_is_refused(tmp_path: Path) -> None:
    """Fails before the change: `read_register` did not exist, so nothing
    refused an absent register and `plan` would have been handed whatever the
    caller invented."""
    missing = tmp_path / "reservations.toml"
    with pytest.raises(allocate.Refusal) as caught:
        allocate.read_register(missing)
    assert "will not create it" in str(caught.value)
    assert "genesis" in str(caught.value)


def test_the_absent_register_refusal_names_no_recovery_path(tmp_path: Path) -> None:
    """A refusal that suggests `--init` is an invitation. Establish by reading:
    the allocator's argument parser must offer no creation flag."""
    source = ALLOCATOR.read_text(encoding="utf-8")
    for invented in ("--init", "--create", "--bootstrap", "--genesis"):
        assert f'"{invented}"' not in source, f"{invented} is a first-run fallback"


def test_a_present_register_is_not_refused_for_absence(tmp_path: Path) -> None:
    """The near miss: a register that exists but is stale, empty of rows, or
    inconsistent must NOT be caught by the absent-register guard. If this ever
    starts raising, the guard has widened past the defect it names."""
    present = tmp_path / "reservations.toml"
    present.write_text("next_free = 1\n", encoding="utf-8")
    assert allocate.read_register(present) == "next_free = 1\n"


# --- claiming exactly one number -------------------------------------------


def test_the_claim_takes_next_free_and_raises_it_by_one() -> None:
    """Fails before the change: there was no writer, so `next_free` was raised
    by hand and nothing checked that the row and the scalar moved together."""
    result = allocate.plan(MINIMAL, "a-third-decision", "2026-09-05", set())
    assert result.number == 3
    parsed = tomllib.loads(result.text)
    assert parsed["next_free"] == 4
    rows = {int(r["number"]): r for r in parsed["reservation"]}
    assert rows[3]["slug"] == "a-third-decision"
    assert rows[3]["status"] == "reserved"
    assert rows[3]["claimed"] == "2026-09-05"


def test_the_rewrite_keeps_the_registers_prose() -> None:
    """The register is mostly doctrine. A TOML round-trip would delete it."""
    result = allocate.plan(MINIMAL, "a-third-decision", "2026-09-05", set())
    assert "# doctrine that must survive a rewrite" in result.text
    assert "# collisions" in result.text


def test_the_new_row_precedes_the_collision_blocks() -> None:
    """A row appended at end-of-file parses but reads as a collision."""
    result = allocate.plan(MINIMAL, "a-third-decision", "2026-09-05", set())
    assert result.text.index("a-third-decision") < result.text.index("[[collision]]")


def test_next_free_stays_on_a_line_of_its_own() -> None:
    """That single scalar on a single line IS the lock: two allocators both
    rewrite it, so git raises a conflict instead of merging two claims."""
    result = allocate.plan(MINIMAL, "a-third-decision", "2026-09-05", set())
    assert re.search(r"^next_free = 4$", result.text, re.MULTILINE)


# --- refusals that keep the protocol honest --------------------------------


def test_a_planted_same_change_authoring_is_refused() -> None:
    """Fails before the change: nothing stopped a branch writing the ADR and
    claiming its number together, which is what every colliding record did."""
    with pytest.raises(allocate.Refusal, match="same change that authors it"):
        allocate.plan(
            MINIMAL, "a-third-decision", "2026-09-05", {"0003-a-third-decision.md"}
        )


def test_an_unrelated_adr_file_is_not_mistaken_for_the_claim() -> None:
    """Near miss: files for OTHER numbers must not trip the guard above."""
    result = allocate.plan(
        MINIMAL,
        "a-third-decision",
        "2026-09-05",
        {"0001-first-decision.md", "0030-much-later.md"},
    )
    assert result.number == 3


def test_a_planted_reused_slug_is_refused() -> None:
    with pytest.raises(allocate.Refusal, match="already holds a number"):
        allocate.plan(MINIMAL, "first-decision", "2026-09-05", set())


def test_a_planted_withdrawn_slug_is_still_refused() -> None:
    """Permanence: a withdrawn decision keeps its number and does not get a
    second one under the same slug."""
    with pytest.raises(allocate.Refusal, match="already holds a number"):
        allocate.plan(MINIMAL, "second-decision", "2026-09-05", set())


def test_a_planted_next_free_pointing_at_a_taken_row_is_refused() -> None:
    stale = MINIMAL.replace("next_free = 3", "next_free = 2")
    with pytest.raises(allocate.Refusal, match="already has a row"):
        allocate.plan(stale, "a-third-decision", "2026-09-05", set())


def test_a_planted_inlined_next_free_is_refused() -> None:
    """If `next_free` stops being one line, the conflict stops being raised."""
    inlined = MINIMAL.replace(
        "next_free = 3", "next_free = 3 # inline comment moves nothing"
    )
    with pytest.raises(allocate.Refusal, match="line of its own"):
        allocate.plan(inlined, "a-third-decision", "2026-09-05", set())


@pytest.mark.parametrize(
    "bad", ["Not-A-Slug", "trailing-", "-leading", "double--hyphen", "has space", ""]
)
def test_a_planted_malformed_slug_is_refused(bad: str) -> None:
    with pytest.raises(allocate.Refusal, match="is not a slug"):
        allocate.plan(MINIMAL, bad, "2026-09-05", set())


def test_a_register_with_no_next_free_is_refused() -> None:
    with pytest.raises(allocate.Refusal, match="not an allocator"):
        allocate.plan("[[reservation]]\nnumber = 1\n", "x-y", "2026-09-05", set())


# --- the allocator stays outside the application ---------------------------


def test_the_allocator_is_not_reachable_from_the_application() -> None:
    """Fails if `tools` becomes an importable package or `app/` grows a
    reference: the allocator is a maintenance CLI and must never be a runtime
    import. Established by reading — `tools/` has no `__init__.py` today."""
    assert not (REPO_ROOT / "tools" / "__init__.py").exists()
    assert not (REPO_ROOT / "tools" / "adr" / "__init__.py").exists()
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "app").rglob("*.py")
        if re.search(
            r"^\s*(from|import)\s+tools\b",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    ]
    assert offenders == [], f"application code imports tools: {offenders}"


def test_the_real_register_is_one_the_allocator_can_read() -> None:
    """Non-vacuity: every refusal above is proven on synthetic text. This is
    the one assertion that the real file is the shape they describe."""
    text = allocate.read_register(REGISTER)
    parsed = tomllib.loads(text)
    assert re.search(r"^next_free = \d+$", text, re.MULTILINE)
    assert parsed["reservation"], "the real register carries rows"
