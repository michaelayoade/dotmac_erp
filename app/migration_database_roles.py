"""Pure contract for database roles and the Alembic executor.

The privileged bootstrap and the unprivileged deploy preflight share this
decision code. The migration keeps its own point-in-time copy, pinned to this
module by an architecture test, so an applied revision cannot change meaning
when runtime code evolves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Final

RolePosture = tuple[bool, bool]

#: Exact `(rolbypassrls, rolsuper)` contract.
ROLE_CONTRACT: Final[dict[str, RolePosture]] = {
    "app_admin": (True, False),
    "app_user": (False, False),
    "platform_api": (False, False),
}
MIGRATION_EXECUTOR: Final[str] = "app_admin"
OWNERSHIP_PLAN_VERSION: Final[int] = 1
OwnershipPlanRow = tuple[str, str, str, str]

# Non-extension objects that Alembic may need to alter must be owned by the
# migration executor. Database ownership alone does not transfer ownership of
# existing schemas, relations, enums/domains, or routines. Both the deploy
# preflight and Alembic environment execute this exact read-only inventory.
MIGRATION_OWNERSHIP_SQL: Final[str] = """
WITH non_owned(object_kind) AS (
    SELECT 'database'
    FROM pg_database AS database_catalog
    WHERE database_catalog.datname = current_database()
      AND pg_get_userbyid(database_catalog.datdba) <> current_user

    UNION ALL

    SELECT 'schema'
    FROM pg_namespace AS namespace_catalog
    WHERE namespace_catalog.nspname !~ '^(pg_|information_schema)'
      AND pg_get_userbyid(namespace_catalog.nspowner) <> current_user
      AND pg_get_userbyid(namespace_catalog.nspowner) <> 'pg_database_owner'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_extension AS extension_catalog
          WHERE extension_catalog.extnamespace = namespace_catalog.oid
      )

    UNION ALL

    SELECT 'relation'
    FROM pg_class AS relation_catalog
    JOIN pg_namespace AS namespace_catalog
      ON namespace_catalog.oid = relation_catalog.relnamespace
    WHERE namespace_catalog.nspname !~ '^(pg_|information_schema)'
      AND relation_catalog.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
      AND pg_get_userbyid(relation_catalog.relowner) <> current_user
      AND NOT EXISTS (
          SELECT 1
          FROM pg_depend AS dependency_catalog
          WHERE dependency_catalog.classid = 'pg_class'::regclass
            AND dependency_catalog.objid = relation_catalog.oid
            AND dependency_catalog.deptype = 'e'
      )

    UNION ALL

    SELECT 'type'
    FROM pg_type AS type_catalog
    JOIN pg_namespace AS namespace_catalog
      ON namespace_catalog.oid = type_catalog.typnamespace
    WHERE namespace_catalog.nspname !~ '^(pg_|information_schema)'
      AND type_catalog.typtype IN ('d', 'e')
      AND pg_get_userbyid(type_catalog.typowner) <> current_user
      AND NOT EXISTS (
          SELECT 1
          FROM pg_depend AS dependency_catalog
          WHERE dependency_catalog.classid = 'pg_type'::regclass
            AND dependency_catalog.objid = type_catalog.oid
            AND dependency_catalog.deptype = 'e'
      )

    UNION ALL

    SELECT 'routine'
    FROM pg_proc AS routine_catalog
    JOIN pg_namespace AS namespace_catalog
      ON namespace_catalog.oid = routine_catalog.pronamespace
    WHERE namespace_catalog.nspname !~ '^(pg_|information_schema)'
      AND pg_get_userbyid(routine_catalog.proowner) <> current_user
      AND NOT EXISTS (
          SELECT 1
          FROM pg_depend AS dependency_catalog
          WHERE dependency_catalog.classid = 'pg_proc'::regclass
            AND dependency_catalog.objid = routine_catalog.oid
            AND dependency_catalog.deptype = 'e'
      )
)
SELECT object_kind, count(*)::bigint
FROM non_owned
GROUP BY object_kind
ORDER BY object_kind
"""


#: The cutover plan: the SAME predicates as `MIGRATION_OWNERSHIP_SQL`, returning
#: one executable `ALTER … OWNER TO` per object instead of a count.
#:
#: Two properties make this safe to run against a production estate.
#:
#: **The statement text is built by PostgreSQL, not by Python.** `::regclass`
#: and `::regprocedure` render a correctly schema-qualified, correctly quoted
#: identifier for every name — including the ones with capitals, spaces or
#: reserved words that hand-rolled quoting gets wrong. `format(%I)` quotes the
#: target role the same way.
#:
#: **The exclusions are the preflight's exclusions.** Extension-owned objects
#: (`pg_depend.deptype = 'e'`), system schemas and `pg_database_owner` schemas
#: are skipped here exactly as they are skipped there, so the set this cutover
#: repairs is by construction the set the executor contract refuses. The
#: post-condition is asserted for real: after a full run,
#: `MIGRATION_OWNERSHIP_SQL` must return no rows.
#:
#: Indexes, constraints and triggers deliberately have no entry — ownership
#: follows their table. Partitions appear individually (`relkind = 'r'`) because
#: `ALTER TABLE … OWNER` on a partitioned parent does NOT cascade to them.
OWNERSHIP_PLAN_SQL: Final[str] = """
SELECT 'database' AS object_kind,
       pg_get_userbyid(database_catalog.datdba) AS current_owner,
       quote_ident(database_catalog.datname) AS object_name,
       format('ALTER DATABASE %%I OWNER TO %%I',
              database_catalog.datname, %(target)s::text) AS statement,
       10 AS execution_order
