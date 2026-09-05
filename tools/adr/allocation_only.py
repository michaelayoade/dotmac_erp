"""A pull request that claims an ADR number claims it and nothing else.

Step 3 of the register's protocol is the load-bearing one: the claim lands on
``main`` BEFORE the ADR is written.  A claim that travels with its document is
a claim on a branch, and a claim on a branch is not a claim — it is what every
colliding record in this repository already did.

``tools/adr/allocate.py`` refuses to RUN beside another change, which stops the
honest mistake.  It does not stop a hand-edited register, and the register is a
text file that anyone can hand-edit.  This is the gate that does: if a pull
request ADDS a ``reserved`` row, the register must be the only file it touches.

**Only allocations are gated, and that is stated rather than implied.**  A pull
request that corrects a note, records a collision, or lands the register itself
is not an allocation and is not held to the one-file rule — it has not handed
out a number.  Widening the gate to every register edit would block the genesis
migration with the rule the genesis migration introduces.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess  # nosec B404
import sys
import tomllib
from pathlib import Path
from typing import Any

GIT = shutil.which("git") or "git"

REGISTER_PATH = "docs/adr/reservations.toml"

REPO_ROOT = Path(__file__).resolve().parents[2]


def reserved_numbers(text: str) -> set[int]:
    """Numbers this register hands out as fresh claims."""
    if not text.strip():
        return set()
    parsed: dict[str, Any] = tomllib.loads(text)
    return {
        int(row["number"])
        for row in parsed.get("reservation", [])
        if row.get("status") == "reserved"
    }


def is_genesis(base_text: str) -> bool:
    """The base carries no register at all, so this change introduces one.

    Genesis cannot be held to the one-file rule, because the rule is what
    genesis introduces. It is recognised EXPLICITLY and announced by the gate
    rather than falling out of an empty-set subtraction, so that a branch that
    merely DELETED the register cannot be mistaken for the migration that
    creates it — deleting it is the far more likely accident, and it would
    otherwise buy a free pass through this gate.
    """
    return not base_text.strip()


def verdict(changed: set[str], base_text: str, head_text: str) -> list[str]:
    """Every reason this pull request may not land, as reviewable sentences."""
    if REGISTER_PATH not in changed:
        return []
    if is_genesis(base_text):
        if not head_text.strip():
            return [
                f"{REGISTER_PATH} is absent on both sides of this change. The "
                "register is not optional; restore it."
            ]
        return []

    claimed = sorted(reserved_numbers(head_text) - reserved_numbers(base_text))
    if not claimed:
        return []

    others = sorted(changed - {REGISTER_PATH})
    if not others:
        return []

    numbers = ", ".join(f"ADR-{n:04d}" for n in claimed)
    return [
        f"this pull request claims {numbers} and also changes: "
        + ", ".join(others)
        + f". An allocation changes {REGISTER_PATH} alone, so that it can land "
        "on main ahead of the document it reserves a number for. Split the "
        "claim into its own pull request."
    ]


def changed_files(base: str, head: str, repo_root: Path = REPO_ROOT) -> set[str]:
    out = subprocess.run(  # noqa: S603  # nosec B603
        [GIT, "diff", "--name-only", f"{base}...{head}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def show(ref: str, path: str, repo_root: Path = REPO_ROOT) -> str:
    result = subprocess.run(  # noqa: S603  # nosec B603
        [GIT, "show", f"{ref}:{path}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)

    base_text = show(args.base, REGISTER_PATH)
    head_text = show(args.head, REGISTER_PATH)
    if is_genesis(base_text) and head_text.strip():
        print(
            f"NOTICE: {args.base} carries no {REGISTER_PATH}, so this is the "
            "one-time genesis migration and the allocation-only rule does not "
            "apply to it. This notice should appear exactly once, ever."
        )
    problems = verdict(changed_files(args.base, args.head), base_text, head_text)
    for problem in problems:
        print(f"refused: {problem}", file=sys.stderr)
    if not problems:
        print("no ADR number is claimed here, or the claim travels alone.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
