"""Claim the next ADR number, by rewriting the register and nothing else.

``docs/adr/reservations.toml`` is the serialized allocator (see its own header
for the protocol).  This is the only supported way to execute step 1 and step 2
of that protocol: take ``next_free``, append a ``reserved`` row, raise
``next_free`` by one.

Three properties are deliberate and are what the tests hold this to.

**An absent register is a permanent refusal.**  There is no first-run
behaviour, no ``--init``, and no silent creation of a register that happens to
start at 1.  The register is being introduced by a one-time genesis migration
that re-derives every row already on ``main`` from git history and resolves each
to the pull request that landed it.  A fallback that invents an empty register
would let a branch that merely lost the file allocate 0001 a second time — which
is the exact failure the register exists to stop, reintroduced as a convenience.

**The edit is textual, not a TOML round-trip.**  The register is mostly prose:
the protocol, the permanence rule, the status vocabulary, and a note on nearly
every row.  ``tomllib`` cannot write, and every writer available would discard
those comments.  A register whose doctrine is deleted by its own tool is worse
than no tool, so this appends and substitutes text and re-parses to confirm it
still reads.

**It refuses to allocate alongside any other change.**  Step 3 of the protocol
is the load-bearing one: the claim must land on ``main`` before the ADR is
written.  A claim that travels with the ADR is a claim on a branch, and a claim
on a branch is not a claim.  The allocator therefore refuses to run in a
worktree that has any other modified or staged path.

Usage::

    python tools/adr/allocate.py --slug some-decision-slug
    python tools/adr/allocate.py --slug some-decision-slug --check

This module is not importable from the application.  ``tools/`` carries no
``__init__.py`` and nothing under ``app/`` references it; the tests load it by
path.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = REPO_ROOT / "docs" / "adr"
REGISTER = ADR_DIR / "reservations.toml"

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NEXT_FREE = re.compile(r"^next_free = (\d+)$", re.MULTILINE)

ABSENT_REGISTER = (
    "docs/adr/reservations.toml does not exist, and this tool will not create "
    "it. The register is introduced once, by the genesis migration that "
    "re-derives the rows already on main from git history; it is not a "
    "first-run fallback. If the file is missing here, the branch is behind "
    "main or the file was deleted -- fix that, do not allocate."
)


class Refusal(Exception):
    """The allocation was refused. The message is the reason, in full."""


@dataclass(frozen=True)
class Allocation:
    number: int
    slug: str
    claimed: str
    text: str


def read_register(register: Path = REGISTER) -> str:
    """The register's text, or a refusal naming genesis as the only creator."""
    if not register.is_file():
        raise Refusal(ABSENT_REGISTER)
    return register.read_text(encoding="utf-8")


def _parse(text: str) -> dict[str, Any]:
    try:
        parsed: dict[str, Any] = tomllib.loads(text)
        return parsed
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - defensive
        raise Refusal(f"the register does not parse as TOML: {exc}") from exc


def plan(text: str, slug: str, claimed: str, authored: set[str]) -> Allocation:
    """The register text with one number claimed, or a refusal.

    ``authored`` is the set of ``NNNN-<slug>.md`` names present in the ADR
    directory. It is a parameter rather than a directory read so the refusals
    below are testable without a filesystem.
    """
    if not SLUG.match(slug):
        raise Refusal(
            f"{slug!r} is not a slug: lowercase words joined by single hyphens, "
            "matching the ADR filename exactly."
        )

    register = _parse(text)
    if "next_free" not in register:
        raise Refusal("the register has no next_free; it is not an allocator.")
    number = int(register["next_free"])

    rows = register.get("reservation", [])
    taken = {int(row["number"]): row for row in rows}
    if number in taken:
        raise Refusal(
            f"next_free is {number}, but {number} already has a row "
            f"({taken[number].get('slug')!r}). The register is inconsistent; "
            "reconcile it before allocating."
        )

    claimants = {row.get("slug") for row in rows}
    if slug in claimants:
        raise Refusal(
            f"{slug!r} already holds a number in the register. A slug is the "
            "identity of one decision, and a decision keeps one number."
        )

    prefix = f"{number:04d}-"
    if any(name.startswith(prefix) for name in authored):
        raise Refusal(
            f"docs/adr/{number:04d}-*.md already exists, so this would claim a "
            "number in the same change that authors it. Step 3 of the protocol "
            "requires the claim to land on main first."
        )

    match = NEXT_FREE.search(text)
    if match is None:
        raise Refusal(
            "next_free is not on a line of its own, so the conflict that "
            "serializes two allocators would not be raised. Restore it to "
            "`next_free = <n>` on one line."
        )

    row = (
        f'\n[[reservation]]\nnumber = {number}\nslug = "{slug}"\n'
        f'status = "reserved"\nclaimed = "{claimed}"\n'
    )
    updated = text[: match.start()] + f"next_free = {number + 1}" + text[match.end() :]
    updated = _append_row(updated, row)

    reparsed = _parse(updated)
    if int(reparsed["next_free"]) != number + 1:
        raise Refusal("the rewritten register does not carry the raised next_free.")
    if not any(int(r["number"]) == number for r in reparsed.get("reservation", [])):
        raise Refusal("the rewritten register does not carry the new row.")

    return Allocation(number=number, slug=slug, claimed=claimed, text=updated)


def _append_row(text: str, row: str) -> str:
    """Place the row after the last ``[[reservation]]`` block.

    Appending at end-of-file would put the row after the ``[[collision]]``
    blocks, where TOML would still read it correctly but a reader would not.
    """
    last = text.rfind("\n[[reservation]]")
    if last == -1:
        return text.rstrip("\n") + "\n" + row
    end = text.find("\n[[collision]]", last)
    if end == -1:
        return text.rstrip("\n") + "\n" + row
    head = text[:end].rstrip("\n")
    return head + "\n" + row + text[end:]


def other_changed_paths(repo_root: Path = REPO_ROOT) -> list[str]:
    """Every modified or staged path that is not the register itself."""
    out = subprocess.run(  # noqa: S603
        ["git", "status", "--porcelain", "--untracked-files=no"],  # noqa: S607
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paths = []
    for line in out.splitlines():
        path = line[3:].strip()
        if path and path != "docs/adr/reservations.toml":
            paths.append(path)
    return sorted(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--claimed", default=date.today().isoformat())
    parser.add_argument(
        "--check",
        action="store_true",
        help="report the number that would be claimed; write nothing.",
    )
    args = parser.parse_args(argv)

    try:
        text = read_register()
        dirty = other_changed_paths()
        if dirty and not args.check:
            raise Refusal(
                "the worktree carries other changes: "
                + ", ".join(dirty)
                + ". An allocation is committed alone, so that it can land on "
                "main ahead of the ADR it claims a number for."
            )
        authored = {p.name for p in ADR_DIR.glob("*.md")}
        allocation = plan(text, args.slug, args.claimed, authored)
    except Refusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2

    if args.check:
        print(f"would claim ADR-{allocation.number:04d} for {allocation.slug}")
        return 0

    REGISTER.write_text(allocation.text, encoding="utf-8")
    print(
        f"claimed ADR-{allocation.number:04d} for {allocation.slug}. "
        "Commit this file alone and land it on main before writing the ADR."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
