"""Host ``party_person_catalog.v1`` as an ERP People projection.

ERP keeps ``public.people`` as the person authority and its existing
engine/session as the transaction authority. ``public.parties`` and
``public.party_persons`` are assembly-owned projections matching the pinned
kernel identity contract, so a composed module that references a person has a
truthful foreign-key target and a real tenant predicate.

This is the same shape as ``20260813_tenant_projection``, for the same reason:
kernel ``0001`` can never run here, so every effect a module declares has to be
supplied from ERP's own lineage and bound in ``app/migration_bindings.py``.

## What this does and does not claim

It supplies identity REFERENCES and the person profile. It supplies no
employment state, no authentication and no RBAC — those stay where they are
until their own modules cut over. When ``dotmac-party`` and ``dotmac-people``
are composed, the authority moves and this projection's direction reverses;
until then ``public.people`` decides and these two tables follow.

An existing catalogue is adopted only after its schema and rows are verified.
Unknown drift is refused rather than overwritten, exactly as the tenant
projection refuses an Organization row it cannot represent. Application runtime
code is not imported from this migration; the copied constants are guarded by
``tests/migrations/test_party_person_projection_migration.py``.

The closing ``require_prerequisites`` call is deliberate. Two tables with
familiar names can still carry the wrong key, an unforced policy or a missing
grant. The exact pinned kernel contract refuses that drift before Alembic
records this provider revision.

Revision ID: 20260824_party_person_projection
Revises: 20260822_bank_fee_source_identity
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from dotmac_kernel.migrations.verify import require_prerequisites

revision = "20260824_party_person_projection"
down_revision = "20260822_bank_fee_source_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUIRES = ("party_person_catalog.v1",)

# Copied from the runtime projection owner; migrations never import mutable
# runtime code. tests/migrations/test_party_person_projection_migration.py
# pins them.
PERSON_PARTY_TYPE = "person"
ORGANIZATION_PARTY_TYPE = "organization"
MAX_DISPLAY_NAME_LENGTH = 200
MAX_PERSON_NAME_LENGTH = 100

PROJECTED_TABLES = ("public.parties", "public.party_persons")

_PARTY_COLUMNS = {"id", "tenant_id", "party_type", "display_name", "is_active"}
_PERSON_COLUMNS = {"party_id", "first_name", "last_name"}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name, schema="public")


def _column_names(table: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table, schema="public")
    }


def _unique_column_sets(table: str) -> set[tuple[str, ...]]:
    inspector = sa.inspect(op.get_bind())
    return {
        tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints(table, schema="public")
    }


def _assert_catalog_shape() -> None:
    """Refuse a pre-existing catalogue that is not the one we would create."""
    inspector = sa.inspect(op.get_bind())
    if not _column_names("parties") >= _PARTY_COLUMNS:
        raise RuntimeError(
            "public.parties is missing kernel Party columns "
            f"{sorted(_PARTY_COLUMNS - _column_names('parties'))!r}"
        )
    if not _column_names("party_persons") >= _PERSON_COLUMNS:
        raise RuntimeError(
            "public.party_persons is missing kernel PartyPerson columns "
            f"{sorted(_PERSON_COLUMNS - _column_names('party_persons'))!r}"
        )
    parties_pk = tuple(
        inspector.get_pk_constraint("parties", schema="public").get(
            "constrained_columns"
        )
        or ()
    )
    persons_pk = tuple(
        inspector.get_pk_constraint("party_persons", schema="public").get(
            "constrained_columns"
        )
        or ()
    )
    if parties_pk != ("id",):
        raise RuntimeError("public.parties must have primary key (id)")
    if persons_pk != ("party_id",):
        raise RuntimeError("public.party_persons must have primary key (party_id)")
    if ("tenant_id", "id") not in _unique_column_sets("parties"):
        raise RuntimeError("public.parties must carry unique (tenant_id, id)")


def _create_catalog() -> None:
    if _has_table("party_persons") and not _has_table("parties"):
        raise RuntimeError("party_persons exists without its parties owner")
    if not _has_table("parties"):
        op.create_table(
            "parties",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("party_type", sa.String(length=20), nullable=False),
            sa.Column(
                "display_name",
                sa.String(length=MAX_DISPLAY_NAME_LENGTH),
                nullable=False,
            ),
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["public.tenants.id"],
                ondelete="CASCADE",
                name="fk_parties_tenant",
            ),
            sa.UniqueConstraint("tenant_id", "id", name="uq_parties_tenant_id"),
            sa.CheckConstraint(
                f"party_type IN ('{PERSON_PARTY_TYPE}', '{ORGANIZATION_PARTY_TYPE}')",
                name="ck_parties_party_type",
            ),
            schema="public",
        )
        op.create_index(
            "ix_parties_tenant_id", "parties", ["tenant_id"], schema="public"
        )
    if not _has_table("party_persons"):
        op.create_table(
            "party_persons",
            sa.Column("party_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "first_name",
                sa.String(length=MAX_PERSON_NAME_LENGTH),
                nullable=False,
            ),
            sa.Column(
                "last_name",
                sa.String(length=MAX_PERSON_NAME_LENGTH),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["party_id"],
                ["public.parties.id"],
                ondelete="CASCADE",
                name="fk_party_persons_party",
            ),
            schema="public",
        )


def _protect_catalog() -> None:
    """ENABLE + FORCE row-level security, and grant the tenant role.

    ``party_persons`` carries no tenant column of its own, deliberately: a
    second copy of the tenant would be a second answer to which tenant a person
    belongs to. Its policy reaches the tenant through the FK instead, which is
    the same EXISTS-joined shape the kernel uses for its own subtype tables.
    """
    op.execute("ALTER TABLE public.parties ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.parties FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY parties_tenant_isolation
            ON public.parties
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id())
        """
    )
    op.execute("ALTER TABLE public.party_persons ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.party_persons FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY party_persons_tenant_isolation
            ON public.party_persons
            USING (
                EXISTS (
                    SELECT 1
                      FROM public.parties AS party
                     WHERE party.id = public.party_persons.party_id
                       AND party.tenant_id = public.app_current_tenant_id()
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1
                      FROM public.parties AS party
                     WHERE party.id = public.party_persons.party_id
                       AND party.tenant_id = public.app_current_tenant_id()
                )
            )
        """
    )
    # The projection is written by ERP request traffic, which runs as
    # app_user, so SELECT alone would make the reconciler unable to keep the
    # catalogue current. Isolation here is the FORCEd policy above, not a
    # withheld grant.
    for table in PROJECTED_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO app_user")


def _assert_projection_is_representable() -> None:
    bind = op.get_bind()
    unnameable = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                  FROM public.people
                 WHERE btrim(COALESCE(display_name, '')) = ''
                   AND btrim(COALESCE(first_name, '') || ' '
                             || COALESCE(last_name, '')) = ''
                """
            )
        )
        or 0
    )
    if unnameable:
        raise RuntimeError(
            f"{unnameable} person row(s) have no name that can fill "
            "Party.display_name; repair them before adopting the projection"
        )

    oversized = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                  FROM public.people
                 WHERE char_length(btrim(COALESCE(first_name, ''))) > :name_length
                    OR char_length(btrim(COALESCE(last_name, ''))) > :name_length
                    OR char_length(
                           COALESCE(
                               NULLIF(btrim(COALESCE(display_name, '')), ''),
                               btrim(COALESCE(first_name, '') || ' '
                                     || COALESCE(last_name, ''))
                           )
                       ) > :display_length
                """
            ),
            {
                "name_length": MAX_PERSON_NAME_LENGTH,
                "display_length": MAX_DISPLAY_NAME_LENGTH,
            },
        )
        or 0
    )
    if oversized:
        raise RuntimeError(
            f"{oversized} person row(s) exceed the kernel Party name limits; "
            "repair them before adopting the projection"
        )

    tenantless = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                  FROM public.people AS person
             LEFT JOIN public.tenants AS tenant
                    ON tenant.id = person.organization_id
                 WHERE tenant.id IS NULL
                """
            )
        )
        or 0
    )
    if tenantless:
        raise RuntimeError(
            f"{tenantless} person row(s) belong to an organization with no tenant "
            "projection; run the tenant projection first"
        )


