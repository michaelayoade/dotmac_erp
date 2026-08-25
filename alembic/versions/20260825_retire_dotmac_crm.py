"""Retire direct CRM runtime state and qualify surviving Sub correlations.

Revision ID: 20260825_retire_dotmac_crm
Revises: 20260825_weekly_meeting_reports
Create Date: 2026-08-25

The migration never copies or logs secret material. It deactivates the retired
integration, clears its stored credential material, revokes generated service
keys, and converts the mixed legacy correlation columns to explicit source
provenance. Ambiguous rows are ``legacy_unknown`` until an authenticated Sub
observation adopts the exact correlation.
"""

from __future__ import annotations

from alembic import op

revision = "20260825_retire_dotmac_crm"
down_revision = "20260825_weekly_meeting_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Disable the retired credential owners without ever selecting, copying or
    # rendering their values. The enum member remains reserved for old rows.
    op.execute(
        """
        UPDATE sync.integration_config
           SET is_active = false,
               api_key = NULL,
               api_secret = NULL,
               updated_at = NOW()
         WHERE integration_type = 'DOTMAC_CRM'
        """
    )
    op.execute(
        """
        UPDATE public.api_keys
           SET is_active = false,
               revoked_at = COALESCE(revoked_at, NOW())
         WHERE label ILIKE 'dotmac-crm-service-%'
        """
    )

    # The old mapping mixed CRM and Sub writers. Preserve every linked local
    # row, rename the ledger to its real generic role, and mark all old
    # provenance unknown. Only a new authenticated source observation may
    # adopt an exact legacy correlation.
    op.execute("ALTER TABLE sync.crm_sync_mapping RENAME TO source_correlation")
    op.execute(
        "ALTER TABLE sync.source_correlation "
        "RENAME COLUMN crm_entity_type TO source_entity_type"
    )
    op.execute(
        "ALTER TABLE sync.source_correlation "
        "RENAME COLUMN crm_id TO source_reference"
    )
    op.execute(
        "ALTER TABLE sync.source_correlation "
        "RENAME COLUMN crm_status TO source_status"
    )
    op.execute(
        "ALTER TABLE sync.source_correlation "
        "RENAME COLUMN crm_data TO source_payload"
    )
    op.execute(
        "ALTER TABLE sync.source_correlation "
        "RENAME COLUMN crm_updated_at TO source_updated_at"
    )
    op.execute("ALTER TYPE sync.crm_entity_type RENAME TO source_entity_type")
    op.execute(
        "ALTER TYPE sync.crm_sync_status RENAME TO source_correlation_status"
    )
    op.execute(
        "ALTER TABLE sync.source_correlation "
        "ADD COLUMN source_application VARCHAR(50)"
    )
    op.execute(
        "UPDATE sync.source_correlation "
        "SET source_application = 'legacy_unknown', source_status = 'ARCHIVED'"
    )
    op.execute(
        "ALTER TABLE sync.source_correlation "
        "ALTER COLUMN source_application SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE sync.source_correlation "
        "DROP CONSTRAINT uq_crm_sync_org_type_id"
    )
    op.execute(
        "ALTER TABLE sync.source_correlation "
        "ADD CONSTRAINT uq_source_correlation_org_app_type_ref UNIQUE "
        "(organization_id, source_application, source_entity_type, source_reference)"
    )
    op.execute(
        "ALTER TABLE sync.source_correlation "
        "RENAME CONSTRAINT fk_crm_sync_organization "
        "TO fk_source_correlation_organization"
    )
    op.execute("ALTER INDEX sync.idx_crm_sync_org RENAME TO idx_source_correlation_org")
    op.execute(
        "ALTER INDEX sync.idx_crm_sync_crm_id "
        "RENAME TO idx_source_correlation_source_reference"
    )
    op.execute(
        "ALTER INDEX sync.idx_crm_sync_local "
        "RENAME TO idx_source_correlation_local"
    )
    op.execute(
        "ALTER INDEX sync.idx_crm_sync_status "
        "RENAME TO idx_source_correlation_status"
    )
    op.execute(
        "COMMENT ON TABLE sync.source_correlation IS "
        "'Source-qualified correlation evidence; never a source of domain truth'"
    )
    op.execute(
        "COMMENT ON COLUMN sync.source_correlation.source_reference IS "
        "'Opaque identifier within source_application'"
    )
    op.execute(
        "COMMENT ON COLUMN sync.source_correlation.source_payload IS "
        "'Rebuildable source observation used for local context'"
    )
    op.execute(
        "COMMENT ON COLUMN sync.source_correlation.source_updated_at IS "
        "'Last update time reported by the source application'"
    )

    # The renamed ledger becomes live Sub infrastructure, so its historical
    # lack of tenant isolation is not inherited under a neutral name. Both the
    # application role and the table owner are constrained by the same policy.
    op.execute(
        "ALTER TABLE sync.source_correlation ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE sync.source_correlation FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY source_correlation_tenant_isolation
            ON sync.source_correlation
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
    op.execute("GRANT USAGE ON SCHEMA sync TO app_user")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON TABLE sync.source_correlation TO app_user"
    )

    # Material support is still live for Sub. Qualify its opaque source key and
    # remove the retired source from the executable vocabulary.
    op.execute(
        "ALTER TABLE inv.material_request DROP CONSTRAINT "
        "uq_material_request_org_crm_id"
    )
    op.execute(
        "ALTER TABLE inv.material_request DROP CONSTRAINT "
        "ck_material_request_source_system"
    )
    op.execute(
        "ALTER TABLE inv.material_request RENAME COLUMN crm_id TO source_reference"
    )
    op.execute(
        "UPDATE inv.material_request SET source_system = 'legacy_unknown' "
        "WHERE source_system = 'crm'"
    )
    op.execute(
        "ALTER TABLE inv.material_request ADD CONSTRAINT "
        "ck_material_request_source_system "
        "CHECK (source_system IN ('legacy_unknown', 'sub', 'erp'))"
    )
    op.execute(
        "ALTER TABLE inv.material_request ADD CONSTRAINT "
        "uq_material_request_org_source_ref UNIQUE "
        "(organization_id, source_system, source_reference)"
    )
    op.execute(
        "ALTER INDEX inv.idx_material_request_crm_id "
        "RENAME TO idx_material_request_source_reference"
    )

    # Expense intake was also shared and carried no discriminator. Preserve it
    # as ambiguous history; every new Sub write is explicitly source-qualified.
    op.execute(
        "ALTER TABLE expense.expense_claim DROP CONSTRAINT "
        "uq_expense_claim_org_crm_id"
    )
    op.execute(
        "ALTER TABLE expense.expense_claim RENAME COLUMN crm_id TO source_reference"
    )
    op.execute(
        "ALTER TABLE expense.expense_claim ADD COLUMN source_system VARCHAR(50) "
        "NOT NULL DEFAULT 'local'"
    )
    op.execute(
        "UPDATE expense.expense_claim SET source_system = 'legacy_unknown' "
        "WHERE source_reference IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE expense.expense_claim ADD CONSTRAINT "
        "uq_expense_claim_org_source_ref UNIQUE "
        "(organization_id, source_system, source_reference)"
    )
    op.execute(
        "ALTER INDEX expense.idx_expense_claim_crm_id "
        "RENAME TO idx_expense_claim_source_reference"
    )

    # Customer correlation had no remaining reader. It is not domain state and
    # is dropped rather than being promoted into a new live identity.
    op.execute("DROP INDEX IF EXISTS ar.idx_customer_crm_id")
    op.execute("ALTER TABLE ar.customer DROP COLUMN crm_id")

    # This generic ledger also received mixed writers. Preserve the evidence,
    # but make the ambiguity explicit and terminal for retry workers.
    op.execute(
        """
        UPDATE sync.sync_entity
           SET source_system = 'legacy_unknown',
               sync_status = 'SKIPPED',
               error_message = COALESCE(
                   error_message,
                   'retired source correlation; preserved for audit only'
               )
         WHERE source_system = 'crm'
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "CRM retirement is irreversible: credential material was cleared and "
        "ambiguous provenance was deliberately not reconstructed"
    )
