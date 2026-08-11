"""A dry run must not create bank accounts — and that stopped being free.

Both statement importers create `BankAccount` rows via `ensure_bank_account`.
Zenith's call was UNCONDITIONAL. That was harmless only by accident: a dry run
skipped the final `db.commit()`, the session closed, and the pending INSERT
went out with the rollback. The write was always attempted; nothing ever
persisted it.

`batch_operation` removes the accident. It commits when it marks the run
COMPLETED — that commit is the point of the design, since a record that
vanishes with the failure it was recording is worse than no record. But it
commits the caller's transaction too. So an unguarded write on the dry-run path
now lands, and `--dry-run` silently starts provisioning accounts.

The fix is a guard in each importer. This test is what keeps the guard, because
the failure it prevents is invisible: the dry run still prints
"[DRY RUN] Would import statement" either way.

## What is checked

Every call to a known writing function must be conditional on `dry_run`, in
either of the two shapes people actually write:

* enclosed — `if dry_run: ... else: write`, or `if not dry_run: write`;
* early-exit — `if dry_run: continue` (or `return`/`break`), after which the
  rest of that block is unreachable in dry-run mode. Both importers guard
  `import_statement` this way, and a detector that only understood enclosure
  would push authors to restructure working code to satisfy it.

Polarity is NOT checked. `if not dry_run: return` followed by a write would
pass while being backwards — a false negative, and a deliberate one: polarity
analysis buys little here and a guard test that is itself subtly wrong is worse
than one with a stated blind spot. The property that matters, and the one that
was actually missing, is that the call is conditional on the mode at all.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

# The importers that both take `--dry-run` and can create rows.
IMPORTERS = ("import_uba_statements.py", "import_zenith_statements.py")

# Functions that WRITE. `find_bank_account` is deliberately absent: it is the
# read half, and the dry-run branch is supposed to call it.
WRITING_CALLS = frozenset({"ensure_bank_account", "import_statement"})


_TERMINATORS = (ast.Continue, ast.Break, ast.Return, ast.Raise)


def _tests_dry_run(node: ast.If) -> bool:
    return "dry_run" in {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}


def _collect(statements: list[ast.stmt], *, guarded: bool, out: list[str]) -> None:
    """Walk one statement list, tracking whether we are past a dry-run guard.

    `guarded` is inherited by nested blocks and additionally becomes true for
    everything following an `if dry_run: ... continue`-shaped early exit.
    """
    for statement in statements:
        if isinstance(statement, ast.If) and _tests_dry_run(statement):
            _collect(statement.body, guarded=True, out=out)
            _collect(statement.orelse, guarded=True, out=out)
            if statement.body and isinstance(statement.body[-1], _TERMINATORS):
                guarded = True
            continue

        # Nested blocks are walked separately below, so that a guard inside a
        # loop body is seen as a guard rather than being flattened away.
        blocks = [
            block
            for field in ("body", "orelse", "finalbody")
            if isinstance(block := getattr(statement, field, None), list)
            and block
            and isinstance(block[0], ast.stmt)
        ]
        nested = {id(node) for block in blocks for s in block for node in ast.walk(s)}

        if not guarded:
            for node in ast.walk(statement):
                if id(node) in nested or not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                if name in WRITING_CALLS:
                    out.append(f"{name} at line {node.lineno}")

        for block in blocks:
            _collect(block, guarded=guarded, out=out)


def _unguarded_writes(source: str) -> list[str]:
    """Names of writing calls reachable while `dry_run` is true."""
    out: list[str] = []
    _collect(ast.parse(source).body, guarded=False, out=out)
    return sorted(set(out))


@pytest.mark.parametrize("script", IMPORTERS)
def test_a_dry_run_cannot_reach_a_write(script: str) -> None:
    source = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
    offenders = _unguarded_writes(source)
    assert offenders == [], (
        f"{script} calls a writing function outside any `dry_run` guard. "
        "`batch_operation` commits on completion, so this write WILL persist "
        "during --dry-run:\n  " + "\n  ".join(offenders)
    )


def test_the_detector_fires_on_the_shape_zenith_actually_had() -> None:
    """Sensitivity proof, using the real pre-fix structure rather than a
    contrived one: the create sits at loop level, and only the import below it
    is guarded."""
    offenders = _unguarded_writes(
        "\n".join(
            [
                "def run(dry_run=False):",
                "    for account in accounts:",
                "        bank_account = ensure_bank_account(db, account=account)",
                "        if dry_run:",
                "            continue",
                "        service.import_statement(db=db)",
            ]
        )
    )
    assert any(o.startswith("ensure_bank_account") for o in offenders)


def test_the_detector_sees_through_loops_and_with_blocks() -> None:
    """The real scripts nest the write two levels deep, inside `with
    session_for_org(...)` and then a `for account in ...`. A detector that only
    looked at top-level statements would call both importers clean while
    reading nothing."""
    offenders = _unguarded_writes(
        "\n".join(
            [
                "def run(dry_run=False):",
                "    with session_for_org(org) as db:",
                "        for account in accounts:",
                "            ensure_bank_account(db)",
            ]
        )
    )
    assert offenders == ["ensure_bank_account at line 4"]


def test_the_detector_does_not_fire_on_a_guarded_write() -> None:
    """Specificity: both guard polarities must pass, or the test would push
    authors toward one arbitrary spelling."""
    assert (
        _unguarded_writes(
            "\n".join(
                [
                    "def run(dry_run=False):",
                    "    if dry_run:",
                    "        account = find_bank_account(db)",
                    "    else:",
                    "        account = ensure_bank_account(db)",
                    "    if not dry_run:",
                    "        service.import_statement(db=db)",
                ]
            )
        )
        == []
    )
