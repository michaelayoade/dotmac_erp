"""The guard for the `dotmac_erp_app` -> `app_user` identity cutover.

Two halves, and the second is the one that matters.

**The static half** proves the committed artefacts are what the frozen census
generates: the manifest and both SQL files are regenerated in memory and
byte-compared, the row counts are restated here so a drifting census cannot
move them silently, and the SQL is read back to prove it contains nothing but
`GRANT` -- no `REVOKE`, no `ALTER`, no ownership change, no role membership,
no `GRANT ALL`.

**The sensitivity half** proves the runtime refusals actually bite. ADR-0018
requires a detector to carry a sensitivity proof, because a check that fires
on nothing and a check that fires on everything both "pass". So every one of
the nine refusals is exercised by PLANTING the corresponding defect into an
otherwise-clean snapshot and asserting the violation NAMES it -- and the
NEGATIVE CONTROL asserts the same clean snapshot produces zero violations, so
none of them can be passing because the detector flags everything.

**The plane half** (`test_plane_sensitivity_*`) proves the thing Decision 2
actually claims: that the persistence plane is read from a DECLARATION rather
than from a name. Nine proofs, each planted. Three of them --
`a_tenant_table_in_a_mod_schema_is_not_falsely_denied`,
`a_tenant_table_in_public_follows_its_declaration` and
`moving_a_table_between_schemas_does_not_change_its_plane` -- exist precisely
because they cannot be passed by any name-based rule, and they are the reason
the other six are not merely restatements of "mod_ means denied".

The clean snapshot is built by `app.privilege_manifest.clean_snapshot` from
the real committed manifest -- 1,761 rows -- so these proofs run against the
production shape rather than a two-row toy.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re

import pytest

from app.persistence_planes import (
    PLANE_CONTROL,
    PLANE_TENANT,
    PlaneResolver,
    RelationPlane,
    SchemaPlane,
    UnclassifiedRelation,
    default_resolver,
)
from app.privilege_manifest import (
    ACCEPTED_PRIVILEGE_ORIGIN,
    BASELINE_TOTALS,
    COLUMN_LEVEL_PRIVILEGES,
    DENIAL_LEDGER_TITLE,
    DENIAL_REASON,
    DENIAL_REASON_EXECUTE,
    DENIED_TABLE_PRIVILEGES,
    DENIED_TOTALS,
    DISPOSITION_DENIED,
    DISPOSITION_GRANT,
    DISPOSITION_REVIEW_REQUIRED,
    EXPECTED_OWNER,
    FUNCTION_EXECUTOR_DECLARATIONS,
    MODULE_ERA_ALLOWLIST,
    NO_RUNTIME_EXECUTOR,
    PUBLIC_PSEUDO_ROLE,
    ROUTINE_SQL_TITLE,
    SECTION_CONTROL_PLANE,
    SECTION_FUNCTIONS,
    SECTION_RELATIONS,
    SECTION_SCHEMA_USAGE,
    SECTION_SEQUENCES,
    SECTIONS,
    SETTLED_SCHEMA_USAGE,
    SOURCE_ROLE,
    TARGET_ROLE,
    ObservedColumnGrant,
    ObservedMembership,
    ObservedPrivilege,
    ObservedRolePosture,
    PrivilegeManifest,
    PrivilegeSnapshot,
    UnparseableSignature,
    baseline_violations,
    clean_snapshot,
    cutover_violations,
    denial_violations,
    function_denial_violations,
    function_name_and_identity_arguments,
    manifest_from_census,
    manifest_from_json,
    manifest_to_json,
    relation_identity,
    render_denial_ledger,
    render_grant_sql,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CENSUS_PATH = REPO_ROOT / "docs/inventories/erp-privilege-census-2026-09-04.json"
MANIFEST_PATH = (
    REPO_ROOT / "docs/inventories/erp-identity-cutover-manifest-2026-09-04.json"
)
ROUTINE_SQL_PATH = REPO_ROOT / "scripts/erp_identity_cutover_grants.sql"
DENIAL_LEDGER_PATH = REPO_ROOT / "scripts/erp_identity_cutover_denied.sql"

#: The census's own headline numbers, restated here so the guard cannot pass
#: by the census and the manifest drifting together. These are the frozen
#: production capture of 2026-09-04T09:09:14Z from erp.dotmac.io.
CENSUS_RELATION_PRIVILEGES = 1716
CENSUS_SEQUENCE_PRIVILEGES = 3
CENSUS_FUNCTIONS = 5
CENSUS_REVERSE_GAP = 132

#: The manifest's section sizes, stated a THIRD time (census, BASELINE_TOTALS,
#: here) exactly as `tests/architecture/test_database_role_contract.py` states
#: the role contract a third time, and for the same reason.
EXPECTED_SECTION_ROWS = {
    # 37, not 42: the five DERIVED usage rows were settled as no-ops on
    # 2026-09-04 and removed. Lowering a baseline is legitimate only as an
    # edit like this one, in the same commit as the change that earned it.
    SECTION_SCHEMA_USAGE: 37,
    # 1696, not 1712, and control_plane 20 rather than module_era 4: Decision 2
    # resolved the plane by declaration and moved four `public` relations
    # (16 privileges) out of the bulk sweep. The census total is unchanged.
    SECTION_RELATIONS: 1696,
    SECTION_SEQUENCES: 3,
    SECTION_FUNCTIONS: 5,
    SECTION_CONTROL_PLANE: 20,
}

#: Every relation the plane resolver classifies as CONTROL plane, stated here
#: a second time so a declaration silently added or dropped fails rather than
#: flowing through into the manifest.
EXPECTED_CONTROL_PLANE_RELATIONS = {
    "relation:mod_files.platform_stored_files",
    "relation:public.platform_idempotency_records",
    "relation:public.platform_outbox_events",
    "relation:public.tenant_domains",
    "relation:public.tenants",
}

#: The permitted executor of each denied SECURITY DEFINER function -- the
#: OPERATIONAL half of Decision 1. "" means no runtime principal at all.
EXPECTED_PERMITTED_EXECUTORS = {
    "function:hr.enforce_employment_type_projection()": "",
    "function:public.claim_outbox_batch(text, integer, integer)": ("outbox_dispatcher"),
    "function:public.claim_platform_outbox_batch(text, integer, integer)": (
        "platform_outbox_dispatcher"
    ),
    (
        "function:public.settle_outbox_event"
        "(uuid, text, text, timestamp with time zone, integer, text)"
    ): "outbox_dispatcher",
    (
        "function:public.settle_platform_outbox_event"
        "(uuid, text, text, timestamp with time zone, integer, text)"
    ): "platform_outbox_dispatcher",
}

#: The five SECURITY DEFINER functions, with the argument-type identity the
#: parser must derive from each census signature. Hand-checked; a parser that
#: stripped `timestamp` as if it were a parameter name would fail here.
EXPECTED_FUNCTION_IDENTITIES = {
    "function:hr.enforce_employment_type_projection()",
    "function:public.claim_outbox_batch(text, integer, integer)",
    "function:public.claim_platform_outbox_batch(text, integer, integer)",
    (
        "function:public.settle_outbox_event"
        "(uuid, text, text, timestamp with time zone, integer, text)"
    ),
    (
        "function:public.settle_platform_outbox_event"
        "(uuid, text, text, timestamp with time zone, integer, text)"
    ),
}


@pytest.fixture(scope="module")
def census() -> dict:
    return json.loads(CENSUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> PrivilegeManifest:
    return manifest_from_json(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def snapshot(manifest: PrivilegeManifest) -> PrivilegeSnapshot:
    return clean_snapshot(manifest)


# ---------------------------------------------------------------------------
# The static half: the committed artefacts ARE what the census generates
# ---------------------------------------------------------------------------


def test_the_committed_manifest_is_what_the_census_generates(census: dict) -> None:
    """Generated, never hand-edited -- byte-compared, not eyeballed."""
    assert manifest_to_json(manifest_from_census(census)) == MANIFEST_PATH.read_text(
        encoding="utf-8"
    ), (
        "the committed manifest is not what the census generates. It is a "
        "GENERATED artefact: run `python scripts/generate_privilege_manifest.py` "
        "and review the diff rather than editing it."
    )


def test_the_committed_sql_is_what_the_manifest_renders(census: dict) -> None:
    built = manifest_from_census(census)
    assert render_grant_sql(
        built.routine(), ROUTINE_SQL_TITLE
    ) == ROUTINE_SQL_PATH.read_text(encoding="utf-8")
    assert render_denial_ledger(
        built.denied(), DENIAL_LEDGER_TITLE
    ) == DENIAL_LEDGER_PATH.read_text(encoding="utf-8")


def test_generation_is_deterministic(census: dict) -> None:
    """Same census in, same bytes out -- twice, from a fresh parse each time."""
    first = manifest_to_json(manifest_from_census(census))
    second = manifest_to_json(
        manifest_from_census(json.loads(CENSUS_PATH.read_text(encoding="utf-8")))
    )
    assert first == second


def test_the_census_headline_numbers_have_not_moved(census: dict) -> None:
    assert len(census["tables"]) == CENSUS_RELATION_PRIVILEGES
    assert len(census["sequences"]) == CENSUS_SEQUENCE_PRIVILEGES
    assert len(census["functions"]) == CENSUS_FUNCTIONS
    assert (
        sum(entry["privileges"] for entry in census["reverse_gap"])
        == CENSUS_REVERSE_GAP
    )
    assert census["source_role"] == SOURCE_ROLE
    assert census["target_role"] == TARGET_ROLE


def test_every_census_privilege_is_a_direct_grant(census: dict) -> None:
    """The manifest states this as a fact; the fact has to be true.

    A privilege reached via PUBLIC or via ownership is NOT an equivalent of a
    direct grant, and the whole "the baseline is a clean set of deliberate
    grants" claim rests on this holding for all 1,716.
    """
    assert all(row["direct_grant"] for row in census["tables"])
    assert not any(row["via_public"] for row in census["tables"])
    assert not any(row["via_ownership"] for row in census["tables"])


def test_section_row_counts_match_the_baseline(manifest: PrivilegeManifest) -> None:
    assert manifest.counts() == EXPECTED_SECTION_ROWS
    assert dict(BASELINE_TOTALS) == EXPECTED_SECTION_ROWS
    assert baseline_violations(manifest) == []


def test_the_manifest_covers_every_census_privilege(
    census: dict, manifest: PrivilegeManifest
) -> None:
    """1,696 tenant-plane rows + 20 control-plane rows = the census's 1,716.

    The census total did not move. What moved is which side of the plane
    boundary sixteen of them are on.
    """
    relation_rows = len(manifest.section(SECTION_RELATIONS)) + len(
        manifest.section(SECTION_CONTROL_PLANE)
    )
    assert relation_rows == len(census["tables"])
    assert len(manifest.section(SECTION_SEQUENCES)) == len(census["sequences"])
    assert len(manifest.section(SECTION_FUNCTIONS)) == len(census["functions"])


def test_identity_is_oid_independent(manifest: PrivilegeManifest) -> None:
    """No row may be keyed by anything a restore reassigns.

    A restored database recreates every object with a NEW oid, and Change 2
    applies this manifest to exactly such a restore. An oid anywhere in a key
    would make the manifest describe a database that no longer exists.
    """
    for row in manifest.rows:
        assert not re.search(r"\boid\b", row.identity), row.identity
        assert row.identity.startswith(
            ("schema:", "relation:", "sequence:", "function:")
        ), row.identity
        assert row.schema and row.object_name
    assert len({row.identity for row in manifest.rows}) == len(
        {(row.schema, row.object_name, row.object_kind) for row in manifest.rows}
    )


def test_functions_are_keyed_by_full_signature(manifest: PrivilegeManifest) -> None:
    rows = manifest.section(SECTION_FUNCTIONS)
    assert {row.identity for row in rows} == EXPECTED_FUNCTION_IDENTITIES
    for row in rows:
        assert row.signature, "the operator-facing signature is kept for review"
        assert "(" in row.identity and row.identity.endswith(")")
        # The identity carries TYPES; the census signature carries parameter
        # names. Confusing the two is how an overload gets granted by mistake.
        assert "p_worker" not in row.identity


def test_the_signature_parser_refuses_to_guess() -> None:
    """A wrong answer here grants EXECUTE on the wrong function body."""
    name, args = function_name_and_identity_arguments(
        "settle_outbox_event(p_id uuid, p_at timestamp with time zone)"
    )
    assert (name, args) == ("settle_outbox_event", "uuid, timestamp with time zone")
    # `timestamp` is an identifier-shaped word that begins a real type; a
    # naive "the first token is the parameter name" rule mangles it.
    assert function_name_and_identity_arguments("f(timestamp with time zone)") == (
        "f",
        "timestamp with time zone",
    )
    with pytest.raises(UnparseableSignature):
        function_name_and_identity_arguments("f(p_thing some_unknown_domain)")


def test_decision_1_all_five_execute_grants_are_denied_with_an_executor(
    census: dict, manifest: PrivilegeManifest
) -> None:
    """Decision 1. The review is over; the answer for all five was no.

    A SECURITY DEFINER function runs as its OWNER. Every one of these is owned
    by `app_admin`, which is BYPASSRLS, so EXECUTE is a grant of whatever the
    body does with that role's reach. `review_required` said "someone must
    read this"; `denied_by_architecture` says "this is never applied", and
    those are opposite instructions to whoever runs the SQL.

    The rows STAY. A denial that is merely absent cannot be told apart from a
    denial nobody thought of -- and each records its PERMITTED EXECUTOR, so
    the refusal is a complete instruction rather than half of one: somebody
    still drains the outbox.
    """
    assert all(entry["security_definer"] for entry in census["functions"])
    rows = manifest.section(SECTION_FUNCTIONS)
    assert len(rows) == CENSUS_FUNCTIONS
    assert {row.identity for row in rows} == set(EXPECTED_PERMITTED_EXECUTORS)
    for row in rows:
        assert row.disposition == DISPOSITION_DENIED, row.identity
        assert row.denied and not row.review_required, row.identity
        assert row.denial_reason == DENIAL_REASON_EXECUTE, row.identity
        assert row.owner == EXPECTED_OWNER, row.identity
        assert "SECURITY DEFINER" in row.reason
        assert row.owner in row.reason, "the reason names the owner it runs as"
        expected = EXPECTED_PERMITTED_EXECUTORS[row.identity]
        assert row.permitted_principals == ((expected,) if expected else ())
        declaration = FUNCTION_EXECUTOR_DECLARATIONS[row.identity]
        assert declaration.permitted_executor == expected
        assert declaration.executor_note, "a blank executor must be a decision"
        assert "alembic/versions/" in declaration.authority, (
            "the migration that grants (or revokes) the executor is the "
            "evidence; a declaration with no evidence is an assertion"
        )
        if expected:
            assert expected in row.reason
        else:
            assert "no runtime principal" in row.reason

    # Michael's reason, and it is the subtle half: a trigger function cannot
    # be invoked as an ordinary function, so the grant would confer nothing --
    # and it is refused anyway, because it would reverse a tested revoke.
    fence = next(
        row for row in rows if row.object_name == "enforce_employment_type_projection"
    )
    assert "reverse a tested migration decision" in fence.reason

    # What is DECIDED about the fence, asserted directly. The note explains
    # why no runtime principal needs direct execution, and an earlier version
    # of this test demanded the literal string "CREATE TRIGGER" from that
    # sentence -- which makes a rewording a failure while every decision
    # stays put, and makes the prose editable to turn the test green. A test
    # must not enforce an incidental spelling. The things that are actually
    # decided are these:
    fence_declaration = FUNCTION_EXECUTOR_DECLARATIONS[fence.identity]
    assert fence.disposition == DISPOSITION_DENIED
    assert fence.denial_reason == DENIAL_REASON_EXECUTE
    assert fence_declaration.permitted_executor == NO_RUNTIME_EXECUTOR
    assert not fence_declaration.has_runtime_executor
    assert fence.permitted_principals == ()
    assert "no runtime principal" in fence.reason
    assert fence.object_name not in ROUTINE_SQL_PATH.read_text(encoding="utf-8"), (
        "a denied function renders no SQL: it must not appear in the file "
        "that is applied"
    )
    ledger_mentions = [
        line
        for line in DENIAL_LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        if fence.object_name in line
    ]
    assert ledger_mentions, "the refusal is recorded, not merely absent"
    assert all(line.startswith("--") for line in ledger_mentions), (
        "and it is recorded as comment, so the ledger renders nothing"
    )

    # And no GRANT EXECUTE is emitted, anywhere, in any file.
    assert "EXECUTE" not in ROUTINE_SQL_PATH.read_text(encoding="utf-8")
    ledger = DENIAL_LEDGER_PATH.read_text(encoding="utf-8")
    assert "GRANT EXECUTE" in ledger, "the refusal stays VISIBLE"
    assert all(line.startswith("--") for line in ledger.splitlines() if line.strip()), (
        "every GRANT EXECUTE in the ledger is a comment, not a statement"
    )


def test_decision_2_every_control_plane_relation_is_denied_by_declaration(
    manifest: PrivilegeManifest,
) -> None:
    """Decision 2. FIVE relations, not one, and none of them by their name.

    The old rule read the plane off the schema: `mod_` meant deny, anything
    else meant sweep. It got one relation right and four wrong. Four of the
    five below live in `public`, whose name says nothing at all -- what puts
    each of them here is a migration that creates it with no tenant column, no
    RLS, and an explicit `REVOKE ... FROM app_user`.
    """
    rows = manifest.section(SECTION_CONTROL_PLANE)
    assert {row.identity for row in rows} == EXPECTED_CONTROL_PLANE_RELATIONS
    assert len(rows) == EXPECTED_SECTION_ROWS[SECTION_CONTROL_PLANE]
    for row in rows:
        assert row.disposition == DISPOSITION_DENIED, row.identity
        assert row.denied and not row.review_required, row.identity
        assert row.denial_reason == DENIAL_REASON, row.identity
        assert row.plane == PLANE_CONTROL, row.identity
        assert row.plane_declared_by, "the row names WHICH declaration decided it"
        assert row.permitted_principals, (
            "a relation the tenant role may not reach and that nothing else "
            "may reach either is unreachable, not isolated"
        )
        assert row.reason.startswith(DENIAL_REASON)
        assert "ADR-0023" in row.reason

    # Four of the five are in `public`. That is the whole finding.
    in_public = {row.identity for row in rows if row.schema == "public"}
    assert len(in_public) == 4, sorted(in_public)
    assert "relation:public.platform_outbox_events" in in_public

    # Exactly one comes from a module manifest, and it says so.
    from_module = {row.plane_declared_by for row in rows if row.schema != "public"}
    assert from_module == {"module manifest: files.platform_tables"}

    # None of them appears in the grant file, at all.
    routine_sql = ROUTINE_SQL_PATH.read_text(encoding="utf-8")
    for row in rows:
        assert f'"{row.schema}"."{row.object_name}"' not in routine_sql, row.identity

    ledger = DENIAL_LEDGER_PATH.read_text(encoding="utf-8")
    assert "NOT GRANTED: GRANT SELECT ON TABLE" in ledger, (
        "the denial is kept VISIBLE -- a denial that is merely absent cannot "
        "be told apart from one nobody thought of"
    )
    assert DENIAL_REASON in ledger


def test_the_module_era_allowlist_is_still_a_namespace_fact(
    manifest: PrivilegeManifest,
) -> None:
    """`mod_` survives for exactly ONE question, and it is not the plane.

    "Does the retiring role reach module storage?" is a NAMESPACE question and
    `MODULE_SCHEMA_PREFIX` is the right instrument for it. "Which plane is this
    relation on?" is an OWNERSHIP question and the prefix is the wrong
    instrument, which is what Decision 2 settled. Keeping the two apart is the
    point; collapsing them again is the regression.
    """
    module_rows = {
        row.identity for row in manifest.rows if row.schema.startswith("mod_")
    }
    assert module_rows == set(MODULE_ERA_ALLOWLIST)
    # And the prefix decides nothing about the plane: four control-plane
    # relations carry no prefix, and (were one composed) a module tenant table
    # would carry the prefix and be granted -- see
    # test_plane_sensitivity_3_a_tenant_table_in_a_mod_schema_is_not_denied.
    assert any(
        row.schema != "mod_files" for row in manifest.section(SECTION_CONTROL_PLANE)
    )


def test_the_settled_schema_usage_rows_are_gone_with_their_origins_kept(
    manifest: PrivilegeManifest, census: dict
) -> None:
    """Item 3, settled 2026-09-04. Removed as no-ops; origins preserved.

    The five schemas all returned `legacy=True, app_user=True`, so the delta
    reading holds and the derived GRANT changed nothing. What must NOT be
    lost is that the origins DIFFER: four are direct grants to `app_user`,
    and `public` is reached through `PUBLIC` -- the one origin this manifest
    refuses everywhere else, because it reaches every login in the cluster.
    Flattening that into "all five already have USAGE" would discard the only
    part with a consequence for Change 3.
    """
    settled = set(SETTLED_SCHEMA_USAGE)
    assert settled == {"hr", "mod_files", "public", "rpt", "sync"}

    observed = {str(entry["schema"]) for entry in census["schemas"]}
    with_relations = {str(row["schema"]) for row in census["tables"]}
    assert with_relations - observed == settled, (
        "these are exactly the schemas that carry relation privileges and no "
        "observed schema-USAGE row"
    )

    usage_schemas = {row.schema for row in manifest.section(SECTION_SCHEMA_USAGE)}
    assert usage_schemas == observed
    assert not (usage_schemas & settled), (
        "the settled rows are REMOVED, not re-marked; a no-op GRANT carried "
        "as review debt is debt nobody can discharge"
    )
    assert all(
        row.disposition == DISPOSITION_GRANT
        for row in manifest.section(SECTION_SCHEMA_USAGE)
    )

    origins = {schema: origin for schema, (origin, _) in SETTLED_SCHEMA_USAGE.items()}
    assert origins == {
        "hr": "direct",
        "mod_files": "direct",
        "rpt": "direct",
        "sync": "direct",
        "public": "public",
    }, "public is reached via PUBLIC:USAGE, not by a grant to app_user"

    exclusions = {
        row.scope: row
        for row in manifest.exclusions
        if row.kind == "settled-schema-usage"
    }
    assert set(exclusions) == settled
    assert "PUBLIC:USAGE" in exclusions["public"].reason
    assert "NOT a direct grant" in exclusions["public"].reason
    for schema in ("hr", "rpt", "sync"):
        assert "held DIRECTLY" in exclusions[schema].reason
        assert "alembic/versions/" in exclusions[schema].reason, (
            "a direct grant has an author; the migration that made it is the "
            "evidence, not the assertion"
        )
    # mod_files carries BOTH planes' roles in one schema ACL -- which is why
    # isolation there has to be a table-level fact.
    assert "platform_api:USAGE" in exclusions["mod_files"].reason
    assert "app_user:USAGE" in exclusions["mod_files"].reason
    assert "TABLE level" in exclusions["mod_files"].reason


def test_every_row_carries_one_of_the_three_dispositions(
    manifest: PrivilegeManifest,
) -> None:
    """A boolean cannot say 'never'. Three outcomes, and they partition."""
    counted = {
        DISPOSITION_GRANT: len(manifest.routine()),
        DISPOSITION_REVIEW_REQUIRED: len(manifest.review_required()),
        DISPOSITION_DENIED: len(manifest.denied()),
    }
    assert sum(counted.values()) == len(manifest.rows)
    # Nothing is review_required any more: both open items were RULED on.
    # The disposition stays in the vocabulary because the next open question
    # will need it, and an empty set is a fact rather than a deletion.
    assert counted[DISPOSITION_REVIEW_REQUIRED] == 0
    assert counted[DISPOSITION_DENIED] == sum(DENIED_TOTALS.values()) == 25
    assert manifest.denied_counts() == dict(DENIED_TOTALS)
    assert len(manifest.denied_functions()) == CENSUS_FUNCTIONS
    assert len(manifest.denied_relations()) == 20
    with pytest.raises(ValueError, match="unknown disposition"):
        replace(manifest.rows[0], disposition="probably_fine")
    # A denial with no stated reason is refused: a refusal whose reason is
    # inferred from its section changes meaning when the sections do.
    with pytest.raises(ValueError, match="states no reason"):
        replace(manifest.routine()[0], disposition=DISPOSITION_DENIED)


def test_the_reverse_gap_is_recorded_as_a_preserved_exclusion(
    census: dict, manifest: PrivilegeManifest
) -> None:
    """132 privileges the target already holds. Preserved, never revoked."""
    preserved = manifest.preserved_scopes()
    assert preserved == {
        entry["schema"]: entry["privileges"] for entry in census["reverse_gap"]
    }
    assert sum(preserved.values()) == CENSUS_REVERSE_GAP
    people = next(
        row
        for row in manifest.exclusions
        if row.exclusion_id == "reverse-gap:mod_people"
    )
    assert "employment_types" in people.reason, (
        "the mod_people arithmetic -- 6 tables x 4 privileges = 24, of which "
        "the runtime holds 4 -- is what shows the dual grant was "
        "table-specific rather than schema-wide, and it must survive in the "
        "artefact"
    )
    assert "COUNT only" in people.reason, (
        "the census recorded this gap as a per-schema count; claiming "
        "object-level verification would be claiming evidence that was never "
        "captured"
    )


def test_the_prohibitions_are_stated_in_the_manifest(
    manifest: PrivilegeManifest,
) -> None:
    prohibited = {
        row.exclusion_id
        for row in manifest.exclusions
        if row.kind == "prohibited-action"
    }
    assert prohibited == {
        "no-plane-inferred-from-a-name",
        "no-grant-all",
        "no-role-membership",
        "no-ownership-transfer",
        "no-privileges-added-to-legacy-role",
        "no-role-attribute-change",
        "no-module-activation-change",
        "no-revoke",
    }
    planes = next(
        row
        for row in manifest.exclusions
        if row.exclusion_id == "no-plane-inferred-from-a-name"
    )
    for forbidden in ("`mod_` schema-name prefix", "`public` schema", "tenant_id"):
        assert forbidden in planes.reason
    assert "REFUSES generation" in planes.reason

    # Every denied relation gets its OWN exclusion row naming the declaration
    # and the migration behind it -- not one shared line for the set.
    denied = {
        row.scope: row
        for row in manifest.exclusions
        if row.kind == "denied-control-plane-relation"
    }
    assert set(denied) == {
        identity.split(":", 1)[1] for identity in EXPECTED_CONTROL_PLANE_RELATIONS
    }
    for scope, row in denied.items():
        assert row.reason.startswith(DENIAL_REASON), scope
        assert "will NOT be given" in row.reason, scope
        assert "Permitted principals:" in row.reason, scope
    assert "alembic/versions/20260824_outbox_relay.py" in (
        denied["public.platform_outbox_events"].reason
    )

    # And every denied function gets one naming its permitted executor.
    executes = {
        row.scope: row
        for row in manifest.exclusions
        if row.kind == "denied-security-definer-execute"
    }
    assert set(executes) == set(EXPECTED_PERMITTED_EXECUTORS)
    for identity, expected in EXPECTED_PERMITTED_EXECUTORS.items():
        reason = executes[identity].reason
        assert reason.startswith(DENIAL_REASON_EXECUTE), identity
        assert (expected or "NONE -- no runtime principal") in reason, identity


def test_the_manifest_states_the_target_and_why_the_files_stay_split(
    manifest: PrivilegeManifest,
) -> None:
    """The arithmetic this whole programme computes, in the artefact itself.

    legacy compatibility privileges
      - architecturally forbidden access
      - unapproved SECURITY DEFINER execution
      + module-era privileges app_user already owns
    """
    target = next(note for note in manifest.notes if note.startswith("TARGET:"))
    assert "legacy compatibility privileges" in target
    assert "architecturally forbidden access" in target
    assert "unapproved SECURITY DEFINER execution" in target
    assert "module-era privileges app_user already owns" in target
    assert "PERMANENT" in target, (
        "the split is a property of the shape, not a staging step: bulk-safe "
        "grants in the grant file, every denial in a comment-only ledger"
    )


def test_the_recorded_finding_about_the_platform_relay_was_ACTED_on(
    manifest: PrivilegeManifest,
) -> None:
    """The finding was recorded on 2026-09-04 and RULED on the same day.

    It used to read "UNRESOLVED, RECORDED": `public.platform_outbox_events`
    sat in the routine sweep because its schema was `public` rather than
    `mod_`, while `20260824_outbox_relay` creates it as the control-plane
    relay ledger and explicitly REVOKEs it from `app_user` at table and column
    level. A generator does not make a disposition on its own, so it was
    recorded rather than acted on.

    Michael ruled it a Change-1 BLOCKER. This test is the same finding at the
    other end of that ruling: the note must now say what was DONE, name every
    migration that was the evidence, and the relation must actually be denied.
    A finding that quietly turns back into an open note is the regression.
    """
    assert not any(note.startswith("UNRESOLVED, RECORDED:") for note in manifest.notes)
    denied = next(
        note for note in manifest.notes if note.startswith("DENIED, control plane")
    )
    for evidence in (
        "public.platform_outbox_events",
        "20260824_outbox_relay",
        "public.platform_idempotency_records",
        "20260820_idempotency_ledger",
        "public.tenants",
        "public.tenant_domains",
        "20260813_tenant_projection",
    ):
        assert evidence in denied, evidence
    assert "REVERSED a tested migration decision" in denied

    decision_2 = next(note for note in manifest.notes if note.startswith("DECISION 2"))
    assert "heuristic is REMOVED" in decision_2.replace("`mod_` plane ", "")
    assert "REFUSES generation" in decision_2

    decision_1 = next(note for note in manifest.notes if note.startswith("DECISION 1 "))
    assert "hr.enforce_employment_type_projection" not in decision_1
    assert "trigger fence has NONE" in decision_1
    assert "outbox_dispatcher" in decision_1
    assert "platform_outbox_dispatcher" in decision_1
    assert "reverse a tested migration decision" in decision_1

    public_default = next(
        note for note in manifest.notes if note.startswith("DECISION 1, the verifier")
    )
    assert "PUBLIC BY DEFAULT" in public_default
    assert "REMEDIATION REQUIRED BEFORE CUTOVER" in public_default


def test_the_generated_sql_only_grants() -> None:
    """No REVOKE, no ALTER, no ownership change, no membership, no GRANT ALL."""
    path = ROUTINE_SQL_PATH
    text = path.read_text(encoding="utf-8")
    statements = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert statements[0] == "BEGIN;"
    assert statements[-1] == "COMMIT;"
    for statement in statements[1:-1]:
        assert statement.startswith("GRANT "), statement
        assert statement.endswith(";"), statement
        assert not re.search(r"\bGRANT\s+ALL\b", statement, re.IGNORECASE), statement
        # One privilege per statement. A comma-joined privilege list is how a
        # reviewer stops being able to see what is granted, and it is the same
        # shape `has_table_privilege` reads as an ANY test.
        head = statement.split(" ON ", 1)[0]
        assert "," not in head, statement
        assert statement.split(" TO ", 1)[1] == f'"{TARGET_ROLE}";', statement
    body = "\n".join(statements)
    for forbidden in (
        "REVOKE",
        "ALTER ",
        "OWNER TO",
        "CREATE ",
        "DROP ",
        "SET ROLE",
        "WITH ADMIN",
        "WITH GRANT OPTION",
    ):
        assert forbidden not in body.upper(), forbidden
    # The legacy role is never a grantee: it is being retired, not extended.
    assert SOURCE_ROLE not in body
    # And no denied item renders SQL here -- Michael's Change-1 condition,
    # checked against the bytes rather than against the generator's intent.
    for identity in EXPECTED_CONTROL_PLANE_RELATIONS:
        _, _, qualified = identity.partition(":")
        schema, _, name = qualified.partition(".")
        assert f'"{schema}"."{name}"' not in body, identity
    assert "EXECUTE" not in body


def test_no_denied_item_renders_sql_because_the_ledger_has_none() -> None:
    """The strongest form of the condition: a fact about the bytes.

    A denial rendered as a comment INSIDE an applyable file is one uncomment
    away from being applied, and a reviewer skimming a 2,000-line transaction
    would not see the difference. So the denials live in a file with no
    `BEGIN`, no `COMMIT` and no statement at all -- applying it with psql is a
    no-op by construction rather than by convention.
    """
    text = DENIAL_LEDGER_PATH.read_text(encoding="utf-8")
    executable = [
        line for line in text.splitlines() if line.strip() and not line.startswith("--")
    ]
    assert executable == [], executable
    assert "BEGIN;" not in text and "COMMIT;" not in text
    # It is still a LEDGER, not an empty file: every denial is named, with the
    # statement that is NOT run, why, and who may do it instead.
    assert text.count("NOT GRANTED: GRANT ") == 25
    assert text.count("PERMITTED INSTEAD:") == 10, (
        "five control-plane relations and five denied functions, one line each"
    )
    for identity in EXPECTED_CONTROL_PLANE_RELATIONS:
        assert identity in text, identity
    for identity in EXPECTED_PERMITTED_EXECUTORS:
        assert identity in text, identity
    assert "no runtime principal" in text


def test_the_renderers_refuse_to_be_handed_the_wrong_rows(
    manifest: PrivilegeManifest,
) -> None:
    """Non-vacuity: the split is enforced, not merely observed.

    Both halves have to be able to fail, or "no denied item renders SQL" is a
    property of how the generator happens to be called today rather than of
    the renderer.
    """
    with pytest.raises(ValueError, match="denied rows"):
        render_grant_sql(manifest.rows, ROUTINE_SQL_TITLE)
    with pytest.raises(ValueError, match="grantable rows"):
        render_denial_ledger(manifest.rows, DENIAL_LEDGER_TITLE)


def test_the_exceptional_rows_never_leak_into_the_sweep(
    manifest: PrivilegeManifest,
) -> None:
    """The split IS the control, and it is PERMANENT.

    Michael's ruling: bulk-safe grants in the bulk file, the five functions
    isolated, the control-plane grants prohibited, the schema cases resolved.
    Keeping exceptional authorization separate from mechanical compatibility
    is the point -- a 1,700-line file with escalation decisions buried in it
    gets skimmed, and that does not stop being true once the decisions are
    made.
    """
    routine = manifest.routine()
    exceptional = manifest.exceptional()
    assert len(routine) + len(exceptional) == len(manifest.rows)
    assert len(exceptional) == 25, (
        "5 SECURITY DEFINER EXECUTE (denied, Decision 1) + 20 control-plane "
        "relation privileges (denied, Decision 2 -- five relations x four). "
        "The 5 derived schema-USAGE rows were settled and removed on "
        "2026-09-04."
    )
    assert not any(row.review_required or row.denied for row in routine)
    routine_sql = ROUTINE_SQL_PATH.read_text(encoding="utf-8")
    for row in exceptional:
        if row.object_kind == "schema":
            marker = f'ON SCHEMA "{row.schema}" TO'
        elif row.object_kind == "function":
            marker = f'"{row.schema}"."{row.object_name}"('
        else:
            marker = f'"{row.schema}"."{row.object_name}" TO'
        assert marker not in routine_sql, row.identity


def test_the_roles_agree_with_the_runtime_admission_contract() -> None:
    """One canonical runtime identity, named the same in both contracts."""
    from app.runtime_admission import RUNTIME_ROLE

    assert TARGET_ROLE == RUNTIME_ROLE
    assert SOURCE_ROLE != TARGET_ROLE


# ---------------------------------------------------------------------------
# The negative control -- ADR-0018
# ---------------------------------------------------------------------------


def test_negative_control_a_clean_snapshot_produces_no_violations(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """Without this, all eight proofs below are worthless.

    A detector that returned a violation for every input would "catch" every
    planted defect and prove nothing at all. This asserts the opposite end:
    the exact same 1,761-row fixture every proof mutates produces ZERO
    violations when nothing is wrong.
    """
    assert cutover_violations(manifest, snapshot) == []
    assert len(snapshot.privileges) == (
        len(manifest.rows) - len(manifest.denied()) + CENSUS_REVERSE_GAP
    ), "a denied row is not a privilege the target is expected to hold"
    # The clean state is SILENT on both denials -- the other half of the
    # ADR-0018 pair. A negative verifier that stayed quiet only because it
    # never fired would be worthless, and one that complained about a clean
    # database would be worse.
    assert denial_violations(manifest, snapshot) == []
    assert snapshot.denied_privileges, "the denial must actually be probed"
    assert snapshot.denied_column_grants
    assert function_denial_violations(manifest, snapshot) == []
    # Three questions per function, minus the one that has no executor to ask
    # about: 5 x 2 target/PUBLIC answers + 4 executor answers.
    assert len(snapshot.denied_function_execute) == 14
    assert all(
        entry.effective and entry.probed for entry in snapshot.denied_function_execute
    ), "an answer that is not effective and not probed is not an answer"
    assert {entry.role for entry in snapshot.denied_function_execute} == {
        TARGET_ROLE,
        PUBLIC_PSEUDO_ROLE,
        "outbox_dispatcher",
        "platform_outbox_dispatcher",
    }


# ---------------------------------------------------------------------------
# The eight sensitivity proofs. Each plants ONE defect and requires it NAMED.
# ---------------------------------------------------------------------------


def _only_violation(violations: list[str]) -> str:
    """One planted defect must produce one violation, not a cascade.

    Specificity is half the proof: a detector that answers a single mutation
    with fifty complaints is not naming the defect, it is drowning it.
    """
    assert len(violations) == 1, violations[:5]
    return violations[0]


def test_sensitivity_1_a_vanished_object_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    victim = manifest.section(SECTION_RELATIONS)[0]
    objects = tuple(
        entry if entry.identity != victim.identity else replace(entry, exists=False)
        for entry in snapshot.objects
    )
    # The privileges on a vanished object cannot be held either.
    privileges = tuple(
        entry
        if entry.identity != victim.identity
        else replace(entry, held=False, origin="none")
        for entry in snapshot.privileges
    )
    violations = cutover_violations(
        manifest,
        replace(snapshot, objects=objects, privileges=privileges),
    )
    vanished = _only_violation(violations)
    assert vanished.startswith("OBJECT VANISHED")
    assert victim.identity in vanished
    assert "BASELINE_TOTALS was not lowered" in vanished


def test_sensitivity_2_an_added_privilege_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    victim = manifest.section(SECTION_RELATIONS)[0]
    extra = ObservedPrivilege(
        identity=victim.identity,
        role=TARGET_ROLE,
        privilege="TRUNCATE",
        held=True,
        origin=ACCEPTED_PRIVILEGE_ORIGIN,
    )
    violations = cutover_violations(
        manifest,
        replace(snapshot, privileges=(*snapshot.privileges, extra)),
    )
    added = _only_violation(violations)
    assert added.startswith("PRIVILEGE ADDED")
    assert "TRUNCATE" in added and victim.identity in added


def test_sensitivity_3_an_absent_expected_privilege_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    victim = manifest.section(SECTION_SEQUENCES)[0]
    privileges = tuple(
        entry
        if not (
            entry.identity == victim.identity and entry.privilege == victim.privilege
        )
        else replace(entry, **{"held": False, "origin": "none"})
        for entry in snapshot.privileges
    )
    violations = cutover_violations(manifest, replace(snapshot, privileges=privileges))
    absent = _only_violation(violations)
    assert absent.startswith("PRIVILEGE ABSENT")
    assert victim.identity in absent and victim.privilege in absent


def test_sensitivity_3b_a_privilege_reached_by_the_wrong_origin_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """Effective privilege is not the same fact as a deliberate grant."""
    victim = manifest.section(SECTION_RELATIONS)[0]
    privileges = tuple(
        entry
        if not (
            entry.identity == victim.identity and entry.privilege == victim.privilege
        )
        else replace(entry, origin="public")
        for entry in snapshot.privileges
    )
    violations = cutover_violations(manifest, replace(snapshot, privileges=privileges))
    origin = _only_violation(violations)
    assert origin.startswith("PRIVILEGE ORIGIN")
    assert "'public'" in origin and victim.identity in origin


def test_sensitivity_4_a_kind_change_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """A table replaced by a view keeps its name and is a different object."""
    victim = next(
        row for row in manifest.section(SECTION_RELATIONS) if row.relkind == "r"
    )
    objects = tuple(
        entry if entry.identity != victim.identity else replace(entry, relkind="v")
        for entry in snapshot.objects
    )
    violations = cutover_violations(manifest, replace(snapshot, objects=objects))
    kind = _only_violation(violations)
    assert kind.startswith("KIND CHANGE")
    assert victim.identity in kind and "'v'" in kind


def test_sensitivity_5_a_confused_function_overload_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """The defect this whole identity scheme exists to prevent.

    `claim_outbox_batch(text, integer, integer)` is in the manifest. A second
    overload -- `claim_outbox_batch(text)` -- is a DIFFERENT function body. A
    verifier that resolved by bare name would grant EXECUTE on whichever one
    it happened to find and report success.
    """
    victim = next(
        row
        for row in manifest.section(SECTION_FUNCTIONS)
        if row.object_name == "claim_outbox_batch"
    )
    wrong = "function:public.claim_outbox_batch(text)"
    objects = tuple(
        entry
        if entry.identity != victim.identity
        else replace(entry, resolved_identity=wrong, candidate_count=2)
        for entry in snapshot.objects
    )
    violations = cutover_violations(manifest, replace(snapshot, objects=objects))
    assert len(violations) == 2, violations
    ambiguity = next(v for v in violations if v.startswith("OVERLOAD AMBIGUITY"))
    confusion = next(v for v in violations if v.startswith("OVERLOAD CONFUSION"))
    assert victim.identity in ambiguity
    assert victim.identity in confusion and wrong in confusion


def test_sensitivity_6_a_membership_grant_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    violations = cutover_violations(
        manifest,
        replace(
            snapshot,
            memberships=(ObservedMembership(TARGET_ROLE, SOURCE_ROLE),),
        ),
    )
    membership = _only_violation(violations)
    assert membership.startswith("ROLE MEMBERSHIP")
    assert TARGET_ROLE in membership and SOURCE_ROLE in membership


def test_sensitivity_6b_bypassrls_and_an_ownership_change_are_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    victim = manifest.section(SECTION_RELATIONS)[0]
    objects = tuple(
        entry if entry.identity != victim.identity else replace(entry, owner="postgres")
        for entry in snapshot.objects
    )
    violations = cutover_violations(
        manifest,
        replace(
            snapshot,
            objects=objects,
            postures=(
                ObservedRolePosture(SOURCE_ROLE, False, False),
                ObservedRolePosture(TARGET_ROLE, True, False),
            ),
        ),
    )
    assert len(violations) == 2, violations
    assert any(v.startswith("ROLE ATTRIBUTE") and "BYPASSRLS" in v for v in violations)
    ownership = next(v for v in violations if v.startswith("OWNERSHIP CHANGE"))
    assert victim.identity in ownership and "postgres" in ownership


def test_sensitivity_7_a_module_privilege_on_the_legacy_role_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """`dotmac_erp_app` is being retired. It must never gain module access."""
    intruder = ObservedPrivilege(
        identity=relation_identity("mod_people", "employees"),
        role=SOURCE_ROLE,
        privilege="SELECT",
        held=True,
    )
    violations = cutover_violations(
        manifest,
        replace(
            snapshot,
            legacy_module_privileges=(
                *snapshot.legacy_module_privileges,
                intruder,
            ),
        ),
    )
    leak = _only_violation(violations)
    assert leak.startswith("LEGACY MODULE PRIVILEGE")
    assert "mod_people.employees" in leak and SOURCE_ROLE in leak
    # And the one pre-existing module-era grant must NOT fire: an allowlist
    # that also flags its own contents is a detector nobody can act on.
    #
    # That was asserted as `"mod_files" not in <any violation>`, which was
    # never quite the property: the refusal now NAMES the frozen allowlist
    # in its own message, so the substring stopped telling "the allowlisted
    # grant fired" apart from "the message quotes the allowlist" and could
    # not pass again however correct the detector was. It is asserted on the
    # SUBJECT of the violation instead, which is the thing that was meant.
    (allowlisted,) = sorted(MODULE_ERA_ALLOWLIST)
    assert allowlisted in {
        entry.identity
        for entry in snapshot.legacy_module_privileges
        if entry.role == SOURCE_ROLE and entry.held
    }, "non-vacuous: the legacy role really does hold the allowlisted grant"
    assert not any(f" on {allowlisted}. " in v for v in violations), (
        "the frozen exception is not reported as a leak by the detector "
        "whose allowlist holds it"
    )

    # The two things the allowlist entry means, stated where a reader
    # arriving at `mod_files` will meet them. Files declares BOTH planes in
    # ONE schema, so the plane is decided per DECLARED RELATION and the
    # schema name decides nothing: the platform relation is denied, and the
    # tenant relation beside it is not.
    assert {
        row.disposition for row in manifest.rows if row.identity == allowlisted
    } == {DISPOSITION_DENIED}
    resolver = default_resolver()
    assert resolver.resolve("mod_files", "platform_stored_files").plane == PLANE_CONTROL
    assert resolver.resolve("mod_files", "stored_files").plane == PLANE_TENANT
    assert not any(
        row.schema == "mod_files" and row.object_name == "stored_files" and row.denied
        for row in manifest.rows
    ), "a tenant-plane Files relation is not denied by its schema's company"


def test_sensitivity_8_a_silently_lowered_baseline_is_named(
    manifest: PrivilegeManifest,
) -> None:
    """Two-directional: a baseline that FALLS on its own fails too.

    Losing rows is the easier failure to miss, because everything downstream
    still passes -- fewer grants to check, all of them present. That is
    exactly what a quietly dropped schema looks like.
    """
    lowered = replace(
        manifest,
        rows=tuple(
            row
            for row in manifest.rows
            if row.identity != manifest.section(SECTION_RELATIONS)[0].identity
        ),
    )
    fell = _only_violation(baseline_violations(lowered))
    assert fell.startswith("BASELINE FELL")
    assert str(EXPECTED_SECTION_ROWS[SECTION_RELATIONS]) in fell

    grown = replace(
        manifest,
        rows=(
            *manifest.rows,
            replace(
                manifest.section(SECTION_RELATIONS)[0],
                object_name="invented_table",
                identity=relation_identity("ap", "invented_table"),
            ),
        ),
    )
    grew = _only_violation(baseline_violations(grown))
    assert grew.startswith("BASELINE GREW")


def test_sensitivity_8b_a_revoked_exclusion_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """The exclusions ratchet in both directions too. Nothing is revoked."""
    dropped = next(
        entry
        for entry in snapshot.privileges
        if entry.identity.startswith("relation:mod_tax.preserved_")
    )
    violations = cutover_violations(
        manifest,
        replace(
            snapshot,
            privileges=tuple(
                entry for entry in snapshot.privileges if entry is not dropped
            ),
        ),
    )
    fell = _only_violation(violations)
    assert fell.startswith("EXCLUSION FELL")
    assert "mod_tax" in fell


def test_sensitivity_9_a_planted_table_grant_on_the_denied_relation_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """Half one of the denial proof: the relation ACL.

    `app_user` holding SELECT on `mod_files.platform_stored_files` is the
    plainest form of the defect ADR-0023 forbids -- the tenant application
    role reaching a control-plane relation.
    """
    victim = manifest.denied_relations()[0].identity
    planted = tuple(
        entry
        if not (entry.identity == victim and entry.privilege == "SELECT")
        else replace(entry, held=True)
        for entry in snapshot.denied_privileges
    )
    violations = cutover_violations(
        manifest, replace(snapshot, denied_privileges=planted)
    )
    held = _only_violation(violations)
    assert held.startswith("DENIED PRIVILEGE HELD")
    assert victim in held and "SELECT" in held
    assert DENIAL_REASON in held


def test_sensitivity_10_a_planted_column_grant_on_the_denied_relation_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """Half two, and the one that is easy to miss.

    `GRANT SELECT(storage_key) ON mod_files.platform_stored_files TO app_user`
    leaves `relacl` untouched. `has_table_privilege(..., 'SELECT')` answers
    FALSE, the table-level half above stays silent, and the tenant role reads
    the column anyway. A denial proved only against the relation ACL is not
    proved, so this defect is planted at COLUMN level with the table level
    left clean -- if the column check were missing, this test would pass
    trivially with zero violations.
    """
    victim = manifest.denied_relations()[0].identity
    planted = tuple(
        entry
        if not (entry.identity == victim and entry.privilege == "SELECT")
        else replace(entry, columns_held=("storage_key",))
        for entry in snapshot.denied_column_grants
    )
    mutated = replace(snapshot, denied_column_grants=planted)
    # The table-level answer is untouched and still false: this defect is
    # invisible to every relation-level check in the module.
    assert all(
        not entry.held
        for entry in mutated.denied_privileges
        if entry.identity == victim
    )
    violations = cutover_violations(manifest, mutated)
    column = _only_violation(violations)
    assert column.startswith("DENIED COLUMN PRIVILEGE HELD")
    assert victim in column and "storage_key" in column and "SELECT" in column


def test_sensitivity_11_a_denial_nobody_probed_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """An absence is the one claim that passes for free.

    A verifier that asked nothing produces an empty violation list, which is
    byte-identical to a clean database. So not-probing is itself a refusal,
    in both halves -- and `columns_probed == 0` is refused as loudly as a
    missing observation, because "no columns held" from a probe that examined
    no columns is not evidence.
    """
    denied = {row.identity for row in manifest.denied_relations()}
    assert len(denied) == 5, sorted(denied)
    victim = sorted(denied)[0]
    blind = replace(snapshot, denied_privileges=(), denied_column_grants=())
    violations = denial_violations(manifest, blind)
    # Every denied relation is answered for, not just the first one found: a
    # verifier that probed one relation and stopped would look identical to a
    # clean database for the other four.
    assert len(violations) == len(denied) * (
        len(DENIED_TABLE_PRIVILEGES) + len(COLUMN_LEVEL_PRIVILEGES)
    ), violations[:5]
    assert all(v.startswith("UNPROBED") for v in violations)
    assert any("TRUNCATE" in v for v in violations), (
        "all seven table privileges must be answered, not the four the "
        "census happened to record"
    )
    assert any("REFERENCES" in v for v in violations)
    assert any("TRIGGER" in v for v in violations)
    named = {name for name in denied if any(name in v for v in violations)}
    assert named == denied
    assert any(victim in v for v in violations)

    # And a column probe that looked at ZERO columns is not a clean answer.
    # Built explicitly rather than by `replace`, because the shape being
    # refused is the one a verifier produces when the catalog query returned
    # no rows: an observation that exists, holds nothing, and examined
    # nothing.
    hollow = tuple(
        ObservedColumnGrant(
            identity=entry.identity,
            role=entry.role,
            privilege=entry.privilege,
            columns_held=(),
            columns_probed=0,
        )
        for entry in snapshot.denied_column_grants
    )
    violations = cutover_violations(
        manifest, replace(snapshot, denied_column_grants=hollow)
    )
    assert len(violations) == len(denied) * len(COLUMN_LEVEL_PRIVILEGES), violations[:5]
    assert all(v.startswith("UNPROBED COLUMN DENIAL") for v in violations)
    assert any("attacl" in v for v in violations)


def test_the_denial_is_owned_by_exactly_one_refusal(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """One decision, one owner -- otherwise one defect makes two complaints.

    The denied rows are in the manifest, so the ordinary privilege check
    would otherwise demand `app_user` HOLD them (the exact inversion the
    denial exists to prevent) and would additionally report a held privilege
    as `PRIVILEGE ADDED` beside `denial_violations`' own finding.
    """
    victim = manifest.denied_relations()[0]
    intruder = ObservedPrivilege(
        identity=victim.identity,
        role=TARGET_ROLE,
        privilege=victim.privilege,
        held=True,
        origin=ACCEPTED_PRIVILEGE_ORIGIN,
    )
    violations = cutover_violations(
        manifest, replace(snapshot, privileges=(*snapshot.privileges, intruder))
    )
    assert violations == [], (
        "the ordinary privilege checks must stay out of a denied relation "
        "entirely; denial_violations owns it, probing all seven privileges "
        "and every column"
    )


def test_the_proofs_are_all_distinct() -> None:
    """A roll-call, so a deleted proof is visible rather than merely absent."""
    proofs = {name for name in globals() if name.startswith("test_sensitivity_")}
    required = {
        "test_sensitivity_1_a_vanished_object_is_named",
        "test_sensitivity_2_an_added_privilege_is_named",
        "test_sensitivity_3_an_absent_expected_privilege_is_named",
        "test_sensitivity_3b_a_privilege_reached_by_the_wrong_origin_is_named",
        "test_sensitivity_4_a_kind_change_is_named",
        "test_sensitivity_5_a_confused_function_overload_is_named",
        "test_sensitivity_6_a_membership_grant_is_named",
        "test_sensitivity_6b_bypassrls_and_an_ownership_change_are_named",
        "test_sensitivity_7_a_module_privilege_on_the_legacy_role_is_named",
        "test_sensitivity_8_a_silently_lowered_baseline_is_named",
        "test_sensitivity_8b_a_revoked_exclusion_is_named",
        "test_sensitivity_9_a_planted_table_grant_on_the_denied_relation_is_named",
        "test_sensitivity_10_a_planted_column_grant_on_the_denied_relation_is_named",
        "test_sensitivity_11_a_denial_nobody_probed_is_named",
        "test_sensitivity_12_a_planted_execute_grant_on_a_denied_function_is_named",
        "test_sensitivity_13_a_surviving_public_execute_default_is_named",
        "test_sensitivity_13b_a_non_effective_answer_is_refused",
        "test_sensitivity_14_an_unexecutable_permitted_executor_is_named",
    }
    assert proofs == required


PLANE_PROOF_COVERAGE = {
    1: "test_plane_sensitivity_1_a_platform_table_in_public_is_caught",
    2: "test_plane_sensitivity_2_a_platform_table_in_a_mod_schema_is_caught",
    3: "test_plane_sensitivity_3_a_tenant_table_in_a_mod_schema_is_not_denied",
    4: "test_plane_sensitivity_4_a_tenant_table_in_public_follows_its_declaration",
    5: "test_plane_sensitivity_5_an_undeclared_relation_refuses_generation",
    6: "test_sensitivity_9_a_planted_table_grant_on_the_denied_relation_is_named",
    7: "test_sensitivity_10_a_planted_column_grant_on_the_denied_relation_is_named",
    8: "test_plane_sensitivity_8_a_schema_move_does_not_change_the_plane",
    9: "test_plane_sensitivity_9_changing_the_declaration_changes_the_result",
}


def test_the_nine_plane_proofs_are_present_and_each_is_planted() -> None:
    """The roll-call Michael asked for, by number, plus its negative control.

    Numbers 3, 4 and 8 carry the argument. A resolver that still read names
    would pass 1, 2, 6, 7 and 9 -- those are satisfied by any rule that denies
    `platform*` things -- and would fail exactly these three, because each of
    them requires the plane to come from a declaration that CONTRADICTS what
    the name suggests: a tenant table wearing a `mod_` prefix, a tenant
    relation living in `public` beside four control-plane ones, and a
    control-plane relation whose schema changed underneath it.
    """
    names = set(globals())
    for number, proof in sorted(PLANE_PROOF_COVERAGE.items()):
        assert proof in names, (number, proof)
    assert len(set(PLANE_PROOF_COVERAGE.values())) == 9
    # The negative control on the REAL data, without which all nine could be
    # passing because the resolver denies everything.
    assert "test_plane_negative_control_the_real_census_is_mostly_tenant" in names


def test_every_section_is_represented(manifest: PrivilegeManifest) -> None:
    assert set(SECTIONS) == set(manifest.counts())
    assert all(manifest.counts()[section] > 0 for section in SECTIONS)


# ---------------------------------------------------------------------------
# Decision 1: the three EXECUTE proofs. The middle one is the subtle one.
# ---------------------------------------------------------------------------


def test_sensitivity_12_a_planted_execute_grant_on_a_denied_function_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """The plain form: the tenant role can execute a denied definer."""
    victim = manifest.denied_functions()[0].identity
    planted = tuple(
        entry
        if not (entry.identity == victim and entry.role == TARGET_ROLE)
        else replace(entry, held=True)
        for entry in snapshot.denied_function_execute
    )
    violations = cutover_violations(
        manifest, replace(snapshot, denied_function_execute=planted)
    )
    held = _only_violation(violations)
    assert held.startswith("DENIED EXECUTE HELD")
    assert victim in held and TARGET_ROLE in held
    assert DENIAL_REASON_EXECUTE in held


def test_sensitivity_13_a_surviving_public_execute_default_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """The half that is easy to omit, and the one Michael made decisive.

    `CREATE FUNCTION` grants `EXECUTE` to `PUBLIC` **by default**. A function
    whose ACL names no `app_user` entry at all can therefore still be
    executable by `app_user`, and `REVOKE ... FROM app_user` does nothing
    about it -- only `REVOKE ... FROM PUBLIC` does. So the check asks about
    `PUBLIC` by name and reports a surviving default as REMEDIATION OWED
    rather than passing it.

    The census recorded `public_execute = False` for all five, so this is
    quiet on today's data. That is exactly why it is planted: a check that is
    only correct on the data it was written against is not a check.
    """
    victim = manifest.denied_functions()[0].identity
    planted = tuple(
        entry
        if not (entry.identity == victim and entry.role == PUBLIC_PSEUDO_ROLE)
        else replace(entry, held=True)
        for entry in snapshot.denied_function_execute
    )
    mutated = replace(snapshot, denied_function_execute=planted)
    # The app_user answer is untouched and still false. A verifier that read
    # the ACL for `app_user` would see nothing here at all.
    assert all(
        not entry.held
        for entry in mutated.denied_function_execute
        if entry.identity == victim and entry.role == TARGET_ROLE
    )
    violations = cutover_violations(manifest, mutated)
    public = _only_violation(violations)
    assert public.startswith("PUBLIC EXECUTE INHERITED")
    assert victim in public
    assert "REMEDIATION REQUIRED BEFORE CUTOVER" in public
    assert f"`REVOKE ... FROM {TARGET_ROLE}` alone does NOT" in public


def test_sensitivity_13b_a_non_effective_answer_is_refused(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """Non-vacuity for the shape of the question, not just its answer.

    An ACL reading and an effective privilege question return the same value
    on a clean database and different values on the one that matters. So the
    snapshot records HOW the answer was obtained, and an answer that did not
    come from `has_function_privilege` is refused rather than believed.
    """
    victim = manifest.denied_functions()[0].identity
    downgraded = tuple(
        entry
        if not (entry.identity == victim and entry.role == TARGET_ROLE)
        else replace(entry, effective=False)
        for entry in snapshot.denied_function_execute
    )
    violations = cutover_violations(
        manifest, replace(snapshot, denied_function_execute=downgraded)
    )
    answer = _only_violation(violations)
    assert answer.startswith("NON-EFFECTIVE EXECUTE ANSWER")
    assert "inherited through" in answer and "PUBLIC" in answer


def test_sensitivity_14_an_unexecutable_permitted_executor_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """A denial can pass for the wrong reason: nobody can run it at all.

    If `outbox_dispatcher` loses EXECUTE, `app_user` still cannot execute the
    function and every negative check stays silent -- while the relay stops
    draining. The positive assertion is what keeps "denied to the tenant role"
    from degenerating into "broken for everyone".
    """
    victim = next(
        row
        for row in manifest.denied_functions()
        if row.permitted_principals == ("outbox_dispatcher",)
    )
    planted = tuple(
        entry
        if not (entry.identity == victim.identity and entry.role == "outbox_dispatcher")
        else replace(entry, held=False)
        for entry in snapshot.denied_function_execute
    )
    violations = cutover_violations(
        manifest, replace(snapshot, denied_function_execute=planted)
    )
    executor = _only_violation(violations)
    assert executor.startswith("PERMITTED EXECUTOR CANNOT EXECUTE")
    assert victim.identity in executor and "outbox_dispatcher" in executor
    assert "unreachable to EVERYONE" in executor


def test_a_function_denial_nobody_probed_is_named(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """An absence nobody looked for is not an absence -- the EXECUTE half."""
    blind = replace(snapshot, denied_function_execute=())
    violations = function_denial_violations(manifest, blind)
    assert len(violations) == 14, violations[:5]
    assert all(v.startswith("UNPROBED EXECUTE DENIAL") for v in violations)
    assert any(PUBLIC_PSEUDO_ROLE in v for v in violations)
    assert any("outbox_dispatcher" in v for v in violations)


def test_the_function_denial_is_owned_by_exactly_one_refusal(
    manifest: PrivilegeManifest, snapshot: PrivilegeSnapshot
) -> None:
    """One decision, one owner -- as for the relation denial.

    The five EXECUTE rows are in the manifest, so the ordinary privilege check
    would otherwise demand `app_user` HOLD the EXECUTE it was just refused.
    """
    victim = manifest.denied_functions()[0]
    intruder = ObservedPrivilege(
        identity=victim.identity,
        role=TARGET_ROLE,
        privilege="EXECUTE",
        held=True,
        origin=ACCEPTED_PRIVILEGE_ORIGIN,
    )
    violations = cutover_violations(
        manifest, replace(snapshot, privileges=(*snapshot.privileges, intruder))
    )
    assert violations == [], (
        "function_denial_violations owns the EXECUTE denial; the ordinary "
        "checks must stay out of it entirely"
    )


def test_the_denial_ratchet_is_two_directional(
    manifest: PrivilegeManifest,
) -> None:
    """A denial that disappears on its own is the failure this shape prevents.

    And one that appears on its own is an unreviewed refusal of access
    somebody may be relying on. Both directions, both edits to `DENIED_TOTALS`
    or both defects.
    """
    dropped = replace(
        manifest,
        rows=tuple(
            row
            for row in manifest.rows
            if row.identity != manifest.denied_functions()[0].identity
        ),
    )
    violations = baseline_violations(dropped)
    assert any(v.startswith("DENIALS FELL") for v in violations), violations
    assert any(v.startswith("BASELINE FELL") for v in violations), violations

    promoted = replace(
        manifest,
        rows=tuple(
            row
            if row.section != SECTION_SEQUENCES
            else replace(
                row,
                section=SECTION_CONTROL_PLANE,
                disposition=DISPOSITION_DENIED,
                denial_reason=DENIAL_REASON,
            )
            for row in manifest.rows
        ),
    )
    violations = baseline_violations(promoted)
    assert any(v.startswith("DENIALS GREW") for v in violations), violations


# ---------------------------------------------------------------------------
# Decision 2: the nine plane proofs. Each plants ONE thing, by declaration.
# ---------------------------------------------------------------------------


def _census_of(*relations: tuple[str, str]) -> dict:
    """A minimal census carrying exactly the relations named.

    Small on purpose: these proofs are about the plane RESOLVER, and a
    1,716-row fixture would bury which relation moved. The negative control
    below runs against the real census instead.
    """
    return {
        "captured_at": "2026-09-04T09:09:14.274044+00:00",
        "host": "erp.dotmac.io",
        "database": "dotmac_erp@7650449984751865891",
        "server_version": "16.4",
        "source_role": SOURCE_ROLE,
        "target_role": TARGET_ROLE,
        "schemas": [{"schema": schema} for schema, _ in dict.fromkeys(relations)],
        "tables": [
            {
                "schema": schema,
                "name": name,
                "kind": "r",
                "privilege": privilege,
                "owner": EXPECTED_OWNER,
                "direct_grant": True,
            }
            for schema, name in relations
            for privilege in ("SELECT", "INSERT")
        ],
        "sequences": [],
        "functions": [],
        "reverse_gap": [],
    }


def _planes(*relations: RelationPlane, schemas: tuple[str, ...] = ()) -> PlaneResolver:
    return PlaneResolver(
        relation_planes=relations,
        schema_planes=tuple(
            SchemaPlane(
                schema=schema,
                plane=PLANE_TENANT,
                declared_by="test declaration",
                authority="planted by the proof",
            )
            for schema in schemas
        ),
    )


def _control(relation: str, schema: str) -> RelationPlane:
    return RelationPlane(
        relation=relation,
        plane=PLANE_CONTROL,
        schema=schema,
        declared_by="test declaration",
        authority="planted by the proof",
        permitted_principals=("platform_api",),
    )


def _tenant(relation: str, schema: str) -> RelationPlane:
    return RelationPlane(
        relation=relation,
        plane=PLANE_TENANT,
        schema=schema,
        declared_by="test declaration",
        authority="planted by the proof",
    )


def _dispositions(census: dict, resolver: PlaneResolver) -> dict[str, str]:
    built = manifest_from_census(census, resolver=resolver)
    return {row.identity: row.disposition for row in built.rows}


def test_plane_sensitivity_1_a_platform_table_in_public_is_caught() -> None:
    """A control-plane relation whose schema is `public`, not `mod_`.

    This is the case the old heuristic got wrong four times over.
    """
    census = _census_of(("public", "platform_outbox_events"), ("public", "invoices"))
    resolver = _planes(
        _control("platform_outbox_events", "public"), schemas=("public",)
    )
    dispositions = _dispositions(census, resolver)
    assert dispositions["relation:public.platform_outbox_events"] == DISPOSITION_DENIED
    assert dispositions["relation:public.invoices"] == DISPOSITION_GRANT


def test_plane_sensitivity_2_a_platform_table_in_a_mod_schema_is_caught() -> None:
    """The case the old heuristic got right -- still right, for a new reason."""
    census = _census_of(("mod_files", "platform_stored_files"))
    resolver = _planes(_control("platform_stored_files", "mod_files"))
    dispositions = _dispositions(census, resolver)
    assert (
        dispositions["relation:mod_files.platform_stored_files"] == DISPOSITION_DENIED
    )


def test_plane_sensitivity_3_a_tenant_table_in_a_mod_schema_is_not_denied() -> None:
    """A `mod_` relation the module declares TENANT is GRANTED.

    No name-based rule can pass this and proof 2 at once: both relations carry
    the same prefix and come out on opposite planes. That is the point -- the
    prefix decides nothing, the declaration decides everything.
    """
    census = _census_of(
        ("mod_files", "stored_files"), ("mod_files", "platform_stored_files")
    )
    resolver = _planes(
        _tenant("stored_files", "mod_files"),
        _control("platform_stored_files", "mod_files"),
    )
    dispositions = _dispositions(census, resolver)
    assert dispositions["relation:mod_files.stored_files"] == DISPOSITION_GRANT
    assert (
        dispositions["relation:mod_files.platform_stored_files"] == DISPOSITION_DENIED
    )


def test_plane_sensitivity_4_a_tenant_table_in_public_follows_its_declaration() -> None:
    """A relation-level TENANT declaration inside `public` is honoured.

    `public` holds both planes at once. Proof 1 shows a control-plane relation
    there; this shows a relation the declaration puts on the tenant plane
    EXPLICITLY, at relation level, standing beside it. A rule that read the
    schema could not tell them apart.
    """
    census = _census_of(
        ("public", "platform_outbox_events"), ("public", "platform_audit_trail")
    )
    resolver = _planes(
        _control("platform_outbox_events", "public"),
        # Same schema, same `platform` word in the name, opposite plane --
        # because the declaration says so.
        _tenant("platform_audit_trail", "public"),
        schemas=("public",),
    )
    dispositions = _dispositions(census, resolver)
    assert dispositions["relation:public.platform_outbox_events"] == DISPOSITION_DENIED
    assert dispositions["relation:public.platform_audit_trail"] == DISPOSITION_GRANT


def test_plane_sensitivity_5_an_undeclared_relation_refuses_generation() -> None:
    """Unknown fails CLOSED. It does not default to the tenant plane."""
    census = _census_of(("nobody_declared_this", "orphan"))
    resolver = _planes(schemas=("public",))
    with pytest.raises(UnclassifiedRelation) as caught:
        manifest_from_census(census, resolver=resolver)
    message = str(caught.value)
    assert "nobody_declared_this.orphan" in message
    assert "generation refuses rather than defaulting" in message
    for forbidden in ("`mod_` schema-name prefix", "`public` schema", "current ACLs"):
        assert forbidden in message


def test_plane_sensitivity_8_a_schema_move_does_not_change_the_plane() -> None:
    """Moving a relation between schemas does not change what it IS.

    The declaration is keyed by relation name and RECORDS the schema, so a
    relation observed somewhere else keeps its plane and the move is reported.
    A resolver that lost the classification at exactly this moment would be
    the name-based heuristic wearing a different hat.
    """
    declaration = _control("platform_outbox_events", "public")
    resolver = _planes(declaration, schemas=("public", "mod_relay", "legacy"))

    for schema in ("public", "mod_relay", "legacy"):
        verdict = resolver.resolve(schema, "platform_outbox_events")
        assert verdict.plane == PLANE_CONTROL, schema
        assert verdict.schema_moved == (schema != "public"), schema

    moved_census = _census_of(("legacy", "platform_outbox_events"))
    built = manifest_from_census(moved_census, resolver=resolver)
    rows = built.section(SECTION_CONTROL_PLANE)
    assert {row.identity for row in rows} == {"relation:legacy.platform_outbox_events"}
    assert all(row.disposition == DISPOSITION_DENIED for row in rows)
    assert all(
        "schema MOVE does not change what a relation is" in row.reason for row in rows
    )

    # The mirror image: a TENANT relation moved into `public` beside the
    # control-plane ones stays tenant, and is still granted.
    tenant_resolver = _planes(_tenant("invoices", "ar"), schemas=("public", "ar"))
    assert tenant_resolver.resolve("public", "invoices").plane == PLANE_TENANT


def test_plane_sensitivity_9_changing_the_declaration_changes_the_result() -> None:
    """The non-vacuity proof: the resolver READS the declaration.

    Same census, same relation, two declarations -- and the disposition flips.
    A resolver whose answer never moves when the declaration moves is not
    reading it.
    """
    census = _census_of(("mod_people", "employment_types"))
    identity = "relation:mod_people.employment_types"

    as_tenant = _dispositions(
        census, _planes(_tenant("employment_types", "mod_people"))
    )
    assert as_tenant[identity] == DISPOSITION_GRANT

    as_control = _dispositions(
        census, _planes(_control("employment_types", "mod_people"))
    )
    assert as_control[identity] == DISPOSITION_DENIED


def test_plane_negative_control_the_real_census_is_mostly_tenant(
    manifest: PrivilegeManifest,
) -> None:
    """Without this, all nine proofs above could pass by denying everything.

    The real production census resolves to 1,696 GRANTED relation privileges
    and 20 DENIED ones. A resolver that flagged everything would "catch" every
    planted control-plane table and prove nothing at all.
    """
    relations = [row for row in manifest.rows if row.object_kind == "relation"]
    tenant = [row for row in relations if row.plane == PLANE_TENANT]
    control = [row for row in relations if row.plane == PLANE_CONTROL]
    assert len(tenant) == EXPECTED_SECTION_ROWS[SECTION_RELATIONS] == 1696
    assert len(control) == EXPECTED_SECTION_ROWS[SECTION_CONTROL_PLANE] == 20
    assert all(row.disposition == DISPOSITION_GRANT for row in tenant)
    assert all(row.disposition == DISPOSITION_DENIED for row in control)
    # Every relation carries a plane and names the declaration behind it --
    # silence is not an answer here either.
    assert all(row.plane and row.plane_declared_by for row in relations)
