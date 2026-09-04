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
the real committed manifest -- 1,766 rows -- so these proofs run against the
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
    SOURCE_ROLE,
    TARGET_ROLE,
    ObservedMembership,
    ObservedPrivilege,
    ObservedRolePosture,
    PrivilegeManifest,
    PrivilegeSnapshot,
    UnparseableSignature,
    baseline_violations,
    clean_snapshot,
    cutover_violations,
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
    SECTION_SCHEMA_USAGE: 42,
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
        built.review_required(), REVIEW_SQL_TITLE
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


def test_the_module_era_grant_is_separated_and_frozen(
    manifest: PrivilegeManifest,
) -> None:
    rows = manifest.section(SECTION_MODULE_ERA)
    assert {row.identity for row in rows} == set(MODULE_ERA_ALLOWLIST)
    assert all(row.review_required for row in rows)
    assert all("ADR-0023" in row.reason for row in rows)
    assert "mod_files" not in ROUTINE_SQL_PATH.read_text(encoding="utf-8")


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
        "no-grant-all",
        "no-role-membership",
        "no-ownership-transfer",
        "no-privileges-added-to-legacy-role",
        "no-role-attribute-change",
        "no-module-activation-change",
        "no-revoke",
    }


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


def test_the_review_required_rows_never_leak_into_the_sweep(
    manifest: PrivilegeManifest,
) -> None:
    """The split IS the control. A buried escalation decision gets skimmed."""
    routine = manifest.routine()
    review = manifest.review_required()
    assert len(routine) + len(review) == len(manifest.rows)
    assert len(review) == 14, (
        "5 SECURITY DEFINER EXECUTE + 4 control-plane module-era + 5 derived "
        "schema USAGE"
    )
    assert not any(row.review_required for row in routine)
    routine_sql = ROUTINE_SQL_PATH.read_text(encoding="utf-8")
    for row in review:
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
    the exact same 1,766-row fixture the eight proofs mutate produces ZERO
    violations when nothing is wrong.
    """
    assert cutover_violations(manifest, snapshot) == []
    assert len(snapshot.privileges) == len(manifest.rows) + CENSUS_REVERSE_GAP


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


def test_the_eight_proofs_are_all_distinct() -> None:
    """A roll-call, so a deleted proof is visible rather than merely absent."""
    proofs = {name for name in globals() if name.startswith("test_sensitivity_")}
    assert len(proofs) == 11, sorted(proofs)
    for required in (
        "test_sensitivity_1_a_vanished_object_is_named",
        "test_sensitivity_2_an_added_privilege_is_named",
        "test_sensitivity_3_an_absent_expected_privilege_is_named",
        "test_sensitivity_4_a_kind_change_is_named",
        "test_sensitivity_5_a_confused_function_overload_is_named",
        "test_sensitivity_6_a_membership_grant_is_named",
        "test_sensitivity_7_a_module_privilege_on_the_legacy_role_is_named",
        "test_sensitivity_8_a_silently_lowered_baseline_is_named",
    ):
        assert required in proofs


def test_every_section_is_represented(manifest: PrivilegeManifest) -> None:
    assert set(SECTIONS) == set(manifest.counts())
    assert all(manifest.counts()[section] > 0 for section in SECTIONS)
