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
#: The relay's two least-privilege drain identities, kept SEPARATE from
#: `ROLE_CONTRACT` on purpose. `ROLE_CONTRACT` is what `module_database_roles.v1`
#: means, and that effect is already supplied and bound; folding two more roles
#: into it would silently change what an applied revision asserted. These belong
#: to `outbox_relay.v1` and are verified by its own provider.
#:
#: Both are `(rolbypassrls, rolsuper) = (False, False)`. A dispatcher exists to
#: drain events through two SECURITY DEFINER functions and nothing else — one
#: that could bypass row-level security would make the whole hardening
#: decorative, and a superuser bypasses it whether or not the flag is set.
RELAY_DISPATCHER_CONTRACT: Final[dict[str, RolePosture]] = {
    "outbox_dispatcher": (False, False),
    "platform_outbox_dispatcher": (False, False),
}

MIGRATION_EXECUTOR: Final[str] = "app_admin"

#: The roles a LONG-RUNNING process connects as. Removing a credential from an
#: env file accomplishes nothing if the credential that remains can become the
#: one that was removed: PostgreSQL role ATTRIBUTES are not inherited through
#: membership, but `SET ROLE` adopts them wholesale, so a member of `app_admin`
#: is one statement away from BYPASSRLS and a member of a superuser role is one
#: statement away from everything.
#:
#: `app_admin` is deliberately NOT a subject. It is BYPASSRLS by contract and
#: exists to own DDL, so "may it reach BYPASSRLS" is not a question about it.
#: Whether `app_admin` can reach SUPERUSER or CREATEROLE is a real question and
#: this contract does not ask it — that region is UNMONITORED, stated here
#: rather than silently exempted (ADR-0018).
NON_ESCALATING_ROLES: Final[frozenset[str]] = frozenset(
    {
        "app_user",
        "platform_api",
        "outbox_dispatcher",
        "platform_outbox_dispatcher",
    }
)

#: The attributes that make a reachable role an escalation target. Named
#: individually because a category ("privileged roles") is not checkable.
ESCALATING_ATTRIBUTES: Final[tuple[str, ...]] = (
    "SUPERUSER",
    "CREATEROLE",
    "BYPASSRLS",
)

#: Transitive role membership, computed from the two PUBLICLY READABLE
#: catalogues.
#:
#: `pg_has_role()` is the shorter spelling and is NOT used: PostgreSQL restricts
#: which roles a non-superuser may interrogate with it, and this query has to
#: run on the deploy preflight's `app_admin` connection, which is NOSUPERUSER by
#: contract. A check that silently answers "no edges" because it was not allowed
#: to look is worse than no check. `pg_roles` and `pg_auth_members` are
#: world-readable, so the recursive walk answers the same question from evidence
#: `app_admin` can actually see.
#:
#: Membership is the right relation for BOTH halves of the ruling. Inheritance
#: (`rolinherit`) grants a member the target's PRIVILEGES automatically;
#: `SET ROLE` adopts the target's ATTRIBUTES on demand and needs only
#: membership. Walking membership therefore over-reports rather than
#: under-reports, which is the correct direction for a security gate.
#:
#: What it does NOT see, stated rather than implied: escalation through a
#: SECURITY DEFINER routine owned by a privileged role, through `rolreplication`
#: or `rolcreatedb`, or through anything granted after this observation. Those
#: regions are UNMONITORED.
ROLE_ESCALATION_SQL: Final[str] = """
WITH RECURSIVE reachable(subject, target_oid) AS (
    SELECT subject_role.rolname, membership.roleid
    FROM pg_roles AS subject_role
    JOIN pg_auth_members AS membership
      ON membership.member = subject_role.oid
    WHERE subject_role.rolname = ANY(%(subjects)s)

    UNION

    SELECT reachable.subject, membership.roleid
    FROM reachable
    JOIN pg_auth_members AS membership
      ON membership.member = reachable.target_oid
)
SELECT reachable.subject,
       target_role.rolname,
       target_role.rolsuper,
       target_role.rolcreaterole,
       target_role.rolbypassrls
FROM reachable
JOIN pg_roles AS target_role ON target_role.oid = reachable.target_oid
WHERE target_role.rolsuper
   OR target_role.rolcreaterole
   OR target_role.rolbypassrls
ORDER BY reachable.subject, target_role.rolname
"""

