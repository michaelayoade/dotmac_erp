"""Pure contract for database roles and the Alembic executor.

The privileged bootstrap and the unprivileged deploy preflight share this
decision code. The migration keeps its own point-in-time copy, pinned to this
module by an architecture test, so an applied revision cannot change meaning
when runtime code evolves.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

RolePosture = tuple[bool, bool]

#: Exact `(rolbypassrls, rolsuper)` contract.
ROLE_CONTRACT: Final[dict[str, RolePosture]] = {
    "app_admin": (True, False),
    "app_user": (False, False),
    "platform_api": (False, False),
}
MIGRATION_EXECUTOR: Final[str] = "app_admin"

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
    "ROLE_CONTRACT",
    "RolePosture",
    "migration_executor_violations",
    "migration_ownership_violations",
    "posture",
    "role_contract_violations",
]
