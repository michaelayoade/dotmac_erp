#!/usr/bin/env python3
"""Verify the `dotmac_erp_app` -> `app_user` cutover. READ-ONLY, NO DDL.

The decision lives in `app.privilege_manifest`; this is the seam that runs the
SQL and prints the transcript. It opens a read-only transaction, issues
SELECTs against the catalog and the `has_*_privilege` family, and writes
nothing. It is safe to run against production, and against the restored copy
Change 2 uses.

## Why every authorization question is asked about an OID

`has_table_privilege('app_user', 'hr.employees', 'SELECT')` -- the NAME form --
does not merely check a privilege. It first RESOLVES the name, and name
resolution requires USAGE on the schema. Missing schema USAGE is precisely
what this programme exists to find, so the name form answers "false" for two
completely different reasons and cannot tell them apart. A live probe hit
exactly this on 2026-09-04.

So resolution and authorization are split, deliberately:

* **resolution** is a catalog SELECT (`pg_namespace`, `pg_class`, `pg_proc`),
  which reads the catalog directly and needs no USAGE on anything;
* **authorization** is `has_schema_privilege(role, oid, ...)`,
  `has_table_privilege(role, oid, ...)`, `has_sequence_privilege(role, oid,
  ...)` and `has_function_privilege(role, oid, ...)`, every one taking the OID
  the first step produced.

Names still appear in every report line -- an OID means nothing to a reviewer
-- but no check is ever made against one.

## Why every privilege is asked for separately

`has_table_privilege(role, oid, 'SELECT,INSERT,UPDATE')` is an **ANY** test in
PostgreSQL: it returns true if the role holds ANY of the three. It is never an
ALL assertion, and reading it as one certifies a role that can read but not
write. Every privilege below is therefore its own call -- SELECT, INSERT,
UPDATE and DELETE separately; SELECT, UPDATE and USAGE on a sequence
separately; EXECUTE on its own.

## Why effective privilege is not the answer

`has_*_privilege` answers "can this role do it", which is the union of every
path: a direct grant, ownership, `PUBLIC`, a role membership, and whatever
`ALTER DEFAULT PRIVILEGES` armed. Those are not equivalent outcomes. `PUBLIC`
shares the privilege with every login in the cluster including the next one
created; ownership carries DROP and defeats RLS; membership hides which role
the privilege belongs to; a default privilege silently arms FUTURE objects.
The cutover requires DIRECT grants, so this script decomposes each object's
ACL with `aclexplode` and classifies the origin, and the guard refuses
anything but `direct`.

## Why the DENIED relation is probed differently

Every other check here asks "does the target hold what the manifest says?".
A DENIED relation asks the opposite -- "does the target hold NOTHING?" -- and
an absence is the one claim that passes for free. So it gets its own probe, and
that probe is wider in two directions.

It asks about all SEVEN table privileges, not the four the census recorded:
TRUNCATE, REFERENCES and TRIGGER are privileges too, and a denial silent about
them denies nothing.

And it asks at COLUMN level. `GRANT SELECT(storage_key) ON ... TO app_user`
leaves `relacl` untouched and makes `has_table_privilege(..., 'SELECT')`
answer FALSE while the role reads the column -- so a denial proved only
against the relation ACL is not proved. `has_column_privilege(role, oid,
attnum, priv)` gives the effective per-column answer, and `aclexplode` over
`pg_attribute.attacl` -- the catalog behind
`information_schema.column_privileges` -- reports the origin. The number of
columns examined is carried into the snapshot, because "nothing held" from a
probe that looked at nothing is not evidence, and the guard refuses it.

## The DENIED functions, and why `PUBLIC` is asked about by name

A denied `SECURITY DEFINER` function gets THREE effective questions, not one:

    has_function_privilege('app_user', oid, 'EXECUTE')          must be false
    has_function_privilege('public',   oid, 'EXECUTE')          must be false
    has_function_privilege(<declared executor>, oid, 'EXECUTE')  must be true

The second one is the half that is easy to omit and expensive to get wrong.
`CREATE FUNCTION` grants `EXECUTE` to `PUBLIC` **by default**, so a function
whose `proacl` names no `app_user` entry at all can still be executable by
`app_user`. `REVOKE ... FROM app_user` does nothing to a grant held through
`PUBLIC`; only `REVOKE ... FROM PUBLIC` removes it, and that is a SEPARATE
REMEDIATION owed before cutover rather than a Change-1 grant to withhold.
`has_function_privilege` -- "can this role do it" -- sees the inherited grant;
reading the ACL for the role's own name does not, which is why every answer is
recorded with `effective=True` and the guard refuses one that is not.

PostgreSQL spells the pseudo-role `public` in `has_function_privilege`; it is
recorded in the snapshot as `PUBLIC`, which is how `aclexplode` and every
`GRANT`/`REVOKE` statement name it.

The third question is the positive one. A denial that passes because the
function became unreachable to EVERYONE is not the denial that was decided --
the relay would stop draining and a negative-only check would say nothing.

## Usage

    VERIFY_DATABASE_URL=postgresql://app_admin@host/db \\
        python scripts/verify_identity_cutover_privileges.py

`app_admin` (or any role that can read the catalog) is the right connection:
the script asks about OTHER roles' privileges, so it must not be either of
them. It never needs -- and must never be given -- write access.

Exit codes: 0 verified, 1 refusal, 2 usage/connection error.
"""

