"""Create performance management tables.

Revision ID: create_performance_tables
Revises: create_recruit_training
Create Date: 2025-01-20

Phase 5: Performance Management tables for People module.
"""
from typing import Sequence, Union

from alembic import op
from app.alembic_utils import ensure_enum
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'create_performance_tables'
down_revision: Union[str, None] = 'create_recruit_training'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create perf schema
    op.execute("CREATE SCHEMA IF NOT EXISTS perf")

    # Create enum types
    bind = op.get_bind()
    ensure_enum(
        bind,
        "appraisal_cycle_status",
        "DRAFT",
        "ACTIVE",
        "REVIEW",
        "CALIBRATION",
        "COMPLETED",
        "CANCELLED",
        schema="perf",
    )
    ensure_enum(
        bind,
        "kpi_status",
        "DRAFT",
        "ACTIVE",
        "ACHIEVED",
        "MISSED",
        "DEFERRED",
        "CANCELLED",
        schema="perf",
    )
    ensure_enum(
        bind,
        "appraisal_status",
        "DRAFT",
        "SELF_ASSESSMENT",
        "PENDING_REVIEW",
        "UNDER_REVIEW",
        "PENDING_CALIBRATION",
        "CALIBRATION",
        "COMPLETED",
        "CANCELLED",
        schema="perf",
    )

    # ========== APPRAISAL CYCLE ==========
    op.create_table(
        'appraisal_cycle',
        sa.Column('cycle_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('cycle_code', sa.String(30), nullable=False),
        sa.Column('cycle_name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('review_period_start', sa.Date(), nullable=False),
        sa.Column('review_period_end', sa.Date(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('self_assessment_deadline', sa.Date(), nullable=True),
        sa.Column('manager_review_deadline', sa.Date(), nullable=True),
        sa.Column('calibration_deadline', sa.Date(), nullable=True),
        sa.Column('status', postgresql.ENUM('DRAFT', 'ACTIVE', 'REVIEW', 'CALIBRATION', 'COMPLETED', 'CANCELLED', name='appraisal_cycle_status', schema='perf', create_type=False), nullable=False, server_default='DRAFT'),
        sa.Column('include_probation_employees', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('min_tenure_months', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('erpnext_id', sa.String(255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('cycle_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        sa.UniqueConstraint('organization_id', 'cycle_code', name='uq_appraisal_cycle_code'),
        schema='perf'
    )
    op.create_index('idx_appraisal_cycle_org', 'appraisal_cycle', ['organization_id'], schema='perf')
    op.create_index('idx_appraisal_cycle_status', 'appraisal_cycle', ['organization_id', 'status'], schema='perf')
    op.create_index('idx_appraisal_cycle_dates', 'appraisal_cycle', ['organization_id', 'start_date', 'end_date'], schema='perf')
    op.create_index('idx_appraisal_cycle_erpnext', 'appraisal_cycle', ['erpnext_id'], schema='perf')

    # ========== KRA (Key Result Area) ==========
    op.create_table(
        'kra',
        sa.Column('kra_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kra_code', sa.String(30), nullable=False),
        sa.Column('kra_name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('department_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('designation_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('default_weightage', sa.Numeric(5, 2), nullable=False, server_default='0.00'),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('measurement_criteria', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('erpnext_id', sa.String(255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('kra_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['department_id'], ['hr.department.department_id']),
        sa.ForeignKeyConstraint(['designation_id'], ['hr.designation.designation_id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        sa.UniqueConstraint('organization_id', 'kra_code', name='uq_kra_code'),
        schema='perf'
    )
    op.create_index('idx_kra_org', 'kra', ['organization_id'], schema='perf')
    op.create_index('idx_kra_dept', 'kra', ['organization_id', 'department_id'], schema='perf')
    op.create_index('idx_kra_desig', 'kra', ['organization_id', 'designation_id'], schema='perf')
    op.create_index('idx_kra_erpnext', 'kra', ['erpnext_id'], schema='perf')

    # ========== APPRAISAL TEMPLATE ==========
    op.create_table(
        'appraisal_template',
        sa.Column('template_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('template_code', sa.String(30), nullable=False),
        sa.Column('template_name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('department_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('designation_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('rating_scale_max', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('erpnext_id', sa.String(255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('template_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['department_id'], ['hr.department.department_id']),
        sa.ForeignKeyConstraint(['designation_id'], ['hr.designation.designation_id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        sa.UniqueConstraint('organization_id', 'template_code', name='uq_appraisal_template_code'),
        schema='perf'
    )
    op.create_index('idx_appraisal_template_org', 'appraisal_template', ['organization_id'], schema='perf')
    op.create_index('idx_appraisal_template_dept', 'appraisal_template', ['organization_id', 'department_id'], schema='perf')
    op.create_index('idx_appraisal_template_desig', 'appraisal_template', ['organization_id', 'designation_id'], schema='perf')
    op.create_index('idx_appraisal_template_erpnext', 'appraisal_template', ['erpnext_id'], schema='perf')

    # ========== APPRAISAL TEMPLATE KRA ==========
    op.create_table(
        'appraisal_template_kra',
        sa.Column('template_kra_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('template_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kra_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('weightage', sa.Numeric(5, 2), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('template_kra_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['template_id'], ['perf.appraisal_template.template_id']),
        sa.ForeignKeyConstraint(['kra_id'], ['perf.kra.kra_id']),
        schema='perf'
    )
    op.create_index('idx_template_kra_org', 'appraisal_template_kra', ['organization_id'], schema='perf')
    op.create_index('idx_template_kra_template', 'appraisal_template_kra', ['template_id'], schema='perf')
    op.create_index('idx_template_kra_kra', 'appraisal_template_kra', ['kra_id'], schema='perf')

    # ========== KPI (Key Performance Indicator) ==========
    op.create_table(
        'kpi',
        sa.Column('kpi_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('employee_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kra_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('kpi_name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('target_value', sa.Numeric(12, 2), nullable=False),
        sa.Column('unit_of_measure', sa.String(30), nullable=True),
        sa.Column('threshold_value', sa.Numeric(12, 2), nullable=True),
        sa.Column('stretch_value', sa.Numeric(12, 2), nullable=True),
        sa.Column('actual_value', sa.Numeric(12, 2), nullable=True),
        sa.Column('achievement_percentage', sa.Numeric(5, 2), nullable=True),
        sa.Column('weightage', sa.Numeric(5, 2), nullable=False, server_default='0.00'),
        sa.Column('status', postgresql.ENUM('DRAFT', 'ACTIVE', 'ACHIEVED', 'MISSED', 'DEFERRED', 'CANCELLED', name='kpi_status', schema='perf', create_type=False), nullable=False, server_default='DRAFT'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('erpnext_id', sa.String(255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('kpi_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['employee_id'], ['hr.employee.employee_id']),
        sa.ForeignKeyConstraint(['kra_id'], ['perf.kra.kra_id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        schema='perf'
    )
    op.create_index('idx_kpi_org', 'kpi', ['organization_id'], schema='perf')
    op.create_index('idx_kpi_employee', 'kpi', ['employee_id'], schema='perf')
    op.create_index('idx_kpi_kra', 'kpi', ['kra_id'], schema='perf')
    op.create_index('idx_kpi_status', 'kpi', ['organization_id', 'status'], schema='perf')
    op.create_index('idx_kpi_period', 'kpi', ['organization_id', 'period_start', 'period_end'], schema='perf')
    op.create_index('idx_kpi_erpnext', 'kpi', ['erpnext_id'], schema='perf')

    # ========== APPRAISAL ==========
    op.create_table(
        'appraisal',
        sa.Column('appraisal_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('employee_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('cycle_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('template_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('manager_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', postgresql.ENUM('DRAFT', 'SELF_ASSESSMENT', 'PENDING_REVIEW', 'UNDER_REVIEW', 'PENDING_CALIBRATION', 'CALIBRATION', 'COMPLETED', 'CANCELLED', name='appraisal_status', schema='perf', create_type=False), nullable=False, server_default='DRAFT'),
        sa.Column('self_assessment_date', sa.Date(), nullable=True),
        sa.Column('self_overall_rating', sa.Integer(), nullable=True),
        sa.Column('self_summary', sa.Text(), nullable=True),
        sa.Column('achievements', sa.Text(), nullable=True),
        sa.Column('challenges', sa.Text(), nullable=True),
        sa.Column('development_needs', sa.Text(), nullable=True),
        sa.Column('manager_review_date', sa.Date(), nullable=True),
        sa.Column('manager_overall_rating', sa.Integer(), nullable=True),
        sa.Column('manager_summary', sa.Text(), nullable=True),
        sa.Column('manager_recommendations', sa.Text(), nullable=True),
        sa.Column('calibration_date', sa.Date(), nullable=True),
        sa.Column('calibrated_rating', sa.Integer(), nullable=True),
        sa.Column('calibration_notes', sa.Text(), nullable=True),
        sa.Column('final_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('final_rating', sa.Integer(), nullable=True),
        sa.Column('rating_label', sa.String(50), nullable=True),
        sa.Column('completed_on', sa.Date(), nullable=True),
        sa.Column('erpnext_id', sa.String(255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status_changed_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('appraisal_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['employee_id'], ['hr.employee.employee_id']),
        sa.ForeignKeyConstraint(['cycle_id'], ['perf.appraisal_cycle.cycle_id']),
        sa.ForeignKeyConstraint(['template_id'], ['perf.appraisal_template.template_id']),
        sa.ForeignKeyConstraint(['manager_id'], ['hr.employee.employee_id']),
        sa.ForeignKeyConstraint(['status_changed_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        schema='perf'
    )
    op.create_index('idx_appraisal_org', 'appraisal', ['organization_id'], schema='perf')
    op.create_index('idx_appraisal_employee', 'appraisal', ['employee_id'], schema='perf')
    op.create_index('idx_appraisal_cycle', 'appraisal', ['cycle_id'], schema='perf')
    op.create_index('idx_appraisal_manager', 'appraisal', ['manager_id'], schema='perf')
    op.create_index('idx_appraisal_status', 'appraisal', ['organization_id', 'status'], schema='perf')
    op.create_index('idx_appraisal_erpnext', 'appraisal', ['erpnext_id'], schema='perf')

    # ========== APPRAISAL KRA SCORE ==========
    op.create_table(
        'appraisal_kra_score',
        sa.Column('score_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('appraisal_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kra_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('weightage', sa.Numeric(5, 2), nullable=False),
        sa.Column('self_rating', sa.Integer(), nullable=True),
        sa.Column('self_comments', sa.Text(), nullable=True),
        sa.Column('manager_rating', sa.Integer(), nullable=True),
        sa.Column('manager_comments', sa.Text(), nullable=True),
        sa.Column('final_rating', sa.Integer(), nullable=True),
        sa.Column('weighted_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('score_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['appraisal_id'], ['perf.appraisal.appraisal_id']),
        sa.ForeignKeyConstraint(['kra_id'], ['perf.kra.kra_id']),
        schema='perf'
    )
    op.create_index('idx_kra_score_org', 'appraisal_kra_score', ['organization_id'], schema='perf')
    op.create_index('idx_kra_score_appraisal', 'appraisal_kra_score', ['appraisal_id'], schema='perf')
    op.create_index('idx_kra_score_kra', 'appraisal_kra_score', ['kra_id'], schema='perf')

    # ========== APPRAISAL FEEDBACK (360 Feedback) ==========
    op.create_table(
        'appraisal_feedback',
        sa.Column('feedback_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('appraisal_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('feedback_from_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('feedback_type', sa.String(20), nullable=False),
        sa.Column('overall_rating', sa.Integer(), nullable=True),
        sa.Column('strengths', sa.Text(), nullable=True),
        sa.Column('areas_for_improvement', sa.Text(), nullable=True),
        sa.Column('general_comments', sa.Text(), nullable=True),
        sa.Column('is_anonymous', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('submitted_on', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('feedback_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['appraisal_id'], ['perf.appraisal.appraisal_id']),
        sa.ForeignKeyConstraint(['feedback_from_id'], ['hr.employee.employee_id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        schema='perf'
    )
    op.create_index('idx_feedback_org', 'appraisal_feedback', ['organization_id'], schema='perf')
    op.create_index('idx_feedback_appraisal', 'appraisal_feedback', ['appraisal_id'], schema='perf')
    op.create_index('idx_feedback_from', 'appraisal_feedback', ['feedback_from_id'], schema='perf')

    # ========== SCORECARD ==========
    op.create_table(
        'scorecard',
        sa.Column('scorecard_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('employee_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('period_label', sa.String(50), nullable=True),
        sa.Column('financial_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('customer_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('process_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('learning_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('overall_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('overall_rating', sa.Integer(), nullable=True),
        sa.Column('rating_label', sa.String(50), nullable=True),
        sa.Column('previous_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('score_change', sa.Numeric(5, 2), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('is_finalized', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('finalized_on', sa.Date(), nullable=True),
        sa.Column('erpnext_id', sa.String(255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('scorecard_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['employee_id'], ['hr.employee.employee_id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        schema='perf'
    )
    op.create_index('idx_scorecard_org', 'scorecard', ['organization_id'], schema='perf')
    op.create_index('idx_scorecard_employee', 'scorecard', ['employee_id'], schema='perf')
    op.create_index('idx_scorecard_period', 'scorecard', ['organization_id', 'period_start', 'period_end'], schema='perf')
    op.create_index('idx_scorecard_erpnext', 'scorecard', ['erpnext_id'], schema='perf')

    # ========== SCORECARD ITEM ==========
    op.create_table(
        'scorecard_item',
        sa.Column('item_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('scorecard_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('perspective', sa.String(20), nullable=False),
        sa.Column('metric_name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('target_value', sa.Numeric(12, 2), nullable=True),
        sa.Column('actual_value', sa.Numeric(12, 2), nullable=True),
        sa.Column('unit_of_measure', sa.String(30), nullable=True),
        sa.Column('weightage', sa.Numeric(5, 2), nullable=False, server_default='0.00'),
        sa.Column('score', sa.Numeric(5, 2), nullable=True),
        sa.Column('weighted_score', sa.Numeric(5, 2), nullable=True),
        sa.Column('status', sa.String(20), nullable=True),
        sa.Column('sequence', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('item_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['scorecard_id'], ['perf.scorecard.scorecard_id']),
        schema='perf'
    )
    op.create_index('idx_scorecard_item_org', 'scorecard_item', ['organization_id'], schema='perf')
    op.create_index('idx_scorecard_item_scorecard', 'scorecard_item', ['scorecard_id'], schema='perf')

    # ========== RLS POLICIES ==========
    for table in ['appraisal_cycle', 'kra', 'appraisal_template', 'appraisal_template_kra',
                  'kpi', 'appraisal', 'appraisal_kra_score', 'appraisal_feedback',
                  'scorecard', 'scorecard_item']:
        op.execute(f"ALTER TABLE perf.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON perf.{table}
            USING (organization_id = current_setting('app.current_organization_id')::uuid)
        """)


def downgrade() -> None:
    # Drop RLS policies
    for table in ['appraisal_cycle', 'kra', 'appraisal_template', 'appraisal_template_kra',
                  'kpi', 'appraisal', 'appraisal_kra_score', 'appraisal_feedback',
                  'scorecard', 'scorecard_item']:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON perf.{table}")
        op.execute(f"ALTER TABLE perf.{table} DISABLE ROW LEVEL SECURITY")

    # Drop tables in reverse order (respecting FK dependencies)
    op.drop_table('scorecard_item', schema='perf')
    op.drop_table('scorecard', schema='perf')
    op.drop_table('appraisal_feedback', schema='perf')
    op.drop_table('appraisal_kra_score', schema='perf')
    op.drop_table('appraisal', schema='perf')
    op.drop_table('kpi', schema='perf')
    op.drop_table('appraisal_template_kra', schema='perf')
    op.drop_table('appraisal_template', schema='perf')
    op.drop_table('kra', schema='perf')
    op.drop_table('appraisal_cycle', schema='perf')

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS perf.appraisal_status")
    op.execute("DROP TYPE IF EXISTS perf.kpi_status")
    op.execute("DROP TYPE IF EXISTS perf.appraisal_cycle_status")

    # Drop schema
    op.execute("DROP SCHEMA IF EXISTS perf CASCADE")
