"""Add PMS governance workflow, grievances, and stakeholder feedback tables.

Revision ID: 20260401_pms_governance_phase1
Revises: 20260331_link_vehicle_to_expenses_and_ap_invoices
Create Date: 2026-04-01
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260401_pms_governance_phase1"
down_revision = "20260331_link_vehicle_to_expenses_and_ap_invoices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("institutional_performance", schema="perf"):
        cols = {
            col["name"]
            for col in inspector.get_columns("institutional_performance", schema="perf")
        }
        if "workflow_stage" not in cols:
            op.add_column(
                "institutional_performance",
                sa.Column(
                    "workflow_stage",
                    sa.String(length=40),
                    nullable=False,
                    server_default="DRAFT",
                ),
                schema="perf",
            )
        if "owner_id" not in cols:
            op.add_column(
                "institutional_performance",
                sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
                schema="perf",
            )
        if "reviewer_id" not in cols:
            op.add_column(
                "institutional_performance",
                sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
                schema="perf",
            )
        if "approver_id" not in cols:
            op.add_column(
                "institutional_performance",
                sa.Column("approver_id", postgresql.UUID(as_uuid=True), nullable=True),
                schema="perf",
            )
        if "submitted_for_review_date" not in cols:
            op.add_column(
                "institutional_performance",
                sa.Column("submitted_for_review_date", sa.Date(), nullable=True),
                schema="perf",
            )
        if "central_review_date" not in cols:
            op.add_column(
                "institutional_performance",
                sa.Column("central_review_date", sa.Date(), nullable=True),
                schema="perf",
            )
        if "approved_date" not in cols:
            op.add_column(
                "institutional_performance",
                sa.Column("approved_date", sa.Date(), nullable=True),
                schema="perf",
            )
        if "returned_date" not in cols:
            op.add_column(
                "institutional_performance",
                sa.Column("returned_date", sa.Date(), nullable=True),
                schema="perf",
            )
        if "final_signoff_date" not in cols:
            op.add_column(
                "institutional_performance",
                sa.Column("final_signoff_date", sa.Date(), nullable=True),
                schema="perf",
            )
        if "workflow_note" not in cols:
            op.add_column(
                "institutional_performance",
                sa.Column("workflow_note", sa.Text(), nullable=True),
                schema="perf",
            )

        fk_names = {
            fk["name"]
            for fk in inspector.get_foreign_keys(
                "institutional_performance", schema="perf"
            )
            if fk.get("name")
        }
        if "fk_inst_perf_owner" not in fk_names:
            op.create_foreign_key(
                "fk_inst_perf_owner",
                "institutional_performance",
                "employee",
                ["owner_id"],
                ["employee_id"],
                source_schema="perf",
                referent_schema="hr",
                ondelete="SET NULL",
            )
        if "fk_inst_perf_reviewer" not in fk_names:
            op.create_foreign_key(
                "fk_inst_perf_reviewer",
                "institutional_performance",
                "employee",
                ["reviewer_id"],
                ["employee_id"],
                source_schema="perf",
                referent_schema="hr",
                ondelete="SET NULL",
            )
        if "fk_inst_perf_approver" not in fk_names:
            op.create_foreign_key(
                "fk_inst_perf_approver",
                "institutional_performance",
                "employee",
                ["approver_id"],
                ["employee_id"],
                source_schema="perf",
                referent_schema="hr",
                ondelete="SET NULL",
            )

    if not inspector.has_table("institutional_governance_action", schema="perf"):
        op.create_table(
            "institutional_governance_action",
            sa.Column(
                "action_id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("inst_perf_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("actor_employee_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("actor_role", sa.String(length=50), nullable=False),
            sa.Column("action_type", sa.String(length=50), nullable=False),
            sa.Column("from_stage", sa.String(length=40), nullable=True),
            sa.Column("to_stage", sa.String(length=40), nullable=True),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.ForeignKeyConstraint(
                ["inst_perf_id"], ["perf.institutional_performance.inst_perf_id"]
            ),
            sa.ForeignKeyConstraint(["actor_employee_id"], ["hr.employee.employee_id"]),
            sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
            sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
            sa.PrimaryKeyConstraint("action_id"),
            schema="perf",
        )
        op.create_index(
            "idx_inst_gov_action_inst_perf",
            "institutional_governance_action",
            ["organization_id", "inst_perf_id"],
            schema="perf",
        )

    if not inspector.has_table("pms_governance_grievance", schema="perf"):
        op.create_table(
            "pms_governance_grievance",
            sa.Column(
                "grievance_id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("appraisal_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("inst_perf_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "raised_by_employee_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column(
                "assigned_to_employee_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column(
                "channel",
                sa.String(length=40),
                nullable=False,
                server_default="INTERNAL",
            ),
            sa.Column(
                "status",
                sa.String(length=30),
                nullable=False,
                server_default="OPEN",
            ),
            sa.Column("committee_level", sa.String(length=30), nullable=True),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("resolution_notes", sa.Text(), nullable=True),
            sa.Column(
                "escalated_to_fcsc",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("escalated_date", sa.Date(), nullable=True),
            sa.Column(
                "raised_date",
                sa.Date(),
                nullable=False,
                server_default=sa.text("CURRENT_DATE"),
            ),
            sa.Column("resolved_date", sa.Date(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.ForeignKeyConstraint(["appraisal_id"], ["perf.appraisal.appraisal_id"]),
            sa.ForeignKeyConstraint(
                ["inst_perf_id"], ["perf.institutional_performance.inst_perf_id"]
            ),
            sa.ForeignKeyConstraint(
                ["raised_by_employee_id"], ["hr.employee.employee_id"]
            ),
            sa.ForeignKeyConstraint(
                ["assigned_to_employee_id"], ["hr.employee.employee_id"]
            ),
            sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
            sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
            sa.PrimaryKeyConstraint("grievance_id"),
            schema="perf",
        )
        op.create_index(
            "idx_pms_grievance_org_status",
            "pms_governance_grievance",
            ["organization_id", "status"],
            schema="perf",
        )

    if not inspector.has_table("pms_stakeholder_feedback", schema="perf"):
        op.create_table(
            "pms_stakeholder_feedback",
            sa.Column(
                "feedback_id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("inst_perf_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "source_type",
                sa.String(length=40),
                nullable=False,
                server_default="SERVICOM",
            ),
            sa.Column(
                "channel",
                sa.String(length=40),
                nullable=False,
                server_default="PORTAL",
            ),
            sa.Column(
                "status",
                sa.String(length=30),
                nullable=False,
                server_default="RECEIVED",
            ),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("feedback_text", sa.Text(), nullable=False),
            sa.Column("submitted_by_name", sa.String(length=120), nullable=True),
            sa.Column("submitted_by_contact", sa.String(length=120), nullable=True),
            sa.Column("sentiment", sa.String(length=20), nullable=True),
            sa.Column("owner_employee_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("action_taken", sa.Text(), nullable=True),
            sa.Column(
                "received_date",
                sa.Date(),
                nullable=False,
                server_default=sa.text("CURRENT_DATE"),
            ),
            sa.Column("closed_date", sa.Date(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.ForeignKeyConstraint(
                ["inst_perf_id"], ["perf.institutional_performance.inst_perf_id"]
            ),
            sa.ForeignKeyConstraint(["owner_employee_id"], ["hr.employee.employee_id"]),
            sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
            sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
            sa.PrimaryKeyConstraint("feedback_id"),
            schema="perf",
        )
        op.create_index(
            "idx_pms_feedback_org_status",
            "pms_stakeholder_feedback",
            ["organization_id", "status"],
            schema="perf",
        )

    if not inspector.has_table("contract_amendment_workflow", schema="perf"):
        op.create_table(
            "contract_amendment_workflow",
            sa.Column(
                "amendment_workflow_id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "original_contract_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column(
                "status",
                sa.String(length=30),
                nullable=False,
                server_default="PENDING",
            ),
            sa.Column("appraisee_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("appraiser_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("hod_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("hr_head_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("appraisee_signed_date", sa.Date(), nullable=True),
            sa.Column("appraiser_signed_date", sa.Date(), nullable=True),
            sa.Column("hod_signed_date", sa.Date(), nullable=True),
            sa.Column("hr_head_signed_date", sa.Date(), nullable=True),
            sa.Column("rejected_by_stage", sa.String(length=20), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("signoff_note", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.ForeignKeyConstraint(
                ["contract_id"], ["perf.performance_contract.contract_id"]
            ),
            sa.ForeignKeyConstraint(
                ["original_contract_id"], ["perf.performance_contract.contract_id"]
            ),
            sa.ForeignKeyConstraint(["appraisee_id"], ["hr.employee.employee_id"]),
            sa.ForeignKeyConstraint(["appraiser_id"], ["hr.employee.employee_id"]),
            sa.ForeignKeyConstraint(["hod_id"], ["hr.employee.employee_id"]),
            sa.ForeignKeyConstraint(["hr_head_id"], ["hr.employee.employee_id"]),
            sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
            sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
            sa.PrimaryKeyConstraint("amendment_workflow_id"),
            schema="perf",
        )
        op.create_index(
            "idx_contract_amendment_org_status",
            "contract_amendment_workflow",
            ["organization_id", "status"],
            schema="perf",
        )
        op.create_index(
            "idx_contract_amendment_contract",
            "contract_amendment_workflow",
            ["organization_id", "contract_id"],
            schema="perf",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("pms_stakeholder_feedback", schema="perf"):
        op.drop_index(
            "idx_pms_feedback_org_status",
            table_name="pms_stakeholder_feedback",
            schema="perf",
        )
        op.drop_table("pms_stakeholder_feedback", schema="perf")

    if inspector.has_table("contract_amendment_workflow", schema="perf"):
        op.drop_index(
            "idx_contract_amendment_contract",
            table_name="contract_amendment_workflow",
            schema="perf",
        )
        op.drop_index(
            "idx_contract_amendment_org_status",
            table_name="contract_amendment_workflow",
            schema="perf",
        )
        op.drop_table("contract_amendment_workflow", schema="perf")

    if inspector.has_table("pms_governance_grievance", schema="perf"):
        op.drop_index(
            "idx_pms_grievance_org_status",
            table_name="pms_governance_grievance",
            schema="perf",
        )
        op.drop_table("pms_governance_grievance", schema="perf")

    if inspector.has_table("institutional_governance_action", schema="perf"):
        op.drop_index(
            "idx_inst_gov_action_inst_perf",
            table_name="institutional_governance_action",
            schema="perf",
        )
        op.drop_table("institutional_governance_action", schema="perf")

    if inspector.has_table("institutional_performance", schema="perf"):
        fk_names = {
            fk["name"]
            for fk in inspector.get_foreign_keys(
                "institutional_performance", schema="perf"
            )
            if fk.get("name")
        }
        if "fk_inst_perf_approver" in fk_names:
            op.drop_constraint(
                "fk_inst_perf_approver",
                "institutional_performance",
                schema="perf",
                type_="foreignkey",
            )
        if "fk_inst_perf_reviewer" in fk_names:
            op.drop_constraint(
                "fk_inst_perf_reviewer",
                "institutional_performance",
                schema="perf",
                type_="foreignkey",
            )
        if "fk_inst_perf_owner" in fk_names:
            op.drop_constraint(
                "fk_inst_perf_owner",
                "institutional_performance",
                schema="perf",
                type_="foreignkey",
            )

        cols = {
            col["name"]
            for col in inspector.get_columns("institutional_performance", schema="perf")
        }
        for col_name in (
            "workflow_note",
            "final_signoff_date",
            "returned_date",
            "approved_date",
            "central_review_date",
            "submitted_for_review_date",
            "approver_id",
            "reviewer_id",
            "owner_id",
            "workflow_stage",
        ):
            if col_name in cols:
                op.drop_column("institutional_performance", col_name, schema="perf")