RoleEscalationEdge = tuple[str, str, bool, bool, bool]


def role_escalation_violations(
    edges: Sequence[RoleEscalationEdge],
) -> tuple[str, ...]:
    """Name EVERY way a runtime role can become a privileged one.

    One line per (subject, reachable target) pair, spelling out which attributes
    the target carries, because "app_user can escalate" does not tell an
    operator which GRANT to revoke.

    A subject reaching a target it is not supposed to reach is reported whether
    the edge is direct or transitive; the SQL above has already flattened the
    chain, so this function never has to know the graph's shape.
    """
    violations: list[str] = []
    for subject, target, superuser, createrole, bypassrls in edges:
        if subject not in NON_ESCALATING_ROLES:
            continue
        attributes = tuple(
            name
            for name, held in zip(
                ESCALATING_ATTRIBUTES,
                (superuser, createrole, bypassrls),
                strict=True,
            )
            if held
        )
        if not attributes:
            continue
        violations.append(
            f"runtime role {subject!r} is a member of {target!r} "
            f"({'/'.join(attributes)}); SET ROLE adopts those attributes, so "
            "withholding a privileged connection string does not withhold the "
            "privilege"
        )
    return tuple(sorted(violations))


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


def relay_dispatcher_violations(
    observed: Mapping[str, RolePosture],
) -> tuple[str, ...]:
    """Name every way the relay's drain identities fail their contract."""
    violations = [
        f"database role {role!r} is missing"
        for role in sorted(set(RELAY_DISPATCHER_CONTRACT) - set(observed))
    ]
    for role, expected in RELAY_DISPATCHER_CONTRACT.items():
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


def database_identity_violations(
    observed_database: str,
    expected_database: str | None,
) -> tuple[str, ...]:
    """Refuse a verification that reached a database nobody authorised.

    `migration_executor_violations` asserts WHO the connection is and
    `migration_ownership_violations` asserts WHAT it owns. Neither asserts
    WHERE it landed, so a reconciliation aimed at the wrong database passed
    every check the preflight made: a staging cluster that also has a correctly
    shaped `app_admin` owning its own objects satisfies both.

    `expected_database is None` is NOT silently accepted as "fine". The caller
    is required to surface it as an unverified identity — see
    :func:`unverified_database_identity_notice`. An expectation that is present
    and wrong is a hard violation.
    """
    if expected_database is None:
        return ()
    if observed_database != expected_database:
        return (
            f"connection reached database {observed_database!r}, authorised "
            f"for {expected_database!r}; role posture and object ownership are "
            "satisfiable by the wrong cluster, so neither detects this",
        )
    return ()


def unverified_database_identity_notice(
    observed_database: str,
    expected_database: str | None,
    variable: str,
) -> str | None:
    """The sentence a run must print when nothing bound it to a database.

    Returned as text rather than raised: an operator who has not yet adopted the
    expectation should not be blocked by it, but must not be able to read a
    clean run as evidence of something it did not check.
    """
    if expected_database is not None:
        return None
    return (
        f"database identity UNVERIFIED: reached {observed_database!r} and "
        f"nothing said which database was authorised. Set {variable} to make "
        "this an assertion instead of an observation."
    )


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
    "ESCALATING_ATTRIBUTES",
    "MIGRATION_EXECUTOR",
    "MIGRATION_OWNERSHIP_SQL",
    "NON_ESCALATING_ROLES",
    "OWNERSHIP_PLAN_SQL",
    "OWNERSHIP_PLAN_VERSION",
    "OwnershipPlanRow",
    "ROLE_CONTRACT",
    "ROLE_ESCALATION_SQL",
    "RoleEscalationEdge",
    "RolePosture",
    "database_identity_violations",
    "migration_executor_violations",
    "migration_ownership_violations",
    "ownership_plan_sha256",
    "posture",
    "role_contract_violations",
    "role_escalation_violations",
    "unexpected_owners",
    "unverified_database_identity_notice",
]