from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path
import sys

# Direct execution sets ``sys.path[0]`` to ``scripts/`` rather than the
# repository root. Same preamble as scripts/verify_runtime_admission.py, for
# the same reason.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.privilege_manifest import (  # noqa: E402
    COLUMN_LEVEL_PRIVILEGES,
    DENIED_TABLE_PRIVILEGES,
    FUNCTION_EXECUTOR_DECLARATIONS,
    MODULE_SCHEMA_PREFIX,
    PUBLIC_PSEUDO_ROLE,
    SECTION_CONTROL_PLANE,
    SECTION_FUNCTIONS,
    SECTION_RELATIONS,
    SECTION_SCHEMA_USAGE,
    SECTION_SEQUENCES,
    GrantRow,
    ObservedColumnGrant,
    ObservedFunctionExecute,
    ObservedMembership,
    ObservedObject,
    ObservedPrivilege,
    ObservedRolePosture,
    PrivilegeManifest,
    PrivilegeSnapshot,
    cutover_violations,
    manifest_from_json,
    relation_identity,
)

#: How PostgreSQL's `has_*_privilege` family spells the pseudo-role. The
#: snapshot records it as `PUBLIC` (the spelling `GRANT`, `REVOKE` and
#: `aclexplode` use); this is the argument the catalog function wants.
PUBLIC_ROLE_ARGUMENT = "public"

DATABASE_URL_VAR = "VERIFY_DATABASE_URL"
MANIFEST_PATH = (
    REPO_ROOT / "docs/inventories/erp-identity-cutover-manifest-2026-09-04.json"
)

#: Resolve schemas by name. A catalog read: no USAGE required, which is the
#: whole reason resolution is separate from authorization.
SCHEMA_RESOLUTION_SQL = """
SELECT n.oid::bigint, n.nspname::text, pg_catalog.pg_get_userbyid(n.nspowner)::text
FROM pg_catalog.pg_namespace AS n
WHERE n.nspname = ANY(%(names)s::text[])
"""

#: Resolve relations by (schema, name). `relkind` comes back as DATA so a
#: table replaced by a view of the same name is a KIND CHANGE, not a match.
RELATION_RESOLUTION_SQL = """
SELECT c.oid::bigint,
       n.nspname::text,
       c.relname::text,
       c.relkind::text,
       pg_catalog.pg_get_userbyid(c.relowner)::text
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
JOIN unnest(%(schemas)s::text[], %(names)s::text[]) AS want(nspname, relname)
  ON want.nspname = n.nspname AND want.relname = c.relname
"""

#: Resolve the whole OVERLOAD FAMILY of each function name, not just an exact
#: match. `oid::regprocedure::text` renders the argument TYPE list -- never
#: parameter names -- which is the identity that survives a restore. Selecting
#: the family is what lets the guard say "you named one overload and the
#: catalog has another" instead of the far less useful "it is missing".
FUNCTION_RESOLUTION_SQL = """
SELECT p.oid::bigint,
       n.nspname::text,
       p.proname::text,
       p.oid::regprocedure::text,
       pg_catalog.pg_get_userbyid(p.proowner)::text,
       p.prosecdef
FROM pg_catalog.pg_proc AS p
JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
JOIN unnest(%(schemas)s::text[], %(names)s::text[]) AS want(nspname, proname)
  ON want.nspname = n.nspname AND want.proname = p.proname
"""

#: One independent answer per (oid, privilege). Never a comma-joined
#: privilege string: that is an ANY test, and this must be an ALL assertion.
SCHEMA_PRIVILEGE_SQL = """
SELECT w.oid::bigint, w.priv,
       pg_catalog.has_schema_privilege(%(role)s::name, w.oid::oid, w.priv)
FROM unnest(%(oids)s::bigint[], %(privs)s::text[]) AS w(oid, priv)
"""
TABLE_PRIVILEGE_SQL = """
SELECT w.oid::bigint, w.priv,
       pg_catalog.has_table_privilege(%(role)s::name, w.oid::oid, w.priv)
FROM unnest(%(oids)s::bigint[], %(privs)s::text[]) AS w(oid, priv)
"""
SEQUENCE_PRIVILEGE_SQL = """
SELECT w.oid::bigint, w.priv,
       pg_catalog.has_sequence_privilege(%(role)s::name, w.oid::oid, w.priv)
FROM unnest(%(oids)s::bigint[], %(privs)s::text[]) AS w(oid, priv)
"""
FUNCTION_PRIVILEGE_SQL = """
SELECT w.oid::bigint, w.priv,
       pg_catalog.has_function_privilege(%(role)s::name, w.oid::oid, w.priv)
FROM unnest(%(oids)s::bigint[], %(privs)s::text[]) AS w(oid, priv)
"""

