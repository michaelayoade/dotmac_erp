#!/usr/bin/env python3
"""Create or adopt the three Dotmac database roles. EXPLICITLY PRIVILEGED.

`app_admin`, `app_user` and `platform_api` are the roles every Dotmac module
grants to. Something has to create them, and `CREATE ROLE` needs superuser or
`CREATEROLE` — privileges ordinary application migrations must never assume.

## Why this is a separate, named step

Two paths were considered (decision, 2026-08-14):

- **adopt-only migrations** — never create a role, only verify. Rejected: it
  strands every NEW installation, because nothing in the deploy path would ever
  create the roles and a fresh cluster could never satisfy the prerequisite.
- **a one-time, explicitly elevated bootstrap owner** — chosen. The role setup
  is built once, here, by an operator who supplies elevated credentials on
  purpose, and ordinary `alembic upgrade` keeps running as unprivileged
  `app_admin` and fails closed when the roles are absent.

The elevation is therefore a deliberate operator action with its own connection
string, not an implicit escalation hidden inside a migration.

## What it does NOT do

- **Never sets a password.** Operators set those out of band; a password in a
  repo, a log or a shell history is a credential leak, and this script prints
  role names only.
- **Never grants object privileges.** Migrations own grants, because they own
  the objects. This creates identities, nothing more.
- **Never repairs a wrong-shaped existing role by default.** An `app_user` that
  is already SUPERUSER is a security finding, not a typo: `--repair` will fix it
  and it is opt-in so nobody silently rewrites cluster access.

## Usage

    BOOTSTRAP_DATABASE_URL=postgresql://postgres@host/db \\
        python scripts/bootstrap_database_roles.py [--dry-run] [--repair]

Deploy preflight uses the same verifier without elevated credentials:

    MIGRATION_DATABASE_URL=postgresql://app_admin@host/db python \
        scripts/bootstrap_database_roles.py --verify-only

Exit codes: 0 satisfied (or created), 1 contract drift,
2 usage/connection error.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

# Direct execution sets ``sys.path[0]`` to ``scripts/`` rather than the
# repository root. Deploy and CI intentionally use the documented
# ``python scripts/bootstrap_database_roles.py`` entrypoint, so install the
# root before importing the shared runtime contract.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import psycopg
from psycopg import sql

from app.migration_database_roles import (
    MIGRATION_OWNERSHIP_SQL,
    ROLE_CONTRACT,
    migration_executor_violations,
    migration_ownership_violations,
    role_contract_violations,
)

BOOTSTRAP_URL_VAR = "BOOTSTRAP_DATABASE_URL"
MIGRATION_URL_VAR = "MIGRATION_DATABASE_URL"


def _attributes(bypassrls: bool, superuser: bool) -> str:
    return (
        f"{'BYPASSRLS' if bypassrls else 'NOBYPASSRLS'} "
        f"{'SUPERUSER' if superuser else 'NOSUPERUSER'}"
    )


def _observe(conn: psycopg.Connection) -> dict[str, tuple[bool, bool]]:
    rows = conn.execute(
        "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles WHERE rolname = ANY(%s)",
        (list(ROLE_CONTRACT),),
    ).fetchall()
    return {str(r[0]): (bool(r[1]), bool(r[2])) for r in rows}


def bootstrap(conn: psycopg.Connection, *, dry_run: bool, repair: bool) -> int:
    observed = _observe(conn)

    wrong_existing = [
        violation
        for violation in role_contract_violations(observed)
        if not violation.endswith("is missing")
    ]
    if wrong_existing and not repair:
        for violation in wrong_existing:
            print(
                f"DRIFT: {violation}. Re-run with --repair to correct it — "
                "this is opt-in because silently rewriting cluster access is "
                "not a fix.",
                file=sys.stderr,
            )
        return 1

    for role, (want_bypass, want_super) in ROLE_CONTRACT.items():
        identifier = sql.Identifier(role)
        wanted = _attributes(want_bypass, want_super)

        if role not in observed:
            # LOGIN so the role can be connected as; no password, by design.
            statement = sql.SQL("CREATE ROLE {} LOGIN {}").format(
                identifier, sql.SQL(wanted)
            )
            if dry_run:
                print(f"would create: {role} LOGIN {wanted}")
            else:
                conn.execute(statement)
                print(f"created: {role} LOGIN {wanted}")
            continue

        if observed[role] == (want_bypass, want_super):
            print(f"adopted: {role} already {wanted}")
            continue

        have = _attributes(*observed[role])
        if dry_run:
            print(f"would repair: {role} {have} -> {wanted}")
        else:
            conn.execute(
                sql.SQL("ALTER ROLE {} {}").format(identifier, sql.SQL(wanted))
            )
            print(f"repaired: {role} {have} -> {wanted}")

    return 0


def verify_migration_connection(conn: psycopg.Connection) -> int:
    current_user = str(conn.execute("SELECT current_user").fetchone()[0])
    observed = _observe(conn)
    ownership_rows = conn.execute(MIGRATION_OWNERSHIP_SQL).fetchall()
    non_owned_counts = {str(row[0]): int(row[1]) for row in ownership_rows}
    violations = (
        *migration_executor_violations(current_user, observed),
        *migration_ownership_violations(non_owned_counts),
    )
    for violation in violations:
        print(f"MIGRATION CONTRACT: {violation}", file=sys.stderr)
    return 1 if violations else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change; execute nothing",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="correct an existing role whose attributes violate the contract",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify roles and the app_admin executor; execute no DDL",
    )
    args = parser.parse_args()

    if args.verify_only and (args.dry_run or args.repair):
        parser.error("--verify-only cannot be combined with --dry-run or --repair")

    url_var = MIGRATION_URL_VAR if args.verify_only else BOOTSTRAP_URL_VAR
    url = os.environ.get(url_var, "").strip()
    if not url:
        print(
            f"{url_var} is not set. Bootstrap and migration identities are "
            "deliberately separate from the application's connection string.",
            file=sys.stderr,
        )
        return 2

    try:
        connect_url = url.replace("postgresql+psycopg://", "postgresql://", 1)
        with psycopg.connect(connect_url, autocommit=False) as conn:
            if args.verify_only:
                return verify_migration_connection(conn)
            return bootstrap(conn, dry_run=args.dry_run, repair=args.repair)
    except psycopg.Error as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
