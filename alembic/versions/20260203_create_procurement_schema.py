"""Create Procurement schema and tables.

Revision ID: 20260203_create_procurement_schema
Revises: 20260202_create_fleet_management_schema
Create Date: 2026-02-03

This migration creates:
- proc schema for procurement management module
- Enums for plan status, requisition status, RFQ status, etc.
- Tables: procurement_plan, procurement_plan_item, purchase_requisition,
          purchase_requisition_line, request_for_quotation, rfq_invitation,
          quotation_response, quotation_response_line, bid_evaluation,
          bid_evaluation_score, procurement_contract, vendor_prequalification
"""
from alembic import op
from app.alembic_utils import ensure_enum

revision = "20260203_create_procurement_schema"
down_revision = "20260202_create_fleet_management_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create proc schema
    op.execute("CREATE SCHEMA IF NOT EXISTS proc")

    # Create enums
    bind = op.get_bind()

    ensure_enum(
        bind,
        "procurement_plan_status",
        "DRAFT",
        "SUBMITTED",
        "APPROVED",
        "ACTIVE",
        "CLOSED",
        schema="proc",
    )

    ensure_enum(
        bind,
        "plan_item_status",
        "PENDING",
        "IN_PROGRESS",
        "COMPLETED",
        "CANCELLED",
        schema="proc",
    )

    ensure_enum(
        bind,
        "procurement_method",
        "DIRECT",
        "SELECTIVE",
        "OPEN_COMPETITIVE",
        schema="proc",
    )

    ensure_enum(
        bind,
        "requisition_status",
        "DRAFT",
        "SUBMITTED",
        "BUDGET_VERIFIED",
        "APPROVED",
        "CONVERTED",
        "REJECTED",
        "CANCELLED",
        schema="proc",
    )

    ensure_enum(
        bind,
        "urgency_level",
        "NORMAL",
        "URGENT",
        "EMERGENCY",
        schema="proc",
    )

    ensure_enum(
        bind,
        "rfq_status",
        "DRAFT",
        "PUBLISHED",
        "CLOSED",
        "EVALUATED",
        "AWARDED",
        "CANCELLED",
        schema="proc",
    )

    ensure_enum(
        bind,
        "quotation_response_status",
        "RECEIVED",
        "UNDER_EVALUATION",
        "ACCEPTED",
        "REJECTED",
        schema="proc",
    )

    ensure_enum(
        bind,
        "evaluation_status",
        "DRAFT",
        "IN_PROGRESS",
        "COMPLETED",
        "APPROVED",
        schema="proc",
    )

    ensure_enum(
        bind,
        "contract_status",
        "DRAFT",
        "ACTIVE",
        "COMPLETED",
        "TERMINATED",
        "EXPIRED",
        schema="proc",
    )

    ensure_enum(
        bind,
        "prequalification_status",
        "PENDING",
        "UNDER_REVIEW",
        "QUALIFIED",
        "DISQUALIFIED",
        "EXPIRED",
        "BLACKLISTED",
        schema="proc",
    )

    # ─────────────────────────────────────────────────────────────
    # procurement_plan
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.procurement_plan (
            plan_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL
                REFERENCES core_org.organization(organization_id),
            plan_number VARCHAR(30) NOT NULL,
            fiscal_year VARCHAR(10) NOT NULL,
            title VARCHAR(200) NOT NULL,
            status proc.procurement_plan_status NOT NULL DEFAULT 'DRAFT',
            total_estimated_value NUMERIC(20,6) NOT NULL DEFAULT 0,
            currency_code VARCHAR(3) NOT NULL DEFAULT 'NGN',
            approved_by_user_id UUID,
            approved_at TIMESTAMPTZ,
            created_by_user_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,

            CONSTRAINT uq_proc_plan_org_number
                UNIQUE (organization_id, plan_number)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_plan_status
            ON proc.procurement_plan(organization_id, status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_plan_fiscal_year
            ON proc.procurement_plan(organization_id, fiscal_year)
    """)

    # ─────────────────────────────────────────────────────────────
    # procurement_plan_item
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.procurement_plan_item (
            item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            plan_id UUID NOT NULL
                REFERENCES proc.procurement_plan(plan_id) ON DELETE CASCADE,
            organization_id UUID NOT NULL
                REFERENCES core_org.organization(organization_id),
            line_number INTEGER NOT NULL,
            description TEXT NOT NULL,
            budget_line_code VARCHAR(50),
            budget_id UUID,
            estimated_value NUMERIC(20,6) NOT NULL,
            procurement_method proc.procurement_method
                NOT NULL DEFAULT 'OPEN_COMPETITIVE',
            planned_quarter INTEGER NOT NULL
                CHECK (planned_quarter >= 1 AND planned_quarter <= 4),
            approving_authority VARCHAR(100),
            category VARCHAR(100),
            status proc.plan_item_status NOT NULL DEFAULT 'PENDING',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_plan_item_plan
            ON proc.procurement_plan_item(plan_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_plan_item_status
            ON proc.procurement_plan_item(organization_id, status)
    """)

    # ─────────────────────────────────────────────────────────────
    # purchase_requisition
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.purchase_requisition (
            requisition_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL
                REFERENCES core_org.organization(organization_id),
            requisition_number VARCHAR(30) NOT NULL,
            requisition_date DATE NOT NULL,
            requester_id UUID NOT NULL,
            department_id UUID,
            status proc.requisition_status NOT NULL DEFAULT 'DRAFT',
            urgency proc.urgency_level NOT NULL DEFAULT 'NORMAL',
            justification TEXT,
            total_estimated_amount NUMERIC(20,6) NOT NULL DEFAULT 0,
            currency_code VARCHAR(3) NOT NULL DEFAULT 'NGN',
            budget_verified BOOLEAN NOT NULL DEFAULT FALSE,
            budget_verified_by_id UUID,
            budget_verified_at TIMESTAMPTZ,
            material_request_id UUID,
            plan_item_id UUID,
            approval_request_id UUID,
            approved_by_user_id UUID,
            approved_at TIMESTAMPTZ,
            created_by_user_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,

            CONSTRAINT uq_proc_requisition_org_number
                UNIQUE (organization_id, requisition_number)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_requisition_status
            ON proc.purchase_requisition(organization_id, status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_requisition_requester
            ON proc.purchase_requisition(requester_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_requisition_date
            ON proc.purchase_requisition(organization_id, requisition_date)
    """)

    # ─────────────────────────────────────────────────────────────
    # purchase_requisition_line
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.purchase_requisition_line (
            line_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            requisition_id UUID NOT NULL
                REFERENCES proc.purchase_requisition(requisition_id)
                ON DELETE CASCADE,
            organization_id UUID NOT NULL
                REFERENCES core_org.organization(organization_id),
            line_number INTEGER NOT NULL,
            item_id UUID,
            description TEXT NOT NULL,
            quantity NUMERIC(20,6) NOT NULL,
            uom VARCHAR(20),
            estimated_unit_price NUMERIC(20,6) NOT NULL,
            estimated_amount NUMERIC(20,6) NOT NULL,
            expense_account_id UUID,
            cost_center_id UUID,
            project_id UUID,
            delivery_date DATE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_req_line_requisition
            ON proc.purchase_requisition_line(requisition_id)
    """)

    # ─────────────────────────────────────────────────────────────
    # request_for_quotation
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.request_for_quotation (
            rfq_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL
                REFERENCES core_org.organization(organization_id),
            rfq_number VARCHAR(30) NOT NULL,
            title VARCHAR(200) NOT NULL,
            rfq_date DATE NOT NULL,
            closing_date DATE NOT NULL,
            status proc.rfq_status NOT NULL DEFAULT 'DRAFT',
            procurement_method proc.procurement_method
                NOT NULL DEFAULT 'OPEN_COMPETITIVE',
            requisition_id UUID,
            plan_item_id UUID,
            evaluation_criteria JSONB,
            terms_and_conditions TEXT,
            estimated_value NUMERIC(20,6),
            currency_code VARCHAR(3) NOT NULL DEFAULT 'NGN',
            created_by_user_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,

            CONSTRAINT uq_proc_rfq_org_number
                UNIQUE (organization_id, rfq_number)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_rfq_status
            ON proc.request_for_quotation(organization_id, status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_rfq_closing
            ON proc.request_for_quotation(organization_id, closing_date)
    """)

    # ─────────────────────────────────────────────────────────────
    # rfq_invitation
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.rfq_invitation (
            invitation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            rfq_id UUID NOT NULL
                REFERENCES proc.request_for_quotation(rfq_id)
                ON DELETE CASCADE,
            supplier_id UUID NOT NULL,
            invited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            responded BOOLEAN NOT NULL DEFAULT FALSE,
            response_date TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_rfq_inv_rfq
            ON proc.rfq_invitation(rfq_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_rfq_inv_supplier
            ON proc.rfq_invitation(supplier_id)
    """)

    # ─────────────────────────────────────────────────────────────
    # quotation_response
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.quotation_response (
            response_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            rfq_id UUID NOT NULL
                REFERENCES proc.request_for_quotation(rfq_id),
            organization_id UUID NOT NULL
                REFERENCES core_org.organization(organization_id),
            supplier_id UUID NOT NULL,
            response_number VARCHAR(30) NOT NULL,
            response_date DATE NOT NULL,
            total_amount NUMERIC(20,6) NOT NULL,
            currency_code VARCHAR(3) NOT NULL DEFAULT 'NGN',
            delivery_period_days INTEGER,
            validity_days INTEGER,
            technical_proposal TEXT,
            notes TEXT,
            status proc.quotation_response_status NOT NULL DEFAULT 'RECEIVED',
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,

            CONSTRAINT uq_proc_quot_resp_org_number
                UNIQUE (organization_id, response_number)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_quot_resp_rfq
            ON proc.quotation_response(rfq_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_quot_resp_supplier
            ON proc.quotation_response(supplier_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_quot_resp_status
            ON proc.quotation_response(organization_id, status)
    """)

    # ─────────────────────────────────────────────────────────────
    # quotation_response_line
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.quotation_response_line (
            line_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            response_id UUID NOT NULL
                REFERENCES proc.quotation_response(response_id)
                ON DELETE CASCADE,
            requisition_line_id UUID,
            line_number INTEGER NOT NULL,
            description TEXT NOT NULL,
            quantity NUMERIC(20,6) NOT NULL,
            unit_price NUMERIC(20,6) NOT NULL,
            line_amount NUMERIC(20,6) NOT NULL,
            delivery_date DATE
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_quot_resp_line_response
            ON proc.quotation_response_line(response_id)
    """)

    # ─────────────────────────────────────────────────────────────
    # bid_evaluation
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.bid_evaluation (
            evaluation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            rfq_id UUID NOT NULL
                REFERENCES proc.request_for_quotation(rfq_id),
            organization_id UUID NOT NULL
                REFERENCES core_org.organization(organization_id),
            evaluation_date DATE NOT NULL,
            status proc.evaluation_status NOT NULL DEFAULT 'DRAFT',
            recommended_supplier_id UUID,
            recommended_response_id UUID,
            evaluation_report TEXT,
            approval_request_id UUID,
            evaluated_by_user_id UUID NOT NULL,
            approved_by_user_id UUID,
            approved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_eval_rfq
            ON proc.bid_evaluation(rfq_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_eval_status
            ON proc.bid_evaluation(organization_id, status)
    """)

    # ─────────────────────────────────────────────────────────────
    # bid_evaluation_score
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.bid_evaluation_score (
            score_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            evaluation_id UUID NOT NULL
                REFERENCES proc.bid_evaluation(evaluation_id)
                ON DELETE CASCADE,
            response_id UUID NOT NULL,
            criterion_name VARCHAR(100) NOT NULL,
            weight NUMERIC(5,2) NOT NULL,
            score NUMERIC(5,2) NOT NULL,
            weighted_score NUMERIC(10,4) NOT NULL,
            comments TEXT
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_eval_score_eval
            ON proc.bid_evaluation_score(evaluation_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_eval_score_response
            ON proc.bid_evaluation_score(response_id)
    """)

    # ─────────────────────────────────────────────────────────────
    # procurement_contract
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.procurement_contract (
            contract_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL
                REFERENCES core_org.organization(organization_id),
            contract_number VARCHAR(30) NOT NULL,
            title VARCHAR(200) NOT NULL,
            supplier_id UUID NOT NULL,
            rfq_id UUID,
            evaluation_id UUID,
            purchase_order_id UUID,
            contract_date DATE NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            contract_value NUMERIC(20,6) NOT NULL,
            currency_code VARCHAR(3) NOT NULL DEFAULT 'NGN',
            status proc.contract_status NOT NULL DEFAULT 'DRAFT',
            bpp_clearance_number VARCHAR(50),
            bpp_clearance_date DATE,
            payment_terms TEXT,
            terms_and_conditions TEXT,
            performance_bond_required BOOLEAN NOT NULL DEFAULT FALSE,
            performance_bond_amount NUMERIC(20,6),
            retention_percentage NUMERIC(5,2),
            amount_paid NUMERIC(20,6) NOT NULL DEFAULT 0,
            completion_date DATE,
            completion_certificate_issued BOOLEAN NOT NULL DEFAULT FALSE,
            created_by_user_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,

            CONSTRAINT uq_proc_contract_org_number
                UNIQUE (organization_id, contract_number)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_contract_status
            ON proc.procurement_contract(organization_id, status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_contract_supplier
            ON proc.procurement_contract(supplier_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_contract_dates
            ON proc.procurement_contract(organization_id, start_date, end_date)
    """)

    # ─────────────────────────────────────────────────────────────
    # vendor_prequalification
    # ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS proc.vendor_prequalification (
            prequalification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL
                REFERENCES core_org.organization(organization_id),
            supplier_id UUID NOT NULL,
            application_date DATE NOT NULL,
            status proc.prequalification_status NOT NULL DEFAULT 'PENDING',
            categories JSONB,
            valid_from DATE,
            valid_to DATE,
            documents_verified BOOLEAN NOT NULL DEFAULT FALSE,
            tax_clearance_valid BOOLEAN NOT NULL DEFAULT FALSE,
            pension_compliance BOOLEAN NOT NULL DEFAULT FALSE,
            itf_compliance BOOLEAN NOT NULL DEFAULT FALSE,
            nsitf_compliance BOOLEAN NOT NULL DEFAULT FALSE,
            financial_capability_score NUMERIC(5,2),
            technical_capability_score NUMERIC(5,2),
            overall_score NUMERIC(5,2),
            review_notes TEXT,
            reviewed_by_user_id UUID,
            reviewed_at TIMESTAMPTZ,
            blacklisted BOOLEAN NOT NULL DEFAULT FALSE,
            blacklist_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_preq_supplier
            ON proc.vendor_prequalification(supplier_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_preq_status
            ON proc.vendor_prequalification(organization_id, status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_preq_validity
            ON proc.vendor_prequalification(organization_id, valid_from, valid_to)
    """)


def downgrade() -> None:
    # Drop tables in reverse order (respect foreign key dependencies)
    op.execute("DROP TABLE IF EXISTS proc.vendor_prequalification CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.procurement_contract CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.bid_evaluation_score CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.bid_evaluation CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.quotation_response_line CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.quotation_response CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.rfq_invitation CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.request_for_quotation CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.purchase_requisition_line CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.purchase_requisition CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.procurement_plan_item CASCADE")
    op.execute("DROP TABLE IF EXISTS proc.procurement_plan CASCADE")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS proc.prequalification_status CASCADE")
    op.execute("DROP TYPE IF EXISTS proc.contract_status CASCADE")
    op.execute("DROP TYPE IF EXISTS proc.evaluation_status CASCADE")
    op.execute("DROP TYPE IF EXISTS proc.quotation_response_status CASCADE")
    op.execute("DROP TYPE IF EXISTS proc.rfq_status CASCADE")
    op.execute("DROP TYPE IF EXISTS proc.urgency_level CASCADE")
    op.execute("DROP TYPE IF EXISTS proc.requisition_status CASCADE")
    op.execute("DROP TYPE IF EXISTS proc.procurement_method CASCADE")
    op.execute("DROP TYPE IF EXISTS proc.plan_item_status CASCADE")
    op.execute("DROP TYPE IF EXISTS proc.procurement_plan_status CASCADE")

    # Drop schema
    op.execute("DROP SCHEMA IF EXISTS proc CASCADE")