def _assert_existing_projection_is_truthful() -> None:
    """Refuse to overwrite a person party that disagrees with its source."""
    bind = op.get_bind()
    drift = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                  FROM public.parties AS party
                  JOIN public.people AS person
                    ON person.id = party.id
                 WHERE party.tenant_id <> person.organization_id
                    OR party.party_type <> :person_type
                    OR party.display_name <> COALESCE(
                           NULLIF(btrim(COALESCE(person.display_name, '')), ''),
                           btrim(COALESCE(person.first_name, '') || ' '
                                 || COALESCE(person.last_name, ''))
                       )
                    OR party.is_active IS DISTINCT FROM person.is_active
                """
            ),
            {"person_type": PERSON_PARTY_TYPE},
        )
        or 0
    )
    if drift:
        raise RuntimeError(
            f"{drift} existing party row(s) disagree with their person; "
            "refusing to overwrite unknown projection drift"
        )

    orphans = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                  FROM public.parties AS party
             LEFT JOIN public.people AS person
                    ON person.id = party.id
                 WHERE party.party_type = :person_type
                   AND person.id IS NULL
                """
            ),
            {"person_type": PERSON_PARTY_TYPE},
        )
        or 0
    )
    if orphans:
        raise RuntimeError(
            f"{orphans} person party row(s) have no authoritative person; "
            "resolve them before adopting the projection"
        )


def _insert_missing_projection_rows() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO public.parties
                (id, tenant_id, party_type, display_name, is_active)
            SELECT person.id,
                   person.organization_id,
                   :person_type,
                   COALESCE(
                       NULLIF(btrim(COALESCE(person.display_name, '')), ''),
                       btrim(COALESCE(person.first_name, '') || ' '
                             || COALESCE(person.last_name, ''))
                   ),
                   person.is_active
              FROM public.people AS person
             WHERE NOT EXISTS (
                       SELECT 1
                         FROM public.parties AS party
                        WHERE party.id = person.id
                   )
            """
        ),
        {"person_type": PERSON_PARTY_TYPE},
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO public.party_persons (party_id, first_name, last_name)
            SELECT person.id,
                   btrim(COALESCE(person.first_name, '')),
                   btrim(COALESCE(person.last_name, ''))
              FROM public.people AS person
             WHERE NOT EXISTS (
                       SELECT 1
                         FROM public.party_persons AS profile
                        WHERE profile.party_id = person.id
                   )
            """
        )
    )


def upgrade() -> None:
    created = not _has_table("parties")
    _create_catalog()
    _assert_catalog_shape()
    if created:
        _protect_catalog()
    _assert_projection_is_representable()
    _assert_existing_projection_is_truthful()
    _insert_missing_projection_rows()
    require_prerequisites(op.get_bind(), REQUIRES)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS party_persons_tenant_isolation ON public.party_persons"
    )
    op.execute("DROP POLICY IF EXISTS parties_tenant_isolation ON public.parties")
    op.drop_table("party_persons", schema="public")
    op.drop_index("ix_parties_tenant_id", table_name="parties", schema="public")
    op.drop_table("parties", schema="public")