FROM pg_database AS database_catalog
WHERE database_catalog.datname = current_database()
  AND pg_get_userbyid(database_catalog.datdba) <> %(target)s::text

UNION ALL

SELECT 'schema',
       pg_get_userbyid(namespace_catalog.nspowner),
       quote_ident(namespace_catalog.nspname),
       format('ALTER SCHEMA %%I OWNER TO %%I',
              namespace_catalog.nspname, %(target)s::text),
       20
FROM pg_namespace AS namespace_catalog
WHERE namespace_catalog.nspname !~ '^(pg_|information_schema)'
  AND pg_get_userbyid(namespace_catalog.nspowner) <> %(target)s::text
  AND pg_get_userbyid(namespace_catalog.nspowner) <> 'pg_database_owner'
  AND NOT EXISTS (
      SELECT 1 FROM pg_extension AS extension_catalog
      WHERE extension_catalog.extnamespace = namespace_catalog.oid
  )

UNION ALL

SELECT 'relation',
       pg_get_userbyid(relation_catalog.relowner),
       relation_catalog.oid::regclass::text,
       format('ALTER %%s %%s OWNER TO %%I',
              CASE relation_catalog.relkind
                  WHEN 'S' THEN 'SEQUENCE'
                  WHEN 'v' THEN 'VIEW'
                  WHEN 'm' THEN 'MATERIALIZED VIEW'
                  WHEN 'f' THEN 'FOREIGN TABLE'
                  ELSE 'TABLE'
              END,
              relation_catalog.oid::regclass::text, %(target)s::text),
       CASE relation_catalog.relkind
           WHEN 'r' THEN 40
           WHEN 'p' THEN 40
           WHEN 'f' THEN 40
           WHEN 'S' THEN 50
           WHEN 'v' THEN 60
           WHEN 'm' THEN 60
       END
FROM pg_class AS relation_catalog
JOIN pg_namespace AS namespace_catalog
  ON namespace_catalog.oid = relation_catalog.relnamespace
WHERE namespace_catalog.nspname !~ '^(pg_|information_schema)'
  AND relation_catalog.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
  AND pg_get_userbyid(relation_catalog.relowner) <> %(target)s::text
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend AS dependency_catalog
      WHERE dependency_catalog.classid = 'pg_class'::regclass
        AND dependency_catalog.objid = relation_catalog.oid
        AND dependency_catalog.deptype = 'e'
  )

UNION ALL

SELECT 'type',
       pg_get_userbyid(type_catalog.typowner),
       type_catalog.oid::regtype::text,
       format('ALTER %%s %%s OWNER TO %%I',
              CASE type_catalog.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END,
              type_catalog.oid::regtype::text, %(target)s::text),
       30
FROM pg_type AS type_catalog
JOIN pg_namespace AS namespace_catalog
  ON namespace_catalog.oid = type_catalog.typnamespace
