"""Add the ERP-local managed application lifecycle receipt.

Revision ID: 20260817_app_lifecycle
Revises: 20260815_tenant_catalog_discovery
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260817_app_lifecycle"
down_revision = "20260815_tenant_catalog_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "application_lifecycle_operations"
IMMUTABLE_COLUMNS = (
    "operation_id",
    "organization_id",
    "idempotency_key",
    "person_id",
    "desired_state",
    "provider_binding",
    "issuer",
    "subject",
    "target",
    "target_digest",
    "expected_state",
    "expected_state_digest",
    "plan_digest",
    "current_state",
    "actions",
    "created_at",
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_people_organization_id_id",
        "people",
        ["organization_id", "id"],
        schema="public",
    )
    op.add_column(
        "federated_identities",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "federated_identities",
        sa.Column(
            "provider_binding",
            sa.String(length=80),
            server_default="primary",
            nullable=False,
        ),
        schema="public",
    )
    op.add_column(
        "federated_identities",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.execute(
        "UPDATE public.federated_identities AS binding "
        "SET organization_id = person.organization_id "
        "FROM public.people AS person "
        "WHERE person.id = binding.person_id"
    )
    op.alter_column(
        "federated_identities",
        "organization_id",
        nullable=False,
        schema="public",
    )
    op.alter_column(
        "federated_identities",
        "provider_binding",
        server_default=None,
        schema="public",
    )
    op.drop_constraint(
        "uq_federated_identities_issuer_subject",
        "federated_identities",
        schema="public",
        type_="unique",
    )
    op.drop_constraint(
        "uq_federated_identities_person_issuer",
        "federated_identities",
        schema="public",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_federated_identity_org_provider_subject",
        "federated_identities",
        ["organization_id", "provider_binding", "issuer", "subject"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_federated_identity_org_person_provider_issuer",
        "federated_identities",
        ["organization_id", "person_id", "provider_binding", "issuer"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_federated_identity_person_id",
        "federated_identities",
        ["person_id", "id"],
        schema="public",
    )
    op.create_foreign_key(
        "fk_federated_identity_organization",
        "federated_identities",
        "organization",
        ["organization_id"],
        ["organization_id"],
        source_schema="public",
        referent_schema="core_org",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_federated_identity_person_org",
        "federated_identities",
        "people",
        ["organization_id", "person_id"],
        ["organization_id", "id"],
        source_schema="public",
        referent_schema="public",
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_federated_identities_organization_id",
        "federated_identities",
        ["organization_id"],
        schema="public",
    )
    op.execute("ALTER TABLE public.federated_identities ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.federated_identities FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY federated_identities_tenant_isolation "
        "ON public.federated_identities "
        "USING (organization_id = get_current_organization_id()) "
        "WITH CHECK (organization_id = get_current_organization_id())"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON TABLE public.federated_identities TO app_user"
    )

    op.add_column(
        "sessions",
        sa.Column(
            "external_identity_binding_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        schema="public",
    )
    op.create_foreign_key(
        "fk_sessions_external_identity_person",
        "sessions",
        "federated_identities",
        ["person_id", "external_identity_binding_id"],
        ["person_id", "id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_sessions_external_identity_binding",
        "sessions",
        ["external_identity_binding_id"],
        schema="public",
    )

    op.create_table(
        "oidc_login_states",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=255), nullable=False),
        sa.Column("nonce", sa.String(length=255), nullable=False),
        sa.Column("redirect_uri", sa.String(length=512), nullable=False),
        sa.Column("return_to", sa.String(length=512), nullable=False),
        sa.Column("issued_at", sa.Integer(), nullable=False),
        sa.Column("provider_binding", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_oidc_login_state_organization",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "state_hash",
            name="uq_oidc_login_state_org_hash",
        ),
        schema="public",
    )
    op.create_index(
        "ix_oidc_login_state_expiry",
        "oidc_login_states",
        ["organization_id", "expires_at"],
        schema="public",
    )
    op.execute("ALTER TABLE public.oidc_login_states ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.oidc_login_states FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY oidc_login_states_tenant_isolation "
        "ON public.oidc_login_states "
        "USING (organization_id = get_current_organization_id()) "
        "WITH CHECK (organization_id = get_current_organization_id())"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON TABLE public.oidc_login_states TO app_user"
    )
    op.create_table(
        TABLE,
        sa.Column(
            "operation_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("desired_state", sa.String(length=16), nullable=False),
        sa.Column("provider_binding", sa.String(length=80), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("target", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target_digest", sa.String(length=71), nullable=False),
        sa.Column(
            "expected_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("expected_state_digest", sa.String(length=71), nullable=False),
        sa.Column("plan_digest", sa.String(length=71), nullable=False),
        sa.Column("current_state", sa.String(length=16), nullable=False),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "operation_state",
            sa.String(length=16),
            server_default="planned",
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column(
            "changed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "result_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("result_state_digest", sa.String(length=71), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "desired_state IN ('active', 'inactive')",
            name="ck_app_lifecycle_desired_state",
        ),
        sa.CheckConstraint(
            "current_state IN ('active', 'inactive')",
            name="ck_app_lifecycle_current_state",
        ),
        sa.CheckConstraint(
            "operation_state IN ('planned', 'applied', 'cancelled')",
            name="ck_app_lifecycle_operation_state",
        ),
        sa.CheckConstraint(
            "outcome IN ('blocked', 'refused', 'succeeded')",
            name="ck_app_lifecycle_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_app_lifecycle_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "person_id"],
            ["public.people.organization_id", "public.people.id"],
            name="fk_app_lifecycle_person_org",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("operation_id"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_app_lifecycle_org_idempotency_key",
        ),
        schema="public",
    )
    op.create_index(
        "ix_app_lifecycle_org_person_created",
        TABLE,
        ["organization_id", "person_id", "created_at"],
        schema="public",
    )
    op.execute(f"ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {TABLE}_tenant_isolation ON public.{TABLE}
        USING (organization_id = get_current_organization_id())
        WITH CHECK (organization_id = get_current_organization_id())
        """
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{TABLE} TO app_user"
    )

    comparisons = " OR ".join(
        f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in IMMUTABLE_COLUMNS
    )
    op.execute(
        f"""
        CREATE FUNCTION public.reject_application_lifecycle_plan_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            IF {comparisons} THEN
                RAISE EXCEPTION 'application lifecycle plan fields are immutable';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER reject_application_lifecycle_plan_mutation
        BEFORE UPDATE ON public.{TABLE}
        FOR EACH ROW EXECUTE FUNCTION public.reject_application_lifecycle_plan_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS reject_application_lifecycle_plan_mutation "
        f"ON public.{TABLE}"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.reject_application_lifecycle_plan_mutation()"
    )
    op.drop_table(TABLE, schema="public")
    op.drop_table("oidc_login_states", schema="public")
    op.drop_index(
        "ix_sessions_external_identity_binding",
        table_name="sessions",
        schema="public",
    )
    op.drop_constraint(
        "fk_sessions_external_identity_person",
        "sessions",
        schema="public",
        type_="foreignkey",
    )
    op.drop_column("sessions", "external_identity_binding_id", schema="public")
    op.execute(
        "DROP POLICY federated_identities_tenant_isolation ON public.federated_identities"
    )
    op.execute("ALTER TABLE public.federated_identities NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.federated_identities DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_federated_identities_organization_id",
        table_name="federated_identities",
        schema="public",
    )
    op.drop_constraint(
        "fk_federated_identity_person_org",
        "federated_identities",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_federated_identity_organization",
        "federated_identities",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_federated_identity_person_id",
        "federated_identities",
        schema="public",
        type_="unique",
    )
    op.drop_constraint(
        "uq_federated_identity_org_person_provider_issuer",
        "federated_identities",
        schema="public",
        type_="unique",
    )
    op.drop_constraint(
        "uq_federated_identity_org_provider_subject",
        "federated_identities",
        schema="public",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_federated_identities_person_issuer",
        "federated_identities",
        ["person_id", "issuer"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_federated_identities_issuer_subject",
        "federated_identities",
        ["issuer", "subject"],
        schema="public",
    )
    op.drop_column("federated_identities", "disabled_at", schema="public")
    op.drop_column("federated_identities", "provider_binding", schema="public")
    op.drop_column("federated_identities", "organization_id", schema="public")
    op.drop_constraint(
        "uq_people_organization_id_id",
        "people",
        schema="public",
        type_="unique",
    )