#: Decompose each object's ACL into `(grantee, privilege)` pairs so a held
#: privilege can be classified as direct / PUBLIC / ownership / inherited.
#: `acldefault` stands in for a NULL ACL, which is NOT "no privileges": for a
#: function a NULL `proacl` means EXECUTE is granted to PUBLIC.
SCHEMA_ACL_SQL = """
SELECT n.oid::bigint,
       (CASE WHEN a.grantee = 0 THEN 'PUBLIC'
             ELSE pg_catalog.pg_get_userbyid(a.grantee) END)::text,
       a.privilege_type::text,
       pg_catalog.pg_get_userbyid(n.nspowner)::text
FROM pg_catalog.pg_namespace AS n,
     LATERAL aclexplode(
       COALESCE(n.nspacl, pg_catalog.acldefault('n', n.nspowner))
     ) AS a
WHERE n.oid = ANY(%(oids)s::bigint[])
"""
RELATION_ACL_SQL = """
SELECT c.oid::bigint,
       (CASE WHEN a.grantee = 0 THEN 'PUBLIC'
             ELSE pg_catalog.pg_get_userbyid(a.grantee) END)::text,
       a.privilege_type::text,
       pg_catalog.pg_get_userbyid(c.relowner)::text
FROM pg_catalog.pg_class AS c,
     LATERAL aclexplode(
       COALESCE(
         c.relacl,
         pg_catalog.acldefault(
           CASE WHEN c.relkind = 'S' THEN 's' ELSE 'r' END, c.relowner
         )
       )
     ) AS a
WHERE c.oid = ANY(%(oids)s::bigint[])
"""
FUNCTION_ACL_SQL = """
SELECT p.oid::bigint,
       (CASE WHEN a.grantee = 0 THEN 'PUBLIC'
             ELSE pg_catalog.pg_get_userbyid(a.grantee) END)::text,
       a.privilege_type::text,
       pg_catalog.pg_get_userbyid(p.proowner)::text
FROM pg_catalog.pg_proc AS p,
     LATERAL aclexplode(
       COALESCE(p.proacl, pg_catalog.acldefault('f', p.proowner))
     ) AS a
WHERE p.oid = ANY(%(oids)s::bigint[])
"""

#: `ALTER DEFAULT PRIVILEGES` naming either role. This grants nothing today
#: and everything tomorrow: it arms objects that do not exist yet, which is a
#: privilege decision nobody will see in any ACL until after the fact.
DEFAULT_ACL_SQL = """
SELECT COALESCE(n.nspname, '<all schemas>')::text,
       d.defaclobjtype::text,
       pg_catalog.pg_get_userbyid(a.grantee)::text,
       a.privilege_type::text
FROM pg_catalog.pg_default_acl AS d
LEFT JOIN pg_catalog.pg_namespace AS n ON n.oid = d.defaclnamespace,
     LATERAL aclexplode(d.defaclacl) AS a
WHERE a.grantee <> 0
  AND pg_catalog.pg_get_userbyid(a.grantee) = ANY(%(roles)s::text[])
"""

#: DIRECT role membership in either direction, plus the transitive answer.
#: `pg_has_role(..., 'USAGE')` follows the whole chain, so a membership hidden
#: one hop away is still reported.
MEMBERSHIP_SQL = """
SELECT member.rolname::text, granted.rolname::text
FROM pg_catalog.pg_auth_members AS am
JOIN pg_catalog.pg_roles AS member ON member.oid = am.member
JOIN pg_catalog.pg_roles AS granted ON granted.oid = am.roleid
WHERE member.rolname = ANY(%(roles)s::text[])
"""
TRANSITIVE_MEMBERSHIP_SQL = """
SELECT r.rolname::text, other.rolname::text
FROM pg_catalog.pg_roles AS r
CROSS JOIN pg_catalog.pg_roles AS other
WHERE r.rolname = ANY(%(roles)s::text[])
  AND other.rolname <> r.rolname
  AND NOT other.rolname LIKE 'pg\\_%%'
  AND pg_catalog.pg_has_role(r.rolname, other.oid, 'USAGE')
"""

POSTURE_SQL = """
SELECT rolname::text, rolbypassrls, rolsuper
FROM pg_catalog.pg_roles
WHERE rolname = ANY(%(roles)s::text[])
"""

#: Every relation in every module schema, so the "the legacy role must never
#: gain module access" refusal has something to bite on. Enumerated from the
#: catalog rather than from the manifest: a refusal scoped to what the
#: manifest already knows about could never see a NEW module grant.
MODULE_RELATION_SQL = """
SELECT c.oid::bigint, n.nspname::text, c.relname::text
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname LIKE %(prefix)s || '%%'
  AND c.relkind = ANY(ARRAY['r', 'p', 'v', 'm', 'f'])
"""

