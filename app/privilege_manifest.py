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

## The two Change-1 blockers, ruled on 2026-09-04

Michael ruled both open manifest items BLOCKERS of Change 1 rather than
optional hardening, and both are applied here.

**Decision 1 -- all five `SECURITY DEFINER` EXECUTE grants are DENIED.**  They
were `review_required`; they are now `denied_by_architecture`, and they stay in
the manifest as deliberate denials because a denial that is merely absent
cannot be told apart from a denial nobody thought of.  Each records its
PERMITTED EXECUTOR (`FUNCTION_EXECUTOR_DECLARATIONS`), and the generator emits
no `GRANT EXECUTE` at all.  Michael's reason is the subtle half: *"Even though
a trigger function cannot be invoked as an ordinary function, granting it
remains unnecessary and would reverse a tested migration decision."*

**Decision 2 -- the `mod_` heuristic is GONE; the plane is resolved by
declaration** (`app.persistence_planes`).  A module's `tables` are tenant
plane, its `platform_tables` are control plane, the host assembly declares its
own, and anything unclassified REFUSES generation.  The prefix, the `public`
schema, a `tenant_id` column, RLS state and current ACLs are evidence to
VALIDATE a declaration, never sources of ownership.  That change moves four
more relations out of the bulk sweep -- see "What the heuristic was getting
wrong" below.

## The target, stated once

    legacy compatibility privileges
      - architecturally forbidden access
      - unapproved `SECURITY DEFINER` execution
      + module-era privileges `app_user` already owns

Everything below is that arithmetic made executable.  The two files it
produces stay SPLIT, permanently: bulk-safe grants in the grant file, every
denial in a comment-only LEDGER that contains no executable statement at all.
Collapsing them would put exceptional authorization back inside mechanical
compatibility, which is the one thing this shape exists to prevent -- a
1,700-line file with escalation decisions buried in it gets skimmed.

## What the heuristic was getting wrong

`public.platform_outbox_events` is the proof.  Its qualified name looks like
part of the legacy estate; its declared behaviour and its migration are
control plane.  Reading the plane off the schema name got it, and three others,
exactly backwards:

| relation | migration | what the migration does |
| --- | --- | --- |
| `public.platform_outbox_events` | `20260824_outbox_relay` | no tenant column, no RLS, `REVOKE ALL PRIVILEGES ... FROM app_user` + column-level REVOKEs |
| `public.platform_idempotency_records` | `20260820_idempotency_ledger` | the same shape, beside the tenant ledger |
| `public.tenants` | `20260813_tenant_projection` | `REVOKE ALL ... FROM PUBLIC` and `FROM app_user`; read through a narrow definer instead |
| `public.tenant_domains` | `20260813_tenant_projection` | the same block |

Granting any of the four would have REVERSED a tested migration decision under
cover of a compatibility sweep.  All five control-plane relations -- these four
plus the one composed module's platform table -- are now `denied_by_architecture`
and render no SQL.

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
* **All five EXECUTE grants are on `SECURITY DEFINER` functions, and all five
  are DENIED.**  A `SECURITY DEFINER` function executes as its OWNER -- here
  `app_admin`, which is `BYPASSRLS` -- so granting EXECUTE hands the target
  whatever that owner can do, through whatever the body does.  The review
  happened, the bodies were read, and the answer for every one was no; the
  permitted executor of each is recorded instead
  (`FUNCTION_EXECUTOR_DECLARATIONS`).  The verifier proves the denial with an
  EFFECTIVE privilege question, not a reading of the ACL -- see
  `function_denial_violations` for why `PUBLIC` is the half that matters.
* **Five relations are CONTROL PLANE by declaration**, and every one of them
  is `denied_by_architecture`.  ADR-0023 requires a control-plane relation to
  be REVOKEd from the tenant application role, and `app_user` IS the tenant
  application role.  The rows stay in the manifest and render as commented
  denials in the ledger rather than as `GRANT`s.  A denial nobody checks is a
  comment, so the guard additionally PROVES the absence -- `denial_violations`
  requires that `app_user` holds none of the seven table privileges AND no
  column-level equivalent, and fails if the verifier did not look at all.  A
  column ACL can grant `SELECT(col)` where the relation ACL shows nothing,
  which is exactly the half a table-level check misses.
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

from app.persistence_planes import (
    FORBIDDEN_PLANE_SIGNALS,
    PLANE_CONTROL,
    PLANE_TENANT,
    PlaneResolver,
    default_resolver,
)

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
#:
#: This is a NAMESPACE fact and nothing more. It answers "is this storage owned
#: by a composed module rather than by the host assembly", which is the
#: question the legacy-role refusal asks. It MUST NOT be consulted to decide a
#: PLANE: that was the heuristic Decision 2 removed, and it got four `public`
#: control-plane relations wrong. Plane comes from
#: `app.persistence_planes.PlaneResolver` and from nowhere else.
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

#: Why a row is denied. Two reasons, because there are two refusals and they
#: are not the same refusal: one is about a RELATION the tenant role may not
#: reach, the other about a FUNCTION the tenant role may not execute. Each is
#: stated once so the artefact, the rendered ledger and the guard all say the
#: same words.
DENIAL_REASON: Final[str] = "ADR-0023 control-plane relation"
DENIAL_REASON_EXECUTE: Final[str] = (
    "SECURITY DEFINER EXECUTE denied: the permitted executor is not the tenant "
    "application role"
)
DENIAL_REASONS: Final[tuple[str, ...]] = (DENIAL_REASON, DENIAL_REASON_EXECUTE)

