"""Every path that migrates declares which database, or is on a shrinking list.

`alembic/env.py` refuses a database it was not authorised for — but only when
something bound `MIGRATION_EXPECTED_DATABASE`. Unbound, it prints `database
identity UNVERIFIED` and proceeds, which is deliberate: mandatory on day one is
how a check gets deleted rather than adopted.

That leniency is what this file exists to stop from becoming permanent. An
optional check with no accounting of who has adopted it is indistinguishable
from no check, because nothing notices when the answer is "nobody".

## Why a two-directional ratchet (ADR-0018)

A backlog that only checks one direction rots in whichever direction it does not
check:

* **Rising** — a new migration entry point lands unbound and nobody notices, so
  the guard silently covers a shrinking share of the paths that matter.
* **Falling** — someone adopts the binding and the backlog is never lowered, so
  the recorded debt overstates reality and the next reader cannot tell what is
  left. Worse, a later regression can re-enter the backlog into the slack and
  fail nothing.

So `UNBOUND` is exact, not a maximum: the test fails when an entry point leaves
it as loudly as when one joins.

## Entry-point FAMILIES, not one directory

ADR-0018 requires a guard to enumerate entry-point families rather than a single
directory. `alembic upgrade` is reachable from four here — the Makefile, GitHub
workflows, the deploy script, and integration fixtures that redirect the
executor at a database they created. A guard that scanned only `.github/` would
have declared the Makefile covered by omission.

## What "bound" means, and why it is re-derived

An entry point is BOUND when the invocation itself supplies
`MIGRATION_EXPECTED_DATABASE` — a step `env:`, a `-e VAR=value`, or a `-e VAR`
pass-through. It is re-derived from the file on every run rather than trusted
from this list, so an entry point cannot be moved into `BOUND` by editing the
list alone.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

VAR = "MIGRATION_EXPECTED_DATABASE"

#: Files that can invoke `alembic upgrade`, one per entry-point family.
SEARCHED = (
    Path("Makefile"),
    Path(".github/workflows/ci.yml"),
    Path(".github/workflows/release-hardened.yml"),
    Path("scripts/deploy.sh"),
)

#: Entry points that DO bind the expectation. Re-derived below; this list is the
#: expectation, not the evidence.
BOUND: frozenset[str] = frozenset(
    {
        ".github/workflows/ci.yml:hardened-image-migration",
        ".github/workflows/ci.yml:integration-alembic-upgrade",
        ".github/workflows/release-hardened.yml:hardened-release-migration",
    }
)

#: THE BACKLOG. Exact, and it may only shrink. Each entry names why it is not
#: bound and what would bind it.
#:
#: * `Makefile:migrate` — a developer's local database, whose name this
#:   repository cannot know. Binding it would mean inventing a value or
#:   demanding one on every local run. The operator may still export the
#:   variable; nothing here supplies it.
#: * `Makefile:docker-migrate` — the same operator database, one layer out. It
#:   is NOT bound by the invocation, and deliberately so: `docker-compose.yml`
#:   gives the `app` service `env_file: - .env`, and unlike
#:   `MIGRATION_DATABASE_URL` — which that file overrides to `''` so the
#:   credential leaves the runtime services — `MIGRATION_EXPECTED_DATABASE` is
#:   NOT overridden. So `env_file` already carries it to the one-shot, exactly
#:   as `.env.example` says it should ("it is a database name, not a
#:   credential"). An added `-e` flag would be redundant, and it would break
#:   `test_operator_migration_entrypoints_do_not_reuse_the_running_app` and
#:   `test_the_one_shot_executor_still_receives_it_on_the_flag`, which pin the
#:   exact `docker compose run --rm -e MIGRATION_DATABASE_URL app` invocation.
#:   Those two guard the credential boundary; this ratchet must not be silenced
#:   by loosening them. The two variables take different routes for a principled
#:   reason — one is overridden and must beat the override, the other is not.
#: * `scripts/deploy.sh:production-migration` — the production one-shot. The
#:   name is real and knowable, but only on the host: `.env.example` ships
#:   `MIGRATION_EXPECTED_DATABASE=` EMPTY, so a production deployment prints
#:   UNVERIFIED until an operator fills it in. Filling it in is a host action
#:   against a named target and is not this repository's to take.
UNBOUND: frozenset[str] = frozenset(
    {
        "Makefile:docker-migrate",
        "Makefile:migrate",
        "scripts/deploy.sh:production-migration",
    }
)

#: Where each entry point lives, so the derivation below reads the real bytes.
#: A regex per entry point rather than a line number: line numbers drift with
#: every edit above them, and a guard that fails on an unrelated insertion gets
#: deleted.
INVOCATIONS: dict[str, tuple[Path, str]] = {
    "Makefile:migrate": (
        Path("Makefile"),
        r"^migrate:.*?(?=^\w[\w-]*:|\Z)",
    ),
    "Makefile:docker-migrate": (
        Path("Makefile"),
        r"^docker-migrate:.*?(?=^\w[\w-]*:|\Z)",
    ),
    ".github/workflows/ci.yml:hardened-image-migration": (
        Path(".github/workflows/ci.yml"),
        r"docker run --rm --name ci-migrate.*?alembic upgrade heads",
    ),
    ".github/workflows/ci.yml:integration-alembic-upgrade": (
        Path(".github/workflows/ci.yml"),
        r"- name: Run Alembic migrations.*?alembic upgrade heads",
    ),
    ".github/workflows/release-hardened.yml:hardened-release-migration": (
        Path(".github/workflows/release-hardened.yml"),
        r"docker run --rm --name ci-hard-migrate.*?alembic upgrade heads",
    ),
    "scripts/deploy.sh:production-migration": (
        Path("scripts/deploy.sh"),
        r"docker compose run --rm -e MIGRATION_DATABASE_URL.*?alembic upgrade heads",
    ),
}


def _region(entry_point: str, sources: Mapping[Path, str] | None = None) -> str:
    """The bytes of one invocation, read from the file it actually lives in.

    `sources` substitutes the CONTENTS of a file without writing it, so a
    sensitivity proof can plant a defect in the bytes this function reads and
    watch the real derivation react. Omitted — which is every production call —
    it reads the tree, unchanged.
    """
    relative, pattern = INVOCATIONS[entry_point]
    text = (
        sources[relative]
        if sources is not None and relative in sources
        else (REPO_ROOT / relative).read_text(encoding="utf-8")
    )
    match = re.search(pattern, text, re.S | re.M)
    assert match is not None, (
        f"{entry_point} no longer matches its locator in {relative}. Either the "
        f"invocation moved — update INVOCATIONS — or it is gone, in which case "
        f"remove it from BOUND/UNBOUND in the same change."
    )
    return match.group(0)


def _binds(entry_point: str, sources: Mapping[Path, str] | None = None) -> bool:
    return VAR in _region(entry_point, sources)


def _derived_unbound(sources: Mapping[Path, str] | None = None) -> set[str]:
    """THE RATCHET'S DERIVATION, in one place.

    Stated once so the ratchet, its clean-tree half and its planted defect all
    exercise the SAME code. Three copies of a two-line comprehension is how a
    sensitivity proof ends up demonstrating something adjacent to the check it
    is supposed to be proving.
    """
    return {
        entry_point
        for entry_point in (BOUND | UNBOUND)
        if not _binds(entry_point, sources)
    }


def _observed_invocations() -> set[str]:
    """Every `alembic upgrade` this repository issues, from the real files.

    Lines that TALK about the command are excluded, in two forms, because a
    guard that counted its own documentation would report entry points that do
    not exist:

    * comments — several files explain the executor contract in prose;
    * `echo` — `scripts/deploy.sh` announces the step it is about to run
      (`echo "-> Applying migrations (alembic upgrade heads)..."`) on the line
      before it runs it. Counting the announcement as a seventh entry point was
      the first thing this scan did, and it is the reason the exclusion is
      stated rather than assumed.

    Both exclusions are narrow on purpose: anything else containing
    `alembic upgrade` is treated as a real invocation and must be classified.
    """
    found: set[str] = set()
    for relative in SEARCHED:
        for line in (REPO_ROOT / relative).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("echo "):
                continue
            if "alembic upgrade" in stripped:
                found.add(f"{relative}:{stripped}")
    return found


def test_every_declared_entry_point_still_exists() -> None:
    """A locator that no longer matches is a silently disabled guard."""
    for entry_point in sorted(BOUND | UNBOUND):
        assert _region(entry_point), entry_point


def test_the_invocation_count_matches_the_declaration() -> None:
    """A new `alembic upgrade` anywhere must be classified, not absorbed.

    This is the RISING half. Without it, a seventh migration path could land
    unbound and every test below would still pass, because they only ever look
    at entry points the list already names.
    """
    observed = _observed_invocations()
    assert len(observed) == len(BOUND | UNBOUND), (
        f"{len(observed)} `alembic upgrade` invocations found but "
        f"{len(BOUND | UNBOUND)} are declared. A new migration entry point must "
        f"be added to BOUND (if it supplies {VAR}) or to UNBOUND (if it cannot), "
        f"with the reason. Found:\n  " + "\n  ".join(sorted(observed))
    )


def test_every_bound_entry_point_really_binds() -> None:
    """Re-derived from the file, so BOUND cannot be satisfied by editing BOUND."""
    for entry_point in sorted(BOUND):
        assert _binds(entry_point), (
            f"{entry_point} is declared BOUND but its invocation does not supply "
            f"{VAR}. Either bind it or move it to UNBOUND with a reason."
        )


def test_the_backlog_is_exact_in_both_directions() -> None:
    """THE RATCHET. Fails when an entry point joins the backlog AND when one
    leaves it without the declaration being lowered.

    The falling direction is the one usually missed. A backlog that overstates
    remaining debt is not merely untidy: it leaves slack a later regression can
    re-enter without failing anything.
    """
    actually_unbound = _derived_unbound()
    assert actually_unbound == UNBOUND, (
        "the migration-identity backlog is wrong in at least one direction.\n"
        f"  newly unbound (rose):  {sorted(actually_unbound - UNBOUND)}\n"
        f"  now bound (fell, lower UNBOUND): {sorted(UNBOUND - actually_unbound)}"
    )


def test_the_backlog_only_contains_entry_points_that_cannot_know_the_name() -> None:
    """Sensitivity on the backlog's PREMISE, not just its size.

    ADR-0018: an exemption states an enforceable premise or the region is
    unmonitored rather than exempt. The premise here is narrow — "this entry
    point cannot know the database name from the repository" — and it is true of
    exactly two things: a developer's local database, and a production host's
    `.env`. Any CI entry point knows its own database, because CI creates it.

    So a CI entry point may never enter the backlog. If one does, the premise
    has been stretched to cover something it does not describe, which is how a
    stated risk decision quietly becomes a blanket exemption.
    """
    for entry_point in sorted(UNBOUND):
        assert not entry_point.startswith(".github/"), (
            f"{entry_point} is a CI entry point in the unbound backlog. CI "
            f"creates its own database and therefore knows its name; the "
            f"backlog's premise does not cover it."
        )


#: The BOUND entry point the planted defect below is applied to. Derived, not
#: hardcoded: whichever entry point is chosen must really bind today, and the
#: proof asserts that before it strips anything — so if this one is ever moved
#: to UNBOUND the proof fails loudly instead of silently tampering with
#: something that was already unbound, which is the exact way the previous
#: version of this proof was vacuous.
def _a_bound_entry_point() -> str:
    for entry_point in sorted(BOUND):
        if _binds(entry_point):
            return entry_point
    raise AssertionError("nothing in BOUND binds; the ratchet has nothing to prove")


def _without_the_variable(region: str) -> str:
    """The same invocation with every line that supplies VAR removed."""
    return "\n".join(line for line in region.splitlines() if VAR not in line)


def test_the_ratchet_notices_an_unbound_entry_point() -> None:
    """SENSITIVITY, planted defect — in the SOURCE, not in the declaration.

    The previous version of this proof was
    `assert {"Makefile:docker-migrate"} | UNBOUND != UNBOUND`. Two things were
    wrong with it, and the second is the one that matters:

    1. `Makefile:docker-migrate` is ALREADY a member of `UNBOUND`, so the union
       was the identity and the assertion was simply false. That is why this
       file was red.
    2. Even with a non-member it was `{x} | S != S` — set algebra over two
       constants. It never called `_binds`, never called `_region`, never read a
       file. **It could not tell a working ratchet from a deleted one**, which
       is precisely the thing a sensitivity proof exists to rule out. Fixing
       only the wrong element would have left a test that passes over a deleted
       ratchet and looks repaired.

    So the defect is now planted where a real regression happens: in the bytes
    of an invocation. A `BOUND` entry point has the lines supplying `VAR`
    stripped out, and the REAL derivation — the same `_derived_unbound` the
    ratchet itself calls — must report that entry point as newly unbound.

    Modelled on `test_the_credential_asymmetry_detector_is_sensitive` in
    `tests/architecture/test_database_role_contract.py`: read the real file,
    tamper the exact bytes, assert the tamper landed, then run the real
    derivation over the tampered text.
    """
    victim = _a_bound_entry_point()
    relative, _ = INVOCATIONS[victim]
    original = (REPO_ROOT / relative).read_text(encoding="utf-8")

    region = _region(victim)
    assert VAR in region, f"{victim} does not bind, so stripping proves nothing"
    tampered = original.replace(region, _without_the_variable(region), 1)
    assert tampered != original, "the tamper target moved; update this proof"

    planted = _derived_unbound({relative: tampered})
    assert planted != UNBOUND, (
        f"{VAR} was stripped from {victim} and the ratchet still agreed with "
        f"the declared backlog — the comparison cannot detect an entry point "
        f"that stops binding"
    )
    assert planted - UNBOUND == {victim}, (
        f"the ratchet reported {sorted(planted - UNBOUND)} rather than exactly "
        f"{victim}; a detector that fires on the wrong entry point is not "
        f"evidence about this one"
    )


def test_the_ratchet_ignores_an_unrelated_edit_to_the_same_invocation() -> None:
    """SENSITIVITY, near-miss — and the half that stops the proof above being
    satisfied by a detector that fires on ANY edit.

    The same region is changed in a way that leaves the binding intact: a
    comment is inserted into it. If the verdict moved, the planted defect above
    would be evidence that the file changed, not that the binding was removed.
    """
    victim = _a_bound_entry_point()
    relative, _ = INVOCATIONS[victim]
    original = (REPO_ROOT / relative).read_text(encoding="utf-8")

    region = _region(victim)
    lines = region.splitlines()
    indent = " " * (len(lines[-1]) - len(lines[-1].lstrip()))
    edited = "\n".join([lines[0], f"{indent}# an unrelated edit", *lines[1:]])
    tampered = original.replace(region, edited, 1)
    assert tampered != original, "the near-miss did not change anything"
    assert VAR in edited, "the near-miss removed the binding; it is not a near miss"

    assert _derived_unbound({relative: tampered}) == UNBOUND, (
        "an edit that left the binding in place moved the ratchet's verdict, so "
        "the planted defect above proves only that the bytes changed"
    )


def test_the_ratchet_does_not_fire_on_the_current_tree() -> None:
    """SENSITIVITY, near-miss. A check that fails over a clean tree proves
    nothing about itself, and one that passes over a clean tree proves nothing
    either — so both halves are stated.

    This pairs with the planted defect above: together they show the ratchet
    discriminates rather than always-fires or always-passes.
    """
    assert _derived_unbound() == UNBOUND