#: Every privilege the TARGET role holds on a relation the manifest covers,
#: enumerated from the ACL rather than probed row by row. Without this the
#: "a new privilege appearing" refusal would be vacuous: a verifier that only
#: asks about the privileges it already expects can never find an extra one.
TARGET_EXTRA_SQL = """
SELECT c.oid::bigint, n.nspname::text, c.relname::text, a.privilege_type::text
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace,
     LATERAL aclexplode(c.relacl) AS a
WHERE c.oid = ANY(%(oids)s::bigint[])
  AND a.grantee <> 0
  AND pg_catalog.pg_get_userbyid(a.grantee) = %(role)s
"""

#: The NEGATIVE probe for a DENIED relation, at COLUMN level.
#:
#: `has_column_privilege(role, oid, attnum, priv)` is the effective answer for
#: one column: it is true if the role reaches the column through a column
#: grant OR through the relation. That is deliberately the wider question,
#: because the denial asserts the role cannot read the data by ANY path, and
#: a check that saw only column ACLs would miss a table grant while a check
#: that saw only the table ACL would miss `GRANT SELECT(col)`.
#:
#: Dropped columns are excluded (`attisdropped`) and system columns are not
#: grantable (`attnum > 0`), so both are skipped -- but the count of what WAS
#: examined is returned, because "nothing held" from a probe that looked at
#: nothing is not evidence.
DENIED_COLUMN_PRIVILEGE_SQL = """
SELECT c.oid::bigint,
       a.attname::text,
       w.priv,
       pg_catalog.has_column_privilege(%(role)s::name, c.oid::oid, a.attnum, w.priv)
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
CROSS JOIN unnest(%(privs)s::text[]) AS w(priv)
WHERE c.oid = ANY(%(oids)s::bigint[])
  AND a.attnum > 0
  AND NOT a.attisdropped
"""

#: The column ACL itself, so a held column privilege can be reported with its
#: ORIGIN rather than only its effect. Note the asymmetry with `relacl`: a
#: NULL `attacl` genuinely means "no column-level grants", because column
#: privileges have no default -- so unlike the relation and function ACLs
#: above, this one needs no `acldefault` stand-in. This is
#: `information_schema.column_privileges` read from the catalog directly, for
#: the same reason every other question here takes an OID: the
#: information_schema view resolves names and is filtered to the roles the
#: reader is a member of, and neither is the question being asked.
DENIED_COLUMN_ACL_SQL = """
SELECT c.oid::bigint,
       a.attname::text,
       acl.privilege_type::text,
       (CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
             ELSE pg_catalog.pg_get_userbyid(acl.grantee) END)::text
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid,
     LATERAL aclexplode(a.attacl) AS acl
WHERE c.oid = ANY(%(oids)s::bigint[])
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND a.attacl IS NOT NULL
"""

RELATION_PRIVILEGE_SECTIONS = (SECTION_RELATIONS, SECTION_CONTROL_PLANE)
MODULE_PROBE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _classify(
    role: str,
    owner: str,
    held: bool,
    privilege: str,
    acl: Sequence[tuple[str, str]],
    default_armed: bool,
) -> str:
    """Direct beats every other path; the rest are reported as what they are."""
    if any(grantee == role and priv == privilege for grantee, priv in acl):
        return "direct"
    if not held:
        return "none"
    if owner == role:
        return "ownership"
    if any(grantee == "PUBLIC" and priv == privilege for grantee, priv in acl):
        return "public"
    if default_armed:
        return "default"
    return "inherited"


def _split(pairs: Sequence[tuple[int, str]]) -> tuple[list[int], list[str]]:
    return [oid for oid, _ in pairs], [priv for _, priv in pairs]