#: PostgreSQL's pseudo-role. It is not a login and it is not in `pg_roles` as a
#: grantee you can revoke from by accident -- it is every role in the cluster,
#: including the next one someone creates. `CREATE FUNCTION` grants EXECUTE to
#: it BY DEFAULT, which is why the denial verifier asks about it by name.
PUBLIC_PSEUDO_ROLE: Final[str] = "PUBLIC"

#: The sentinel for "no runtime principal needs to execute this at all". Used
#: by the trigger fence: PostgreSQL checks EXECUTE on a trigger function when
#: the TRIGGER IS CREATED, not when it fires, and a trigger function cannot be
#: invoked as an ordinary function. So there is no executor to name -- and the
#: declaration must SAY so rather than leaving the field blank by accident,
#: which is what `executor_note` is checked for.
NO_RUNTIME_EXECUTOR: Final[str] = ""


@dataclass(frozen=True)
class ExecutorDeclaration:
    """Who MAY execute a denied `SECURITY DEFINER` function, and on what say-so.

    A denial that only says "not app_user" leaves the operational question
    open: somebody still drains the outbox. Recording the permitted executor
    turns the refusal into a complete instruction -- the tenant role may not,
    THIS principal may -- and gives the verifier a positive assertion to make,
    so the denial cannot pass by the function having become unreachable to
    everyone.
    """

    identity: str
    permitted_executor: str
    executor_note: str
    authority: str

    def __post_init__(self) -> None:
        if not self.executor_note:
            raise ValueError(
                f"{self.identity} declares no executor note. A blank executor "
                "field must be a stated decision ('no runtime principal needs "
                "this') rather than an omission, because the two are "
                "indistinguishable once written."
            )
        if not self.authority:
            raise ValueError(f"{self.identity} cites no authority for its executor")

    @property
    def has_runtime_executor(self) -> bool:
        return self.permitted_executor != NO_RUNTIME_EXECUTOR


#: The permitted executor of every `SECURITY DEFINER` function in the census.
#: Generation REFUSES a census function that is not declared here: an
#: undeclared escalation surface is not one this manifest may dispose of.
FUNCTION_EXECUTOR_DECLARATIONS: Final[Mapping[str, ExecutorDeclaration]] = {
    "function:hr.enforce_employment_type_projection()": ExecutorDeclaration(
        identity="function:hr.enforce_employment_type_projection()",
        permitted_executor=NO_RUNTIME_EXECUTOR,
        executor_note=(
            "Trigger installation and operation ONLY. PostgreSQL checks "
            "EXECUTE on a trigger function when the trigger is CREATED, not "
            "when it fires, and a trigger function cannot be invoked as an "
            "ordinary function -- so NO RUNTIME PRINCIPAL needs direct "
            "execution and granting it confers nothing callable. Granting it "
            "anyway remains unnecessary AND would reverse a tested migration "
            "decision, which is the reason that matters."
        ),
        authority=(
            "alembic/versions/20260828_people_et_activation.py creates the "
            "fence and its BEFORE INSERT OR UPDATE trigger, then `REVOKE ALL "
            "ON FUNCTION hr.enforce_employment_type_projection() FROM PUBLIC` "
            "and `... FROM app_user`, covered by "
            "tests/migrations/test_people_employment_type_activation_migration"
            ".py."
        ),
    ),
    "function:public.claim_outbox_batch(text, integer, integer)": (
        ExecutorDeclaration(
            identity="function:public.claim_outbox_batch(text, integer, integer)",
            permitted_executor="outbox_dispatcher",
            executor_note=(
                "The tenant relay's drain identity. The body carries NO tenant "
                "predicate at all -- the cross-tenant drain IS the function -- "
                "so it requires BYPASSRLS by construction and is an "
                "administrative capability by that rule."
            ),
            authority=(
                "alembic/versions/20260824_outbox_relay.py: `REVOKE ALL ON "
                "FUNCTION ... FROM PUBLIC` then `GRANT EXECUTE ... TO "
                "outbox_dispatcher`, a role the migration refuses to create, "
                "fails closed if absent, and pins to (rolbypassrls, rolsuper) "
                "= (False, False)."
            ),
        )
    ),
    (
        "function:public.settle_outbox_event"
        "(uuid, text, text, timestamp with time zone, integer, text)"
    ): ExecutorDeclaration(
        identity=(
            "function:public.settle_outbox_event"
            "(uuid, text, text, timestamp with time zone, integer, text)"
        ),
        permitted_executor="outbox_dispatcher",
        executor_note=(
            "The other half of the tenant relay pair: same ledger, same absent "
            "tenant predicate, same drain identity."
        ),
        authority=(
            "alembic/versions/20260824_outbox_relay.py grants EXECUTE on this "
            "pair to outbox_dispatcher after revoking from PUBLIC."
        ),
    ),
    "function:public.claim_platform_outbox_batch(text, integer, integer)": (
        ExecutorDeclaration(
            identity=(
                "function:public.claim_platform_outbox_batch(text, integer, integer)"
            ),
            permitted_executor="platform_outbox_dispatcher",
            executor_note=(
                "The CONTROL-PLANE relay's named dispatcher. The body operates "
                "on the control-plane ledger ADR-0023 forbids the tenant role, "
                "so EXECUTE here would be a SECURITY DEFINER path to precisely "
                "the rows that relation's REVOKE exists to withhold."
            ),
            authority=(
                "alembic/versions/20260824_outbox_relay.py: `REVOKE ALL ON "
                "FUNCTION ... FROM PUBLIC` then `GRANT EXECUTE ... TO "
                "platform_outbox_dispatcher`, under the same (False, False) "
                "contract."
            ),
        )
    ),
    (
        "function:public.settle_platform_outbox_event"
        "(uuid, text, text, timestamp with time zone, integer, text)"
    ): ExecutorDeclaration(
        identity=(
            "function:public.settle_platform_outbox_event"
            "(uuid, text, text, timestamp with time zone, integer, text)"
        ),
        permitted_executor="platform_outbox_dispatcher",
        executor_note=(
            "The other half of the control-plane relay pair, with the same "
            "named platform dispatcher as its administrative executor."
        ),
        authority=(
            "alembic/versions/20260824_outbox_relay.py grants EXECUTE on this "
            "pair to platform_outbox_dispatcher after revoking from PUBLIC."
        ),
    ),
}

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
#: Replaces the old `module_era` section. That section grouped by SCHEMA NAME
#: and therefore held exactly one relation while four more with the identical
#: problem sat in the bulk sweep. This one groups by the resolved PLANE, so a
#: control-plane relation lands here wherever it lives.
SECTION_CONTROL_PLANE: Final[str] = "control_plane"

