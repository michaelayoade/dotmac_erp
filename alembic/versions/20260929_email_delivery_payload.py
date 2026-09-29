"""Keep rendered outbound email content behind ERP tenant RLS.

Revision ID: 20260929_email_delivery_payload
Revises: 20260928_seed_fleet_tracking_rbac
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260929_email_delivery_payload"
down_revision = "20260928_seed_fleet_tracking_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_outbox",
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        schema="platform",
    )
    op.add_column(
        "event_outbox",
        sa.Column("email_payload_purged_at", sa.DateTime(timezone=True), nullable=True),
        schema="platform",
    )
    op.execute(
        "UPDATE platform.event_outbox SET terminal_at = published_at "
        "WHERE status = 'PUBLISHED' AND published_at IS NOT NULL"
    )
    op.create_index(
        "idx_outbox_email_terminal",
        "event_outbox",
        ["event_name", "status", "terminal_at", "email_payload_purged_at"],
        schema="platform",
    )
    # PostgreSQL-only JSONB expression index; SQLite metadata tests cannot
    # represent this as a portable ORM Index.
    op.execute(
        "CREATE UNIQUE INDEX idx_outbox_email_delivery_ref ON "
        "platform.event_outbox ((payload ->> 'delivery_id')) "
        "WHERE event_name = 'email.delivery.requested'"
    )
    # Match the checked-in ERP identity-cutover contract for this existing
    # platform relation. Fresh databases have not run that cutover grant file.
    op.execute("GRANT USAGE ON SCHEMA platform TO app_user")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON TABLE platform.event_outbox TO app_user"
    )
    op.create_table(
        "email_delivery",
        sa.Column(
            "delivery_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("to_email", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(998), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("module", sa.String(40), nullable=True),
        sa.Column(
            "attachments",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_email_delivery_organization",
        ),
        schema="public",
    )
    op.execute("ALTER TABLE public.email_delivery ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.email_delivery FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY email_delivery_tenant_isolation
            ON public.email_delivery
            USING (
                organization_id = NULLIF(
                    current_setting('app.current_organization_id', true), ''
                )::uuid
            )
            WITH CHECK (
                organization_id = NULLIF(
                    current_setting('app.current_organization_id', true), ''
                )::uuid
            )
        """
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.email_delivery FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.email_delivery FROM app_user")
    op.execute(
        "GRANT SELECT, INSERT, DELETE ON TABLE public.email_delivery TO app_user"
    )
    op.execute(
        """
        CREATE POLICY email_delivery_mature_terminal_delete
            ON public.email_delivery AS RESTRICTIVE FOR DELETE TO app_user
            USING (
                EXISTS (
                    SELECT 1 FROM platform.event_outbox AS event
                    WHERE event.event_name = 'email.delivery.requested'
                      AND event.payload ->> 'delivery_id' = delivery_id::text
                      AND event.headers ->> 'organization_id' = organization_id::text
                      AND event.status IN ('PUBLISHED', 'DEAD')
                      AND event.terminal_at < now() - interval '30 days'
                )
            )
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform.guard_email_outbox_retention()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            scope_id text;
            private_id uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.event_name <> 'email.delivery.requested' THEN
                    RETURN OLD;
                END IF;
                scope_id := NULLIF(current_setting('app.current_organization_id', true), '');
                IF scope_id IS NULL OR scope_id IS DISTINCT FROM OLD.headers ->> 'organization_id' THEN
                    RAISE EXCEPTION 'email outbox deletion requires its tenant scope';
                END IF;
                IF OLD.status <> 'PUBLISHED'
                   OR OLD.terminal_at IS NULL
                   OR OLD.terminal_at >= clock_timestamp() - interval '30 days' THEN
                    RAISE EXCEPTION 'email outbox deletion requires mature published status';
                END IF;
                private_id := (OLD.payload ->> 'delivery_id')::uuid;
                IF EXISTS (
                    SELECT 1 FROM public.email_delivery
                    WHERE delivery_id = private_id
                      AND organization_id::text = scope_id
                ) THEN
                    RAISE EXCEPTION 'email content must be purged before its outbox event';
                END IF;
                RETURN OLD;
            END IF;

            IF TG_OP = 'INSERT' THEN
                IF NEW.event_name = 'email.delivery.requested' THEN
                    IF NEW.email_payload_purged_at IS NOT NULL THEN
                        RAISE EXCEPTION 'email outbox insert cannot claim content purge';
                    END IF;
                    IF NEW.status IN ('PUBLISHED', 'DEAD') THEN
                        NEW.terminal_at := clock_timestamp();
                    ELSE
                        NEW.terminal_at := NULL;
                    END IF;
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.event_name <> 'email.delivery.requested'
               AND NEW.event_name <> 'email.delivery.requested' THEN
                RETURN NEW;
            END IF;
            IF OLD.event_name IS DISTINCT FROM NEW.event_name
               OR OLD.payload IS DISTINCT FROM NEW.payload
               OR OLD.headers IS DISTINCT FROM NEW.headers THEN
                RAISE EXCEPTION 'email outbox identity and reference are immutable';
            END IF;
            IF OLD.email_payload_purged_at IS NOT NULL
               AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'purged email delivery cannot change terminal status';
            END IF;

            IF NEW.status IN ('PUBLISHED', 'DEAD') THEN
                IF OLD.status IS DISTINCT FROM NEW.status OR OLD.terminal_at IS NULL THEN
                    NEW.terminal_at := clock_timestamp();
                ELSE
                    NEW.terminal_at := OLD.terminal_at;
                END IF;
            ELSE
                NEW.terminal_at := NULL;
            END IF;

            IF NEW.email_payload_purged_at IS DISTINCT FROM OLD.email_payload_purged_at THEN
                IF OLD.email_payload_purged_at IS NOT NULL THEN
                    RAISE EXCEPTION 'email payload purge marker is immutable';
                END IF;
                IF NEW.status <> 'DEAD'
                   OR OLD.terminal_at IS NULL
                   OR OLD.terminal_at >= clock_timestamp() - interval '30 days' THEN
                    RAISE EXCEPTION 'email payload cannot be marked purged before retention';
                END IF;
                scope_id := NULLIF(current_setting('app.current_organization_id', true), '');
                IF scope_id IS NULL OR scope_id IS DISTINCT FROM OLD.headers ->> 'organization_id' THEN
                    RAISE EXCEPTION 'email payload purge requires its tenant scope';
                END IF;
                private_id := (OLD.payload ->> 'delivery_id')::uuid;
                IF EXISTS (
                    SELECT 1 FROM public.email_delivery
                    WHERE delivery_id = private_id
                      AND organization_id::text = scope_id
                ) THEN
                    RAISE EXCEPTION 'email payload still exists';
                END IF;
                NEW.email_payload_purged_at := clock_timestamp();
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION platform.guard_email_outbox_retention() FROM PUBLIC"
    )
    op.execute(
        """
        CREATE TRIGGER email_outbox_retention_guard
        BEFORE INSERT OR UPDATE OR DELETE ON platform.event_outbox
        FOR EACH ROW EXECUTE FUNCTION platform.guard_email_outbox_retention()
        """
    )


def downgrade() -> None:
    # A downgrade cannot silently delete queued message bodies. Operators must
    # settle or explicitly discard those deliveries before retiring this table.
    raise RuntimeError("email_delivery contains pending delivery content; forward-fix")
