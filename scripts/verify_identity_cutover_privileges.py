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
    MODULE_SCHEMA_PREFIX,
    SECTION_FUNCTIONS,
    SECTION_MODULE_ERA,
    SECTION_RELATIONS,
    SECTION_SCHEMA_USAGE,
    SECTION_SEQUENCES,
    GrantRow,
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

RELATION_PRIVILEGE_SECTIONS = (SECTION_RELATIONS, SECTION_MODULE_ERA)
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
            (SECTION_MODULE_ERA, TABLE_PRIVILEGE_SQL, "r"),
        ):
            section_rows = manifest.section(section)
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
        if function_probes:
            oids, privs = _split([(oid, row.privilege) for oid, row in function_probes])
            cursor.execute(
                FUNCTION_PRIVILEGE_SQL,
                {"role": target, "oids": oids, "privs": privs},
            )
            held_by_oid = {(oid, priv): held for oid, priv, held in cursor.fetchall()}
            cursor.execute(FUNCTION_ACL_SQL, {"oids": sorted(set(oids))})
            acl_by_oid = {}
            owner_by_oid = {}
            for oid, grantee, priv, owner in cursor.fetchall():
                acl_by_oid.setdefault(oid, []).append((grantee, priv))
                owner_by_oid[oid] = owner
            for oid, row in function_probes:
                priv = row.privilege
                held = bool(held_by_oid.get((oid, priv), False))
                privileges.append(
                    ObservedPrivilege(
                        identity=row.identity,
                        role=target,
                        privilege=priv,
                        held=held,
                        origin=_classify(
                            target,
                            owner_by_oid.get(oid, ""),
                            held,
                            priv,
                            acl_by_oid.get(oid, ()),
                            (row.schema, "f", target, priv) in default_scopes,
                        ),
                    )
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
            f"  role memberships        {len(snapshot.memberships)}",
            "",
            "EXCLUSIONS (preserved, never revoked)",
        ]
    )
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
