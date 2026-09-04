"""Pure contract for the `dotmac_erp_app` -> `app_user` IDENTITY cutover.

`app.migration_database_roles` answers "may this connection run DDL?".
`app.runtime_admission` answers "is the serving connection the canonical
runtime identity, for the module storage it actually uses?".  This module
answers the step that has to happen BEFORE the second question can ever be
answered yes on ERP production: "what, exactly, does `app_user` have to be
able to do so that repointing the application from the legacy login
`dotmac_erp_app` changes no behaviour at all?"

## The ruling this encodes (2026-09-04)

Identity migration and least-privilege reduction are SEPARATE changes, done in
that order.  Doing both at once makes every missing permission ambiguous: a
request that fails after the cutover could be a mirroring defect or an
intended reduction, and the only way to tell them apart is to put the old
login back.  So Change 1 makes `app_user` behaviourally equivalent to
`dotmac_erp_app` across the legacy estate and changes nothing else:

* mirror the source role's existing effective privileges onto the target;
* preserve every privilege the target ALREADY holds -- nothing is revoked;
* do NOT grant the source role anything it lacks (in particular, no module
  access: `dotmac_erp_app` is the role being retired, not extended);
* no `GRANT ALL`, no role membership in either direction, no ownership
  transfer, no `BYPASSRLS`, no module activation-flag change.

The 1,716 relation privileges below are the COMPATIBILITY BASELINE, not the
permanent least-privilege target.  Reduction is Change 3, driven by a
two-directional ratchet, once the cutover has been stable.

## The target, stated once

    legacy compatibility privileges
      - architecturally forbidden access
      - unapproved `SECURITY DEFINER` execution
      + module-era privileges `app_user` already owns

Everything below is that arithmetic made executable.  The three files it
produces stay SPLIT, permanently: bulk-safe grants in the bulk file, the five
`SECURITY DEFINER` functions isolated, the control-plane grants prohibited,
the schema cases resolved.  Collapsing them would put exceptional
authorization back inside mechanical compatibility, which is the one thing
this shape exists to prevent -- a 1,700-line file with six escalation
decisions buried in it gets skimmed.

## Why identity here is OID-independent

Change 2 applies this manifest to a RESTORED database.  A restore assigns new
OIDs to every object it recreates, so a manifest keyed by OID would be a
manifest of a database that no longer exists.  Every row here is therefore
keyed by catalog identity that survives dump/restore:

* a schema is `schema:<nspname>`;
* a relation is `relation:<nspname>.<relname>`, with the relkind carried as
  the row's DATA rather than as part of its key, so a table replaced by a
  view of the same name is reported as a KIND CHANGE instead of silently
  matching;
* a sequence is `sequence:<nspname>.<relname>`;
* a function is `function:<nspname>.<proname>(<argument types>)` -- the
  ARGUMENT TYPE list, never the bare name.  `claim_outbox_batch(text)` and
  `claim_outbox_batch(text, integer, integer)` are different functions with
  different bodies and, potentially, different owners; a name-keyed manifest
  would grant EXECUTE on whichever one the planner happened to resolve.

OIDs still appear at the far end: the verifier RESOLVES each identity to an
OID by catalog lookup and then asks every authorization question about the
OID.  See `scripts/verify_identity_cutover_privileges.py` for why that
direction is mandatory.

## What the census actually is, and why that matters here

The census is a DELTA, not a dump of the source role's ACL: each recorded
privilege is one the SOURCE role holds and the TARGET role does NOT
(`reverse_gap` records the count of the opposite direction).  So this manifest
is "what must be GRANTED to reach equivalence", and equivalence itself is
`what app_user already holds` UNION `this manifest`.  Nothing here is a claim
that `app_user` holds nothing else, and nothing here may be read as a complete
picture of either role's privileges.

That distinction had one unresolved consequence, and a read-only follow-up
query SETTLED it on 2026-09-04.  Five schemas -- `hr`, `mod_files`, `public`,
`rpt`, `sync` -- carry 448 relation privileges between them and appear in NO
schema-USAGE row.  All five returned `legacy=True, app_user=True`: the DELTA
reading is correct, `app_user` already holds USAGE in every one of them, and
the derived GRANT would have been a no-op.  So the five derived rows are GONE
from the manifest rather than carried as review debt, and what survives is the
ORIGIN of each -- recorded in `SETTLED_SCHEMA_USAGE`, because the origins are
not the same fact and flattening them would lose the one that matters.  Four
schemas hold it by a DIRECT `app_user:USAGE` grant; `public` holds it via
`PUBLIC:USAGE`, which is PostgreSQL's own default and reaches every login in
the cluster.  A privilege reached through `PUBLIC` is exactly what
`REJECTED_PRIVILEGE_ORIGINS` refuses everywhere else in this manifest, so
`public` is a settled no-op for THIS change and an open least-privilege
question for Change 3.

## Facts the census fixed, and what they mean

* **All 1,716 relation privileges are DIRECT grants** for the source role.  Zero arrive via
  `PUBLIC`, zero via ownership.  The baseline is a clean set of deliberate
  grants, so a later run that finds a PUBLIC-derived privilege standing in
  for one of these has found a DEFECT, not an equivalent: `PUBLIC` reaches
  every login in the cluster, including the next one someone creates.
* **All five EXECUTE grants are on `SECURITY DEFINER` functions.**  These are
  not bulk rows and are deliberately not folded into the relation sweep.  A
  `SECURITY DEFINER` function executes as its OWNER -- here `app_admin`,
  which is `BYPASSRLS` -- so granting EXECUTE hands the target whatever that
  owner can do, through whatever the body does.  Every one requires
  individual review before Change 1 is applied.
* **One module-era grant is already in the legacy baseline**:
  `mod_files.platform_stored_files`.  That is a CONTROL-PLANE table under
  ADR-0023, which requires it to be REVOKEd from the tenant application role
  -- and `app_user` is the tenant application role.  That decision is now
  MADE, not deferred: the four rows carry the disposition
  `denied_by_architecture` and render as commented denials rather than
  `GRANT`s.  A denial nobody checks is a comment, so the guard additionally
  PROVES the absence -- `denial_violations` requires that `app_user` holds
  none of the seven table privileges AND no column-level equivalent, and
  fails if the verifier did not look at all.  A column ACL can grant
  `SELECT(col)` where the relation ACL shows nothing, which is exactly the
  half a table-level check misses.
* **The reverse gap is 132 privileges** the TARGET holds and the source does
  not, across five module schemas.  They are preserved, never revoked, and
  recorded as explicit exclusions.  The census records them as a per-schema
  COUNT only, so that exclusion is a count-level ratchet and cannot be
  verified object by object from this artefact.

## Shape

The split mirrors `app.runtime_admission` exactly:

* frozen dataclasses of rows and of observations;
* `manifest_from_census` -- a PURE, deterministic generator over the frozen
  census, so the committed manifest can be regenerated and byte-compared;
* `render_grant_sql` -- a PURE renderer, one statement per row;
* `baseline_violations` and `cutover_violations` -- PURE functions unit
  tested against synthetic inputs including the ones that must make them fire;
* `scripts/generate_privilege_manifest.py` and
  `scripts/verify_identity_cutover_privileges.py` -- the thin adapters.

Nothing in this module connects to a database, and nothing in it writes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
import json
from typing import Any, Final

#: The login the ERP application currently connects as, and the one this
#: programme retires.
SOURCE_ROLE: Final[str] = "dotmac_erp_app"

#: The canonical runtime identity every module GRANT and RLS policy is
#: addressed to. Same constant as `app.runtime_admission.RUNTIME_ROLE`, stated
#: again rather than imported so this contract reads on its own; the
#: architecture test asserts they agree.
TARGET_ROLE: Final[str] = "app_user"

#: The role that OWNS every object in the census. Ownership is not migrated by
#: this programme -- it is recorded so that an ownership CHANGE is detectable.
EXPECTED_OWNER: Final[str] = "app_admin"

#: The schema-name prefix that marks module storage. `dotmac_erp_app` must
#: never gain access to any of it (`MODULE_ERA_ALLOWLIST` is the one frozen,
#: pre-existing exception).
MODULE_SCHEMA_PREFIX: Final[str] = "mod_"

#: The module-era privileges the LEGACY role already held when the census was
#: taken. Frozen: this set may shrink, never grow. Anything else the legacy
#: role holds under a `mod_` schema is a refusal.
MODULE_ERA_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {"relation:mod_files.platform_stored_files"}
)

#: What this manifest DOES with a row. Three outcomes, never a boolean: a
#: boolean can say "not routine" but cannot tell "grant this after a human
#: reads the body" apart from "never grant this at all", and those are
#: opposite instructions to whoever applies the SQL.
DISPOSITION_GRANT: Final[str] = "grant"
DISPOSITION_REVIEW_REQUIRED: Final[str] = "review_required"
DISPOSITION_DENIED: Final[str] = "denied_by_architecture"
DISPOSITIONS: Final[tuple[str, ...]] = (
    DISPOSITION_GRANT,
    DISPOSITION_REVIEW_REQUIRED,
    DISPOSITION_DENIED,
)

#: The reason a row is denied. One string, stated once, so the artefact, the
#: rendered SQL and the guard all say the same words.
DENIAL_REASON: Final[str] = "ADR-0023 control-plane relation"

#: The SEVEN privileges PostgreSQL can hold on a table. A denial that checked
#: only the four the census happened to record would leave TRUNCATE,
#: REFERENCES and TRIGGER unexamined -- and REFERENCES in particular is
#: grantable at column level, which is the half a relation-ACL check misses.
DENIED_TABLE_PRIVILEGES: Final[tuple[str, ...]] = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)

#: The four of those that PostgreSQL also grants per COLUMN. `GRANT
#: SELECT(secret_column)` leaves `relacl` untouched and `has_table_privilege`
#: answering false, so a denial proved only against the relation ACL is not
#: proved at all. `pg_attribute.attacl` / `information_schema.column_privileges`
#: is where these live.
COLUMN_LEVEL_PRIVILEGES: Final[tuple[str, ...]] = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "REFERENCES",
)

SECTION_SCHEMA_USAGE: Final[str] = "schema_usage"
SECTION_RELATIONS: Final[str] = "relations"
SECTION_SEQUENCES: Final[str] = "sequences"
SECTION_FUNCTIONS: Final[str] = "functions"
SECTION_MODULE_ERA: Final[str] = "module_era"

#: Manifest section order. Also the order the SQL renders in, so a reviewer
#: reads the generated file in the same order as the manifest.
SECTIONS: Final[tuple[str, ...]] = (
    SECTION_SCHEMA_USAGE,
    SECTION_RELATIONS,
    SECTION_SEQUENCES,
    SECTION_FUNCTIONS,
    SECTION_MODULE_ERA,
)

#: The two-directional ratchet. These counts are derived from the frozen
#: census and restated here so that a regenerated manifest which quietly grew
#: OR quietly shrank fails the guard. Lowering the baseline is legitimate --
#: it is what Change 3 does -- but it is an edit to this constant, reviewed,
#: never a side effect of a census being retaken.
BASELINE_TOTALS: Final[Mapping[str, int]] = {
    # 37, not 42: the five DERIVED usage rows were removed on 2026-09-04 when
    # the follow-up query settled them as no-ops. Lowering this constant is
    # the reviewed, in-commit act the ratchet demands -- the count did not
    # move on its own.
    SECTION_SCHEMA_USAGE: 37,
    SECTION_RELATIONS: 1712,
    SECTION_SEQUENCES: 3,
    SECTION_FUNCTIONS: 5,
    SECTION_MODULE_ERA: 4,
}

#: Privileges the target must NOT reach through anything but a deliberate
#: direct grant. `ownership` would mean the cutover moved an object;
#: `public` would mean every login in the cluster shares the privilege;
#: `inherited` would mean a role membership this programme forbids;
#: `default` would mean `ALTER DEFAULT PRIVILEGES` is silently arming FUTURE
#: objects, which is a grant nobody wrote down.
ACCEPTED_PRIVILEGE_ORIGIN: Final[str] = "direct"
REJECTED_PRIVILEGE_ORIGINS: Final[tuple[str, ...]] = (
    "ownership",
    "public",
    "inherited",
    "default",
)


class UnparseableSignature(ValueError):
    """A function signature the identity parser refuses to guess at.

    Raising is the point. This parser produces the key that distinguishes one
    overload from another; a silent wrong answer here grants EXECUTE on the
    wrong function body, which is exactly the defect the signature-keyed
    identity exists to prevent.
    """


#: Every full SQL type name the census's five signatures use, plus the common
#: neighbours, normalized to lower case. The parser matches against this set
#: rather than assuming "the first word is the parameter name", because
#: `timestamp with time zone` begins with a word that looks exactly like an
#: identifier. An unknown type raises instead of guessing.
KNOWN_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "bigint",
        "boolean",
        "bytea",
        "character",
        "character varying",
        "date",
        "double precision",
        "inet",
        "integer",
        "interval",
        "json",
        "jsonb",
        "money",
        "name",
        "numeric",
        "oid",
        "real",
        "record",
        "smallint",
        "text",
        "time with time zone",
        "time without time zone",
        "timestamp with time zone",
        "timestamp without time zone",
        "uuid",
        "void",
        "xml",
    }
)

_ARGUMENT_MODES: Final[frozenset[str]] = frozenset({"in", "out", "inout", "variadic"})


def _normalize_type(text: str) -> str:
    """Collapse whitespace, drop any array suffix and length modifier."""
    collapsed = " ".join(text.split()).lower()
    while collapsed.endswith("[]"):
        collapsed = collapsed[:-2].rstrip()
    if collapsed.endswith(")") and "(" in collapsed:
        collapsed = collapsed[: collapsed.index("(")].rstrip()
    return collapsed


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are not inside parentheses (`numeric(10,2)`)."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def function_name_and_identity_arguments(signature: str) -> tuple[str, str]:
    """Split a census signature into `(proname, argument type list)`.

    The census records the signature an operator sees, WITH parameter names
    (`claim_outbox_batch(p_worker text, p_batch integer, ...)`). The identity
    that survives a restore, and the one PostgreSQL uses to tell overloads
    apart, is the ARGUMENT TYPE list alone (`text, integer, integer`). This is
    the deterministic reduction between the two.
    """
    text = signature.strip()
    if not text.endswith(")") or "(" not in text:
        raise UnparseableSignature(f"not a function signature: {signature!r}")
    name = text[: text.index("(")].strip()
    if not name:
        raise UnparseableSignature(f"no function name in {signature!r}")
    inner = text[text.index("(") + 1 : -1].strip()
    if not inner:
        return name, ""
    types: list[str] = []
    for argument in _split_top_level(inner):
        tokens = argument.split()
        if len(tokens) >= 2 and tokens[0].lower() in _ARGUMENT_MODES:
            tokens = tokens[1:]
        whole = _normalize_type(" ".join(tokens))
        if whole in KNOWN_TYPE_NAMES:
            types.append(" ".join(tokens))
            continue
        if len(tokens) >= 2:
            without_name = _normalize_type(" ".join(tokens[1:]))
            if without_name in KNOWN_TYPE_NAMES:
                types.append(" ".join(tokens[1:]))
                continue
        raise UnparseableSignature(
            f"cannot tell the parameter name from the type in {argument!r} "
            f"(signature {signature!r}). Add the type to KNOWN_TYPE_NAMES "
            "after checking it; guessing here would key a grant to the wrong "
            "overload."
        )
    return name, ", ".join(types)


def schema_identity(schema: str) -> str:
    return f"schema:{schema}"


def relation_identity(schema: str, name: str) -> str:
    return f"relation:{schema}.{name}"


def sequence_identity(schema: str, name: str) -> str:
    return f"sequence:{schema}.{name}"


def function_identity(schema: str, name: str, identity_arguments: str) -> str:
    return f"function:{schema}.{name}({identity_arguments})"


def identity_schema(identity: str) -> str:
    """The schema an identity names, without needing the manifest row.

    The verifier reports EXTRA observed privileges by identity alone, and the
    guard has to decide whether each falls in a preserved (never-revoked)
    scope. Parsing the key is enough, and keeps `ObservedPrivilege` a thin
    observation rather than a second copy of the manifest.
    """
    _, _, rest = identity.partition(":")
    if not rest:
        raise ValueError(f"not an identity key: {identity!r}")
    return rest.split(".", 1)[0].split("(", 1)[0]


@dataclass(frozen=True)
class GrantRow:
    """One privilege the cutover must place on the target role.

    `identity` is the OID-independent key; `object_kind` and `relkind` are
    DATA, so a change of kind under a stable name is reported rather than
    matched. `origin` records how the SOURCE role held the privilege when the
    census was taken -- `direct` for all 1,724 object privileges, which is
    what makes a later `public` origin a finding rather than an equivalent.
    """

    section: str
    object_kind: str
    identity: str
    schema: str
    object_name: str
    privilege: str
    source_role: str
    target_role: str
    owner: str
    origin: str
    category: str
    reason: str
    disposition: str
    relkind: str = ""
    signature: str = ""
    identity_arguments: str = ""

    def __post_init__(self) -> None:
        if self.disposition not in DISPOSITIONS:
            raise ValueError(
                f"unknown disposition {self.disposition!r} on {self.identity}; "
                f"the three outcomes are {DISPOSITIONS}"
            )

    @property
    def review_required(self) -> bool:
        """Kept as a READ-ONLY view over the disposition, never a second fact.

        A denied row is not "review required" and must never be reported as
        one: the review is over and the answer was no.
        """
        return self.disposition == DISPOSITION_REVIEW_REQUIRED

    @property
    def denied(self) -> bool:
        return self.disposition == DISPOSITION_DENIED

    def sort_key(self) -> tuple[int, str, str]:
        return (SECTIONS.index(self.section), self.identity, self.privilege)


@dataclass(frozen=True)
class ExclusionRow:
    """Something this change deliberately does NOT do, and why."""

    exclusion_id: str
    kind: str
    scope: str
    privileges: int
    reason: str


@dataclass(frozen=True)
class PrivilegeManifest:
    """The whole reviewed contract for Change 1."""

    captured_at: str
    host: str
    database: str
    server_version: str
    source_role: str
    target_role: str
    rows: tuple[GrantRow, ...]
    exclusions: tuple[ExclusionRow, ...]
    notes: tuple[str, ...]

    def section(self, name: str) -> tuple[GrantRow, ...]:
        return tuple(row for row in self.rows if row.section == name)

    def counts(self) -> dict[str, int]:
        return {name: len(self.section(name)) for name in SECTIONS}

    def review_required(self) -> tuple[GrantRow, ...]:
        """Rows that need a human decision before they may be applied."""
        return tuple(row for row in self.rows if row.review_required)

    def denied(self) -> tuple[GrantRow, ...]:
        """Rows the architecture REFUSES. Never rendered as a `GRANT`."""
        return tuple(row for row in self.rows if row.denied)

    def routine(self) -> tuple[GrantRow, ...]:
        """The mechanical sweep: everything with no judgement call in it."""
        return tuple(
            row for row in self.rows if row.disposition == DISPOSITION_GRANT
        )

    def exceptional(self) -> tuple[GrantRow, ...]:
        """Everything the sweep must NOT contain, in manifest order.

        The split is permanent, not a staging convenience: exceptional
        authorization stays out of mechanical compatibility, so the file a
        reviewer must read line by line stays short enough that they do.
        """
        return tuple(
            sorted(
                (*self.review_required(), *self.denied()), key=GrantRow.sort_key
            )
        )

    def preserved_scopes(self) -> dict[str, int]:
        """Scopes whose TARGET privileges are preserved, and their counts.

        These are the reverse-gap schemas: privileges `app_user` already holds
        that the legacy role does not. They are never revoked, so an extra
        privilege observed inside one of them is not "a privilege appearing" --
        it is the exclusion doing its job. The census recorded them as counts
        only, so the guard ratchets the COUNT in both directions rather than
        pretending to check them object by object.
        """
        return {
            row.scope: row.privileges
            for row in self.exclusions
            if row.kind == "preserved-target-privilege"
        }


# ---------------------------------------------------------------------------
# Generation -- pure, deterministic, from the frozen census
# ---------------------------------------------------------------------------

MIRRORED_USAGE_CATEGORY: Final[str] = "mirrored-schema-usage"

#: The five schemas the census recorded relation privileges in but recorded NO
#: schema-USAGE row for, and how a read-only follow-up query on 2026-09-04
#: settled each. All five came back `legacy=True, app_user=True` -- the DELTA
#: interpretation, so the target already reaches every one of them and the
#: derived GRANT this manifest used to carry would have changed nothing.
#:
#: The rows are therefore REMOVED rather than deferred. What is kept is the
#: ORIGIN, because the origins are NOT the same fact:
#:
#: * `hr`, `rpt` and `sync` hold it by a DIRECT `app_user:USAGE` grant made by
#:   a named ERP migration -- a deliberate, attributable act;
#: * `mod_files` holds it by a DIRECT `app_user:USAGE` grant made by the
#:   composed `dotmac-files` lineage, and its ACL carries `platform_api:USAGE`
#:   alongside. Tenant and platform tables SHARE that schema, which is exactly
#:   why isolation there has to hold at the TABLE level: schema USAGE cannot
#:   separate the two planes and was never meant to;
#: * `public` holds it via `PUBLIC:USAGE` -- PostgreSQL's own default, NOT a
#:   grant to `app_user` at all. That is the same origin
#:   `REJECTED_PRIVILEGE_ORIGINS` refuses everywhere else here, because
#:   `PUBLIC` reaches every login in the cluster including the next one
#:   created. It is a settled no-op for the identity cutover and an open
#:   least-privilege question for Change 3; flattening it into "app_user has
#:   USAGE" would lose precisely that.
SETTLED_SCHEMA_USAGE: Final[Mapping[str, tuple[str, str]]] = {
    "hr": (
        "direct",
        "`app_user:USAGE` held DIRECTLY. ACL: `app_admin:USAGE "
        "app_admin:CREATE dotmac_erp_app:USAGE app_user:USAGE`. Granted by "
        "`alembic/versions/20260828_people_et_activation.py` (and before it "
        "`20260828_people_et_bootstrap.py`).",
    ),
    "mod_files": (
        "direct",
        "`app_user:USAGE` held DIRECTLY. ACL: `app_admin:USAGE "
        "app_admin:CREATE app_user:USAGE platform_api:USAGE "
        "dotmac_erp_app:USAGE` -- it carries `app_user:USAGE` AND "
        "`platform_api:USAGE`, confirming the tenant and platform tables of "
        "the composed `dotmac-files` module share one schema. Isolation "
        "between the planes therefore has to hold at the TABLE level, which "
        "is what the `mod_files.platform_stored_files` denial does.",
    ),
    "public": (
        "public",
        "NOT a direct grant. `app_user` reaches `public` via `PUBLIC:USAGE` "
        "-- PostgreSQL's own default on the `public` schema. ACL: "
        "`pg_database_owner:USAGE pg_database_owner:CREATE PUBLIC:USAGE "
        "dotmac_erp_app:USAGE dotmac_erp_app:CREATE outbox_dispatcher:USAGE "
        "platform_outbox_dispatcher:USAGE`, with no `app_user` entry at all. "
        "`PUBLIC` reaches every login in the cluster, so this is the one "
        "origin `REJECTED_PRIVILEGE_ORIGINS` refuses elsewhere in this "
        "manifest. It makes the derived row a no-op for the identity cutover "
        "and leaves an open question for Change 3.",
    ),
    "rpt": (
        "direct",
        "`app_user:USAGE` held DIRECTLY. ACL: `app_admin:USAGE "
        "app_admin:CREATE dotmac_erp_app:USAGE app_user:USAGE`. Granted by "
        "`alembic/versions/20260828_sales_analysis_refresh_definer.py`.",
    ),
    "sync": (
        "direct",
        "`app_user:USAGE` held DIRECTLY. ACL: `app_admin:USAGE "
        "app_admin:CREATE dotmac_erp_app:USAGE app_user:USAGE`. Granted by "
        "`alembic/versions/20260825_retire_dotmac_crm.py`.",
    ),
}


def _census_relations(census: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(census.get("tables", ()))


def _derived_kind(rows: Iterable[Mapping[str, Any]]) -> str:
    kinds = {str(row["kind"]) for row in rows}
    if len(kinds) != 1:
        raise ValueError(f"one relation reported multiple relkinds: {sorted(kinds)}")
    return kinds.pop()


def manifest_from_census(census: Mapping[str, Any]) -> PrivilegeManifest:
    """Build the manifest. Deterministic: same census in, same bytes out."""
    source = str(census["source_role"])
    target = str(census["target_role"])
    rows: list[GrantRow] = []

    relations = _census_relations(census)
    for row in relations:
        if not row.get("direct_grant", False):
            raise ValueError(
                f"census row {row!r} is not a direct grant; the manifest's "
                "'all 1,716 are direct' claim would be false and the "
                "generator refuses to state it."
            )

    schemas_with_usage = {str(entry["schema"]) for entry in census.get("schemas", ())}
    schemas_with_relations = {str(row["schema"]) for row in relations}
    relation_privilege_counts: dict[str, int] = {}
    for row in relations:
        key = str(row["schema"])
        relation_privilege_counts[key] = relation_privilege_counts.get(key, 0) + 1

    unobserved = sorted(schemas_with_relations - schemas_with_usage)
    if set(unobserved) != set(SETTLED_SCHEMA_USAGE):
        raise ValueError(
            "the set of schemas carrying relation privileges with no observed "
            f"schema-USAGE row is {unobserved}, but SETTLED_SCHEMA_USAGE "
            f"settles {sorted(SETTLED_SCHEMA_USAGE)}. A schema that fell out "
            "of that set is one whose reachability nobody has checked; a new "
            "one is a derived grant this generator refuses to invent."
        )

    for schema in sorted(schemas_with_usage):
        rows.append(
            GrantRow(
                section=SECTION_SCHEMA_USAGE,
                object_kind="schema",
                identity=schema_identity(schema),
                schema=schema,
                object_name=schema,
                privilege="USAGE",
                source_role=source,
                target_role=target,
                owner="",
                origin="direct",
                category=MIRRORED_USAGE_CATEGORY,
                reason=(
                    "The source role holds USAGE on this schema; the target "
                    "needs it to reach anything inside."
                ),
                disposition=DISPOSITION_GRANT,
            )
        )

    by_relation: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in relations:
        by_relation.setdefault((str(row["schema"]), str(row["name"])), []).append(row)

    for (schema, name), entries in sorted(by_relation.items()):
        identity = relation_identity(schema, name)
        relkind = _derived_kind(entries)
        module_era = schema.startswith(MODULE_SCHEMA_PREFIX)
        for entry in sorted(entries, key=lambda item: str(item["privilege"])):
            rows.append(
                GrantRow(
                    section=(SECTION_MODULE_ERA if module_era else SECTION_RELATIONS),
                    object_kind="relation",
                    identity=identity,
                    schema=schema,
                    object_name=name,
                    relkind=relkind,
                    privilege=str(entry["privilege"]),
                    source_role=source,
                    target_role=target,
                    owner=str(entry["owner"]),
                    origin="direct",
                    category=(
                        "denied-by-architecture-control-plane-relation"
                        if module_era
                        else "legacy-estate-compatibility-baseline"
                    ),
                    reason=(
                        f"{DENIAL_REASON}. ADR-0023 requires a module's "
                        "declared `platform_tables` to be REVOKEd from the "
                        "tenant application role, and `app_user` IS the "
                        "tenant application role -- "
                        "`app.runtime_admission` names this exact relation "
                        "as the `files` module's platform table. The legacy "
                        "role holding it is legacy debt to be revoked at "
                        "retirement, not a behaviour to mirror: mirroring it "
                        "would move a control-plane privilege ONTO the "
                        "identity the architecture forbids it on, under "
                        "cover of a compatibility sweep. NOT GRANTED. The "
                        "guard proves the absence at table AND column level."
                        if module_era
                        else (
                            "Held DIRECTLY by the source role at census time "
                            "and not held by the target. Granted so the "
                            "identity cutover changes no behaviour; this is "
                            "the compatibility baseline, not the "
                            "least-privilege target."
                        )
                    ),
                    disposition=(
                        DISPOSITION_DENIED if module_era else DISPOSITION_GRANT
                    ),
                )
            )

    for entry in sorted(
        census.get("sequences", ()),
        key=lambda item: (
            str(item["schema"]),
            str(item["name"]),
            str(item["privilege"]),
        ),
    ):
        schema = str(entry["schema"])
        name = str(entry["name"])
        rows.append(
            GrantRow(
                section=SECTION_SEQUENCES,
                object_kind="sequence",
                identity=sequence_identity(schema, name),
                schema=schema,
                object_name=name,
                privilege=str(entry["privilege"]),
                source_role=source,
                target_role=target,
                owner="",
                origin="direct",
                category="legacy-estate-compatibility-baseline",
                reason=(
                    "Sequence privilege held by the source role. USAGE and "
                    "UPDATE are what let nextval() run; SELECT alone would "
                    "make the allocator fail at its first write."
                ),
                disposition=DISPOSITION_GRANT,
            )
        )

    for entry in sorted(
        census.get("functions", ()),
        key=lambda item: (str(item["schema"]), str(item["signature"])),
    ):
        schema = str(entry["schema"])
        signature = str(entry["signature"])
        name, arguments = function_name_and_identity_arguments(signature)
        security_definer = bool(entry.get("security_definer", False))
        owner = str(entry.get("owner", ""))
        rows.append(
            GrantRow(
                section=SECTION_FUNCTIONS,
                object_kind="function",
                identity=function_identity(schema, name, arguments),
                schema=schema,
                object_name=name,
                signature=signature,
                identity_arguments=arguments,
                privilege="EXECUTE",
                source_role=source,
                target_role=target,
                owner=owner,
                origin="direct",
                category=(
                    "security-definer-execute-individual-review"
                    if security_definer
                    else "function-execute"
                ),
                reason=(
                    "SECURITY DEFINER: this function executes as its owner "
                    f"{owner!r}, so EXECUTE hands the target whatever that "
                    "owner can do through this body. Not a bulk row; review "
                    "the body, its owner and its search_path individually "
                    "before Change 1 is applied."
                    if security_definer
                    else "EXECUTE held by the source role at census time."
                ),
                disposition=(
                    DISPOSITION_REVIEW_REQUIRED
                    if security_definer
                    else DISPOSITION_GRANT
                ),
            )
        )

    exclusions = _exclusions_from_census(census)
    return PrivilegeManifest(
        captured_at=str(census["captured_at"]),
        host=str(census["host"]),
        database=str(census["database"]),
        server_version=str(census["server_version"]),
        source_role=source,
        target_role=target,
        rows=tuple(sorted(rows, key=GrantRow.sort_key)),
        exclusions=exclusions,
        notes=_notes(census, relation_privilege_counts, schemas_with_usage),
    )


def _exclusions_from_census(census: Mapping[str, Any]) -> tuple[ExclusionRow, ...]:
    """The reverse gap, plus every categorical prohibition, stated once."""
    exclusions: list[ExclusionRow] = []
    for entry in sorted(
        census.get("reverse_gap", ()), key=lambda item: str(item["schema"])
    ):
        schema = str(entry["schema"])
        privileges = int(entry["privileges"])
        detail = (
            "Privileges the TARGET role already holds and the source role "
            "does not. Preserved, never revoked: this change adds the legacy "
            "estate to the target, it does not subtract the target's own "
            "module access. The census records this gap as a per-schema "
            "COUNT only -- object-level detail was not captured, so this "
            "exclusion is a count-level ratchet and cannot be verified "
            "object by object from this census."
        )
        if schema == "mod_people":
            detail += (
                " Sharpest reading available: the schema has 6 tables x 4 "
                "privileges = 24, of which the source role holds only 4, so "
                "the dual grant was table-specific to `employment_types` "
                "rather than schema-wide."
            )
        exclusions.append(
            ExclusionRow(
                exclusion_id=f"reverse-gap:{schema}",
                kind="preserved-target-privilege",
                scope=schema,
                privileges=privileges,
                reason=detail,
            )
        )

    for schema, (origin, detail) in sorted(SETTLED_SCHEMA_USAGE.items()):
        exclusions.append(
            ExclusionRow(
                exclusion_id=f"settled-schema-usage:{schema}",
                kind="settled-schema-usage",
                scope=schema,
                privileges=0,
                reason=(
                    f"SETTLED 2026-09-04, origin `{origin}`. A read-only "
                    "follow-up query returned `legacy=True, app_user=True`, "
                    "confirming the DELTA interpretation: the target already "
                    "holds USAGE here, so the derived GRANT this manifest "
                    "used to carry was a no-op and the row is REMOVED rather "
                    f"than deferred. {detail}"
                ),
            )
        )

    for exclusion_id, scope, reason in (
        (
            "denied-control-plane-relation",
            ", ".join(sorted(MODULE_ERA_ALLOWLIST)),
            f"{DENIAL_REASON}. The legacy role holds SELECT/INSERT/UPDATE/"
            "DELETE on this relation; the target role will NOT be given "
            "them. This is the one place the cutover deliberately does not "
            "mirror, because mirroring would carry a control-plane privilege "
            "across the exact boundary ADR-0023 draws. The rows stay in the "
            "manifest with disposition `denied_by_architecture` so the "
            "decision is visible rather than absent, they render as "
            "commented denials rather than GRANTs, and `denial_violations` "
            "proves `app_user` holds none of the seven table privileges and "
            "no column-level equivalent.",
        ),
        (
            "no-grant-all",
            "every generated statement",
            "No `GRANT ALL`. `ALL` is whatever PostgreSQL's privilege set "
            "means on the day it runs, and it silently widens when a version "
            "adds a privilege. Every row names one privilege on one object.",
        ),
        (
            "no-role-membership",
            f"{SOURCE_ROLE} <-> {TARGET_ROLE}",
            "Neither role becomes a member of the other. Membership would "
            "make the cutover reversible by accident, hide which role a "
            "privilege really belongs to, and defeat the direct-grant origin "
            "requirement the verifier enforces.",
        ),
        (
            "no-ownership-transfer",
            f"every object owned by {EXPECTED_OWNER}",
            "No `ALTER ... OWNER TO`. Ownership carries implicit full "
            "privilege plus the right to DROP, and it is what makes RLS "
            "bypassable via `FORCE` semantics. The owner is recorded so a "
            "CHANGE is detectable, never moved.",
        ),
        (
            "no-privileges-added-to-legacy-role",
            SOURCE_ROLE,
            "The source role gains NOTHING. In particular it is not granted "
            "the module access it lacks: it is the role being retired. The "
            "one module-era privilege it already holds "
            f"({', '.join(sorted(MODULE_ERA_ALLOWLIST))}) is frozen and may "
            "only shrink.",
        ),
        (
            "no-role-attribute-change",
            f"{TARGET_ROLE} attributes",
            "No `BYPASSRLS`, no `SUPERUSER`, no `CREATEROLE`. A runtime role "
            "that can step out of row-level security makes every tenant "
            "policy decoration.",
        ),
        (
            "no-module-activation-change",
            "app.runtime_admission activation flags",
            "No module activation flag is touched. Activating a module is a "
            "separate decision with its own obligations "
            "(`app/runtime_admission.py`); an identity cutover that also "
            "flipped one would make an admission failure ambiguous.",
        ),
        (
            "no-revoke",
            "every generated statement",
            "The generated SQL contains no `REVOKE` at all. Reduction is "
            "Change 3, after the cutover is stable.",
        ),
    ):
        exclusions.append(
            ExclusionRow(
                exclusion_id=exclusion_id,
                kind="prohibited-action",
                scope=scope,
                privileges=0,
                reason=reason,
            )
        )
    return tuple(exclusions)


def _notes(
    census: Mapping[str, Any],
    relation_privilege_counts: Mapping[str, int],
    schemas_with_usage: frozenset[str] | set[str],
) -> tuple[str, ...]:
    relations = _census_relations(census)
    derived = sorted(set(relation_privilege_counts) - set(schemas_with_usage))
    unreachable = sum(relation_privilege_counts[schema] for schema in derived)
    return (
        "DELTA: the census records privileges the SOURCE role holds and the "
        "TARGET role does NOT. This manifest is therefore what must be "
        "GRANTED to reach equivalence, not a picture of either role's full "
        "ACL. Equivalence = what app_user already holds UNION these rows.",
        "PROVENANCE: every row is derived from "
        "docs/inventories/erp-privilege-census-2026-09-04.json, captured "
        "read-only from production. The manifest is generated, never "
        "hand-edited; `make privilege-manifest-check` byte-compares it.",
        f"ORIGIN: all {len(relations)} relation privileges in the census are "
        "DIRECT grants -- zero via PUBLIC, zero via ownership. A later run "
        "that finds a PUBLIC-derived privilege standing in for one of these "
        "has found a defect, not an equivalent.",
        "TARGET: legacy compatibility privileges - architecturally forbidden "
        "access - unapproved SECURITY DEFINER execution + module-era "
        "privileges app_user already owns. The file split is a PERMANENT "
        "property of that arithmetic, not a staging convenience: bulk-safe "
        "grants in the bulk file, the five SECURITY DEFINER functions "
        "isolated, the control-plane grants prohibited, the schema cases "
        "resolved. Keeping exceptional authorization separate from "
        "mechanical compatibility is the point.",
        "SECURITY DEFINER: all five EXECUTE rows are on SECURITY DEFINER "
        "functions owned by app_admin (a BYPASSRLS role). They are isolated "
        "in their own section, marked review_required, and rendered into the "
        "review-required SQL file rather than the sweep. Their dispositions "
        "are recorded in "
        "docs/architecture/erp-runtime-identity-cutover.md; nothing here "
        "converts a definer, changes an owner or creates a role, which are "
        "separate authorized acts.",
        "DENIED: relation:mod_files.platform_stored_files carries "
        f"disposition denied_by_architecture ({DENIAL_REASON}). Its four rows "
        "stay in the manifest so the decision is visible, render as "
        "commented denials rather than GRANTs, and are proved absent by "
        "denial_violations at BOTH table and column level -- a column ACL "
        "can grant SELECT(col) where the relation ACL shows nothing.",
        "UNRESOLVED, RECORDED: public.platform_outbox_events is in the "
        "ROUTINE sweep (4 privileges) because its schema is `public` rather "
        "than `mod_`, but alembic revision 20260824_outbox_relay creates it "
        "as the CONTROL-PLANE relay ledger -- no tenant_id, no RLS, granted "
        "to platform_api and app_admin, and explicitly `REVOKE ALL "
        "PRIVILEGES ... FROM app_user` followed by column-level REVOKEs of "
        "SELECT/INSERT/UPDATE/REFERENCES from app_user. Applying the sweep "
        "would REVERSE that deliberate revocation. Same for the EXECUTE row "
        "on hr.enforce_employment_type_projection(), which "
        "20260828_people_et_activation explicitly REVOKEs from app_user. "
        "Neither is changed here: this is a recorded finding for the same "
        "authority that settled the five schema cases, not a disposition "
        "this generator may make on its own.",
        "MATERIALIZED VIEW: rpt.sales_analysis_mv is relkind 'm'. PostgreSQL "
        "has no `GRANT ... ON MATERIALIZED VIEW`, so the rendered statement "
        "says `ON TABLE` -- that is the correct spelling, and the relkind is "
        "kept in the manifest so a kind change is still detectable. "
        "INSERT/UPDATE/DELETE are present in its ACL and are mirrored "
        "faithfully even though DML on a matview is not executable.",
        f"SETTLED: {len(derived)} schemas ({', '.join(derived)}) carry "
        f"{unreachable} relation privileges between them and appear in no "
        "schema-USAGE row. The read-only follow-up query of 2026-09-04 "
        "returned legacy=True, app_user=True for all five: the DELTA reading "
        "is correct, the target already holds USAGE, and the derived GRANT "
        "would have been a no-op. The five rows are REMOVED and "
        "BASELINE_TOTALS lowered 42 -> 37 in the same commit. The ORIGINS "
        "are kept and are NOT uniform: hr, mod_files, rpt and sync hold it "
        "by a DIRECT app_user:USAGE grant, while public holds it via "
        "PUBLIC:USAGE -- PostgreSQL's own default, with no app_user entry in "
        "the ACL at all. See SETTLED_SCHEMA_USAGE and the "
        "settled-schema-usage exclusions.",
        "BASELINE: these counts are the compatibility baseline for the "
        "identity cutover, NOT the permanent least-privilege target. "
        "Reduction is a separate change with its own ratchet.",
    )


# ---------------------------------------------------------------------------
# Serialization -- deterministic
# ---------------------------------------------------------------------------


def manifest_to_json(manifest: PrivilegeManifest) -> str:
    """Stable bytes: sorted keys, fixed indent, one trailing newline."""
    payload = {
        "captured_at": manifest.captured_at,
        "host": manifest.host,
        "database": manifest.database,
        "server_version": manifest.server_version,
        "source_role": manifest.source_role,
        "target_role": manifest.target_role,
        "counts": manifest.counts(),
        "notes": list(manifest.notes),
        "sections": {
            name: [asdict(row) for row in manifest.section(name)] for name in SECTIONS
        },
        "exclusions": [asdict(row) for row in manifest.exclusions],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def manifest_from_json(text: str) -> PrivilegeManifest:
    payload = json.loads(text)
    rows = tuple(
        sorted(
            (
                GrantRow(**row)
                for name in SECTIONS
                for row in payload["sections"].get(name, ())
            ),
            key=GrantRow.sort_key,
        )
    )
    return PrivilegeManifest(
        captured_at=payload["captured_at"],
        host=payload["host"],
        database=payload["database"],
        server_version=payload["server_version"],
        source_role=payload["source_role"],
        target_role=payload["target_role"],
        rows=rows,
        exclusions=tuple(ExclusionRow(**row) for row in payload["exclusions"]),
        notes=tuple(payload["notes"]),
    )


# ---------------------------------------------------------------------------
# SQL rendering -- pure, idempotent by construction
# ---------------------------------------------------------------------------

#: Statement keywords the generated SQL is allowed to contain. Anything else
#: -- REVOKE, ALTER, CREATE, DROP, SET ROLE -- is a generator defect, and the
#: architecture test greps for exactly this.
ALLOWED_SQL_KEYWORDS: Final[tuple[str, ...]] = ("BEGIN", "COMMIT", "GRANT")


def quote_identifier(name: str) -> str:
    if not name or "\x00" in name:
        raise ValueError(f"refusing to quote identifier {name!r}")
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def _grant_statement(row: GrantRow) -> str:
    target = quote_identifier(row.target_role)
    if row.object_kind == "schema":
        return (
            f"GRANT {row.privilege} ON SCHEMA "
            f"{quote_identifier(row.schema)} TO {target};"
        )
    qualified = f"{quote_identifier(row.schema)}.{quote_identifier(row.object_name)}"
    if row.object_kind == "sequence":
        return f"GRANT {row.privilege} ON SEQUENCE {qualified} TO {target};"
    if row.object_kind == "function":
        return (
            f"GRANT {row.privilege} ON FUNCTION "
            f"{qualified}({row.identity_arguments}) TO {target};"
        )
    if row.object_kind == "relation":
        # PostgreSQL has no `GRANT ... ON MATERIALIZED VIEW`; TABLE is the
        # correct object type for relkind 'm' as well as 'r'.
        return f"GRANT {row.privilege} ON TABLE {qualified} TO {target};"
    raise ValueError(f"unknown object kind {row.object_kind!r}")


_HEADER = """\
-- GENERATED FILE -- do not edit. Regenerate with:
--     python scripts/generate_privilege_manifest.py
-- Source of truth: docs/inventories/erp-identity-cutover-manifest-2026-09-04.json
-- Census:          docs/inventories/erp-privilege-census-2026-09-04.json
--
-- {title}
--
-- Apply with:  psql -v ON_ERROR_STOP=1 -f <this file>
--
-- IDEMPOTENT ON RE-RUN. `GRANT` in PostgreSQL is an assertion about an ACL,
-- not an append: granting a privilege the role already holds leaves the ACL
-- byte-identical and returns success. There is nothing to make conditional,
-- no `IF NOT EXISTS` to add, and no ordering that changes the outcome. Every
-- statement below names exactly ONE privilege on exactly ONE object, so a
-- partial re-run converges to the same state as a full one.
--
-- This file contains GRANT statements only. No REVOKE, no ALTER, no CREATE,
-- no DROP, no ownership change, no role membership, no `GRANT ALL`.
--
-- Rows: {count} to grant, {denied} DENIED.
-- A DENIED row is rendered as a comment and is NEVER executed. It is kept
-- here so the refusal is visible: a denial that is merely absent cannot be
-- told apart from a denial nobody thought of.
"""


def render_grant_sql(rows: Sequence[GrantRow], title: str) -> str:
    """Render one statement per row, in manifest order, in one transaction.

    A DENIED row renders as a COMMENT, never as SQL. It is kept in the file
    on purpose: a denial that is simply absent is indistinguishable from a
    denial nobody thought of, and the next person to regenerate this file
    would have no way to tell that the missing GRANT was a decision. The
    executable content stays GRANT-only, so a reviewer reading statements and
    a reviewer reading decisions both get the whole picture.
    """
    ordered = sorted(rows, key=GrantRow.sort_key)
    granted = [row for row in ordered if not row.denied]
    denied = [row for row in ordered if row.denied]
    lines = [
        _HEADER.format(title=title, count=len(granted), denied=len(denied)),
        "",
        "BEGIN;",
        "",
    ]
    current_section = ""
    current_identity = ""
    for row in ordered:
        if row.section != current_section:
            current_section = row.section
            current_identity = ""
            lines.append(f"-- ===== section: {row.section} =====")
        if row.identity != current_identity:
            current_identity = row.identity
            suffix = f" [relkind {row.relkind}]" if row.relkind else ""
            lines.append(f"-- {row.identity}{suffix} -- {row.category}")
            if row.denied:
                lines.append(f"--   DENIED ({row.disposition}): {DENIAL_REASON}.")
                lines.append(
                    "--   The statements below are NOT executed and must not "
                    "be uncommented."
                )
        if row.denied:
            lines.append(f"--   NOT GRANTED: {_grant_statement(row)}")
            continue
        lines.append(_grant_statement(row))
    lines.extend(["", "COMMIT;"])
    return "\n".join(lines) + "\n"


ROUTINE_SQL_TITLE: Final[str] = (
    "Change 1, routine half: mirror the legacy estate onto app_user. "
    "Mechanical rows only -- nothing here needs a judgement call."
)
REVIEW_SQL_TITLE: Final[str] = (
    "Change 1, EXCEPTIONAL half: SECURITY DEFINER function EXECUTE (review "
    "required, one body at a time) and the control-plane module-era relation "
    "(DENIED -- rendered as comments, never executed). DO NOT APPLY until "
    "each remaining row has been individually reviewed and signed off. These "
    "are deliberately NOT folded into the sweep, permanently: exceptional "
    "authorization does not belong inside mechanical compatibility. The five "
    "derived schema-USAGE rows that used to live here were SETTLED on "
    "2026-09-04 as no-ops and removed."
)


# ---------------------------------------------------------------------------
# The guard -- pure decisions over a manifest and a snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedObject:
    """What the catalog says about one manifest identity, after resolution.

    `resolved_identity` is what the verifier ACTUALLY resolved the manifest
    key to. For a function that is `schema.name(argument types)` rebuilt from
    the catalog row it selected, so a manifest key that matched the wrong
    overload -- or matched more than one candidate -- is reported instead of
    being silently accepted. `candidate_count` is the number of catalog rows
    the identity matched; anything but exactly one is ambiguity.
    """

    identity: str
    exists: bool
    object_kind: str
    owner: str
    relkind: str = ""
    resolved_identity: str = ""
    candidate_count: int = 1


@dataclass(frozen=True)
class ObservedPrivilege:
    """One `has_*_privilege` answer, plus where the privilege comes from."""

    identity: str
    role: str
    privilege: str
    held: bool
    origin: str = ACCEPTED_PRIVILEGE_ORIGIN


@dataclass(frozen=True)
class ObservedColumnGrant:
    """The COLUMN-level answer for one (relation, privilege), aggregated.

    `GRANT SELECT(storage_key) ON mod_files.platform_stored_files TO app_user`
    leaves `relacl` untouched and `has_table_privilege(..., 'SELECT')`
    answering FALSE. The grant lives in `pg_attribute.attacl` -- what
    `information_schema.column_privileges` exposes -- and is invisible to
    every relation-level check in this module. A denial proved only against
    the relation ACL is therefore not proved at all, which is why this
    observation exists.

    `columns_probed` is how many columns the verifier actually examined, and
    it is load-bearing: an observation reporting zero columns held because it
    looked at zero columns is not evidence of anything, and the guard refuses
    it rather than reading it as a clean answer.
    """

    identity: str
    role: str
    privilege: str
    columns_held: tuple[str, ...] = ()
    columns_probed: int = 0


@dataclass(frozen=True)
class ObservedRolePosture:
    role: str
    bypassrls: bool
    superuser: bool


@dataclass(frozen=True)
class ObservedMembership:
    member: str
    granted_role: str


@dataclass(frozen=True)
class PrivilegeSnapshot:
    """Everything the read-only verifier observed, in one immutable value."""

    objects: tuple[ObservedObject, ...] = ()
    privileges: tuple[ObservedPrivilege, ...] = ()
    postures: tuple[ObservedRolePosture, ...] = ()
    memberships: tuple[ObservedMembership, ...] = ()
    #: Every privilege observed for the LEGACY role under a `mod_` schema.
    legacy_module_privileges: tuple[ObservedPrivilege, ...] = ()
    #: The NEGATIVE half: table-level answers on every DENIED relation, for
    #: all seven table privileges rather than the four the census recorded.
    denied_privileges: tuple[ObservedPrivilege, ...] = ()
    #: The other negative half: column-level answers on the same relations.
    denied_column_grants: tuple[ObservedColumnGrant, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)


def baseline_violations(manifest: PrivilegeManifest) -> list[str]:
    """Refusal 7 (offline half): the baseline moved without being edited.

    Two-directional on purpose. A baseline that GROWS means the census was
    retaken and something new crept in; a baseline that FALLS means objects
    or grants disappeared and the manifest quietly agreed with the loss. The
    legitimate way to lower it is to edit `BASELINE_TOTALS` in this file --
    reviewed, in a commit, with a reason -- which is exactly what Change 3
    will do.
    """
    violations: list[str] = []
    observed = manifest.counts()
    for section in SECTIONS:
        expected = BASELINE_TOTALS[section]
        actual = observed.get(section, 0)
        if actual == expected:
            continue
        direction = "GREW" if actual > expected else "FELL"
        violations.append(
            f"BASELINE {direction}: section {section!r} holds {actual} rows, "
            f"BASELINE_TOTALS declares {expected}. A baseline that moves on "
            "its own is not evidence of anything. If this is intended, edit "
            "BASELINE_TOTALS in app/privilege_manifest.py in the same commit "
            "and say why."
        )
    return violations


def _object_violations(
    manifest: PrivilegeManifest, observed: Mapping[str, ObservedObject]
) -> list[str]:
    """Refusals 1 (vanished), 4 (kind change) and 5 (overload confusion)."""
    violations: list[str] = []
    seen: set[str] = set()
    for row in manifest.rows:
        if row.identity in seen:
            continue
        seen.add(row.identity)
        entry = observed.get(row.identity)
        if entry is None:
            violations.append(
                f"UNOBSERVED OBJECT: {row.identity} is in the manifest but "
                "the snapshot does not cover it. That is a defect in the "
                "verifier, not a clean database."
            )
            continue
        if not entry.exists:
            violations.append(
                f"OBJECT VANISHED: {row.identity} no longer exists, and "
                "BASELINE_TOTALS was not lowered. An object disappearing is "
                "a change to the estate; the manifest must be regenerated "
                "from a fresh census and the baseline edited deliberately."
            )
            continue
        if entry.object_kind != row.object_kind or entry.relkind != row.relkind:
            violations.append(
                f"KIND CHANGE: {row.identity} was "
                f"{row.object_kind!r}/{row.relkind!r} at census time and is "
                f"now {entry.object_kind!r}/{entry.relkind!r}. A name that "
                "survives while its kind changes is a different object "
                "wearing the same label -- the grant it needs is a different "
                "grant."
            )
        if row.object_kind != "function":
            continue
        if entry.candidate_count != 1:
            violations.append(
                f"OVERLOAD AMBIGUITY: {row.identity} matched "
                f"{entry.candidate_count} catalog rows. A signature that "
                "identifies more than one function identifies none of them; "
                "EXECUTE must never be granted on a guess."
            )
        resolved = entry.resolved_identity or row.identity
        if resolved != row.identity:
            violations.append(
                f"OVERLOAD CONFUSION: the manifest names {row.identity} but "
                f"the catalog resolved {resolved}. These are different "
                "function bodies with potentially different owners; matching "
                "on the bare name is exactly the mistake the signature-keyed "
                "identity exists to prevent."
            )
    return violations


def _privilege_violations(
    manifest: PrivilegeManifest,
    snapshot: PrivilegeSnapshot,
    observed_objects: Mapping[str, ObservedObject],
) -> list[str]:
    """Refusals 2 (added privilege) and 3 (absent expected privilege).

    Objects already reported as VANISHED are skipped here. One planted defect
    should produce one named refusal; answering a dropped table with four
    additional "privilege absent" lines buries the finding it just made.
    """
    vanished = {
        identity for identity, entry in observed_objects.items() if not entry.exists
    }
    # A DENIED relation is owned end to end by `denial_violations`. Leaving it
    # here would make the guard demand `app_user` HOLD the four privileges it
    # just refused -- the exact inversion the denial exists to prevent -- and
    # would answer one planted defect with two complaints from two owners.
    denied_identities = {row.identity for row in manifest.denied()}
    expected = {
        (row.identity, row.privilege): row
        for row in manifest.rows
        if row.target_role == manifest.target_role and not row.denied
    }
    observed = {
        (entry.identity, entry.privilege): entry
        for entry in snapshot.privileges
        if entry.role == manifest.target_role
        and entry.identity not in denied_identities
    }
    violations: list[str] = []
    for key, row in sorted(expected.items()):
        if row.identity in vanished:
            continue
        entry = observed.get(key)
        if entry is None:
            violations.append(
                f"UNOBSERVED PRIVILEGE: {row.privilege} on {row.identity} is "
                "in the manifest but was never probed."
            )
            continue
        if not entry.held:
            violations.append(
                f"PRIVILEGE ABSENT: {manifest.target_role!r} does not hold "
                f"{row.privilege} on {row.identity}, which the manifest "
                "requires. After the cutover this is a request that used to "
                "work and now returns permission denied."
            )
            continue
        if entry.origin != ACCEPTED_PRIVILEGE_ORIGIN:
            violations.append(
                f"PRIVILEGE ORIGIN: {manifest.target_role!r} reaches "
                f"{row.privilege} on {row.identity} via {entry.origin!r}, "
                "not a direct grant. Effective privilege is not the same "
                "fact as a deliberate grant: PUBLIC shares it with every "
                "login in the cluster, inheritance hides which role owns it, "
                "and a default privilege silently arms future objects."
            )
    preserved = manifest.preserved_scopes()
    preserved_seen: dict[str, int] = dict.fromkeys(preserved, 0)
    for key, entry in sorted(observed.items()):
        if key in expected or not entry.held:
            continue
        scope = identity_schema(entry.identity)
        if scope in preserved:
            preserved_seen[scope] += 1
            continue
        violations.append(
            f"PRIVILEGE ADDED: {manifest.target_role!r} holds "
            f"{entry.privilege} on {entry.identity}, which the manifest does "
            "not list and which falls in no preserved scope. The cutover "
            "mirrors a frozen delta; a privilege nobody wrote down is either "
            "drift or an unreviewed grant."
        )
    for scope, expected_count in sorted(preserved.items()):
        actual = preserved_seen[scope]
        if actual == expected_count:
            continue
        direction = "GREW" if actual > expected_count else "FELL"
        violations.append(
            f"EXCLUSION {direction}: the preserved scope {scope!r} holds "
            f"{actual} target privileges the manifest does not list; the "
            f"exclusion records {expected_count}. This change revokes "
            "nothing, so a fall means something outside it took privileges "
            "away, and a rise means module access was granted without "
            "review. The census recorded this scope as a count only, so the "
            "count is the whole ratchet."
        )
    return violations


def _escalation_violations(
    manifest: PrivilegeManifest,
    snapshot: PrivilegeSnapshot,
    observed_objects: Mapping[str, ObservedObject],
) -> list[str]:
    """Refusal 6: ownership, BYPASSRLS/SUPERUSER, or role membership."""
    violations: list[str] = []
    roles = {manifest.source_role, manifest.target_role}
    for membership in snapshot.memberships:
        if membership.member in roles and membership.granted_role in roles:
            violations.append(
                f"ROLE MEMBERSHIP: {membership.member!r} is a member of "
                f"{membership.granted_role!r}. Neither role may be a member "
                "of the other: membership makes the cutover reversible by "
                "accident and turns every direct-grant check into a question "
                "about inheritance."
            )
        elif membership.member == manifest.target_role:
            violations.append(
                f"ROLE MEMBERSHIP: {manifest.target_role!r} is a member of "
                f"{membership.granted_role!r}. The runtime identity must "
                "hold what it holds directly; a membership is a privilege "
                "path nobody reviewed."
            )
    for posture in snapshot.postures:
        if posture.role not in roles:
            continue
        if posture.bypassrls:
            violations.append(
                f"ROLE ATTRIBUTE: {posture.role!r} is BYPASSRLS. Row-level "
                "security a role can step out of is decoration."
            )
        if posture.superuser:
            violations.append(
                f"ROLE ATTRIBUTE: {posture.role!r} is SUPERUSER, which "
                "bypasses row-level security whether or not rolbypassrls is "
                "set."
            )
    checked: set[str] = set()
    for row in manifest.rows:
        if not row.owner or row.identity in checked:
            continue
        checked.add(row.identity)
        entry = observed_objects.get(row.identity)
        if entry is None or not entry.exists:
            continue
        if entry.owner != row.owner:
            violations.append(
                f"OWNERSHIP CHANGE: {row.identity} was owned by "
                f"{row.owner!r} at census time and is now owned by "
                f"{entry.owner!r}. This programme never transfers ownership; "
                "an owner holds implicit full privilege and the right to "
                "DROP, so a moved owner is a larger change than every grant "
                "in this manifest put together."
            )
    return violations


def denial_violations(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> list[str]:
    """Refusal 8: a DENIED relation must be unreachable, and PROVED so.

    Denial is the only disposition in this manifest that asserts an ABSENCE,
    and an absence is the one claim that passes for free. Three things are
    therefore required of every denied relation, and each of them can fail:

    1. all SEVEN table privileges answered -- not the four the census
       happened to record, because TRUNCATE, REFERENCES and TRIGGER are
       privileges too and a denial silent about them denies nothing;
    2. all four COLUMN-grantable privileges answered against the column
       catalog, because `GRANT SELECT(col)` is invisible in `relacl` and
       `has_table_privilege` returns false for a relation the role can
       nonetheless read;
    3. every one of those answers actually TAKEN. A verifier that probed
       nothing produces an empty violation list, which looks exactly like a
       clean database -- so an unprobed privilege is itself a refusal.
    """
    violations: list[str] = []
    denied_identities = sorted({row.identity for row in manifest.denied()})
    if not denied_identities:
        return violations

    table_answers = {
        (entry.identity, entry.privilege): entry
        for entry in snapshot.denied_privileges
        if entry.role == manifest.target_role
    }
    column_answers = {
        (entry.identity, entry.privilege): entry
        for entry in snapshot.denied_column_grants
        if entry.role == manifest.target_role
    }
    for identity in denied_identities:
        for privilege in DENIED_TABLE_PRIVILEGES:
            entry = table_answers.get((identity, privilege))
            if entry is None:
                violations.append(
                    f"UNPROBED DENIAL: {privilege} on {identity} was never "
                    f"asked about for {manifest.target_role!r}. "
                    f"{DENIAL_REASON}: this relation is denied, and a denial "
                    "the verifier did not test is indistinguishable from a "
                    "clean answer. All "
                    f"{len(DENIED_TABLE_PRIVILEGES)} table privileges must be "
                    "answered."
                )
                continue
            if entry.held:
                violations.append(
                    f"DENIED PRIVILEGE HELD: {manifest.target_role!r} holds "
                    f"{privilege} on {identity}. {DENIAL_REASON} -- ADR-0023 "
                    "requires it REVOKEd from the tenant application role, "
                    "and this manifest never grants it. Its presence is a "
                    "grant made outside this programme."
                )
        for privilege in COLUMN_LEVEL_PRIVILEGES:
            entry = column_answers.get((identity, privilege))
            if entry is None or entry.columns_probed == 0:
                probed = "no column observation" if entry is None else "0 columns"
                violations.append(
                    f"UNPROBED COLUMN DENIAL: {privilege} on {identity} has "
                    f"{probed} for {manifest.target_role!r}. A column ACL "
                    "grants where the relation ACL shows nothing, so a "
                    "table-level denial alone proves nothing; read "
                    "pg_attribute.attacl / "
                    "information_schema.column_privileges."
                )
                continue
            if entry.columns_held:
                violations.append(
                    f"DENIED COLUMN PRIVILEGE HELD: {manifest.target_role!r} "
                    f"holds {privilege} on {identity} at COLUMN level: "
                    f"{', '.join(entry.columns_held)}. {DENIAL_REASON}. This "
                    "is the grant a relation-ACL check cannot see -- "
                    "has_table_privilege answers false while the role reads "
                    "the column."
                )
    return violations


def _legacy_module_violations(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> list[str]:
    """Refusal 7: `dotmac_erp_app` must never gain module access."""
    violations: list[str] = []
    for entry in sorted(
        snapshot.legacy_module_privileges,
        key=lambda item: (item.identity, item.privilege),
    ):
        if entry.role != manifest.source_role or not entry.held:
            continue
        if entry.identity in MODULE_ERA_ALLOWLIST:
            continue
        violations.append(
            f"LEGACY MODULE PRIVILEGE: {manifest.source_role!r} holds "
            f"{entry.privilege} on {entry.identity}. The legacy role is "
            "being retired, not extended: module access must reach "
            f"{manifest.target_role!r} only. The one frozen exception is "
            f"{sorted(MODULE_ERA_ALLOWLIST)}, and that set may shrink, never "
            "grow."
        )
    return violations


def cutover_violations(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> list[str]:
    """Every refusal, over one manifest and one snapshot. PURE.

    Seven refusals, in the order a reviewer reads them:

    1. an object disappearing without the baseline being lowered;
    2. a new privilege appearing on the target;
    3. an expected privilege being absent (or reached by the wrong origin);
    4. an object changing kind;
    5. a function overload confused with another;
    6. ownership, BYPASSRLS/SUPERUSER, or role membership being granted;
    7. a module privilege being added to the legacy role -- plus the
       two-directional baseline ratchet, which is the same refusal viewed
       offline;
    8. a DENIED relation being reachable after all, at table OR column level,
       or the verifier not having looked (`denial_violations`).
    """
    observed_objects = {entry.identity: entry for entry in snapshot.objects}
    return [
        *baseline_violations(manifest),
        *_object_violations(manifest, observed_objects),
        *_privilege_violations(manifest, snapshot, observed_objects),
        *_escalation_violations(manifest, snapshot, observed_objects),
        *_legacy_module_violations(manifest, snapshot),
        *denial_violations(manifest, snapshot),
    ]


#: Synthetic column count for the clean fixture. Only "non-zero" matters.
_CLEAN_COLUMNS_PROBED: Final[int] = 12


def clean_snapshot(manifest: PrivilegeManifest) -> PrivilegeSnapshot:
    """The snapshot a correctly cut-over database would produce.

    This is the NEGATIVE CONTROL for the sensitivity proofs (ADR-0018): a
    detector that flagged everything would "catch" all eight planted defects
    while proving nothing. It is also the fixture each proof mutates, so a
    proof can only pass by the mutation being noticed.
    """
    objects: list[ObservedObject] = []
    seen: set[str] = set()
    for row in manifest.rows:
        if row.identity in seen:
            continue
        seen.add(row.identity)
        objects.append(
            ObservedObject(
                identity=row.identity,
                exists=True,
                object_kind=row.object_kind,
                owner=row.owner,
                relkind=row.relkind,
                resolved_identity=row.identity,
                candidate_count=1,
            )
        )
    privileges = [
        ObservedPrivilege(
            identity=row.identity,
            role=manifest.target_role,
            privilege=row.privilege,
            held=True,
            origin=ACCEPTED_PRIVILEGE_ORIGIN,
        )
        for row in manifest.rows
        if not row.denied
    ]
    # The reverse gap: privileges the target already holds inside a preserved
    # scope. Synthesized at the recorded COUNT, because that is the only
    # granularity the census captured -- and included here so the exclusion
    # ratchet is exercised by the clean run rather than only by a planted
    # defect.
    for scope, count in sorted(manifest.preserved_scopes().items()):
        for index in range(count):
            privileges.append(
                ObservedPrivilege(
                    identity=relation_identity(scope, f"preserved_{index:03d}"),
                    role=manifest.target_role,
                    privilege="SELECT",
                    held=True,
                    origin=ACCEPTED_PRIVILEGE_ORIGIN,
                )
            )
    legacy = [
        ObservedPrivilege(
            identity=row.identity,
            role=manifest.source_role,
            privilege=row.privilege,
            held=True,
            origin=ACCEPTED_PRIVILEGE_ORIGIN,
        )
        for row in manifest.section(SECTION_MODULE_ERA)
    ]
    # The denial half of a CORRECTLY cut-over database: every one of the
    # seven table privileges answered and false, every column-grantable
    # privilege answered against a non-empty column set and holding nothing.
    # `_CLEAN_COLUMNS_PROBED` stands for "the verifier examined the
    # relation's columns" -- the count is synthetic because the census never
    # captured column names, and only its being NON-ZERO is load-bearing.
    denied_identities = sorted({row.identity for row in manifest.denied()})
    denied_privileges = [
        ObservedPrivilege(
            identity=identity,
            role=manifest.target_role,
            privilege=privilege,
            held=False,
            origin="none",
        )
        for identity in denied_identities
        for privilege in DENIED_TABLE_PRIVILEGES
    ]
    denied_column_grants = [
        ObservedColumnGrant(
            identity=identity,
            role=manifest.target_role,
            privilege=privilege,
            columns_held=(),
            columns_probed=_CLEAN_COLUMNS_PROBED,
        )
        for identity in denied_identities
        for privilege in COLUMN_LEVEL_PRIVILEGES
    ]
    return PrivilegeSnapshot(
        objects=tuple(objects),
        privileges=tuple(privileges),
        postures=(
            ObservedRolePosture(manifest.source_role, False, False),
            ObservedRolePosture(manifest.target_role, False, False),
        ),
        memberships=(),
        legacy_module_privileges=tuple(legacy),
        denied_privileges=tuple(denied_privileges),
        denied_column_grants=tuple(denied_column_grants),
    )
