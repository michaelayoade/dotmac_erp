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
the eight refusals is exercised by PLANTING the corresponding defect into an
otherwise-clean snapshot and asserting the violation NAMES it -- and the
NEGATIVE CONTROL asserts the same clean snapshot produces zero violations, so
none of the eight can be passing because the detector flags everything.

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

from app.privilege_manifest import (
    ACCEPTED_PRIVILEGE_ORIGIN,
    BASELINE_TOTALS,
    COLUMN_LEVEL_PRIVILEGES,
    DENIAL_REASON,
    DENIED_TABLE_PRIVILEGES,
    DISPOSITION_DENIED,
    DISPOSITION_GRANT,
    DISPOSITION_REVIEW_REQUIRED,
    EXPECTED_OWNER,
    MODULE_ERA_ALLOWLIST,
    REVIEW_SQL_TITLE,
    ROUTINE_SQL_TITLE,
    SECTION_FUNCTIONS,
    SECTION_MODULE_ERA,
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
    function_name_and_identity_arguments,
    manifest_from_census,
    manifest_from_json,
    manifest_to_json,
    relation_identity,
    render_grant_sql,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CENSUS_PATH = REPO_ROOT / "docs/inventories/erp-privilege-census-2026-09-04.json"
MANIFEST_PATH = (
    REPO_ROOT / "docs/inventories/erp-identity-cutover-manifest-2026-09-04.json"
)
ROUTINE_SQL_PATH = REPO_ROOT / "scripts/erp_identity_cutover_grants.sql"
REVIEW_SQL_PATH = REPO_ROOT / "scripts/erp_identity_cutover_review_required.sql"

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
    SECTION_RELATIONS: 1712,
    SECTION_SEQUENCES: 3,
    SECTION_FUNCTIONS: 5,
    SECTION_MODULE_ERA: 4,
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
    assert render_grant_sql(
        built.exceptional(), REVIEW_SQL_TITLE
    ) == REVIEW_SQL_PATH.read_text(encoding="utf-8")


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
    """1,712 relation rows + 4 module-era rows = the census's 1,716."""
    relation_rows = len(manifest.section(SECTION_RELATIONS)) + len(
        manifest.section(SECTION_MODULE_ERA)
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


def test_security_definer_functions_are_isolated_for_review(
    census: dict, manifest: PrivilegeManifest
) -> None:
    """Five escalation surfaces, never folded into a 1,700-row sweep.

    A SECURITY DEFINER function runs as its OWNER. Every one of these is owned
    by `app_admin`, which is BYPASSRLS, so EXECUTE is a grant of whatever the
    body does with that role's reach -- not a row in a table sweep.
    """
    assert all(entry["security_definer"] for entry in census["functions"])
    rows = manifest.section(SECTION_FUNCTIONS)
    assert len(rows) == CENSUS_FUNCTIONS
    for row in rows:
        assert row.review_required, row.identity
        assert row.owner == EXPECTED_OWNER, row.identity
        assert "SECURITY DEFINER" in row.reason
        assert row.owner in row.reason, "the reason names the owner it runs as"
    routine_sql = ROUTINE_SQL_PATH.read_text(encoding="utf-8")
    assert "EXECUTE" not in routine_sql
    review_sql = REVIEW_SQL_PATH.read_text(encoding="utf-8")
    assert review_sql.count("GRANT EXECUTE") == CENSUS_FUNCTIONS


def test_the_module_era_grant_is_denied_not_merely_flagged(
    manifest: PrivilegeManifest,
) -> None:
    """The decision is MADE. `review_required` and `denied` are not the same.

    A row marked `review_required` says "someone must read this before it is
    applied". A row marked `denied_by_architecture` says "this is never
    applied". Reporting the second as the first would put a settled refusal
    back on somebody's decision queue, and the SQL would still carry a
    `GRANT` for them to sign off.
    """
    rows = manifest.section(SECTION_MODULE_ERA)
    assert {row.identity for row in rows} == set(MODULE_ERA_ALLOWLIST)
    assert all(row.disposition == DISPOSITION_DENIED for row in rows)
    assert all(row.denied for row in rows)
    assert not any(row.review_required for row in rows), (
        "a denied row is not review debt: the review happened and the "
        "answer was no"
    )
    assert all(row.reason.startswith(DENIAL_REASON) for row in rows)
    assert all("ADR-0023" in row.reason for row in rows)
    assert "mod_files" not in ROUTINE_SQL_PATH.read_text(encoding="utf-8")

    # And it must not be rendered as an executable GRANT anywhere.
    review_sql = REVIEW_SQL_PATH.read_text(encoding="utf-8")
    statements = [
        line.strip()
        for line in review_sql.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert not any("platform_stored_files" in line for line in statements), (
        "a denied relation must never appear in an executable statement"
    )
    assert "NOT GRANTED: GRANT SELECT ON TABLE" in review_sql, (
        "the denial is kept VISIBLE as a comment -- a denial that is merely "
        "absent cannot be told apart from one nobody thought of"
    )
    assert DENIAL_REASON in review_sql


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
    assert counted[DISPOSITION_REVIEW_REQUIRED] == CENSUS_FUNCTIONS
    assert counted[DISPOSITION_DENIED] == 4
    with pytest.raises(ValueError, match="unknown disposition"):
        replace(manifest.rows[0], disposition="probably_fine")


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
        "denied-control-plane-relation",
        "no-grant-all",
        "no-role-membership",
        "no-ownership-transfer",
        "no-privileges-added-to-legacy-role",
        "no-role-attribute-change",
        "no-module-activation-change",
        "no-revoke",
    }
    denial = next(
        row
        for row in manifest.exclusions
        if row.exclusion_id == "denied-control-plane-relation"
    )
    assert denial.reason.startswith(DENIAL_REASON)
    assert "will NOT be given" in denial.reason


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
        "grants in the bulk file, the five functions isolated, the "
        "control-plane grants prohibited, the schema cases resolved"
    )


def test_the_recorded_finding_about_the_platform_relay_survives(
    manifest: PrivilegeManifest,
) -> None:
    """Found while classifying the definers; recorded, not silently acted on.

    `public.platform_outbox_events` sits in the ROUTINE sweep because its
    schema is `public` rather than `mod_`, yet `20260824_outbox_relay`
    creates it as the control-plane relay ledger and explicitly REVOKEs it
    from `app_user` at both table and column level. Applying the sweep would
    reverse that. The same is true of the `hr` definer EXECUTE row, which
    `20260828_people_et_activation` explicitly revokes from `app_user`.

    Neither is changed here: a disposition is an authorized decision, and a
    generator does not make one on its own. What it must not do is lose the
    finding, so the note is asserted rather than trusted to a review comment.
    """
    finding = next(
        note for note in manifest.notes if note.startswith("UNRESOLVED, RECORDED:")
    )
    assert "public.platform_outbox_events" in finding
    assert "20260824_outbox_relay" in finding
    assert "REVOKE ALL" in finding and "column-level" in finding
    assert "hr.enforce_employment_type_projection()" in finding
    assert "20260828_people_et_activation" in finding


@pytest.mark.parametrize("path", [ROUTINE_SQL_PATH, REVIEW_SQL_PATH])
def test_the_generated_sql_only_grants(path: Path) -> None:
    """No REVOKE, no ALTER, no ownership change, no membership, no GRANT ALL."""
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
    assert len(exceptional) == 9, (
        "5 SECURITY DEFINER EXECUTE (review required) + 4 control-plane "
        "module-era (denied). The 5 derived schema-USAGE rows were settled "
        "and removed on 2026-09-04."
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
    the exact same 1,761-row fixture the fourteen proofs mutate produces ZERO
    violations when nothing is wrong.
    """
    assert cutover_violations(manifest, snapshot) == []
    assert len(snapshot.privileges) == (
        len(manifest.rows) - len(manifest.denied()) + CENSUS_REVERSE_GAP
    ), "a denied row is not a privilege the target is expected to hold"
    # The clean state is SILENT on the denial too -- the other half of the
    # ADR-0018 pair. A negative verifier that stayed quiet only because it
    # never fired would be worthless, and one that complained about a clean
    # database would be worse.
    assert denial_violations(manifest, snapshot) == []
    assert snapshot.denied_privileges, "the denial must actually be probed"
    assert snapshot.denied_column_grants


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
    assert not any("mod_files" in v for v in violations)


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
    victim = manifest.denied()[0].identity
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
    victim = manifest.denied()[0].identity
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
    victim = manifest.denied()[0].identity
    blind = replace(snapshot, denied_privileges=(), denied_column_grants=())
    violations = denial_violations(manifest, blind)
    assert len(violations) == len(DENIED_TABLE_PRIVILEGES) + len(
        COLUMN_LEVEL_PRIVILEGES
    ), violations[:5]
    assert all(v.startswith("UNPROBED") for v in violations)
    assert any("TRUNCATE" in v for v in violations), (
        "all seven table privileges must be answered, not the four the "
        "census happened to record"
    )
    assert any("REFERENCES" in v for v in violations)
    assert any("TRIGGER" in v for v in violations)
    assert all(victim in v for v in violations)

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
    assert len(violations) == len(COLUMN_LEVEL_PRIVILEGES), violations[:5]
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
    victim = manifest.denied()[0]
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


def test_the_eight_proofs_are_all_distinct() -> None:
    """A roll-call, so a deleted proof is visible rather than merely absent."""
    proofs = {name for name in globals() if name.startswith("test_sensitivity_")}
    assert len(proofs) == 14, sorted(proofs)
    for required in (
        "test_sensitivity_1_a_vanished_object_is_named",
        "test_sensitivity_2_an_added_privilege_is_named",
        "test_sensitivity_3_an_absent_expected_privilege_is_named",
        "test_sensitivity_4_a_kind_change_is_named",
        "test_sensitivity_5_a_confused_function_overload_is_named",
        "test_sensitivity_6_a_membership_grant_is_named",
        "test_sensitivity_7_a_module_privilege_on_the_legacy_role_is_named",
        "test_sensitivity_8_a_silently_lowered_baseline_is_named",
        "test_sensitivity_9_a_planted_table_grant_on_the_denied_relation_is_named",
        "test_sensitivity_10_a_planted_column_grant_on_the_denied_relation_is_named",
        "test_sensitivity_11_a_denial_nobody_probed_is_named",
    ):
        assert required in proofs


def test_every_section_is_represented(manifest: PrivilegeManifest) -> None:
    assert set(SECTIONS) == set(manifest.counts())
    assert all(manifest.counts()[section] > 0 for section in SECTIONS)