WHERE namespace_catalog.nspname !~ '^(pg_|information_schema)'
  AND type_catalog.typtype IN ('d', 'e')
  AND pg_get_userbyid(type_catalog.typowner) <> %(target)s::text
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend AS dependency_catalog
      WHERE dependency_catalog.classid = 'pg_type'::regclass
        AND dependency_catalog.objid = type_catalog.oid
        AND dependency_catalog.deptype = 'e'
  )

UNION ALL

SELECT 'routine',
       pg_get_userbyid(routine_catalog.proowner),
       routine_catalog.oid::regprocedure::text,
       format('ALTER %%s %%s OWNER TO %%I',
              CASE routine_catalog.prokind WHEN 'p' THEN 'PROCEDURE'
                                           WHEN 'a' THEN 'AGGREGATE'
                                           ELSE 'FUNCTION' END,
              routine_catalog.oid::regprocedure::text, %(target)s::text),
       70
FROM pg_proc AS routine_catalog
JOIN pg_namespace AS namespace_catalog
  ON namespace_catalog.oid = routine_catalog.pronamespace
WHERE namespace_catalog.nspname !~ '^(pg_|information_schema)'
  AND pg_get_userbyid(routine_catalog.proowner) <> %(target)s::text
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend AS dependency_catalog
      WHERE dependency_catalog.classid = 'pg_proc'::regclass
        AND dependency_catalog.objid = routine_catalog.oid
        AND dependency_catalog.deptype = 'e'
  )

ORDER BY execution_order, object_kind, object_name
"""


def ownership_plan_sha256(
    expected_database: str,
    target: str,
    plan: Sequence[OwnershipPlanRow],
) -> str:
    """Bind an operator approval to one database and exact ordered target set."""
    payload = {
        "contract_version": OWNERSHIP_PLAN_VERSION,
        "expected_database": expected_database,
        "target": target,
        "plan": [list(row) for row in plan],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def unexpected_owners(
    plan_owners: Mapping[str, int],
    approved: frozenset[str],
) -> tuple[str, ...]:
    """Owners in the plan that the operator did not approve.

    A cutover is reviewed by knowing WHOSE objects move. Sweeping everything
    "not owned by app_admin" would silently capture a role nobody expected —
    an integration user, a departed engineer's personal role — and hand its
    objects to the migration executor. So the approved set is an input, and
    anything outside it stops the run.
    """
    return tuple(
        f"{count} object(s) owned by unapproved role {owner!r}"
        for owner, count in sorted(plan_owners.items())
        if owner not in approved
    )


def posture(bypassrls: bool, superuser: bool) -> str:
    return (
        f"{'BYPASSRLS' if bypassrls else 'NOBYPASSRLS'}/"
        f"{'SUPERUSER' if superuser else 'NOSUPERUSER'}"
    )


def role_contract_violations(
    observed: Mapping[str, RolePosture],
) -> tuple[str, ...]:
    violations = [
        f"database role {role!r} is missing"
        for role in sorted(set(ROLE_CONTRACT) - set(observed))
    ]
    for role, expected in ROLE_CONTRACT.items():
        actual = observed.get(role)
        if actual is not None and actual != expected:
            violations.append(
                f"{role} is {posture(*actual)}, contract requires {posture(*expected)}"
            )
    return tuple(violations)


def migration_executor_violations(
    current_user: str,
    observed: Mapping[str, RolePosture],
) -> tuple[str, ...]:
    violations = list(role_contract_violations(observed))
    if current_user != MIGRATION_EXECUTOR:
        violations.append(
            f"migration connection is {current_user!r}, required "
            f"{MIGRATION_EXECUTOR!r}; application and bootstrap credentials "
            "may not execute Alembic"
        )
    return tuple(violations)


def migration_ownership_violations(
    non_owned_counts: Mapping[str, int],
) -> tuple[str, ...]:
    return tuple(
        f"{count} non-extension {object_kind} object(s) are not owned by "
        f"{MIGRATION_EXECUTOR!r}"
        for object_kind, count in sorted(non_owned_counts.items())
        if count > 0
    )


__all__ = [
    "MIGRATION_EXECUTOR",
    "MIGRATION_OWNERSHIP_SQL",
    "OWNERSHIP_PLAN_SQL",
    "OWNERSHIP_PLAN_VERSION",
    "OwnershipPlanRow",
    "ROLE_CONTRACT",
    "RolePosture",
    "migration_executor_violations",
    "migration_ownership_violations",
    "ownership_plan_sha256",
    "posture",
    "role_contract_violations",
    "unexpected_owners",
]
