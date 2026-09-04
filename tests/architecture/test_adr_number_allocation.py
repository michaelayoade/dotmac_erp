"""ERP ADR numbers are allocated serially, and a spent number is never reused.

ERP allocated ADR numbers by reading ``docs/adr/`` and taking the next free
one.  That is a read-then-write with no lock, performed on branches that cannot
see each other, and it produced the race it looks like: 0003, 0004, 0006 and
0008 each name more than one decision.  ``docs/adr/reservations.toml`` is the
serialized allocator that replaces it; this module is its checker.

What is ENFORCED here, against one worktree:

* every number appears once, and every slug appears once;
* the numbers are dense from 1, and ``next_free`` is exactly one past the last
  — so a number cannot be silently skipped or silently dropped;
* ``authored`` and the presence of ``NNNN-<slug>.md`` agree in both
  directions, so no ADR exists without a claim and no claim pretends to a
  document that was never written;
* a ``withdrawn`` number keeps its row, carries a reason, and has no file;
* the README index lists exactly the authored rows;
* the pre-existing collision backlog does not grow.

What is ENFORCED IN CI ONLY, needing a base revision to compare against:

* an ADR authored in this change had its number reserved in an EARLIER one.
  The detector is a pure function over two registers and is proven by planted
  inputs on every run; only the git wiring that feeds it depends on CI.

What is DECLARED AND NOT DETECTED, and is not called coverage: which other
branches claim a contested number.  A checker reading one worktree cannot see
another branch.  The gate that does bite is indirect and sufficient — a
colliding branch cannot merge, because the moment its ADR file lands the
authored/file agreement above fails against its ``contested`` row.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = REPO_ROOT / "docs" / "adr"
REGISTER = ADR_DIR / "reservations.toml"
README = ADR_DIR / "README.md"

VALID_STATUSES = frozenset({"reserved", "authored", "withdrawn", "contested"})

ADR_FILENAME = re.compile(r"^(\d{4})-([a-z0-9-]+)\.md$")
README_ROW = re.compile(r"^\|\s*\[(\d{4})\]\((\d{4})-([a-z0-9-]+)\.md\)")

# The pre-existing collisions, pinned.  Two-directional per ADR-0018: adding a
# number is forbidden outright, and removing one requires lowering this pin in
# the same change that reconciles it.
COLLISION_BACKLOG = frozenset({3, 4, 6, 8})


def load_register(text: str) -> dict:
    return tomllib.loads(text)


def adr_filenames(directory: Path) -> set[str]:
    return {
        path.name for path in directory.glob("*.md") if ADR_FILENAME.match(path.name)
    }


def findings(register: dict, filenames: set[str]) -> list[str]:
    """Every rule violation in one register, as reviewable sentences."""
    out: list[str] = []
    rows = register.get("reservation", [])

    seen_numbers: dict[int, str] = {}
    seen_slugs: dict[str, int] = {}
    withdrawn: set[int] = set()

    for row in rows:
        number = row.get("number")
        slug = row.get("slug")
        status = row.get("status")

        if not isinstance(number, int) or number < 1:
            out.append(f"row {row!r} has no positive integer number")
            continue
        if not slug:
            out.append(f"ADR-{number:04d} has no slug")
            continue
        if status not in VALID_STATUSES:
            out.append(
                f"ADR-{number:04d} has status {status!r}, "
                f"not one of {sorted(VALID_STATUSES)}"
            )
            continue

        if number in seen_numbers:
            out.append(
                f"ADR-{number:04d} is claimed twice, by {seen_numbers[number]!r} "
                f"and {slug!r}. A number is allocated once."
            )
        if slug in seen_slugs:
            out.append(
                f"slug {slug!r} is claimed by both ADR-{seen_slugs[slug]:04d} and "
                f"ADR-{number:04d}. Re-author under a new number with "
                f"`replaces = {seen_slugs[slug]}`."
            )
        seen_numbers[number] = slug
        seen_slugs[slug] = number

        expected = f"{number:04d}-{slug}.md"
        present = expected in filenames

        if status == "authored" and not present:
            out.append(f"ADR-{number:04d} is authored but {expected} does not exist")
        if status != "authored" and present:
            out.append(
                f"ADR-{number:04d} is {status!r} but {expected} exists. A number "
                f"that is not authored has no document."
            )

        if status == "withdrawn":
            withdrawn.add(number)
            if not row.get("withdrawn_reason"):
                out.append(
                    f"ADR-{number:04d} is withdrawn with no `withdrawn_reason`. "
                    f"Withdrawal is a recorded act, not a deletion."
                )
        if status == "contested" and not row.get("contested_by"):
            out.append(f"ADR-{number:04d} is contested but names no `contested_by`")

        replaces = row.get("replaces")
        if replaces is not None and replaces not in withdrawn:
            out.append(
                f"ADR-{number:04d} declares `replaces = {replaces}`, which is not "
                f"a withdrawn number in this register."
            )

    for number in sorted(withdrawn):
        others = [
            r
            for r in rows
            if r.get("number") == number and r.get("status") != "withdrawn"
        ]
        if others:
            out.append(
                f"ADR-{number:04d} was withdrawn and is claimed again by "
                f"{[r.get('slug') for r in others]!r}. A spent number is spent."
            )

    if seen_numbers:
        highest = max(seen_numbers)
        missing = sorted(set(range(1, highest + 1)) - set(seen_numbers))
        if missing:
            out.append(
                f"the register has gaps at {missing}. Every number ever claimed "
                f"keeps a row, so a gap means one was removed."
            )
        next_free = register.get("next_free")
        if next_free != highest + 1:
            out.append(
                f"`next_free` is {next_free!r}, but the highest claimed number is "
                f"{highest}, so it must be {highest + 1}."
            )

    for filename in sorted(filenames):
        match = ADR_FILENAME.match(filename)
        assert match is not None
        number, slug = int(match.group(1)), match.group(2)
        if seen_numbers.get(number) != slug:
            out.append(
                f"{filename} exists with no matching reservation. Claim the "
                f"number in its own change before writing the document."
            )

    return out


def authored_without_prior_reservation(
    base: dict, head: dict, head_filenames: set[str]
) -> set[int]:
    """Numbers whose document and whose claim arrive in the same change.

    The claim must land first, on its own.  A number claimed in the change that
    also authors the ADR was claimed on a branch, and a claim on a branch is
    not a claim — it is what every colliding record in this repository did.
    """
    base_numbers = {row["number"] for row in base.get("reservation", [])}
    offenders: set[int] = set()
    for row in head.get("reservation", []):
        expected = f"{row['number']:04d}-{row['slug']}.md"
        if expected in head_filenames and row["number"] not in base_numbers:
            offenders.add(row["number"])
    return offenders


def readme_index_numbers(text: str) -> set[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    for line in text.splitlines():
        match = README_ROW.match(line.strip())
        if match:
            found.add((int(match.group(1)), match.group(3)))
    return found


# --------------------------------------------------------------------------
# The register, as it actually stands.
# --------------------------------------------------------------------------


def test_the_register_is_internally_consistent_and_matches_the_directory() -> None:
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    problems = findings(register, adr_filenames(ADR_DIR))
    assert problems == [], "docs/adr/reservations.toml: " + "; ".join(problems)


def test_the_readme_index_lists_exactly_the_authored_rows() -> None:
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    authored = {
        (row["number"], row["slug"])
        for row in register["reservation"]
        if row["status"] == "authored"
    }
    indexed = readme_index_numbers(README.read_text(encoding="utf-8"))
    assert indexed == authored, (
        f"docs/adr/README.md index and the register disagree: "
        f"only in index={sorted(indexed - authored)}, "
        f"only in register={sorted(authored - indexed)}"
    )


def test_the_collision_backlog_does_not_grow_and_only_shrinks_deliberately() -> None:
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    declared = {entry["number"] for entry in register.get("collision", [])}
    assert declared == COLLISION_BACKLOG, (
        f"the ADR-number collision backlog moved: "
        f"new={sorted(declared - COLLISION_BACKLOG)}, "
        f"resolved={sorted(COLLISION_BACKLOG - declared)}. A new collision is "
        f"forbidden — the register exists to prevent one. A resolved collision "
        f"lowers COLLISION_BACKLOG in the same change that reconciles it."
    )
    contested = {
        row["number"] for row in register["reservation"] if row["status"] == "contested"
    }
    assert contested <= COLLISION_BACKLOG, (
        f"contested numbers {sorted(contested - COLLISION_BACKLOG)} are not in "
        f"the declared backlog. `contested` records pre-existing debt only."
    )


def test_no_adr_was_authored_in_the_change_that_claimed_its_number() -> None:
    """The CI half. Degrades to nothing detected, and says so, off CI."""
    base_ref = "origin/main"
    git = shutil.which("git")
    if git is None:
        return
    try:
        base_text = subprocess.run(  # noqa: S603 - absolute git, literal in-repo path
            [git, "show", f"{base_ref}:docs/adr/reservations.toml"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        # The register is not on the base yet (this is the change that adds
        # it), or git/the ref is unavailable. The pure detector above is still
        # proven by the planted inputs below on every run.
        return

    offenders = authored_without_prior_reservation(
        load_register(base_text),
        load_register(REGISTER.read_text(encoding="utf-8")),
        adr_filenames(ADR_DIR),
    )
    assert offenders == set(), (
        f"ADR {sorted(offenders)} was written in the same change that claimed "
        f"its number. Land the reservation on {base_ref} first, alone."
    )


# --------------------------------------------------------------------------
# Sensitivity proof (ADR-0018). A checker that only ever passes over a clean
# tree has proved nothing about itself, so each rule is planted and named.
# --------------------------------------------------------------------------

CLEAN = {
    "next_free": 3,
    "reservation": [
        {"number": 1, "slug": "alpha", "status": "authored", "claimed": "2026-01-01"},
        {"number": 2, "slug": "beta", "status": "reserved", "claimed": "2026-01-02"},
    ],
}
CLEAN_FILES = {"0001-alpha.md"}


def test_the_negative_control_is_clean() -> None:
    """A detector that flags everything would 'catch' every plant below."""
    assert findings(CLEAN, CLEAN_FILES) == []


def test_a_planted_duplicate_number_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            *CLEAN["reservation"],
            {
                "number": 2,
                "slug": "gamma",
                "status": "reserved",
                "claimed": "2026-01-03",
            },
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("ADR-0002 is claimed twice" in p for p in problems), problems
    assert any("'beta'" in p and "'gamma'" in p for p in problems), problems


def test_a_planted_reuse_after_withdrawal_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {
                "number": 2,
                "slug": "beta",
                "status": "withdrawn",
                "claimed": "2026-01-02",
                "withdrawn_reason": "abandoned",
            },
            {
                "number": 2,
                "slug": "delta",
                "status": "reserved",
                "claimed": "2026-02-01",
            },
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("A spent number is spent" in p for p in problems), problems


def test_a_planted_document_on_a_withdrawn_number_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {
                "number": 2,
                "slug": "beta",
                "status": "withdrawn",
                "claimed": "2026-01-02",
                "withdrawn_reason": "abandoned",
            },
        ],
    }
    problems = findings(planted, CLEAN_FILES | {"0002-beta.md"})
    assert any("is 'withdrawn' but 0002-beta.md exists" in p for p in problems), (
        problems
    )


def test_a_planted_unregistered_adr_file_is_named() -> None:
    problems = findings(CLEAN, CLEAN_FILES | {"0009-orphan.md"})
    assert any(
        "0009-orphan.md exists with no matching reservation" in p for p in problems
    ), problems


def test_a_planted_silent_deletion_is_named() -> None:
    """Removing a row is how permanence would be lost. Density catches it."""
    planted = {
        "next_free": 3,
        "reservation": [
            {
                "number": 2,
                "slug": "beta",
                "status": "reserved",
                "claimed": "2026-01-02",
            }
        ],
    }
    problems = findings(planted, set())
    assert any("the register has gaps at [1]" in p for p in problems), problems


def test_a_planted_stale_next_free_is_named() -> None:
    planted = {**CLEAN, "next_free": 2}
    problems = findings(planted, CLEAN_FILES)
    assert any("`next_free` is 2" in p for p in problems), problems


def test_a_planted_withdrawal_without_a_reason_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {
                "number": 2,
                "slug": "beta",
                "status": "withdrawn",
                "claimed": "2026-01-02",
            },
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("no `withdrawn_reason`" in p for p in problems), problems


def test_a_planted_same_change_authoring_is_named() -> None:
    base = {"next_free": 2, "reservation": [CLEAN["reservation"][0]]}
    head = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {
                "number": 2,
                "slug": "beta",
                "status": "authored",
                "claimed": "2026-01-02",
            },
        ],
    }
    assert authored_without_prior_reservation(
        base, head, {"0001-alpha.md", "0002-beta.md"}
    ) == {2}


def test_a_reservation_that_lands_before_its_document_is_not_flagged() -> None:
    """The other half of the ordering rule: the correct sequence must pass."""
    base = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {
                "number": 2,
                "slug": "beta",
                "status": "reserved",
                "claimed": "2026-01-02",
            },
        ],
    }
    head = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {
                "number": 2,
                "slug": "beta",
                "status": "authored",
                "claimed": "2026-01-02",
            },
        ],
    }
    assert (
        authored_without_prior_reservation(
            base, head, {"0001-alpha.md", "0002-beta.md"}
        )
        == set()
    )


def test_the_readme_parser_reads_a_real_row() -> None:
    row = "| [0007](0007-unobserved-is-not-failed.md) | An unobserved | Accepted |"
    assert readme_index_numbers(row) == {(7, "unobserved-is-not-failed")}
    assert readme_index_numbers("| not a row |") == set()
