"""Host the kernel tenant catalogue as an ERP Organization projection.

ERP keeps ``core_org.organization`` as the tenancy authority and its existing
engine/session as the transaction authority. These two platform catalogue
tables match the pinned kernel model so shared stateful modules have a truthful
foreign-key target. No kernel identity, RBAC, audit or session table is
created by this slice, and the independent kernel lineage is not stamped.

An existing catalogue is adopted only after its schema and rows are verified.
Unknown drift is refused rather than overwritten. Application runtime code is
not imported from this migration; the copied constants are guarded by tests.

Revision ID: 20260813_tenant_projection
Revises: 20260812_merge_expand_withdrawal
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "20260813_tenant_projection"
down_revision = "20260812_merge_expand_withdrawal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copied from the runtime projection owner; migrations never import mutable
# runtime code. tests/migrations/test_tenant_projection_migration.py pins them.
TENANT_SLUG_PREFIX = "erp-"
MAX_TENANT_NAME_LENGTH = 120

_TENANT_COLUMNS = {
    "id",
    "slug",
    "name",
    "is_active",
    "suspended_at",
    "deleted_at",
    "created_at",
    "updated_at",
}
_DOMAIN_COLUMNS = {
    "id",
    "tenant_id",
    "domain",
    "verified_at",
    "created_at",
    "updated_at",
}

_COLUMN_CONTRACTS: dict[
    str,
    dict[
        str,
        tuple[type[sa.types.TypeEngine[object]], bool, int | None, bool | None, bool],
    ],
] = {
    "tenants": {
        "id": (sa.Uuid, False, None, None, False),
        "slug": (sa.String, False, 63, None, False),
        "name": (sa.String, False, 120, None, False),
        "is_active": (sa.Boolean, False, None, None, True),
        "suspended_at": (sa.DateTime, True, None, True, False),
        "deleted_at": (sa.DateTime, True, None, True, False),
        "created_at": (sa.DateTime, False, None, True, True),
        "updated_at": (sa.DateTime, False, None, True, True),
    },
    "tenant_domains": {
        "id": (sa.Uuid, False, None, None, False),
        "tenant_id": (sa.Uuid, False, None, None, False),
        "domain": (sa.String, False, 253, None, False),
        "verified_at": (sa.DateTime, True, None, True, False),
        "created_at": (sa.DateTime, False, None, True, True),
        "updated_at": (sa.DateTime, False, None, True, True),
    },
}


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


def _index_column_sets(table: str) -> set[tuple[str, ...]]:
    return {
        tuple(index.get("column_names") or ())
        for index in sa.inspect(op.get_bind()).get_indexes(table, schema="public")
    }


def _assert_column_contracts(table: str) -> None:
    columns = {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns(table, schema="public")
    }
    for name, contract in _COLUMN_CONTRACTS[table].items():
        expected_type, nullable, length, timezone, needs_default = contract
        column = columns[name]
        actual_type = column["type"]
        if not isinstance(actual_type, expected_type):
            raise RuntimeError(
                f"public.{table}.{name} has type {actual_type!s}; "
                f"expected {expected_type.__name__}"
            )
        if bool(column["nullable"]) is not nullable:
            raise RuntimeError(
                f"public.{table}.{name} nullable={column['nullable']!r}; "
                f"expected {nullable!r}"
            )
        if length is not None and getattr(actual_type, "length", None) != length:
            raise RuntimeError(
                f"public.{table}.{name} has length "
                f"{getattr(actual_type, 'length', None)!r}; expected {length}"
            )
        if (
            timezone is not None
            and bool(getattr(actual_type, "timezone", False)) is not timezone
        ):
            raise RuntimeError(
                f"public.{table}.{name} timezone={getattr(actual_type, 'timezone', None)!r}; "
                f"expected {timezone!r}"
            )
        if needs_default and column.get("default") is None:
            raise RuntimeError(f"public.{table}.{name} must have a server default")


def _assert_catalog_shape() -> None:
    inspector = sa.inspect(op.get_bind())
    if _column_names("tenants") != _TENANT_COLUMNS:
        raise RuntimeError("public.tenants does not match the kernel Tenant columns")
    if _column_names("tenant_domains") != _DOMAIN_COLUMNS:
        raise RuntimeError(
            "public.tenant_domains does not match the kernel TenantDomain columns"
        )
    _assert_column_contracts("tenants")
    _assert_column_contracts("tenant_domains")
    tenant_pk = tuple(
        inspector.get_pk_constraint("tenants", schema="public").get(
            "constrained_columns"
        )
        or ()
    )
    domain_pk = tuple(
        inspector.get_pk_constraint("tenant_domains", schema="public").get(
            "constrained_columns"
        )
        or ()
    )
    if tenant_pk != ("id",):
        raise RuntimeError("public.tenants must have primary key (id)")
    if domain_pk != ("id",):
        raise RuntimeError("public.tenant_domains must have primary key (id)")
    if ("slug",) not in _unique_column_sets("tenants"):
        raise RuntimeError("public.tenants.slug must be unique")
    if ("domain",) not in _unique_column_sets("tenant_domains"):
        raise RuntimeError("public.tenant_domains.domain must be unique")
    if ("slug",) not in _index_column_sets("tenants"):
        raise RuntimeError("public.tenants.slug must be indexed")
    if ("tenant_id",) not in _index_column_sets("tenant_domains"):
        raise RuntimeError("public.tenant_domains.tenant_id must be indexed")

    foreign_keys = inspector.get_foreign_keys("tenant_domains", schema="public")
    matching = [
        foreign_key
        for foreign_key in foreign_keys
        if foreign_key.get("constrained_columns") == ["tenant_id"]
        and foreign_key.get("referred_schema") in (None, "public")
        and foreign_key.get("referred_table") == "tenants"
        and foreign_key.get("referred_columns") == ["id"]
        and str((foreign_key.get("options") or {}).get("ondelete", "")).upper()
        == "CASCADE"
    ]
    if len(matching) != 1:
        raise RuntimeError(
            "public.tenant_domains.tenant_id must reference tenants.id ON DELETE CASCADE"
        )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        rls_rows = bind.execute(
            sa.text(
                """
                SELECT relname, relrowsecurity, relforcerowsecurity
                  FROM pg_class
                 WHERE oid IN ('public.tenants'::regclass,
                               'public.tenant_domains'::regclass)
                """
            )
        ).all()
        if len(rls_rows) != 2 or any(row[1] or row[2] for row in rls_rows):
            raise RuntimeError("tenant catalog tables must not carry RLS")


def _create_catalog() -> None:
    if _has_table("tenant_domains") and not _has_table("tenants"):
        raise RuntimeError("tenant_domains exists without its tenants owner")
    if not _has_table("tenants"):
        op.create_table(
            "tenants",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("slug", sa.String(length=63), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.Column("suspended_at", sa.DateTime(timezone=True)),
            sa.Column("deleted_at", sa.DateTime(timezone=True)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("slug", name="uq_tenants_slug"),
            schema="public",
        )
        op.create_index("ix_tenants_slug", "tenants", ["slug"], schema="public")
    if not _has_table("tenant_domains"):
        op.create_table(
            "tenant_domains",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid(), nullable=False),
            sa.Column("domain", sa.String(length=253), nullable=False),
            sa.Column("verified_at", sa.DateTime(timezone=True)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["public.tenants.id"],
                ondelete="CASCADE",
                name="fk_tenant_domains_tenant",
            ),
            sa.UniqueConstraint("domain", name="uq_tenant_domains_domain"),
            schema="public",
        )
        op.create_index(
            "ix_tenant_domains_tenant_id",
            "tenant_domains",
            ["tenant_id"],
            schema="public",
        )


def _assert_existing_projection_is_truthful() -> None:
    bind = op.get_bind()
    invalid_names = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                  FROM core_org.organization
                 WHERE legal_name IS NULL
                    OR btrim(legal_name) = ''
                    OR char_length(btrim(legal_name)) > :max_name_length
                """
            ),
            {"max_name_length": MAX_TENANT_NAME_LENGTH},
        )
        or 0
    )
    if invalid_names:
        raise RuntimeError(
            f"{invalid_names} Organization row(s) cannot fit Tenant.name; "
            "repair legal_name before adopting the tenant projection"
        )

    drift = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                  FROM public.tenants AS tenant
                  JOIN core_org.organization AS organization
                    ON organization.organization_id = tenant.id
                 WHERE tenant.slug <> :slug_prefix || organization.organization_id
                    OR tenant.name <> btrim(organization.legal_name)
                    OR tenant.is_active IS DISTINCT FROM organization.is_active
                    OR tenant.deleted_at IS NOT NULL
                """
            ),
            {"slug_prefix": TENANT_SLUG_PREFIX},
        )
        or 0
    )
    if drift:
        raise RuntimeError(
            f"{drift} existing tenant row(s) disagree with their Organization; "
            "refusing to overwrite unknown projection drift"
        )

    active_orphans = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                  FROM public.tenants AS tenant
             LEFT JOIN core_org.organization AS organization
                    ON organization.organization_id = tenant.id
                 WHERE organization.organization_id IS NULL
                   AND tenant.deleted_at IS NULL
                """
            )
        )
        or 0
    )
    if active_orphans:
        raise RuntimeError(
            f"{active_orphans} active tenant row(s) have no authoritative Organization"
        )

    slug_collisions = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                  FROM core_org.organization AS organization
                  JOIN public.tenants AS tenant
                    ON tenant.slug = :slug_prefix || organization.organization_id
                   AND tenant.id <> organization.organization_id
                """
            ),
            {"slug_prefix": TENANT_SLUG_PREFIX},
        )
        or 0
    )
    if slug_collisions:
        raise RuntimeError("an existing tenant holds an Organization projection slug")


def _insert_missing_projection_rows() -> None:
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO public.tenants
                (id, slug, name, is_active, suspended_at, deleted_at,
                 created_at, updated_at)
            SELECT organization.organization_id,
                   :slug_prefix || organization.organization_id,
                   btrim(organization.legal_name),
                   organization.is_active,
                   NULL,
                   NULL,
                   COALESCE(organization.created_at, CURRENT_TIMESTAMP),
                   COALESCE(organization.updated_at,
                            organization.created_at,
                            CURRENT_TIMESTAMP)
              FROM core_org.organization AS organization
             WHERE NOT EXISTS (
                       SELECT 1
                         FROM public.tenants AS tenant
                        WHERE tenant.id = organization.organization_id
                   )
            """
        ),
        {"slug_prefix": TENANT_SLUG_PREFIX},
    )


