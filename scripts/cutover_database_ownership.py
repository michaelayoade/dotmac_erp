#!/usr/bin/env python3
"""Transfer ownership of an existing estate to `app_admin`. EXPLICITLY PRIVILEGED.

Creating the three database roles does not let `app_admin` migrate anything.
**Ownership is a separate fact.** A role owns nothing it did not create, and
only an object's owner (or a superuser) may `ALTER` it — so a database built by
`postgres`, which is the normal history for anything predating the unprivileged
migration model, leaves `app_admin` able to connect and unable to work.

Measured on Seabone: the ERP database is owned by `postgres` and **840 objects
are postgres-owned**. Running the role bootstrap there and then deploying would
satisfy every role check and fail at the first `ALTER`, half applied.

This script is the missing step, and it is deliberately not part of any deploy.

## Why it is plan-first and approval-gated

Ownership transfer is not reversible by re-running something; it rewrites who
controls production objects. So:

- the default is `--dry-run`: print the plan, execute nothing;
- execution needs `--execute` AND `--approve-owner`, naming whose objects move.
  A blanket "everything not owned by app_admin" sweep would silently capture an
  integration role, or a departed engineer's personal role, and hand its objects
  to the migration executor. Whose estate this is must be stated, not inferred;
- everything runs in ONE transaction, so a failure part-way leaves ownership
  exactly as it was rather than half-transferred;
- afterwards it re-runs the executor contract's own inventory and refuses to
  report success unless that comes back empty.

## What it does not touch

Extension-owned objects, `pg_catalog`/`information_schema`, and schemas owned by
`pg_database_owner` — the same exclusions the migration preflight applies, so
this repairs exactly the set that preflight refuses. Indexes, constraints and
triggers follow their table and need no statement of their own.

Grants are NOT changed. Ownership and privilege are different things: `app_user`
keeps precisely the grants it had, and this script never widens anyone's access.

## Usage

    OWNERSHIP_DATABASE_URL=postgresql://postgres@host/db \\
        python scripts/cutover_database_ownership.py                # dry run
    OWNERSHIP_DATABASE_URL=... python scripts/cutover_database_ownership.py \\
        --execute --approve-owner postgres

Exit codes: 0 plan clean or cutover complete, 1 refused (unapproved owner, or
residual non-owned objects afterwards), 2 usage/connection error.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys

import psycopg

from app.migration_database_roles import (
    MIGRATION_EXECUTOR,
    MIGRATION_OWNERSHIP_SQL,
    OWNERSHIP_PLAN_SQL,
    unexpected_owners,
)

OWNERSHIP_URL_VAR = "OWNERSHIP_DATABASE_URL"


def build_plan(
    conn: psycopg.Connection, target: str
) -> list[tuple[str, str, str, str]]:
    """`(object_kind, current_owner, object_name, statement)`, PostgreSQL-rendered."""
    return [
        (str(r[0]), str(r[1]), str(r[2]), str(r[3]))
        for r in conn.execute(OWNERSHIP_PLAN_SQL, {"target": target}).fetchall()
    ]


def residual_counts(conn: psycopg.Connection) -> dict[str, int]:
    """The executor contract's OWN inventory, run as the post-condition."""
    return {
        str(r[0]): int(r[1]) for r in conn.execute(MIGRATION_OWNERSHIP_SQL).fetchall()
    }


def summarise(plan: list[tuple[str, str, str, str]]) -> None:
    by_kind = collections.Counter(kind for kind, _, _, _ in plan)
    by_owner = collections.Counter(owner for _, owner, _, _ in plan)
    print(f"plan: {len(plan)} object(s) to transfer to {MIGRATION_EXECUTOR!r}")
    for kind, count in sorted(by_kind.items()):
        print(f"  {kind:9s} {count}")
    print("current owners:")
    for owner, count in sorted(by_owner.items()):
        print(f"  {owner:20s} {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the transfer; without it the plan is printed and nothing runs",
    )
    parser.add_argument(
        "--approve-owner",
        action="append",
        default=[],
        metavar="ROLE",
        help="a role whose objects may be transferred; repeatable, required with --execute",
    )
    parser.add_argument(
        "--show-statements",
        action="store_true",
        help="print every ALTER statement rather than a summary",
    )
    args = parser.parse_args()

    url = os.environ.get(OWNERSHIP_URL_VAR, "").strip()
    if not url:
        print(
            f"{OWNERSHIP_URL_VAR} is not set. Ownership transfer needs superuser "
            "or membership of the owning role, which neither the application nor "
            "the migration executor holds — this step is separate on purpose.",
            file=sys.stderr,
        )
        return 2

    try:
        # One transaction for the whole cutover: a failure part-way leaves
        # ownership exactly as it was, never half-transferred.
        with psycopg.connect(
            url.replace("postgresql+psycopg://", "postgresql://", 1), autocommit=False
        ) as conn:
            plan = build_plan(conn, MIGRATION_EXECUTOR)
            if not plan:
                print(
                    f"nothing to do: every object is already owned by "
                    f"{MIGRATION_EXECUTOR!r}"
                )
                return 0

            summarise(plan)
            if args.show_statements or not args.execute:
                print("\nstatements:")
                for _, _, _, statement in plan:
                    print(f"  {statement};")

            approved = frozenset(args.approve_owner)
            owners = collections.Counter(owner for _, owner, _, _ in plan)
            surprises = unexpected_owners(owners, approved)

            if not args.execute:
                print(
                    "\nDRY RUN — nothing executed. Re-run with --execute and "
                    "--approve-owner naming each role above."
                )
                if surprises:
                    print("note: not yet approved: " + "; ".join(surprises))
                return 0

            if surprises:
                print(
                    "REFUSED: "
                    + "; ".join(surprises)
                    + ". Name every owner with --approve-owner, or investigate "
                    "why an unexpected role owns objects in this database.",
                    file=sys.stderr,
                )
                return 1

            for _, _, _, statement in plan:
                conn.execute(statement)  # noqa: S608 — rendered by PostgreSQL
            conn.commit()
            print(f"\ntransferred {len(plan)} object(s) to {MIGRATION_EXECUTOR!r}")

            # Post-condition, checked with the migration preflight's own query
            # rather than by trusting the plan we just ran.
            residual = {k: v for k, v in residual_counts(conn).items() if v}
            if residual:
                print(
                    f"REFUSED: {residual} still not owned by {MIGRATION_EXECUTOR!r} "
                    "after the transfer — migrations would still fail. Investigate "
                    "before deploying.",
                    file=sys.stderr,
                )
                return 1
            print("post-check: the migration ownership inventory is empty")
            return 0
    except psycopg.Error as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
