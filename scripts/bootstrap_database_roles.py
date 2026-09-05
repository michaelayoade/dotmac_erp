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

Bind the verification to the database it was authorised for — otherwise a run
aimed at the wrong cluster satisfies every other check and says so only as an
`UNVERIFIED` line:

    MIGRATION_EXPECTED_DATABASE=dotmac_erp \\
    MIGRATION_DATABASE_URL=postgresql://app_admin@host/db python \\
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
    EXPECTED_DATABASE_VAR,
    MIGRATION_OWNERSHIP_SQL,
    NON_ESCALATING_ROLES,
    RELAY_DISPATCHER_CONTRACT,
    ROLE_CONTRACT,
    ROLE_ESCALATION_SQL,
    database_identity_violations,
    migration_executor_violations,
    migration_ownership_violations,
    relay_dispatcher_violations,
    role_contract_violations,
    role_escalation_violations,
    unverified_database_identity_notice,
)

BOOTSTRAP_URL_VAR = "BOOTSTRAP_DATABASE_URL"
MIGRATION_URL_VAR = "MIGRATION_DATABASE_URL"


def _attributes(bypassrls: bool, superuser: bool) -> str:
    return (
        f"{'BYPASSRLS' if bypassrls else 'NOBYPASSRLS'} "
        f"{'SUPERUSER' if superuser else 'NOSUPERUSER'}"
    )


#: Every role this script creates or adopts: the three module roles, plus the
#: relay's two drain identities. They are observed together because a partial
#: bootstrap is the failure this script exists to prevent — a cluster with
#: `app_user` but no `outbox_dispatcher` fails its first relay migration
#: instead of its first request, which is much later and much less obvious.
_ALL_CONTRACTS: dict[str, tuple[bool, bool]] = {
    **ROLE_CONTRACT,
    **RELAY_DISPATCHER_CONTRACT,
}


def _observe(conn: psycopg.Connection) -> dict[str, tuple[bool, bool]]:
    rows = conn.execute(
        "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles WHERE rolname = ANY(%s)",
        (list(_ALL_CONTRACTS),),
    ).fetchall()
    return {str(r[0]): (bool(r[1]), bool(r[2])) for r in rows}


def bootstrap(conn: psycopg.Connection, *, dry_run: bool, repair: bool) -> int:
    observed = _observe(conn)

    wrong_existing = [
        violation
        for violation in (
            *role_contract_violations(observed),
            *relay_dispatcher_violations(observed),
        )
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

    for role, (want_bypass, want_super) in _ALL_CONTRACTS.items():
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


def _escalation_edges(
    conn: psycopg.Connection,
) -> list[tuple[str, str, bool, bool, bool]]:
    """Flatten the live role graph into (subject, reachable privileged role).

    Read from `pg_roles` and `pg_auth_members`, both world-readable, so this
    answers from evidence the NOSUPERUSER preflight connection can actually see.
    """
    rows = conn.execute(
        ROLE_ESCALATION_SQL, {"subjects": sorted(NON_ESCALATING_ROLES)}
    ).fetchall()
    return [
        (str(row[0]), str(row[1]), bool(row[2]), bool(row[3]), bool(row[4]))
        for row in rows
    ]


def verify_migration_connection(conn: psycopg.Connection) -> int:
    current_user = str(conn.execute("SELECT current_user").fetchone()[0])
    observed_database = str(conn.execute("SELECT current_database()").fetchone()[0])
    expected_database = os.environ.get(EXPECTED_DATABASE_VAR, "").strip() or None
    observed = _observe(conn)
    ownership_rows = conn.execute(MIGRATION_OWNERSHIP_SQL).fetchall()
    non_owned_counts = {str(row[0]): int(row[1]) for row in ownership_rows}
    violations = (
        *migration_executor_violations(current_user, observed),
        *migration_ownership_violations(non_owned_counts),
        # Removing the migration DSN from a runtime service is undone by a role
        # graph that lets the runtime role BECOME the migration role. The graph
        # is checked here, on the same connection, because a credential
        # separation nobody re-derives from the live catalogue is a claim about
        # an env file rather than about the database.
        *role_escalation_violations(_escalation_edges(conn)),
        # WHERE the connection landed. Everything above is satisfiable by a
        # different, correctly shaped cluster.
        *database_identity_violations(observed_database, expected_database),
    )
    notice = unverified_database_identity_notice(
        observed_database, expected_database, EXPECTED_DATABASE_VAR
    )
    if notice is not None:
        print(f"MIGRATION CONTRACT: {notice}", file=sys.stderr)
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
        help=(
            "verify roles, the app_admin executor, that no runtime role can "
            "SET ROLE into a privileged one, and the database identity; "
            "execute no DDL"
        ),
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