#: Manifest section order. Also the order the SQL renders in, so a reviewer
#: reads the generated file in the same order as the manifest.
SECTIONS: Final[tuple[str, ...]] = (
    SECTION_SCHEMA_USAGE,
    SECTION_RELATIONS,
    SECTION_SEQUENCES,
    SECTION_FUNCTIONS,
    SECTION_CONTROL_PLANE,
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
    # 1696, not 1712: Decision 2 resolved the plane by declaration and moved
    # 16 privileges (four `public` relations x four) into `control_plane`.
    # The census total is unchanged at 1,716 relation privileges; what moved
    # is which side of the boundary they were on.
    SECTION_RELATIONS: 1696,
    SECTION_SEQUENCES: 3,
    SECTION_FUNCTIONS: 5,
    # 20, not 4: the old `module_era` section held one relation because it was
    # selected by schema name. Five relations are control plane by
    # declaration -- one composed module's platform table and four in `public`.
    SECTION_CONTROL_PLANE: 20,
}

#: The DENIAL ratchet, two-directional like the baseline and for the same
#: reason. A denial that quietly disappears is the failure this whole shape
#: exists to prevent, and a denial that quietly appears is an unreviewed
#: refusal of access somebody may be relying on. Both are edits to this
#: constant or they are defects.
DENIED_TOTALS: Final[Mapping[str, int]] = {
    SECTION_CONTROL_PLANE: 20,
    SECTION_FUNCTIONS: 5,
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
    #: Which refusal this row is, when it is denied. Empty otherwise. Carried
    #: as DATA so the ledger, the guard and the artefact quote one string.
    denial_reason: str = ""
    #: The resolved persistence plane and the declaration that decided it.
    #: Empty for rows a plane does not apply to (a schema, a function). This
    #: is the audit trail Decision 2 demands: a reader can see not only that a
    #: relation was denied but WHICH declaration denied it.
    plane: str = ""
    plane_declared_by: str = ""
    #: Who MAY do this, when the target role may not. A control-plane
    #: relation's operators, or a denied function's permitted executor.
    permitted_principals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.disposition not in DISPOSITIONS:
            raise ValueError(
                f"unknown disposition {self.disposition!r} on {self.identity}; "
                f"the three outcomes are {DISPOSITIONS}"
            )
        if self.disposition == DISPOSITION_DENIED and not self.denial_reason:
            raise ValueError(
                f"{self.identity} is denied but states no reason. A refusal "
                "whose reason is inferred from its section is a refusal that "
                "silently changes meaning when the sections do."
            )
        if self.denial_reason and self.denial_reason not in DENIAL_REASONS:
            raise ValueError(
                f"unknown denial reason {self.denial_reason!r} on "
                f"{self.identity}; the reasons are {DENIAL_REASONS}"
            )
        if self.plane and self.plane not in (PLANE_TENANT, PLANE_CONTROL):
            raise ValueError(f"unknown plane {self.plane!r} on {self.identity}")
        # JSON has no tuples. A row rebuilt from the artefact would otherwise
        # carry a list here and compare unequal to the row that generated it.
        if not isinstance(self.permitted_principals, tuple):
            object.__setattr__(
                self, "permitted_principals", tuple(self.permitted_principals)
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

    def denied_relations(self) -> tuple[GrantRow, ...]:
        """Denied rows a TABLE privilege question can be asked about.

        Split from `denied_functions` because the two denials are proved by
        different catalog questions -- `has_table_privilege` and a column ACL
        for one, `has_function_privilege` against three principals for the
        other. Asking a relation question about a function returns an error,
        not a clean answer.
        """
        return tuple(row for row in self.denied() if row.object_kind == "relation")

    def denied_functions(self) -> tuple[GrantRow, ...]:
        return tuple(row for row in self.denied() if row.object_kind == "function")

    def denied_counts(self) -> dict[str, int]:
        """Denied rows per section, for the two-directional denial ratchet."""
        counted: dict[str, int] = {}
        for row in self.denied():
            counted[row.section] = counted.get(row.section, 0) + 1
        return counted

    def control_plane_identities(self) -> tuple[str, ...]:
        return tuple(
            sorted({row.identity for row in self.rows if row.plane == PLANE_CONTROL})
        )

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


def manifest_from_census(
    census: Mapping[str, Any], resolver: PlaneResolver | None = None
) -> PrivilegeManifest:
    """Build the manifest. Deterministic: same census in, same bytes out.

    `resolver` is injectable so the sensitivity proofs can plant a DIFFERENT
    declaration and watch the result change. That is the non-vacuity half of
    Decision 2: a resolver whose answer never moves when the declaration moves
    is not reading the declaration.
    """
    planes = resolver if resolver is not None else default_resolver()
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
    unsettled = sorted(set(unobserved) - set(SETTLED_SCHEMA_USAGE))
    if unsettled:
        raise ValueError(
            f"{unsettled} carry relation privileges with no observed "
            "schema-USAGE row and are not in SETTLED_SCHEMA_USAGE "
            f"({sorted(SETTLED_SCHEMA_USAGE)}). A schema whose reachability "
            "nobody has checked is a derived grant this generator refuses to "
            "invent. Settle it with a read-only query and record the ORIGIN, "
            "as the five of 2026-09-04 were."
        )
    # The other direction -- a settled schema that now DOES carry an observed
    # USAGE row -- is not a refusal here: it is more information, not less,
    # and the row it produces moves BASELINE_TOTALS, which is the ratchet that
    # owns it. The frozen production census is asserted to match
    # SETTLED_SCHEMA_USAGE exactly by
    # tests/architecture/test_privilege_manifest.py, where the equality is a
    # claim about THAT capture rather than about every census this pure
    # function may be handed.

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
        # THE plane decision, and the only one. It comes from a declaration or
        # it does not come at all: `UnclassifiedRelation` propagates out of
        # generation deliberately, because an undeclared relation defaulted to
        # the tenant plane is exactly the defect Decision 2 removed.
        verdict = planes.resolve(schema, name)
        control = verdict.control_plane
        moved = (
            " The declaration records this relation in schema "
            f"{verdict.declared_schema!r} and it was observed in "
            f"{schema!r}; a schema MOVE does not change what a relation is, "
            "so the plane is unchanged and the move is reported separately."
            if verdict.schema_moved
            else ""
        )
        for entry in sorted(entries, key=lambda item: str(item["privilege"])):
            rows.append(
                GrantRow(
                    section=(
                        SECTION_CONTROL_PLANE if control else SECTION_RELATIONS
                    ),
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
                        if control
                        else "legacy-estate-compatibility-baseline"
                    ),
                    reason=(
                        f"{DENIAL_REASON}. Declared CONTROL PLANE by "
                        f"{verdict.declared_by}: {verdict.authority} ADR-0023 "
                        "requires a control-plane relation to be REVOKEd from "
                        "the tenant application role, and `app_user` IS the "
                        "tenant application role. The legacy role holding it "
                        "is debt to be revoked at retirement, not a behaviour "
                        "to mirror: mirroring it would move a control-plane "
                        "privilege ONTO the identity the architecture forbids "
                        "it on, under cover of a compatibility sweep. NOT "
                        "GRANTED. The guard proves the absence at table AND "
                        f"column level.{moved}"
                        if control
                        else (
                            "Held DIRECTLY by the source role at census time "
                            "and not held by the target. Declared TENANT "
                            f"PLANE by {verdict.declared_by}. Granted so the "
                            "identity cutover changes no behaviour; this is "
                            "the compatibility baseline, not the "
                            f"least-privilege target.{moved}"
                        )
                    ),
                    disposition=(
                        DISPOSITION_DENIED if control else DISPOSITION_GRANT
                    ),
                    denial_reason=DENIAL_REASON if control else "",
                    plane=verdict.plane,
                    plane_declared_by=verdict.declared_by,
                    permitted_principals=verdict.permitted_principals,
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
        # A sequence gets the same declaration question as a relation. An
        # allocator behind a control-plane relation is control plane too, and
        # exempting sequences would leave a hole shaped exactly like the one
        # the `mod_` heuristic left.
        verdict = planes.resolve(schema, name)
        if verdict.control_plane:
            raise ValueError(
                f"sequence {schema}.{name} resolves to the CONTROL plane "
                f"({verdict.declared_by}). This generator has never had to "
                "deny a sequence and refuses to invent the disposition: "
                "decide it deliberately and add the branch, rather than "
                "letting a control-plane allocator be granted by a code path "
                "nobody wrote for it."
            )
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
                    "make the allocator fail at its first write. Declared "
                    f"TENANT PLANE by {verdict.declared_by}."
                ),
                disposition=DISPOSITION_GRANT,
                plane=verdict.plane,
                plane_declared_by=verdict.declared_by,
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
        identity = function_identity(schema, name, arguments)
        if not security_definer:
            raise ValueError(
                f"{identity} is not SECURITY DEFINER. Every function in the "
                "frozen census is, and the disposition below is written for "
                "that fact; an invoker-rights function needs its own reviewed "
                "decision rather than inheriting a denial written about "
                "escalation."
            )
        executor = FUNCTION_EXECUTOR_DECLARATIONS.get(identity)
        if executor is None:
            raise ValueError(
                f"{identity} has no entry in FUNCTION_EXECUTOR_DECLARATIONS. "
                "A denial that cannot say who MAY execute the function is an "
                "instruction with half its content missing -- somebody still "
                "drains the outbox. Declare the permitted executor, with the "
                "migration that grants it, before this manifest disposes of "
                "the row."
            )
        executor_clause = (
            f"PERMITTED EXECUTOR: {executor.permitted_executor!r}. "
            if executor.has_runtime_executor
            else "PERMITTED EXECUTOR: none -- no runtime principal. "
        )
        rows.append(
            GrantRow(
                section=SECTION_FUNCTIONS,
                object_kind="function",
                identity=identity,
                schema=schema,
                object_name=name,
                signature=signature,
                identity_arguments=arguments,
                privilege="EXECUTE",
                source_role=source,
                target_role=target,
                owner=owner,
                origin="direct",
                category="denied-by-architecture-security-definer-execute",
                reason=(
                    f"{DENIAL_REASON_EXECUTE}. SECURITY DEFINER: this "
                    f"function executes as its owner {owner!r}, a BYPASSRLS "
                    "role, so EXECUTE would hand the target whatever that "
                    "owner can do through this body. The review happened and "
                    f"the answer was no. {executor_clause}"
                    f"{executor.executor_note} {executor.authority} NOT "
                    "GRANTED: even where EXECUTE would confer nothing "
                    "callable, granting it remains unnecessary AND would "
                    "reverse a tested migration decision. The guard proves "
                    "the absence with an EFFECTIVE privilege question asked "
                    f"of {target!r} AND of {PUBLIC_PSEUDO_ROLE}."
                ),
                disposition=DISPOSITION_DENIED,
                denial_reason=DENIAL_REASON_EXECUTE,
                permitted_principals=(
                    (executor.permitted_executor,)
                    if executor.has_runtime_executor
                    else ()
                ),
            )
        )

    exclusions = _exclusions_from_census(census, planes)
    return PrivilegeManifest(
        captured_at=str(census["captured_at"]),
        host=str(census["host"]),
        database=str(census["database"]),
        server_version=str(census["server_version"]),
        source_role=source,
        target_role=target,
        rows=tuple(sorted(rows, key=GrantRow.sort_key)),
        exclusions=exclusions,
        notes=_notes(census, relation_privilege_counts, schemas_with_usage, rows),
    )


def _exclusions_from_census(
    census: Mapping[str, Any], planes: PlaneResolver
) -> tuple[ExclusionRow, ...]:
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

    for declaration in planes.control_plane_relations():
        exclusions.append(
            ExclusionRow(
                exclusion_id=(
                    f"control-plane-relation:{declaration.schema}."
                    f"{declaration.relation}"
                ),
                kind="denied-control-plane-relation",
                scope=f"{declaration.schema}.{declaration.relation}",
                privileges=0,
                reason=(
                    f"{DENIAL_REASON}. Declared control plane by "
                    f"{declaration.declared_by}. {declaration.authority} "
                    "Permitted principals: "
                    f"{', '.join(declaration.permitted_principals)}. The "
                    "target role will NOT be given any privilege on it. The "
                    "rows stay in the manifest with disposition "
                    "`denied_by_architecture` so the decision is visible "
                    "rather than absent, they render only in the comment-only "
                    "denial ledger, and `denial_violations` proves `app_user` "
                    "holds none of the seven table privileges and no "
                    "column-level equivalent."
                ),
            )
        )

    for identity, declaration in sorted(FUNCTION_EXECUTOR_DECLARATIONS.items()):
        exclusions.append(
            ExclusionRow(
                exclusion_id=f"denied-execute:{identity}",
                kind="denied-security-definer-execute",
                scope=identity,
                privileges=0,
                reason=(
                    f"{DENIAL_REASON_EXECUTE}. Permitted executor: "
                    + (
                        f"`{declaration.permitted_executor}`. "
                        if declaration.has_runtime_executor
                        else "NONE -- no runtime principal. "
                    )
                    + f"{declaration.executor_note} {declaration.authority} "
                    "The row stays in the manifest as a deliberate denial, "
                    "renders no GRANT, and is proved absent by an EFFECTIVE "
                    "has_function_privilege question asked of the target role "
                    "AND of PUBLIC."
                ),
            )
        )

    for exclusion_id, scope, reason in (
        (
            "no-plane-inferred-from-a-name",
            "every relation and sequence in this manifest",
            "The persistence plane is resolved from a DECLARATION "
            "(app.persistence_planes) and never inferred from "
            + ", ".join(FORBIDDEN_PLANE_SIGNALS)
            + ". Those are evidence to validate a declaration, not sources of "
            "ownership. A relation no declaration covers REFUSES generation "
            "rather than defaulting to the tenant plane.",
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
    rows: Sequence[GrantRow],
) -> tuple[str, ...]:
    relations = _census_relations(census)
    derived = sorted(set(relation_privilege_counts) - set(schemas_with_usage))
    unreachable = sum(relation_privilege_counts[schema] for schema in derived)
    control_identities = sorted(
        {row.identity for row in rows if row.plane == PLANE_CONTROL}
    )
    denied_functions = sorted(
        {row.identity for row in rows if row.denial_reason == DENIAL_REASON_EXECUTE}
    )
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
        "grants in the grant file, every denial in a comment-only ledger that "
        "contains no executable statement at all. Keeping exceptional "
        "authorization separate from mechanical compatibility is the point.",
        f"DECISION 1 (2026-09-04, Michael): all {len(denied_functions)} "
        "SECURITY DEFINER EXECUTE rows are DENIED, not review_required. They "
        "are owned by app_admin, a BYPASSRLS role. Each records its PERMITTED "
        "EXECUTOR: the hr trigger fence has NONE -- trigger installation and "
        "operation only, since PostgreSQL checks EXECUTE at CREATE TRIGGER "
        "rather than at fire time -- the tenant outbox pair is "
        "outbox_dispatcher, and the platform outbox pair is "
        "platform_outbox_dispatcher. The generator emits no GRANT EXECUTE at "
        "all. Even where EXECUTE would confer nothing callable, granting it "
        "remains unnecessary AND would reverse a tested migration decision. "
        "Nothing here converts a definer, changes an owner or creates a role, "
        "which are separate authorized acts. Dispositions recorded in "
        "docs/architecture/erp-runtime-identity-cutover.md.",
        "DECISION 1, the verifier's shape: the denial is proved with an "
        "EFFECTIVE privilege question (has_function_privilege), never a "
        "reading of proacl. PostgreSQL grants EXECUTE to PUBLIC BY DEFAULT on "
        "every function, so `REVOKE ... FROM app_user` alone does NOT "
        "neutralize an effective grant inherited through PUBLIC. The verifier "
        "therefore asks three questions per function -- app_user EXECUTE must "
        "be false, PUBLIC EXECUTE must be false, and the expected "
        "administrative executor must be true -- and a surviving PUBLIC "
        "default is reported as REMEDIATION REQUIRED BEFORE CUTOVER rather "
        "than passed.",
        "DECISION 2 (2026-09-04, Michael): the `mod_` plane heuristic is "
        "REMOVED. The plane is resolved by declaration in "
        "app.persistence_planes -- a module's tables/platform_tables read "
        "from app.runtime_admission.COMPOSED_MODULES, plus the host "
        "assembly's own declaration -- and an unclassified relation REFUSES "
        "generation instead of defaulting to the tenant plane. Plane is never "
        "inferred from "
        + ", ".join(FORBIDDEN_PLANE_SIGNALS)
        + "; those are evidence to validate a declaration, not sources of "
        "ownership.",
        f"DENIED, control plane ({len(control_identities)} relations): "
        + ", ".join(control_identities)
        + f". Each carries disposition denied_by_architecture ({DENIAL_REASON}"
        "), stays in the manifest so the decision is visible, renders only in "
        "the comment-only denial ledger, and is proved absent by "
        "denial_violations at BOTH table and column level -- a column ACL can "
        "grant SELECT(col) where the relation ACL shows nothing. Four of the "
        "five sit in `public`: public.platform_outbox_events is the proof "
        "that a qualified name is not an ownership fact. Its migration "
        "20260824_outbox_relay creates it with no tenant_id, no RLS, GRANTs "
        "to platform_api and app_admin, then REVOKEs ALL PRIVILEGES from "
        "app_user at table and column level -- so the sweep that used to "
        "carry it would have REVERSED a tested migration decision. The same "
        "is true of public.platform_idempotency_records "
        "(20260820_idempotency_ledger) and of public.tenants / "
        "public.tenant_domains (20260813_tenant_projection).",
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
-- Rows: {count} to grant. NO denied row appears in this file at all --
-- the denials live in scripts/erp_identity_cutover_denied.sql, which
-- contains no executable statement, so there is nothing here to uncomment.
"""

_DENIAL_HEADER = """\
-- GENERATED FILE -- do not edit. Regenerate with:
--     python scripts/generate_privilege_manifest.py
-- Source of truth: docs/inventories/erp-identity-cutover-manifest-2026-09-04.json
-- Census:          docs/inventories/erp-privilege-census-2026-09-04.json
--
-- {title}
--
-- THIS FILE IS NOT APPLICABLE AND CONTAINS NO SQL. Every line is a comment;
-- there is no BEGIN, no COMMIT and no statement to uncomment. Applying it with
-- psql is a no-op by construction rather than by convention.
--
-- It exists because a denial that is merely ABSENT cannot be told apart from a
-- denial nobody thought of. {denied} rows are recorded here, each with the
-- privilege that is NOT granted, the reason, the declaration that decided the
-- plane, and who MAY do it instead.
"""


def render_grant_sql(rows: Sequence[GrantRow], title: str) -> str:
    """Render one statement per row, in manifest order, in one transaction.

    A DENIED row may not be passed in. Michael's Change-1 boundary states it
    as a condition: *no denied item renders SQL*. The previous shape rendered
    denials as comments inside this file, which kept them visible but left one
    uncomment away from being applied; they now live in
    `render_denial_ledger`, a file with no executable statement in it at all.
    Passing a denied row here is a generator defect and raises.
    """
    ordered = sorted(rows, key=GrantRow.sort_key)
    denied = [row for row in ordered if row.denied]
    if denied:
        raise ValueError(
            "refusing to render a GRANT file containing denied rows: "
            + ", ".join(sorted({row.identity for row in denied}))
            + ". A denied item renders no SQL -- put it in the denial ledger."
        )
    lines = [
        _HEADER.format(title=title, count=len(ordered)),
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
        lines.append(_grant_statement(row))
    lines.extend(["", "COMMIT;"])
    return "\n".join(lines) + "\n"


def render_denial_ledger(rows: Sequence[GrantRow], title: str) -> str:
    """The denials, as a file that cannot be applied because it has no SQL.

    Every line is a comment. There is no `BEGIN`, no `COMMIT`, and no
    statement to uncomment -- the strongest available form of "no denied item
    renders SQL", because the property is a fact about the bytes rather than a
    promise about how the file is read.

    The rows stay HERE rather than being dropped, for the reason the whole
    three-disposition shape exists: a denial that is merely absent cannot be
    told apart from a denial nobody thought of, and the next person to
    regenerate the grant file would have no way to know the missing GRANT was
    a decision. Each entry names the privilege that is NOT granted, the reason,
    the declaration that decided the plane, and who MAY do it instead.
    """
    ordered = sorted(rows, key=GrantRow.sort_key)
    granted = [row for row in ordered if not row.denied]
    if granted:
        raise ValueError(
            "refusing to render a denial ledger containing grantable rows: "
            + ", ".join(sorted({row.identity for row in granted}))
        )
    lines = [_DENIAL_HEADER.format(title=title, denied=len(ordered)), ""]
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
            lines.append(f"--   DENIED ({row.disposition}): {row.denial_reason}.")
            if row.plane:
                lines.append(
                    f"--   PLANE: {row.plane} -- declared by "
                    f"{row.plane_declared_by}."
                )
            if row.permitted_principals:
                lines.append(
                    "--   PERMITTED INSTEAD: "
                    + ", ".join(row.permitted_principals)
                    + "."
                )
            elif row.object_kind == "function":
                lines.append(
                    "--   PERMITTED INSTEAD: no runtime principal -- trigger "
                    "installation and operation only."
                )
        lines.append(f"--   NOT GRANTED: {_grant_statement(row)}")
    return "\n".join(lines) + "\n"


ROUTINE_SQL_TITLE: Final[str] = (
    "Change 1, routine half: mirror the legacy estate onto app_user. "
    "Mechanical rows only -- nothing here needs a judgement call."
)
DENIAL_LEDGER_TITLE: Final[str] = (
    "Change 1, the DENIALS: five control-plane relations and five SECURITY "
    "DEFINER functions the architecture refuses to mirror onto app_user. "
    "Nothing here is applied, ever -- there is no SQL in this file. The plane "
    "of every relation was resolved from a DECLARATION "
    "(app.persistence_planes), never from a schema name; the five derived "
    "schema-USAGE rows that used to sit beside these were SETTLED on "
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
class ObservedFunctionExecute:
    """One EFFECTIVE `EXECUTE` answer on a denied function, per principal.

    Three fields carry the weight, and each of them can fail.

    `effective` says the answer came from `has_function_privilege(role, oid,
    'EXECUTE')` -- the question "can this role do it" -- rather than from
    reading `proacl` and looking for the role's name. The distinction is the
    whole reason this observation exists. `CREATE FUNCTION` grants EXECUTE to
    `PUBLIC` **by default**, so a function whose ACL carries no `app_user`
    entry at all can still be executable by `app_user`, and a check that read
    the ACL would report a clean denial while the tenant role called the body.
    `REVOKE ... FROM app_user` does not touch a grant held through `PUBLIC`.

    `role` is a principal name, or the pseudo-role `PUBLIC` -- asked about
    explicitly, because "app_user cannot" and "nobody can" are different
    findings and the second one has a different owner.

    `probed` is load-bearing for the same reason `columns_probed` is on the
    column observation: an absence nobody looked for is not an absence.
    """

    identity: str
    role: str
    held: bool
    effective: bool = False
    probed: bool = False


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
    #: EFFECTIVE EXECUTE answers on every DENIED function, for the target
    #: role, for `PUBLIC`, and for the declared permitted executor.
    denied_function_execute: tuple[ObservedFunctionExecute, ...] = ()
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
    denied_observed = manifest.denied_counts()
    for section in sorted(set(DENIED_TOTALS) | set(denied_observed)):
        expected = DENIED_TOTALS.get(section, 0)
        actual = denied_observed.get(section, 0)
        if actual == expected:
            continue
        direction = "GREW" if actual > expected else "FELL"
        violations.append(
            f"DENIALS {direction}: section {section!r} holds {actual} denied "
            f"rows, DENIED_TOTALS declares {expected}. A denial that appears "
            "on its own is an unreviewed refusal of access somebody may be "
            "relying on; a denial that disappears on its own is the failure "
            "this whole shape exists to prevent. Either way, edit "
            "DENIED_TOTALS in app/privilege_manifest.py in the same commit "
            "and say why."
        )
    return violations


def function_denial_violations(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> list[str]:
    """Refusal 9: a DENIED function must be unexecutable, EFFECTIVELY.

    The shape of this check is decided by one fact about PostgreSQL:
    `CREATE FUNCTION` grants `EXECUTE` to `PUBLIC` **by default**. Three
    consequences follow, and the guard is built around all three.

    1. **The question must be effective, not an ACL reading.** A function
       whose `proacl` names no `app_user` entry can still be executable by
       `app_user` through the `PUBLIC` default. `has_function_privilege` --
       "can this role do it" -- catches that; reading `aclexplode(proacl)` for
       the role's own name does not. A non-effective answer is refused rather
       than read.

    2. **`PUBLIC` is asked about by name.** Michael: *"If any function
       currently inherits default `PUBLIC EXECUTE`, treat that as a separate
       remediation required before cutover. `REVOKE ... FROM app_user` alone
       does not neutralize an effective grant inherited through PUBLIC."* So a
       surviving PUBLIC default is reported as REMEDIATION OWED, in its own
       words, rather than folded into the app_user finding or -- worse --
       passed because the app_user probe happened to read an ACL.

       The census recorded `public_execute = False` for all five, so this
       should be quiet today. That is exactly why it must be able to fire: a
       check that is only correct on the data it was written against is not a
       check.

    3. **The permitted executor is asserted POSITIVELY.** A denial can pass
       for the wrong reason -- the function became unreachable to everyone,
       and the outbox stopped draining. So the declared administrative
       executor must answer TRUE, and a function declared to need no runtime
       executor says so explicitly rather than by omission.
    """
    violations: list[str] = []
    denied = manifest.denied_functions()
    if not denied:
        return violations
    answers = {
        (entry.identity, entry.role): entry
        for entry in snapshot.denied_function_execute
    }
    for row in sorted(denied, key=GrantRow.sort_key):
        declaration = FUNCTION_EXECUTOR_DECLARATIONS.get(row.identity)
        if declaration is None:
            violations.append(
                f"UNDECLARED EXECUTOR: {row.identity} is denied but names no "
                "permitted executor. A refusal that cannot say who MAY run "
                "the function is half an instruction."
            )
            continue
        wanted = [manifest.target_role, PUBLIC_PSEUDO_ROLE]
        if declaration.has_runtime_executor:
            wanted.append(declaration.permitted_executor)
        for role in wanted:
            entry = answers.get((row.identity, role))
            if entry is None or not entry.probed:
                where = "no observation" if entry is None else "not probed"
                violations.append(
                    f"UNPROBED EXECUTE DENIAL: EXECUTE on {row.identity} was "
                    f"never answered for {role!r} ({where}). "
                    f"{DENIAL_REASON_EXECUTE}: a denial the verifier did not "
                    "test is indistinguishable from a clean answer."
                )
                continue
            if not entry.effective:
                violations.append(
                    f"NON-EFFECTIVE EXECUTE ANSWER: the answer for {role!r} on "
                    f"{row.identity} did not come from has_function_privilege. "
                    "An ACL reading cannot see a grant inherited through "
                    "PUBLIC, which PostgreSQL applies to every function by "
                    "default -- so it would report a clean denial while the "
                    "role executes the body."
                )
                continue
            if role == PUBLIC_PSEUDO_ROLE and entry.held:
                violations.append(
                    f"PUBLIC EXECUTE INHERITED: {PUBLIC_PSEUDO_ROLE} holds "
                    f"EXECUTE on {row.identity}, so every login in the "
                    f"cluster does -- {manifest.target_role!r} included. "
                    "REMEDIATION REQUIRED BEFORE CUTOVER, as a separate act: "
                    f"`REVOKE ... FROM {manifest.target_role}` alone does NOT "
                    "neutralize an effective grant inherited through PUBLIC, "
                    "and PostgreSQL grants EXECUTE to PUBLIC by default on "
                    "every function it creates. This is not a Change-1 grant "
                    "to withhold; it is a standing grant to remove."
                )
                continue
            if role == manifest.target_role and entry.held:
                violations.append(
                    f"DENIED EXECUTE HELD: {manifest.target_role!r} holds "
                    f"EXECUTE on {row.identity}. {DENIAL_REASON_EXECUTE} -- "
                    "this manifest never grants it, so its presence is a "
                    "grant made outside this programme, or a PUBLIC default "
                    "nobody revoked."
                )
                continue
            if (
                declaration.has_runtime_executor
                and role == declaration.permitted_executor
                and not entry.held
            ):
                violations.append(
                    f"PERMITTED EXECUTOR CANNOT EXECUTE: {role!r} does not "
                    f"hold EXECUTE on {row.identity}, which "
                    f"{declaration.authority} A denial that passes because "
                    "the function became unreachable to EVERYONE is not the "
                    "denial that was decided -- the relay stops draining and "
                    "the guard says nothing."
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
    denied_identities = sorted({row.identity for row in manifest.denied_relations()})
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
       or the verifier not having looked (`denial_violations`);
    9. a DENIED function being EXECUTABLE after all -- by the target role, by
       `PUBLIC`, or by nobody at all -- or the answer having been read off an
       ACL rather than asked effectively (`function_denial_violations`).
    """
    observed_objects = {entry.identity: entry for entry in snapshot.objects}
    return [
        *baseline_violations(manifest),
        *_object_violations(manifest, observed_objects),
        *_privilege_violations(manifest, snapshot, observed_objects),
        *_escalation_violations(manifest, snapshot, observed_objects),
        *_legacy_module_violations(manifest, snapshot),
        *denial_violations(manifest, snapshot),
        *function_denial_violations(manifest, snapshot),
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
    # The legacy role's module-era access, as the census found it. Selected
    # by SCHEMA NAMESPACE rather than by plane, because this is the question
    # "does the retiring role reach module storage" -- a namespace fact, and
    # the one place `MODULE_SCHEMA_PREFIX` is still the right instrument.
    legacy = [
        ObservedPrivilege(
            identity=row.identity,
            role=manifest.source_role,
            privilege=row.privilege,
            held=True,
            origin=ACCEPTED_PRIVILEGE_ORIGIN,
        )
        for row in manifest.rows
        if row.schema.startswith(MODULE_SCHEMA_PREFIX)
    ]
    # The denial half of a CORRECTLY cut-over database: every one of the
    # seven table privileges answered and false, every column-grantable
    # privilege answered against a non-empty column set and holding nothing.
    # `_CLEAN_COLUMNS_PROBED` stands for "the verifier examined the
    # relation's columns" -- the count is synthetic because the census never
    # captured column names, and only its being NON-ZERO is load-bearing.
    denied_identities = sorted({row.identity for row in manifest.denied_relations()})
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
    # The function-denial half of a CORRECTLY cut-over database: for every
    # denied function, the target role answers FALSE, `PUBLIC` answers FALSE
    # (no surviving default), and the declared administrative executor answers
    # TRUE. Every answer is marked EFFECTIVE and PROBED, because those are the
    # two properties the guard refuses to assume.
    denied_function_execute: list[ObservedFunctionExecute] = []
    for row in manifest.denied_functions():
        declaration = FUNCTION_EXECUTOR_DECLARATIONS[row.identity]
        denied_function_execute.append(
            ObservedFunctionExecute(
                identity=row.identity,
                role=manifest.target_role,
                held=False,
                effective=True,
                probed=True,
            )
        )
        denied_function_execute.append(
            ObservedFunctionExecute(
                identity=row.identity,
                role=PUBLIC_PSEUDO_ROLE,
                held=False,
                effective=True,
                probed=True,
            )
        )
        if declaration.has_runtime_executor:
            denied_function_execute.append(
                ObservedFunctionExecute(
                    identity=row.identity,
                    role=declaration.permitted_executor,
                    held=True,
                    effective=True,
                    probed=True,
                )
            )
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
        denied_function_execute=tuple(denied_function_execute),
    )
