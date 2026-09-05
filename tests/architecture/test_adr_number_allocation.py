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
COLLISION_BACKLOG = frozenset({3, 4, 6, 8, 11})

# Raised from {3, 4, 6, 8} on 2026-09-05, deliberately, by Michael's ruling.
# 11 is PRE-EXISTING debt, not debt this register allowed to form: both 0011
# claimants were authored on 2026-09-04, on branches with no common ancestor
# carrying the file, while this register was still unmerged.  A survey of all
# 260 remote refs and 199 local branches on 2026-09-05 found the second
# claimant; a pull request had asserted there was none.  Raising a ratchet is
# permitted only like this — in the change that discovers the entry, naming
# what was found and where.

# Visibility, weakest first.  A claim that only one workstation can see cannot
# support a statement an outside reviewer is expected to check.
VISIBILITY_RANK = {"local_only": 0, "remote_ref": 1, "pr": 2}

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BLOB_SHA = re.compile(r"\b[0-9a-f]{8,40}\b")


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

    out.extend(schema_findings(register))
    return out


def schema_findings(register: dict) -> list[str]:
    """The date and visibility columns, each held to ONE meaning.

    Row 2 was wrong for eleven days because `claimed` silently carried two
    meanings — an authoring date on most rows and something else on that one.
    These rules are what stops the columns drifting back together.
    """
    out: list[str] = []
    strongest: dict[int, int] = {}

    for entry in register.get("collision", []):
        number = entry.get("number")
        for claimant in entry.get("claimant", []):
            slug = claimant.get("slug", "?")
            where = f"ADR-{number:04d} claimant {slug!r}"
            visibility = claimant.get("visibility")
            if visibility not in VISIBILITY_RANK:
                out.append(
                    f"{where} has visibility {visibility!r}, not one of "
                    f"{sorted(VISIBILITY_RANK)}. An unlabelled claimant reads as "
                    f"auditable and may not be."
                )
                continue
            if not BLOB_SHA.search(str(claimant.get("blob", ""))):
                out.append(
                    f"{where} carries no blob. A branch name is not a "
                    f"coordinate — it moves, and a blob does not."
                )
            if not claimant.get("ref"):
                out.append(f"{where} carries no ref")
            if visibility == "pr" and not claimant.get("pr"):
                out.append(f"{where} claims pull-request visibility with no `pr`")
            if isinstance(number, int):
                rank = VISIBILITY_RANK[visibility]
                strongest[number] = max(strongest.get(number, -1), rank)

    for row in register.get("reservation", []):
        number = row.get("number")
        status = row.get("status")
        if not isinstance(number, int):
            continue
        where = f"ADR-{number:04d}"

        claimed = row.get("claimed")
        if not (isinstance(claimed, str) and ISO_DATE.match(claimed)):
            out.append(
                f"{where} has `claimed = {claimed!r}`, which is not an ISO date. "
                f"`claimed` is the git AUTHOR date of the claiming commit and "
                f"has no second meaning."
            )
            claimed = None

        landed = row.get("landed_at")
        if status == "authored":
            if not (isinstance(landed, str) and ISO_DATE.match(landed)):
                out.append(
                    f"{where} is authored but has no ISO `landed_at`. A row on "
                    f"`main` records when it landed, from first-parent history."
                )
            elif claimed and landed < claimed:
                out.append(
                    f"{where} landed on {landed} but claims {claimed}. A change "
                    f"cannot land before it was authored; one column is wrong."
                )
            if row.get("visibility") is not None:
                out.append(
                    f"{where} is on `main` and also carries `visibility`. "
                    f"Visibility describes a claim nobody has landed."
                )
        else:
            if landed is not None:
                out.append(
                    f"{where} is {status!r} and carries `landed_at = {landed!r}`. "
                    f"Only a row on `main` has landed."
                )
            visibility = row.get("visibility")
            if visibility not in VISIBILITY_RANK:
                out.append(
                    f"{where} is {status!r} with visibility {visibility!r}, not "
                    f"one of {sorted(VISIBILITY_RANK)}."
                )
            elif (
                number in strongest and VISIBILITY_RANK[visibility] > strongest[number]
            ):
                claimed_label = visibility
                actual = next(
                    k for k, v in VISIBILITY_RANK.items() if v == strongest[number]
                )
                out.append(
                    f"{where} is labelled {claimed_label!r} but its strongest "
                    f"declared claimant is only {actual!r}. A local-only claim "
                    f"cannot support a remotely auditable count."
                )
            if not BLOB_SHA.search(str(row.get("coordinate", ""))):
                out.append(
                    f"{where} is {status!r} with no blob in `coordinate`. The "
                    f"strongest immutable coordinate is the point of the field."
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

BLOB_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BLOB_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

CLEAN = {
    "next_free": 3,
    "reservation": [
        {
            "number": 1,
            "slug": "alpha",
            "status": "authored",
            "claimed": "2026-01-01",
            "landed_at": "2026-01-02",
        },
        {
            "number": 2,
            "slug": "beta",
            "status": "reserved",
            "claimed": "2026-01-02",
            "visibility": "remote_ref",
            "coordinate": f"blob {BLOB_A} on refs/remotes/origin/beta",
        },
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


# ---------------------------------------------------------------------------
# The date and visibility columns.  Each of these plants the defect the rule
# names, and then plants a NEAR MISS that must NOT be named — a rule that
# fires on both is not reading what it claims to read.
# ---------------------------------------------------------------------------


def test_a_planted_claimed_that_is_not_a_date_is_named() -> None:
    """This is row 2's defect class. `claimed` read 2026-08-15 while the only
    author date on that ADR was 2026-08-16 — a column carrying two meanings."""
    planted = {
        "next_free": 3,
        "reservation": [
            {**CLEAN["reservation"][0], "claimed": "merged in August"},
            CLEAN["reservation"][1],
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("is not an ISO date" in p for p in problems), problems


def test_a_planted_landing_before_its_claim_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            {**CLEAN["reservation"][0], "landed_at": "2025-12-31"},
            CLEAN["reservation"][1],
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("cannot land before it was authored" in p for p in problems), problems


def test_a_same_day_claim_and_landing_is_not_flagged() -> None:
    """Near miss for the rule above: four of the six rows on `main` were
    authored and landed the same day, and must stay clean."""
    same_day = {
        "next_free": 3,
        "reservation": [
            {**CLEAN["reservation"][0], "landed_at": "2026-01-01"},
            CLEAN["reservation"][1],
        ],
    }
    assert findings(same_day, CLEAN_FILES) == []


def test_a_planted_authored_row_with_no_landing_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            {k: v for k, v in CLEAN["reservation"][0].items() if k != "landed_at"},
            CLEAN["reservation"][1],
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("no ISO `landed_at`" in p for p in problems), problems


def test_a_planted_landing_on_an_unlanded_row_is_named() -> None:
    """The inverse, and the one that would quietly launder a branch into
    looking like it reached `main`."""
    planted = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {**CLEAN["reservation"][1], "landed_at": "2026-01-03"},
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("Only a row on `main` has landed" in p for p in problems), problems


def test_a_planted_unlabelled_claim_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {k: v for k, v in CLEAN["reservation"][1].items() if k != "visibility"},
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("with visibility None" in p for p in problems), problems


def test_a_planted_coordinate_without_a_blob_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {**CLEAN["reservation"][1], "coordinate": "on the beta branch"},
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("no blob in `coordinate`" in p for p in problems), problems


def test_a_local_only_claim_cannot_be_labelled_remotely_auditable() -> None:
    """THE rule this schema exists for. Three of 0006's five contents live on
    one workstation; a register that prints one `remote_ref` label over all
    five states something no outside reviewer can check."""
    planted = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {**CLEAN["reservation"][1], "status": "contested", "contested_by": ["x"]},
        ],
        "collision": [
            {
                "number": 2,
                "claimant": [
                    {
                        "slug": "beta",
                        "blob": BLOB_A,
                        "ref": "refs/heads/beta",
                        "visibility": "local_only",
                    },
                    {
                        "slug": "beta-rival",
                        "blob": BLOB_B,
                        "ref": "refs/heads/beta-rival",
                        "visibility": "local_only",
                    },
                ],
            }
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("cannot support a remotely auditable count" in p for p in problems), (
        problems
    )


def test_a_label_matching_its_strongest_claimant_is_not_flagged() -> None:
    """Near miss: the rule compares against the STRONGEST claimant, so a
    `remote_ref` label over one remote and two local claimants is honest and
    must stay clean. That is exactly 0006's real shape."""
    honest = {
        "next_free": 3,
        "reservation": [
            CLEAN["reservation"][0],
            {**CLEAN["reservation"][1], "status": "contested", "contested_by": ["x"]},
        ],
        "collision": [
            {
                "number": 2,
                "claimant": [
                    {
                        "slug": "beta",
                        "blob": BLOB_A,
                        "ref": "refs/remotes/origin/beta",
                        "visibility": "remote_ref",
                    },
                    {
                        "slug": "beta",
                        "blob": BLOB_B,
                        "ref": "refs/heads/beta-local",
                        "visibility": "local_only",
                    },
                ],
            }
        ],
    }
    assert findings(honest, CLEAN_FILES) == []


def test_a_planted_claimant_without_a_blob_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": list(CLEAN["reservation"]),
        "collision": [
            {
                "number": 1,
                "claimant": [
                    {
                        "slug": "alpha",
                        "ref": "refs/heads/alpha",
                        "visibility": "local_only",
                    }
                ],
            }
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("carries no blob" in p for p in problems), problems


def test_a_planted_pr_label_without_a_pr_number_is_named() -> None:
    planted = {
        "next_free": 3,
        "reservation": list(CLEAN["reservation"]),
        "collision": [
            {
                "number": 1,
                "claimant": [
                    {
                        "slug": "alpha",
                        "blob": BLOB_A,
                        "ref": "refs/remotes/origin/alpha",
                        "visibility": "pr",
                    }
                ],
            }
        ],
    }
    problems = findings(planted, CLEAN_FILES)
    assert any("no `pr`" in p for p in problems), problems


def test_every_collision_claimant_in_the_real_register_is_labelled() -> None:
    """Non-vacuity: the plants above are synthetic. This drives the real file
    and requires every declared claimant to carry a coordinate and a label."""
    register = load_register(REGISTER.read_text(encoding="utf-8"))
    claimants = [
        claimant
        for entry in register["collision"]
        for claimant in entry.get("claimant", [])
    ]
    assert len(claimants) >= 13, f"only {len(claimants)} claimants declared"
    for claimant in claimants:
        assert claimant["visibility"] in VISIBILITY_RANK, claimant
        assert BLOB_SHA.search(claimant["blob"]), claimant
        assert claimant["ref"].startswith("refs/"), claimant
