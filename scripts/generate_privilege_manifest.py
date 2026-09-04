#!/usr/bin/env python3
"""Generate the identity-cutover manifest and its SQL. OFFLINE, NO DATABASE.

This is the thin adapter over `app.privilege_manifest`. It reads the frozen
census, builds the manifest, renders two SQL files, and either writes them or
byte-compares them (`--check`, which is what `make privilege-manifest-check`
and CI run).

It never connects to anything. The census is a committed artefact captured
read-only from production on 2026-09-04; re-taking it is a separate,
deliberate act, not something a generator does.

## Why TWO SQL files

`scripts/erp_identity_cutover_grants.sql` is the routine half: 1,712 relation
privileges, 3 sequence privileges and the 37 observed schema USAGE grants.
Mechanical, reviewable in bulk, safe to apply as a unit.

`scripts/erp_identity_cutover_review_required.sql` is everything exceptional,
which must NOT be folded into that sweep:

* the five `SECURITY DEFINER` EXECUTE grants -- each executes as `app_admin`,
  a BYPASSRLS role, so EXECUTE is an escalation surface, not a row. Their
  dispositions are recorded in
  `docs/architecture/erp-runtime-identity-cutover.md`;
* `mod_files.platform_stored_files`, a CONTROL-PLANE relation ADR-0023
  requires to be REVOKEd from the tenant application role. DENIED: its four
  rows render as comments and are never executed.

The five derived schema-USAGE rows that used to sit here were SETTLED on
2026-09-04 -- all five schemas returned `legacy=True, app_user=True`, so the
GRANT was a no-op -- and are removed, with their origins kept in
`SETTLED_SCHEMA_USAGE`.

The split is PERMANENT, not staging: a 1,700-line file with six escalation
decisions buried in it gets skimmed.

## Usage

    python scripts/generate_privilege_manifest.py            # write
    python scripts/generate_privilege_manifest.py --check    # verify only

Exit codes: 0 up to date (or written), 1 drift, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Direct execution sets ``sys.path[0]`` to ``scripts/`` rather than the
# repository root, and the documented entrypoint is
# ``python scripts/generate_privilege_manifest.py``. Same preamble as
# scripts/bootstrap_database_roles.py, for the same reason.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.privilege_manifest import (  # noqa: E402
    REVIEW_SQL_TITLE,
    ROUTINE_SQL_TITLE,
    baseline_violations,
    manifest_from_census,
    manifest_to_json,
    render_grant_sql,
)

CENSUS_PATH = REPO_ROOT / "docs/inventories/erp-privilege-census-2026-09-04.json"
MANIFEST_PATH = (
    REPO_ROOT / "docs/inventories/erp-identity-cutover-manifest-2026-09-04.json"
)
ROUTINE_SQL_PATH = REPO_ROOT / "scripts/erp_identity_cutover_grants.sql"
REVIEW_SQL_PATH = REPO_ROOT / "scripts/erp_identity_cutover_review_required.sql"


def build() -> dict[Path, str]:
    """The three generated artefacts, keyed by path. Deterministic."""
    census = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    manifest = manifest_from_census(census)
    drift = baseline_violations(manifest)
    if drift:
        raise SystemExit(
            "the census no longer matches BASELINE_TOTALS:\n  " + "\n  ".join(drift)
        )
    return {
        MANIFEST_PATH: manifest_to_json(manifest),
        ROUTINE_SQL_PATH: render_grant_sql(manifest.routine(), ROUTINE_SQL_TITLE),
        REVIEW_SQL_PATH: render_grant_sql(manifest.exceptional(), REVIEW_SQL_TITLE),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed artefacts match, write nothing",
    )
    args = parser.parse_args(argv)

    if not CENSUS_PATH.exists():
        print(f"census not found: {CENSUS_PATH}", file=sys.stderr)  # noqa: T201
        return 2

    artefacts = build()
    drifted: list[Path] = []
    for path, content in artefacts.items():
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing == content:
            continue
        if args.check:
            drifted.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")  # noqa: T201

    if args.check and drifted:
        print(  # noqa: T201
            "GENERATED ARTEFACT DRIFT -- these files do not match what the "
            "census generates:",
            file=sys.stderr,
        )
        for path in drifted:
            print(f"  {path.relative_to(REPO_ROOT)}", file=sys.stderr)  # noqa: T201
        print(  # noqa: T201
            "\nThey are GENERATED, never hand-edited. Run "
            "`python scripts/generate_privilege_manifest.py` and review the "
            "diff.",
            file=sys.stderr,
        )
        return 1
    if args.check:
        print("privilege manifest and SQL are up to date")  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