def fetch_snapshot(connection, manifest: PrivilegeManifest) -> PrivilegeSnapshot:
    """Run every read. The ONLY function in this programme that touches a DB."""
    roles = [manifest.source_role, manifest.target_role]
    target = manifest.target_role
    objects: list[ObservedObject] = []
    privileges: list[ObservedPrivilege] = []
    notes: list[str] = []

    rows_by_identity: dict[str, list[GrantRow]] = {}
    for row in manifest.rows:
        rows_by_identity.setdefault(row.identity, []).append(row)

    with connection.cursor() as cursor:
        # --- default privileges, read once and reused for classification ---
        cursor.execute(DEFAULT_ACL_SQL, {"roles": roles})
        default_acl = cursor.fetchall()
        default_scopes = {
            (schema, objtype, grantee, priv)
            for schema, objtype, grantee, priv in default_acl
        }
        for schema, objtype, grantee, priv in sorted(default_acl):
            notes.append(
                f"DEFAULT PRIVILEGE: {grantee} is armed for {priv} on future "
                f"{objtype!r} objects in {schema}. Nothing holds it yet; "
                "everything created there will."
            )

        # --- schemas ---
        schema_rows = manifest.section(SECTION_SCHEMA_USAGE)
        names = sorted({row.schema for row in schema_rows})
        cursor.execute(SCHEMA_RESOLUTION_SQL, {"names": names})
        resolved = {name: (oid, owner) for oid, name, owner in cursor.fetchall()}
        for name in names:
            found = resolved.get(name)
            objects.append(
                ObservedObject(
                    identity=f"schema:{name}",
                    exists=found is not None,
                    object_kind="schema",
                    owner=found[1] if found else "",
                    resolved_identity=f"schema:{name}",
                    candidate_count=1 if found else 0,
                )
            )
        wanted = [
            (resolved[row.schema][0], row.privilege)
            for row in schema_rows
            if row.schema in resolved
        ]
        oids, privs = _split(wanted)
        held_by_oid: dict[tuple[int, str], bool] = {}
        if oids:
            cursor.execute(
                SCHEMA_PRIVILEGE_SQL, {"role": target, "oids": oids, "privs": privs}
            )
            held_by_oid = {(oid, priv): held for oid, priv, held in cursor.fetchall()}
            cursor.execute(SCHEMA_ACL_SQL, {"oids": sorted(set(oids))})
            acl_by_oid: dict[int, list[tuple[str, str]]] = {}
            owner_by_oid: dict[int, str] = {}
            for oid, grantee, priv, owner in cursor.fetchall():
                acl_by_oid.setdefault(oid, []).append((grantee, priv))
                owner_by_oid[oid] = owner
            for row in schema_rows:
                found = resolved.get(row.schema)
                if found is None:
                    continue
                oid = found[0]
                held = bool(held_by_oid.get((oid, row.privilege), False))
                privileges.append(
                    ObservedPrivilege(
                        identity=row.identity,
                        role=target,
                        privilege=row.privilege,
                        held=held,
                        origin=_classify(
                            target,
                            owner_by_oid.get(oid, ""),
                            held,
                            row.privilege,
                            acl_by_oid.get(oid, ()),
                            # A default ACL arms objects created IN a
                            # schema; it can never confer USAGE on the schema
                            # itself, so no objtype can match here.
                            False,
                        ),
                    )
                )

        # --- relations and sequences ---
        relation_rows = [
            row
            for row in manifest.rows
            if row.section in (*RELATION_PRIVILEGE_SECTIONS, SECTION_SEQUENCES)
        ]
        pairs = sorted({(row.schema, row.object_name) for row in relation_rows})
        cursor.execute(
            RELATION_RESOLUTION_SQL,
            {
                "schemas": [schema for schema, _ in pairs],
                "names": [name for _, name in pairs],
            },
        )
        rel_resolved = {
            (schema, name): (oid, relkind, owner)
            for oid, schema, name, relkind, owner in cursor.fetchall()
        }
        for schema, name in pairs:
            found = rel_resolved.get((schema, name))
            row = next(
                r for r in relation_rows if r.schema == schema and r.object_name == name
            )
            objects.append(
                ObservedObject(
                    identity=row.identity,
                    exists=found is not None,
                    object_kind=row.object_kind if found else "",
                    owner=found[2] if found else "",
                    relkind=found[1] if found else "",
                    resolved_identity=row.identity,
                    candidate_count=1 if found else 0,
                )
            )

        for section, statement, objtype in (
            (SECTION_SEQUENCES, SEQUENCE_PRIVILEGE_SQL, "S"),
            (SECTION_RELATIONS, TABLE_PRIVILEGE_SQL, "r"),
            (SECTION_CONTROL_PLANE, TABLE_PRIVILEGE_SQL, "r"),
        ):
            # A DENIED row is not probed here. This loop asserts "the target
            # HOLDS it"; the denied rows assert the exact opposite and are
            # probed by the denial block below, which owns them end to end.
            section_rows = [row for row in manifest.section(section) if not row.denied]
            wanted = [
                (rel_resolved[(row.schema, row.object_name)][0], row.privilege)
                for row in section_rows
                if (row.schema, row.object_name) in rel_resolved
            ]
            if not wanted:
                continue
            oids, privs = _split(wanted)
            cursor.execute(statement, {"role": target, "oids": oids, "privs": privs})
            held_by_oid = {(oid, priv): held for oid, priv, held in cursor.fetchall()}
            cursor.execute(RELATION_ACL_SQL, {"oids": sorted(set(oids))})
            acl_by_oid = {}
            owner_by_oid = {}
            for oid, grantee, priv, owner in cursor.fetchall():
                acl_by_oid.setdefault(oid, []).append((grantee, priv))
                owner_by_oid[oid] = owner
            for row in section_rows:
                found = rel_resolved.get((row.schema, row.object_name))
                if found is None:
                    continue
                oid = found[0]
                held = bool(held_by_oid.get((oid, row.privilege), False))
                privileges.append(
                    ObservedPrivilege(
                        identity=row.identity,
                        role=target,
                        privilege=row.privilege,
                        held=held,
                        origin=_classify(
                            target,
                            owner_by_oid.get(oid, ""),
                            held,
                            row.privilege,
                            acl_by_oid.get(oid, ()),
                            (row.schema, objtype, target, row.privilege)
                            in default_scopes,
                        ),
                    )
                )

        # --- extras on manifest-covered relations (refusal 2, non-vacuous) ---
        covered = sorted({oid for oid, _, _ in rel_resolved.values()})
        if covered:
            cursor.execute(TARGET_EXTRA_SQL, {"oids": covered, "role": target})
            expected_pairs = {(row.identity, row.privilege) for row in manifest.rows}
            for _oid, schema, name, priv in cursor.fetchall():
                identity = relation_identity(schema, name)
                if (identity, priv) in expected_pairs:
                    continue
                privileges.append(
                    ObservedPrivilege(
                        identity=identity,
                        role=target,
                        privilege=priv,
                        held=True,
                        origin="direct",
                    )
                )

        # --- DENIED relations: the negative proof, table AND column ---
        denied_rows = manifest.denied()
        denied_privileges: list[ObservedPrivilege] = []
        denied_column_grants: list[ObservedColumnGrant] = []
        denied_by_oid: dict[int, str] = {}
        for row in denied_rows:
            found = rel_resolved.get((row.schema, row.object_name))
            if found is not None:
                denied_by_oid[found[0]] = row.identity
        if denied_by_oid:
            denied_oids = sorted(denied_by_oid)
            wanted = [
                (oid, priv) for oid in denied_oids for priv in DENIED_TABLE_PRIVILEGES
            ]
            oids, privs = _split(wanted)
            cursor.execute(
                TABLE_PRIVILEGE_SQL, {"role": target, "oids": oids, "privs": privs}
            )
            held_by_oid = {(oid, priv): held for oid, priv, held in cursor.fetchall()}
            for oid, priv in wanted:
                denied_privileges.append(
                    ObservedPrivilege(
                        identity=denied_by_oid[oid],
                        role=target,
                        privilege=priv,
                        held=bool(held_by_oid.get((oid, priv), False)),
                        origin="none",
                    )
                )

            cursor.execute(
                DENIED_COLUMN_PRIVILEGE_SQL,
                {
                    "role": target,
                    "oids": denied_oids,
                    "privs": list(COLUMN_LEVEL_PRIVILEGES),
                },
            )
            probed: dict[tuple[int, str], set[str]] = {}
            held_columns: dict[tuple[int, str], set[str]] = {}
            for oid, column, priv, held in cursor.fetchall():
                probed.setdefault((oid, priv), set()).add(column)
                if held:
                    held_columns.setdefault((oid, priv), set()).add(column)
            for oid in denied_oids:
                for priv in COLUMN_LEVEL_PRIVILEGES:
                    denied_column_grants.append(
                        ObservedColumnGrant(
                            identity=denied_by_oid[oid],
                            role=target,
                            privilege=priv,
                            columns_held=tuple(
                                sorted(held_columns.get((oid, priv), ()))
                            ),
                            columns_probed=len(probed.get((oid, priv), ())),
                        )
                    )

            # The column ACL itself, reported so a held column privilege
            # arrives with its origin rather than only its effect.
            cursor.execute(DENIED_COLUMN_ACL_SQL, {"oids": denied_oids})
            for oid, column, priv, grantee in sorted(cursor.fetchall()):
                notes.append(
                    f"COLUMN ACL: {denied_by_oid.get(oid, oid)} column "
                    f"{column!r} grants {priv} to {grantee}. This entry lives "
                    "in pg_attribute.attacl and is invisible to "
                    "has_table_privilege."
                )

        # --- functions, by overload family ---
        function_rows = manifest.section(SECTION_FUNCTIONS)
        cursor.execute(
            FUNCTION_RESOLUTION_SQL,
            {
                "schemas": [row.schema for row in function_rows],
                "names": [row.object_name for row in function_rows],
            },
        )
        families: dict[tuple[str, str], list[tuple[int, str, str, bool]]] = {}
        for oid, schema, name, regproc, owner, secdef in cursor.fetchall():
            arguments = regproc[regproc.index("(") + 1 : -1]
            families.setdefault((schema, name), []).append(
                (oid, arguments, owner, secdef)
            )
        function_probes: list[tuple[int, GrantRow]] = []
        for row in function_rows:
            family = sorted(families.get((row.schema, row.object_name), ()))
            exact = [entry for entry in family if entry[1] == row.identity_arguments]
            if exact:
                oid, _arguments, owner, secdef = exact[0]
                objects.append(
                    ObservedObject(
                        identity=row.identity,
                        exists=True,
                        object_kind="function",
                        owner=owner,
                        resolved_identity=row.identity,
                        candidate_count=1,
                    )
                )
                function_probes.append((oid, row))
                if not secdef:
                    notes.append(
                        f"{row.identity} is no longer SECURITY DEFINER; the "
                        "manifest recorded it as one."
                    )
            elif family:
                oid, arguments, owner, _secdef = family[0]
                objects.append(
                    ObservedObject(
                        identity=row.identity,
                        exists=True,
                        object_kind="function",
                        owner=owner,
                        resolved_identity=(
                            f"function:{row.schema}.{row.object_name}({arguments})"
                        ),
                        candidate_count=len(family),
                    )
                )
            else:
                objects.append(
                    ObservedObject(
                        identity=row.identity,
                        exists=False,
                        object_kind="function",
                        owner="",
                        resolved_identity="",
                        candidate_count=0,
                    )
                )
        # --- DENIED functions: three EFFECTIVE questions each ---
        #
        # Every one goes through `has_function_privilege`, never through the
        # ACL. `CREATE FUNCTION` grants EXECUTE to PUBLIC by default, so a
        # function whose proacl names no `app_user` entry can still be
        # executable by `app_user`; only the effective question sees that, and
        # the guard refuses an answer not marked `effective`.
        denied_function_execute: list[ObservedFunctionExecute] = []
        if function_probes:
            for oid, row in function_probes:
                declaration = FUNCTION_EXECUTOR_DECLARATIONS.get(row.identity)
                principals = [
                    (target, target),
                    (PUBLIC_PSEUDO_ROLE, PUBLIC_ROLE_ARGUMENT),
                ]
                if declaration is not None and declaration.has_runtime_executor:
                    principals.append(
                        (declaration.permitted_executor, declaration.permitted_executor)
                    )
                for recorded_role, catalog_role in principals:
                    cursor.execute(
                        FUNCTION_PRIVILEGE_SQL,
                        {
                            "role": catalog_role,
                            "oids": [oid],
                            "privs": [row.privilege],
                        },
                    )
                    answered = cursor.fetchall()
                    held = bool(answered[0][2]) if answered else False
                    denied_function_execute.append(
                        ObservedFunctionExecute(
                            identity=row.identity,
                            role=recorded_role,
                            held=held,
                            effective=True,
                            probed=bool(answered),
                        )
                    )
            # The ACL is read too, but only to REPORT an origin -- never to
            # decide the denial. A PUBLIC entry here is the remediation the
            # guard names, arriving with its provenance instead of only its
            # effect.
            cursor.execute(
                FUNCTION_ACL_SQL,
                {"oids": sorted({oid for oid, _row in function_probes})},
            )
            identity_by_oid = {oid: row.identity for oid, row in function_probes}
            for oid, grantee, priv, _owner in sorted(cursor.fetchall()):
                if grantee != "PUBLIC":
                    continue
                notes.append(
                    f"FUNCTION ACL: {identity_by_oid.get(oid, oid)} grants "
                    f"{priv} to PUBLIC. PostgreSQL applies this by default at "
                    "CREATE FUNCTION; REVOKE ... FROM app_user does not remove "
                    "it, and it reaches every login in the cluster."
                )

        # --- the legacy role's module access ---
        cursor.execute(MODULE_RELATION_SQL, {"prefix": MODULE_SCHEMA_PREFIX})
        module_relations = cursor.fetchall()
        legacy: list[ObservedPrivilege] = []
        if module_relations:
            wanted = [
                (oid, priv)
                for oid, _schema, _name in module_relations
                for priv in MODULE_PROBE_PRIVILEGES
            ]
            oids, privs = _split(wanted)
            cursor.execute(
                TABLE_PRIVILEGE_SQL,
                {"role": manifest.source_role, "oids": oids, "privs": privs},
            )
            held_by_oid = {(oid, priv): held for oid, priv, held in cursor.fetchall()}
            names_by_oid = {
                oid: relation_identity(schema, name)
                for oid, schema, name in module_relations
            }
            for oid, priv in wanted:
                legacy.append(
                    ObservedPrivilege(
                        identity=names_by_oid[oid],
                        role=manifest.source_role,
                        privilege=priv,
                        held=bool(held_by_oid.get((oid, priv), False)),
                    )
                )

        # --- role posture and membership ---
        cursor.execute(POSTURE_SQL, {"roles": roles})
        postures = tuple(
            ObservedRolePosture(role, bool(bypass), bool(superuser))
            for role, bypass, superuser in cursor.fetchall()
        )
        cursor.execute(MEMBERSHIP_SQL, {"roles": roles})
        memberships = {
            ObservedMembership(member, granted) for member, granted in cursor.fetchall()
        }
        cursor.execute(TRANSITIVE_MEMBERSHIP_SQL, {"roles": roles})
        memberships |= {
            ObservedMembership(member, granted) for member, granted in cursor.fetchall()
        }

    return PrivilegeSnapshot(
        objects=tuple(objects),
        privileges=tuple(privileges),
        postures=postures,
        memberships=tuple(
            sorted(memberships, key=lambda m: (m.member, m.granted_role))
        ),
        legacy_module_privileges=tuple(legacy),
        denied_privileges=tuple(denied_privileges),
        denied_column_grants=tuple(denied_column_grants),
        denied_function_execute=tuple(denied_function_execute),
        notes=tuple(notes),
    )