def _create_or_adopt_tenant_function() -> None:
    bind = op.get_bind()
    definition = bind.scalar(
        sa.text(
            """
            SELECT pg_get_functiondef(to_regprocedure('public.app_current_tenant_id()'))
            """
        )
    )
    if definition is not None:
        normalized = " ".join(str(definition).lower().split())
        required = (
            "returns uuid",
            "stable",
            "current_setting('app.current_tenant', true)",
            "invalid_text_representation",
        )
        if not all(marker in normalized for marker in required):
            raise RuntimeError(
                "public.app_current_tenant_id() exists with incompatible semantics"
            )
        return

    op.execute(
        """
        CREATE FUNCTION public.app_current_tenant_id()
        RETURNS uuid
        LANGUAGE plpgsql
        STABLE
        AS $$
        BEGIN
            RETURN NULLIF(current_setting('app.current_tenant', true), '')::uuid;
        EXCEPTION
            WHEN invalid_text_representation THEN
                RETURN NULL;
        END;
        $$;
        """
    )


def _lock_down_catalog() -> None:
    op.execute("REVOKE ALL ON public.tenants FROM PUBLIC;")
    op.execute("REVOKE ALL ON public.tenant_domains FROM PUBLIC;")
    op.execute("REVOKE ALL ON FUNCTION public.app_current_tenant_id() FROM PUBLIC;")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                REVOKE ALL ON public.tenants FROM app_user;
                REVOKE ALL ON public.tenant_domains FROM app_user;
                GRANT EXECUTE ON FUNCTION public.app_current_tenant_id() TO app_user;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_api') THEN
                GRANT EXECUTE ON FUNCTION public.app_current_tenant_id() TO platform_api;
            END IF;
        END$$;
        """
    )


def upgrade() -> None:
    _create_catalog()
    _assert_catalog_shape()
    _assert_existing_projection_is_truthful()
    _insert_missing_projection_rows()
    _create_or_adopt_tenant_function()
    _lock_down_catalog()


def downgrade() -> None:
    raise RuntimeError(
        "20260813_tenant_projection is forward-fix only: dropping or replacing "
        "tenant identity would invalidate module foreign keys and cannot safely "
        "distinguish a newly created catalogue from a verified adopted one"
    )
