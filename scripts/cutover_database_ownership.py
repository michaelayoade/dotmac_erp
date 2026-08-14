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
Run it in a maintenance window after verifying a restorable backup; its
advisory lock coordinates this tool, not arbitrary application or operator DDL.

## Why it is plan-first and approval-gated

Ownership transfer is not reversible by re-running something; it rewrites who
controls production objects. So:

- the default is a dry run: print the plan, execute nothing;
- every invocation names the expected database, so a valid elevated credential
  cannot silently operate on the wrong database;
- execution needs `--execute`, the dry run's `--plan-sha256`, AND
  `--approve-owner`, naming whose objects move.
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
        python scripts/cutover_database_ownership.py \\
        --expected-database <db>                                    # dry run
    OWNERSHIP_DATABASE_URL=... python scripts/cutover_database_ownership.py \\
        --expected-database <db> --execute --approve-owner postgres \\
        --plan-sha256 <reviewed-token>

Exit codes: 0 plan clean or cutover complete, 1 refused (unapproved owner, or
residual non-owned objects afterwards), 2 usage/connection error.
"""

from __future__ import annotations

import argparse
import collections
import hmac
import os
from pathlib import Path
import sys

# Direct execution sets sys.path[0] to scripts/, while the documented operator
# command imports the shared runtime contract from app/. Match the privileged
# role bootstrap's entrypoint boundary.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import psycopg
from psycopg import sql

from app.migration_database_roles import (
    MIGRATION_EXECUTOR,
    MIGRATION_OWNERSHIP_SQL,
    OWNERSHIP_PLAN_SQL,
    OwnershipPlanRow,
    ownership_plan_sha256,
    unexpected_owners,
)

OWNERSHIP_URL_VAR = "OWNERSHIP_DATABASE_URL"
ADVISORY_LOCK_NAME = "dotmac_erp:database_ownership_cutover:v1"


def build_plan(conn: psycopg.Connection, target: str) -> list[OwnershipPlanRow]:
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


def summarise(plan: list[OwnershipPlanRow]) -> None:
    by_kind = collections.Counter(kind for kind, _, _, _ in plan)
    by_owner = collections.Counter(owner for _, owner, _, _ in plan)
    print(f"plan: {len(plan)} object(s) to transfer to {MIGRATION_EXECUTOR!r}")
    for kind, count in sorted(by_kind.items()):
        print(f"  {kind:9s} {count}")
    print("current owners:")
    for owner, count in sorted(by_owner.items()):
        print(f"  {owner:20s} {count}")


def _sha256(value: str) -> str:
    token = value.lower()
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        raise argparse.ArgumentTypeError(
            "plan token must be exactly 64 hexadecimal characters"
        )
    return token


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--expected-database",
        required=True,
        help="exact current_database() value; prevents a wrong-database cutover",
    )
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
    parser.add_argument(
        "--plan-sha256",
        type=_sha256,
        help="exact token emitted by the reviewed dry run; required with --execute",
    )
    return parser


def _validated_args() -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args()
    if args.execute and not args.plan_sha256:
        parser.error("--execute requires --plan-sha256 from a reviewed dry run")
    if not args.execute and args.plan_sha256:
        parser.error("--plan-sha256 is accepted only with --execute")
    return args


def main() -> int:
    args = _validated_args()

    url = os.environ.get(OWNERSHIP_URL_VAR, "").strip()
    if not url:
        print(
            f"{OWNERSHIP_URL_VAR} is not set. Ownership transfer needs superuser "
            "or membership of the owning role, which neither the application nor "
            "the migration executor holds — this step is separate on purpose.",
            file=sys.stderr,
        )
        return 2

    transferred_count: int | None = None
    try:
        # One transaction for the whole cutover: a failure part-way leaves
        # ownership exactly as it was, never half-transferred.
        with psycopg.connect(
            url.replace("postgresql+psycopg://", "postgresql://", 1), autocommit=False
        ) as conn:
            if args.execute:
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (ADVISORY_LOCK_NAME,),
                )
            else:
                conn.execute("SET TRANSACTION READ ONLY")

            database_row = conn.execute("SELECT current_database()").fetchone()
            assert database_row is not None
            current_database = str(database_row[0])
            if current_database != args.expected_database:
                print(
                    f"REFUSED: connected to {current_database!r}; expected database "
                    f"{args.expected_database!r}",
                    file=sys.stderr,
                )
                return 1

            plan = build_plan(conn, MIGRATION_EXECUTOR)
            plan_sha256 = ownership_plan_sha256(
                args.expected_database, MIGRATION_EXECUTOR, plan
            )
            if plan:
                summarise(plan)
            else:
                print(
                    f"nothing to do: every object is already owned by "
                    f"{MIGRATION_EXECUTOR!r}"
                )
            if plan and (args.show_statements or not args.execute):
                print("\nstatements:")
                for _, _, _, statement in plan:
                    print(f"  {statement};")
            print(f"PLAN_SHA256={plan_sha256}")
            sys.stdout.flush()

            approved = frozenset(args.approve_owner)
            owners = collections.Counter(owner for _, owner, _, _ in plan)
            surprises = unexpected_owners(owners, approved)

            if not args.execute:
                print(
                    "\nDRY RUN — nothing executed. Re-run the identical command "
                    "with --execute, --plan-sha256 set to the token above, and "
                    "--approve-owner naming each role above."
                )
                if surprises:
                    print("note: not yet approved: " + "; ".join(surprises))
                return 0

            if not hmac.compare_digest(plan_sha256, args.plan_sha256):
                print(
                    "REFUSED: current catalogue does not match the reviewed plan; "
                    "run the dry run again and review the changed target set",
                    file=sys.stderr,
                )
                return 1

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

            # Post-condition, checked with the migration preflight's own query
            # AS the migration executor and BEFORE commit. Running it as the
            # elevated operator reports every newly transferred object as
            # non-owned; committing first makes a refusal irreversible.
            conn.execute(
                sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(MIGRATION_EXECUTOR))
            )
            residual = {k: v for k, v in residual_counts(conn).items() if v}
            if residual:
                print(
                    f"REFUSED: {residual} still not owned by {MIGRATION_EXECUTOR!r} "
                    "after the transfer — migrations would still fail. Investigate "
                    "before deploying.",
                    file=sys.stderr,
                )
                conn.rollback()
                return 1
            transferred_count = len(plan)
    except psycopg.Error as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 2

    assert transferred_count is not None
    print(f"\ntransferred {transferred_count} object(s) to {MIGRATION_EXECUTOR!r}")
    print("post-check: the migration ownership inventory is empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
