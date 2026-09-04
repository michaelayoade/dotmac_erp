"""Which persistence PLANE a relation belongs to, resolved by DECLARATION.

ADR-0023 splits a module's storage into two planes: the TENANT plane
(`tenant_id NOT NULL`, FORCEd row-level security, reachable by the tenant
application role) and the CONTROL plane (no tenant column, no RLS, REVOKEd
from the tenant application role -- the revocation IS the isolation).  Every
decision that turns on "is this relation control plane?" needs an answer, and
the answer has to come from somewhere.

## The heuristic this module replaces, and why it was wrong

`app/privilege_manifest.py` used to read the plane off the schema name: a
relation under a `mod_` schema was treated as module-era and denied, and
everything else was swept into the compatibility grant file.  That produced a
right answer for the one composed module's platform table and FOUR wrong ones:
the relay ledger, the idempotency ledger and the two tenant-catalogue relations
in `public` are control-plane relations whose qualified names carry no `mod_`
prefix at all.  Their migrations
(`20260824_outbox_relay`, `20260820_idempotency_ledger`,
`20260813_tenant_projection`) create them with no tenant column, no RLS and an
explicit `REVOKE ... FROM app_user`, and a compatibility sweep that granted
them would have REVERSED four tested migration decisions.

A name is not an ownership fact.  Neither is a schema, a column, a policy or
an ACL.

## The rule (Michael, 2026-09-04)

    Never infer plane from the `mod_` prefix, the `public` schema, the
    presence or absence of `tenant_id`, RLS state, or current ACLs.  Those are
    EVIDENCE TO VALIDATE THE DECLARATION, not sources of ownership.

So the plane comes from a declaration and from nothing else:

* a composed module's `ModuleManifest.tables`  -> TENANT plane;
* a composed module's `ModuleManifest.platform_tables` -> CONTROL plane;
* the host/assembly's own plane declaration below -> whatever it says;
* anything unclassified -> `UnclassifiedRelation`, which REFUSES generation.

Unknown fails CLOSED.  A relation nobody has classified is not "probably
tenant"; it is a relation whose owner has not been decided, and deciding it by
default is how the four `public` control-plane relations got swept up in the
first place.

## Where the module declarations are READ from, and why not copied

`app.runtime_admission.COMPOSED_MODULES` is the assembly's manifest-derived
declaration of every composed module's `tenant_tables` and `platform_tables`.
Its own docstring says why the tables are recorded there rather than imported
from the module: importing a composed-but-inert module would make an inert
composition live.  `tests/architecture/test_runtime_admission_is_read_only.py`
proves both lists against `tests/integration/tenant_table_inventory.tsv`, so
the declaration cannot drift from the schema the migrations actually build.

This module READS that structure.  It does not restate it.  A second copy of a
table list is a second writer, and a second writer drifts:
`tests/architecture/test_persistence_planes.py` asserts that no module table
name appears as a literal anywhere in this file, so a future edit that pastes
one in fails rather than quietly becoming authoritative.

The kernel's `ModuleManifest` objects themselves are NOT readable here:
`dotmac_kernel` is not an installed import in this assembly's static toolchain,
and the composed module distributions are not vendored into the tree.
`COMPOSED_MODULES` is the manifest-derived declaration that IS readable, and it
is proven against the live catalog by the test named above -- which is what
makes reading it equivalent to reading the manifests rather than a substitute
for it.

## The host/assembly half, and why it is enumerated here

There is no host-side `ModuleManifest`: the ERP assembly's own estate is built
by ERP's own alembic lineage, not by a composed module, so nothing else in the
tree declares its planes.  `HOST_CONTROL_PLANE_RELATIONS` and
`HOST_TENANT_SCHEMAS` below are therefore the FIRST writer of that fact, not a
copy of one -- and each control-plane entry carries the migration that is its
evidence, checked statically by the test module so a declaration cannot claim
an authority that does not say what it claims.

## Why a relation-level declaration is keyed by NAME, with the schema as data

A declaration says what a relation IS.  Moving a relation to another schema
does not change what it is, so the plane must not change either -- a control
plane relation that someone relocates to `public` is still control plane, and
a resolver that lost the classification at exactly that moment would be the
name-based heuristic wearing a different hat.  So `RelationPlane.schema` is
RECORDED DATA, not part of the key: a relation observed under a different
schema than its declaration records is reported as a MOVE (`schema_moved`),
which is a finding, while its plane is unchanged.

Two declarations of the same relation name that disagree about the plane are
refused at construction (`AmbiguousPlaneDeclaration`).  A relation whose plane
depends on which declaration you happen to read first has no declared plane.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.runtime_admission import COMPOSED_MODULES, ComposedModule

#: The two planes.  Strings rather than an enum for the same reason
#: `SettingDomain` is an open registered string in the kernel: the artefacts
#: this feeds are JSON, and a value that survives serialization unchanged is
#: one fewer conversion to get wrong.
PLANE_TENANT: Final[str] = "tenant"
PLANE_CONTROL: Final[str] = "control"
PLANES: Final[tuple[str, ...]] = (PLANE_TENANT, PLANE_CONTROL)

#: Signals that may NEVER decide a plane.  Stated as data so the prohibition is
#: quotable by the tests and the generated artefacts rather than living only in
#: a docstring.
FORBIDDEN_PLANE_SIGNALS: Final[tuple[str, ...]] = (
    "the `mod_` schema-name prefix",
    "the `public` schema",
    "the presence or absence of a `tenant_id` column",
    "row-level-security state",
    "current ACLs",
)


class UnclassifiedRelation(LookupError):
    """No declaration covers this relation, so it has no plane.

    Raising is the point.  Defaulting an unclassified relation to the tenant
    plane is what put four control-plane relations into a compatibility grant
    sweep; defaulting it to the control plane would silently withhold access
    that a live caller needs.  Neither default is an answer, so there is none.
    """


class AmbiguousPlaneDeclaration(ValueError):
    """Two declarations name the same relation and disagree about its plane."""


@dataclass(frozen=True)
class RelationPlane:
    """One relation-level plane DECLARATION.

    `relation` is the key.  `schema` is DATA -- see the module docstring for
    why a schema move is a finding rather than a reclassification.

    `permitted_principals` is who the declaration says MAY reach a
    control-plane relation.  It is empty for a tenant-plane declaration, where
    the answer is simply "the tenant application role, under RLS".
    """

    relation: str
    plane: str
    schema: str
    declared_by: str
    authority: str
    permitted_principals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.plane not in PLANES:
            raise ValueError(
                f"unknown plane {self.plane!r} declared for {self.relation!r}; "
                f"the two planes are {PLANES}"
            )
        if self.plane == PLANE_CONTROL and not self.permitted_principals:
            raise ValueError(
                f"the control-plane declaration for {self.relation!r} names no "
                "permitted principal. A relation the tenant role may not reach "
                "and that nothing else may reach either is not isolated, it is "
                "unreachable -- say who operates it."
            )
        if not self.authority:
            raise ValueError(
                f"the plane declaration for {self.relation!r} cites no "
                "authority. A declaration with no evidence behind it is an "
                "assertion, and this module exists because assertions about "
                "planes were being read off names."
            )


@dataclass(frozen=True)
class SchemaPlane:
    """The host assembly's plane declaration for one of its OWN schemas.

    This is a declaration, not an inference from the schema name: the ERP
    legacy estate is built by ERP's own alembic lineage as tenant-scoped
    storage, and that is a fact about the lineage rather than about the string.
    Relation-level declarations override it, which is exactly how the four
    control-plane relations inside `public` come out control plane while the
    other 152 relations in the same schema come out tenant.
    """

    schema: str
    plane: str
    declared_by: str
    authority: str

    def __post_init__(self) -> None:
        if self.plane not in PLANES:
            raise ValueError(f"unknown plane {self.plane!r} for schema {self.schema!r}")


@dataclass(frozen=True)
class PlaneVerdict:
    """The resolved plane for one qualified relation, and where it came from."""

    schema: str
    relation: str
    plane: str
    declared_by: str
    authority: str
    permitted_principals: tuple[str, ...] = ()
    #: True when the relation was observed under a schema its declaration does
    #: not record.  The plane is UNCHANGED; the move is reported separately.
    schema_moved: bool = False
    declared_schema: str = ""

    @property
    def control_plane(self) -> bool:
        return self.plane == PLANE_CONTROL


# ---------------------------------------------------------------------------
# The host/assembly declaration
# ---------------------------------------------------------------------------

#: The ERP assembly's own control-plane relations.  There is no host-side
#: `ModuleManifest` to read, so this is the FIRST declaration of the fact, and
#: every entry cites the migration that is its evidence.  The evidence is
#: checked statically by `tests/architecture/test_persistence_planes.py`: a
#: declaration that cites a revision which does not actually revoke the
#: relation from the tenant application role fails there.
#:
#: Note what is NOT the reason any of these is here.  Three of the four sit in
#: `public`, whose name says nothing; `platform_outbox_events` and
#: `platform_idempotency_records` merely happen to begin with the word; and
#: `tenants`/`tenant_domains` are named for the concept they catalogue rather
#: than for a plane.  What puts each of them here is a migration that creates
#: it with no tenant column, no RLS and an explicit revoke from `app_user`.
HOST_CONTROL_PLANE_RELATIONS: Final[tuple[RelationPlane, ...]] = (
    RelationPlane(
        relation="platform_outbox_events",
        plane=PLANE_CONTROL,
        schema="public",
        declared_by="host assembly (ERP alembic lineage)",
        authority=(
            "alembic/versions/20260824_outbox_relay.py creates the "
            "control-plane relay ledger with no tenant_id column, no RLS and "
            "no policy, GRANTs it to platform_api and app_admin, then "
            "`REVOKE ALL PRIVILEGES ON TABLE public.platform_outbox_events "
            "FROM app_user` followed by column-level REVOKEs of "
            "SELECT/INSERT/UPDATE/REFERENCES from app_user."
        ),
        permitted_principals=("platform_api", "app_admin"),
    ),
    RelationPlane(
        relation="platform_idempotency_records",
        plane=PLANE_CONTROL,
        schema="public",
        declared_by="host assembly (ERP alembic lineage)",
        authority=(
            "alembic/versions/20260820_idempotency_ledger.py creates the "
            "control-plane ledger beside the tenant one, GRANTs it to "
            "platform_api and app_admin, then `REVOKE ALL PRIVILEGES ON TABLE "
            "public.platform_idempotency_records FROM app_user` followed by "
            "column-level REVOKEs of SELECT/INSERT/UPDATE/REFERENCES."
        ),
        permitted_principals=("platform_api", "app_admin"),
    ),
    RelationPlane(
        relation="tenants",
        plane=PLANE_CONTROL,
        schema="public",
        declared_by="host assembly (ERP alembic lineage)",
        authority=(
            "alembic/versions/20260813_tenant_projection.py locks the tenant "
            "catalogue down with `REVOKE ALL ON public.tenants FROM PUBLIC` "
            "and `REVOKE ALL ON public.tenants FROM app_user`, and gives the "
            "runtime role EXECUTE on public.app_current_tenant_id() instead -- "
            "the catalogue is read through a narrow SECURITY DEFINER function, "
            "never as rows."
        ),
        permitted_principals=("app_admin",),
    ),
    RelationPlane(
        relation="tenant_domains",
        plane=PLANE_CONTROL,
        schema="public",
        declared_by="host assembly (ERP alembic lineage)",
        authority=(
            "alembic/versions/20260813_tenant_projection.py locks it down in "
            "the same block: `REVOKE ALL ON public.tenant_domains FROM PUBLIC` "
            "and `REVOKE ALL ON public.tenant_domains FROM app_user`."
        ),
        permitted_principals=("app_admin",),
    ),
)

#: The ERP assembly's own TENANT-plane schemas.
#:
#: One declaration per schema, enumerated rather than pattern-matched, because
#: "every schema that is not `mod_`" is the heuristic this module exists to
#: delete.  The shared authority is the same for all of them and is stated
#: once: each is created by ERP's own alembic lineage as tenant-scoped storage
#: under `core_org.organization` scoping and row-level security, none is the
#: platform half of a dual-plane module, and none carries a `platform_tables`
#: declaration anywhere in the tree.  Relation-level declarations above
#: override this, which is how `public` holds both planes at once.
#:
#: A schema absent from this tuple and from every module manifest is
#: UNCLASSIFIED and refuses generation.  That is the fail-closed half: a new
#: schema appearing in a re-taken census forces an edit here, in a commit, with
#: a reason -- it cannot arrive as a default.
HOST_TENANT_SCHEMA_AUTHORITY: Final[str] = (
    "Created by ERP's own alembic lineage as tenant-scoped storage; it is not "
    "the platform half of a dual-plane module and carries no `platform_tables` "
    "declaration anywhere in the tree. Relation-level control-plane "
    "declarations override this schema-level declaration."
)

HOST_TENANT_SCHEMAS: Final[tuple[str, ...]] = (
    "ap",
    "ar",
    "attendance",
    "audit",
    "automation",
    "banking",
    "common",
    "cons",
    "core_config",
    "core_fx",
    "core_org",
    "erpnext_staging",
    "exp",
    "expense",
    "fa",
    "fin_inst",
    "fleet",
    "forms",
    "gl",
    "hr",
    "inv",
    "ipsas",
    "lease",
    "leave",
    "migration",
    "payments",
    "payroll",
    "people",
    "perf",
    "platform",
    "pm",
    "proc",
    "public",
    "recruit",
    "rpt",
    "scheduling",
    "settings",
    "support",
    "sync",
    "tax",
    "training",
)

HOST_SCHEMA_PLANES: Final[tuple[SchemaPlane, ...]] = tuple(
    SchemaPlane(
        schema=schema,
        plane=PLANE_TENANT,
        declared_by="host assembly (ERP alembic lineage)",
        authority=HOST_TENANT_SCHEMA_AUTHORITY,
    )
    for schema in HOST_TENANT_SCHEMAS
)


# ---------------------------------------------------------------------------
# The module half -- DERIVED from the assembly's composed-module declaration
# ---------------------------------------------------------------------------


def module_relation_planes(
    modules: Sequence[ComposedModule] = COMPOSED_MODULES,
) -> tuple[RelationPlane, ...]:
    """Every composed module's declared relations, as plane declarations.

    Derived, never restated.  `tenant_tables` becomes a TENANT declaration and
    `platform_tables` becomes a CONTROL declaration, and nothing about the
    schema's NAME participates: two relations sharing one `mod_` schema come
    out on OPPOSITE planes when the module declares them so, which is the
    whole point -- the prefix they share decides nothing.
    """
    declarations: list[RelationPlane] = []
    for module in modules:
        for table in module.tenant_tables:
            declarations.append(
                RelationPlane(
                    relation=table,
                    plane=PLANE_TENANT,
                    schema=module.schema,
                    declared_by=f"module manifest: {module.module_code}.tables",
                    authority=(
                        "app.runtime_admission.COMPOSED_MODULES records this "
                        "as a manifest-derived tenant table for module "
                        f"{module.module_code!r}; "
                        "tests/architecture/test_runtime_admission_is_read_only"
                        ".py proves the list against "
                        "tests/integration/tenant_table_inventory.tsv."
                    ),
                )
            )
        for table in module.platform_tables:
            declarations.append(
                RelationPlane(
                    relation=table,
                    plane=PLANE_CONTROL,
                    schema=module.schema,
                    declared_by=(
                        f"module manifest: {module.module_code}.platform_tables"
                    ),
                    authority=(
                        "app.runtime_admission.COMPOSED_MODULES records this "
                        "as a manifest-derived platform table for module "
                        f"{module.module_code!r}; ADR-0023 requires a module's "
                        "platform tables to be REVOKEd from the tenant "
                        "application role."
                    ),
                    permitted_principals=("platform_api", "app_admin"),
                )
            )
    return tuple(declarations)


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


class PlaneResolver:
    """Answers "which plane?" from declarations, and refuses when none does."""

    def __init__(
        self,
        relation_planes: Iterable[RelationPlane],
        schema_planes: Iterable[SchemaPlane],
    ) -> None:
        by_relation: dict[str, RelationPlane] = {}
        for declaration in relation_planes:
            existing = by_relation.get(declaration.relation)
            if existing is not None and existing.plane != declaration.plane:
                raise AmbiguousPlaneDeclaration(
                    f"{declaration.relation!r} is declared "
                    f"{existing.plane!r} by {existing.declared_by} and "
                    f"{declaration.plane!r} by {declaration.declared_by}. A "
                    "relation whose plane depends on which declaration is read "
                    "first has no declared plane."
                )
            by_relation[declaration.relation] = declaration
        self._by_relation = by_relation
        self._by_schema = {
            declaration.schema: declaration for declaration in schema_planes
        }

    @property
    def relation_declarations(self) -> Mapping[str, RelationPlane]:
        return dict(self._by_relation)

    @property
    def schema_declarations(self) -> Mapping[str, SchemaPlane]:
        return dict(self._by_schema)

    def control_plane_relations(self) -> tuple[RelationPlane, ...]:
        return tuple(
            sorted(
                (
                    declaration
                    for declaration in self._by_relation.values()
                    if declaration.plane == PLANE_CONTROL
                ),
                key=lambda item: (item.schema, item.relation),
            )
        )

    def resolve(self, schema: str, relation: str) -> PlaneVerdict:
        """The plane of one qualified relation, or `UnclassifiedRelation`."""
        declaration = self._by_relation.get(relation)
        if declaration is not None:
            return PlaneVerdict(
                schema=schema,
                relation=relation,
                plane=declaration.plane,
                declared_by=declaration.declared_by,
                authority=declaration.authority,
                permitted_principals=declaration.permitted_principals,
                schema_moved=declaration.schema != schema,
                declared_schema=declaration.schema,
            )
        schema_declaration = self._by_schema.get(schema)
        if schema_declaration is not None:
            return PlaneVerdict(
                schema=schema,
                relation=relation,
                plane=schema_declaration.plane,
                declared_by=schema_declaration.declared_by,
                authority=schema_declaration.authority,
                declared_schema=schema,
            )
        raise UnclassifiedRelation(
            f"no plane declaration covers {schema}.{relation}. A relation with "
            "no declared plane is not 'probably tenant' -- generation refuses "
            "rather than defaulting, because defaulting is exactly how four "
            "control-plane relations in `public` were swept into a "
            "compatibility grant file. Declare it: a composed module's "
            "`tables`/`platform_tables` (read from "
            "app.runtime_admission.COMPOSED_MODULES), or "
            "HOST_CONTROL_PLANE_RELATIONS / HOST_TENANT_SCHEMAS in "
            "app/persistence_planes.py. Never from "
            + ", ".join(FORBIDDEN_PLANE_SIGNALS)
            + "."
        )


def default_resolver() -> PlaneResolver:
    """The resolver over the assembly's own declarations, as composed today."""
    return PlaneResolver(
        relation_planes=(
            *module_relation_planes(),
            *HOST_CONTROL_PLANE_RELATIONS,
        ),
        schema_planes=HOST_SCHEMA_PLANES,
    )


__all__ = [
    "FORBIDDEN_PLANE_SIGNALS",
    "HOST_CONTROL_PLANE_RELATIONS",
    "HOST_SCHEMA_PLANES",
    "HOST_TENANT_SCHEMAS",
    "HOST_TENANT_SCHEMA_AUTHORITY",
    "PLANES",
    "PLANE_CONTROL",
    "PLANE_TENANT",
    "AmbiguousPlaneDeclaration",
    "PlaneResolver",
    "PlaneVerdict",
    "RelationPlane",
    "SchemaPlane",
    "UnclassifiedRelation",
    "default_resolver",
    "module_relation_planes",
]
