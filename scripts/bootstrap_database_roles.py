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

Exit codes: 0 satisfied (or created), 1 drift found without `--repair`,
2 usage/connection error.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg import sql

#: The exact contract, as `(rolbypassrls, rolsuper)`.
#:
#: `app_admin` bypasses RLS because offline and migration work has to see every
#: tenant's rows; an `app_admin` that cannot turns maintenance into a silent
#: zero-row success. It is NOT a superuser — that would hand cluster-wide
#: authority (DDL on any database, role creation, `COPY PROGRAM`) to something
#: whose only real requirement is reading past RLS.
#:
#: The two online roles must have neither. A superuser bypasses RLS regardless
#: of `rolbypassrls`, so both attributes are checked; reading only the flag
#: would certify `app_user SUPERUSER NOBYPASSRLS` as isolated when it is not.
ROLE_CONTRACT: dict[str, tuple[bool, bool]] = {
    "app_admin": (True, False),
    "app_user": (False, False),
    "platform_api": (False, False),
}

BOOTSTRAP_URL_VAR = "BOOTSTRAP_DATABASE_URL"


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
    drift = 0

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
        drift += 1
        if not repair:
            print(
                f"DRIFT: {role} is {have}, contract requires {wanted}. "
                "Re-run with --repair to correct it — this is opt-in because "
                "silently rewriting cluster access is not a fix.",
                file=sys.stderr,
            )
            continue
        if dry_run:
            print(f"would repair: {role} {have} -> {wanted}")
        else:
            conn.execute(
                sql.SQL("ALTER ROLE {} {}").format(identifier, sql.SQL(wanted))
            )
            print(f"repaired: {role} {have} -> {wanted}")
            drift -= 1

    return 1 if drift else 0


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
    args = parser.parse_args()

    url = os.environ.get(BOOTSTRAP_URL_VAR, "").strip()
    if not url:
        print(
            f"{BOOTSTRAP_URL_VAR} is not set. This step is deliberately separate "
            "from the application's own connection strings: it needs superuser "
            "or CREATEROLE, which ordinary migrations must never hold.",
            file=sys.stderr,
        )
        return 2

    try:
        with psycopg.connect(url, autocommit=not args.dry_run) as conn:
            return bootstrap(conn, dry_run=args.dry_run, repair=args.repair)
    except psycopg.Error as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