def _report(manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot) -> list[str]:
    """A gate that prints nothing is an unmonitored region (ADR-0018)."""
    counts = manifest.counts()
    lines = [
        "IDENTITY CUTOVER VERIFICATION",
        f"  census        {manifest.captured_at} on {manifest.host}",
        f"  database      {manifest.database} (PostgreSQL {manifest.server_version})",
        f"  source role   {manifest.source_role} (retiring; gains nothing)",
        f"  target role   {manifest.target_role}",
        "",
        "MANIFEST SECTIONS",
    ]
    for section, count in sorted(counts.items()):
        lines.append(f"  {section:16s} {count:5d} rows")
    lines.extend(
        [
            "",
            "OBSERVED",
            f"  objects resolved        {sum(1 for o in snapshot.objects if o.exists)}"
            f" / {len(snapshot.objects)}",
            f"  privilege answers       {len(snapshot.privileges)}"
            " (one call per privilege; never a comma-joined ANY test)",
            f"  legacy module probes    {len(snapshot.legacy_module_privileges)}",
            f"  denial table probes     {len(snapshot.denied_privileges)}"
            f" (all {len(DENIED_TABLE_PRIVILEGES)} table privileges, per denied"
            " relation)",
            f"  denial column probes    {len(snapshot.denied_column_grants)}"
            " (pg_attribute.attacl / has_column_privilege; a column grant is"
            " invisible to has_table_privilege)",
            f"  denial EXECUTE probes   {len(snapshot.denied_function_execute)}"
            " (has_function_privilege -- EFFECTIVE, asked of the target role,"
            " of PUBLIC, and of the declared executor)",
            f"  role memberships        {len(snapshot.memberships)}",
            "",
            "DENIED BY ARCHITECTURE, control-plane relations"
            " (never granted, absence proved)",
        ]
    )
    seen_relations: set[str] = set()
    for row in manifest.denied_relations():
        if row.identity in seen_relations:
            continue
        seen_relations.add(row.identity)
        columns = sum(
            entry.columns_probed
            for entry in snapshot.denied_column_grants
            if entry.identity == row.identity
        )
        lines.append(
            f"  {row.identity} -- plane declared by {row.plane_declared_by}; "
            f"{columns} column answers taken; permitted instead: "
            + (", ".join(row.permitted_principals) or "nobody")
        )
    if not manifest.denied_relations():
        lines.append("  (none)")
    lines.extend(
        ["", "DENIED BY ARCHITECTURE, SECURITY DEFINER EXECUTE (absence proved)"]
    )
    seen_functions: set[str] = set()
    for row in manifest.denied_functions():
        if row.identity in seen_functions:
            continue
        seen_functions.add(row.identity)
        answers = {
            entry.role: entry
            for entry in snapshot.denied_function_execute
            if entry.identity == row.identity
        }
        executor = FUNCTION_EXECUTOR_DECLARATIONS.get(row.identity)
        permitted = (
            executor.permitted_executor
            if executor is not None and executor.has_runtime_executor
            else "none -- no runtime principal"
        )
        rendered = ", ".join(
            f"{role}={'YES' if entry.held else 'no'}"
            f"{'' if entry.effective else ' (NOT EFFECTIVE)'}"
            for role, entry in sorted(answers.items())
        )
        lines.append(
            f"  {row.identity} -- permitted executor: {permitted}; "
            f"effective EXECUTE: {rendered or 'NOT PROBED'}"
        )
    if not manifest.denied_functions():
        lines.append("  (none)")
    lines.extend(["", "EXCLUSIONS (preserved, never revoked)"])
    for scope, count in sorted(manifest.preserved_scopes().items()):
        lines.append(f"  {scope:20s} {count:5d} target privileges preserved")
    if snapshot.notes:
        lines.extend(["", "NOTES"])
        lines.extend(f"  {note}" for note in snapshot.notes)
    return lines


def main(argv: list[str] | None = None) -> int:
    del argv
    url = os.environ.get(DATABASE_URL_VAR, "").strip()
    if not url:
        print(f"{DATABASE_URL_VAR} is not set", file=sys.stderr)  # noqa: T201
        return 2
    if not MANIFEST_PATH.exists():
        print(f"manifest not found: {MANIFEST_PATH}", file=sys.stderr)  # noqa: T201
        return 2

    manifest = manifest_from_json(MANIFEST_PATH.read_text(encoding="utf-8"))

    import psycopg  # imported here so the module stays importable offline

    with psycopg.connect(url, autocommit=False) as connection:
        connection.read_only = True
        snapshot = fetch_snapshot(connection, manifest)
        connection.rollback()

    for line in _report(manifest, snapshot):
        print(line)  # noqa: T201

    violations = cutover_violations(manifest, snapshot)
    if violations:
        print("\nREFUSED:", file=sys.stderr)  # noqa: T201
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)  # noqa: T201
        return 1
    print("\nVERIFIED: every manifest row holds, by direct grant.")  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
