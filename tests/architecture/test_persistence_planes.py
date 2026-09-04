"""The guard for `app.persistence_planes` -- plane by DECLARATION, or not at all.

Decision 2 replaced a heuristic with a declaration, and a declaration only
earns that name if three things hold. Each has its own section below.

1. **The module half is READ, not restated.** If the table lists were pasted
   into `app/persistence_planes.py` they would be a second writer, and a second
   writer drifts. So the file is checked for the literal names it must not
   contain, and the resolver is checked against `COMPOSED_MODULES` itself.

2. **The host half cites evidence that actually says what it claims.** There is
   no host-side `ModuleManifest`, so the host declarations are the first writer
   of that fact -- which makes their citations the only thing standing between
   a declaration and an assertion. Every cited migration is READ, statically,
   and must contain the revoke it is cited for.

3. **The forbidden signals are actually unused.** A resolver that still read a
   name would pass every positive test written against relations whose names
   agree with their planes. The proofs that bite are the ones where the name
   and the declaration DISAGREE.

Nothing here connects to anything. The migrations are read as text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.persistence_planes import (
    FORBIDDEN_PLANE_SIGNALS,
    HOST_CONTROL_PLANE_RELATIONS,
    HOST_SCHEMA_PLANES,
    HOST_TENANT_SCHEMAS,
    PLANE_CONTROL,
    PLANE_TENANT,
    AmbiguousPlaneDeclaration,
    PlaneResolver,
    RelationPlane,
    SchemaPlane,
    UnclassifiedRelation,
    default_resolver,
    module_relation_planes,
)
from app.runtime_admission import COMPOSED_MODULES

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANES_SOURCE = REPO_ROOT / "app/persistence_planes.py"

#: The tenant application role every control-plane declaration must be able to
#: point at a revoke for. Stated here rather than imported, so this test fails
#: if the two contracts ever disagree about who the tenant role is.
TENANT_APPLICATION_ROLE = "app_user"


# ---------------------------------------------------------------------------
# 1. The module half is DERIVED
# ---------------------------------------------------------------------------


def test_module_declarations_come_from_the_composed_module_list() -> None:
    """Every module table, both planes, exactly as the assembly declares it."""
    declared = module_relation_planes()
    expected_tenant = {
        (module.schema, table)
        for module in COMPOSED_MODULES
        for table in module.tenant_tables
    }
    expected_control = {
        (module.schema, table)
        for module in COMPOSED_MODULES
        for table in module.platform_tables
    }
    tenant = {
        (item.schema, item.relation) for item in declared if item.plane == PLANE_TENANT
    }
    control = {
        (item.schema, item.relation)
        for item in declared
        if item.plane == PLANE_CONTROL
    }
    assert tenant == expected_tenant
    assert control == expected_control
    assert expected_control, (
        "at least one composed module must declare a platform table, or the "
        "control-plane half of this resolver is exercised by nothing"
    )
    assert not tenant & control, "a table cannot be on both planes"
    for item in declared:
        assert item.declared_by.startswith("module manifest: ")
        assert "COMPOSED_MODULES" in item.authority


def test_module_declarations_change_when_the_module_declaration_changes() -> None:
    """Non-vacuity: derived means derived, not coincidentally equal.

    Passing a DIFFERENT composed-module list must produce a different answer.
    A function that ignored its input and returned a constant would satisfy
    every assertion above.
    """
    from dataclasses import replace

    original = COMPOSED_MODULES[0]
    mutated = replace(
        original,
        tenant_tables=("a_table_no_module_declares",),
        platform_tables=("a_platform_table_no_module_declares",),
    )
    declared = module_relation_planes((mutated,))
    assert {item.relation for item in declared} == {
        "a_table_no_module_declares",
        "a_platform_table_no_module_declares",
    }
    planes = {item.relation: item.plane for item in declared}
    assert planes["a_table_no_module_declares"] == PLANE_TENANT
    assert planes["a_platform_table_no_module_declares"] == PLANE_CONTROL


def test_no_module_table_name_is_a_literal_in_the_plane_module() -> None:
    """A copied list is a second writer. This is the check that it stays one.

    The whole module -- code and prose -- is searched, because a name pasted
    into a docstring today is a name someone lifts into a constant tomorrow.
    """
    source = PLANES_SOURCE.read_text(encoding="utf-8")
    for module in COMPOSED_MODULES:
        for table in (*module.tenant_tables, *module.platform_tables):
            assert table not in source, (
                f"{table!r} appears literally in app/persistence_planes.py. "
                "Module table lists are READ from "
                "app.runtime_admission.COMPOSED_MODULES; a second copy drifts "
                "and then the two disagree with nobody noticing."
            )
        assert module.schema not in source, (
            f"the module schema {module.schema!r} appears literally in "
            "app/persistence_planes.py"
        )


# ---------------------------------------------------------------------------
# 2. The host half cites evidence, and the evidence is read
# ---------------------------------------------------------------------------


def test_every_host_control_plane_declaration_cites_a_real_revoke() -> None:
    """A declaration with no evidence behind it is an assertion.

    The citation names a migration; the migration is read; it must actually
    revoke that relation from the tenant application role. This is the
    "evidence to validate the declaration" half of Michael's rule -- the
    evidence never DECIDES the plane, but a declaration whose evidence does
    not exist is not a declaration anybody checked.
    """
    assert HOST_CONTROL_PLANE_RELATIONS, "the host half must not be empty"
    for declaration in HOST_CONTROL_PLANE_RELATIONS:
        cited = [
            token.strip().rstrip(".,")
            for token in declaration.authority.split()
            if token.startswith("alembic/versions/")
        ]
        assert cited, declaration.relation
        found = False
        for reference in cited:
            path = REPO_ROOT / reference
            assert path.exists(), (path, declaration.relation)
            # Normalized, because a migration writes its SQL as adjacent
            # Python string literals and the statement is therefore split
            # across lines and quotes. Searching the raw text would make this
            # check pass or fail on formatting.
            source = " ".join(
                path.read_text(encoding="utf-8")
                .replace('"', " ")
                .replace("'", " ")
                .split()
            )
            qualified = f"{declaration.schema}.{declaration.relation}"
            if (
                f"REVOKE ALL PRIVILEGES ON TABLE {qualified} FROM "
                f"{TENANT_APPLICATION_ROLE}" in source
                or f"REVOKE ALL ON {qualified} FROM {TENANT_APPLICATION_ROLE}"
                in source
            ):
                found = True
        assert found, (
            f"{declaration.schema}.{declaration.relation} is declared control "
            f"plane citing {cited}, but none of those revisions revokes it "
            f"from {TENANT_APPLICATION_ROLE}. Either the declaration is wrong "
            "or the citation is."
        )
        assert declaration.permitted_principals, declaration.relation
        assert TENANT_APPLICATION_ROLE not in declaration.permitted_principals


def test_the_host_declaration_covers_the_four_public_relations() -> None:
    """Stated a second time, so a declaration dropped in silence fails here."""
    assert {
        (item.schema, item.relation) for item in HOST_CONTROL_PLANE_RELATIONS
    } == {
        ("public", "platform_outbox_events"),
        ("public", "platform_idempotency_records"),
        ("public", "tenants"),
        ("public", "tenant_domains"),
    }
    assert all(item.plane == PLANE_CONTROL for item in HOST_CONTROL_PLANE_RELATIONS)


def test_the_host_schema_declarations_are_enumerated_and_tenant() -> None:
    assert len(HOST_SCHEMA_PLANES) == len(HOST_TENANT_SCHEMAS) == 41
    assert len(set(HOST_TENANT_SCHEMAS)) == len(HOST_TENANT_SCHEMAS)
    assert list(HOST_TENANT_SCHEMAS) == sorted(HOST_TENANT_SCHEMAS), (
        "kept sorted so a schema added in the middle is a one-line diff"
    )
    assert all(item.plane == PLANE_TENANT for item in HOST_SCHEMA_PLANES)
    assert all(item.authority for item in HOST_SCHEMA_PLANES)
    # `public` holds BOTH planes: declared tenant at schema level, with four
    # relation-level control-plane declarations overriding it. That is the
    # shape the `mod_` heuristic could not express.
    assert "public" in HOST_TENANT_SCHEMAS
    resolver = default_resolver()
    assert resolver.resolve("public", "audit_events").plane == PLANE_TENANT
    assert resolver.resolve("public", "tenants").plane == PLANE_CONTROL


# ---------------------------------------------------------------------------
# 3. The declaration is what decides, and it can refuse
# ---------------------------------------------------------------------------


def test_an_undeclared_relation_raises_rather_than_defaulting() -> None:
    resolver = default_resolver()
    with pytest.raises(UnclassifiedRelation) as caught:
        resolver.resolve("some_schema_nobody_declared", "a_table")
    message = str(caught.value)
    assert "some_schema_nobody_declared.a_table" in message
    for signal in FORBIDDEN_PLANE_SIGNALS:
        assert signal in message, (
            "the refusal states what may NOT be used to answer it, or the "
            "next person answers it with the prefix again"
        )


def test_two_declarations_that_disagree_are_refused_at_construction() -> None:
    """A plane that depends on read order is not a declared plane."""
    with pytest.raises(AmbiguousPlaneDeclaration, match="no declared plane"):
        PlaneResolver(
            relation_planes=(
                RelationPlane(
                    relation="contested",
                    plane=PLANE_TENANT,
                    schema="a",
                    declared_by="one",
                    authority="x",
                ),
                RelationPlane(
                    relation="contested",
                    plane=PLANE_CONTROL,
                    schema="b",
                    declared_by="two",
                    authority="x",
                    permitted_principals=("platform_api",),
                ),
            ),
            schema_planes=(),
        )


def test_the_real_declarations_do_not_collide() -> None:
    """Construction of the real resolver is itself the assertion."""
    resolver = default_resolver()
    assert len(resolver.relation_declarations) == (
        len(module_relation_planes()) + len(HOST_CONTROL_PLANE_RELATIONS)
    )
    assert len(resolver.control_plane_relations()) == 5


def test_a_control_plane_declaration_must_name_who_may_reach_it() -> None:
    """Unreachable is not the same as isolated, and the difference matters."""
    with pytest.raises(ValueError, match="not isolated, it is"):
        RelationPlane(
            relation="orphan",
            plane=PLANE_CONTROL,
            schema="public",
            declared_by="test",
            authority="x",
        )


def test_a_declaration_without_authority_is_refused() -> None:
    with pytest.raises(ValueError, match="cites no authority"):
        RelationPlane(
            relation="orphan",
            plane=PLANE_TENANT,
            schema="public",
            declared_by="test",
            authority="",
        )


def test_an_unknown_plane_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown plane"):
        RelationPlane(
            relation="orphan",
            plane="somewhere_else",
            schema="public",
            declared_by="test",
            authority="x",
        )
    with pytest.raises(ValueError, match="unknown plane"):
        SchemaPlane(
            schema="public",
            plane="somewhere_else",
            declared_by="test",
            authority="x",
        )


def test_a_schema_move_is_reported_but_does_not_reclassify() -> None:
    """The proof that the declaration is keyed by WHAT, not by WHERE."""
    resolver = default_resolver()
    home = resolver.resolve("public", "platform_outbox_events")
    assert home.plane == PLANE_CONTROL
    assert not home.schema_moved

    moved = resolver.resolve("mod_relay", "platform_outbox_events")
    assert moved.plane == PLANE_CONTROL, (
        "moving a relation does not change what it is"
    )
    assert moved.schema_moved
    assert moved.declared_schema == "public"
