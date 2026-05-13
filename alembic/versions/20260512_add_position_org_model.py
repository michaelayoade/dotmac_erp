"""add_position_org_model

Revision ID: 20260512_position_org
Revises: 95ab1bf7b754
Create Date: 2026-05-12 00:00:00.000000
"""

from alembic import op


revision = "20260512_position_org"
down_revision = "95ab1bf7b754"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE t.typname = 'position_assignment_type'
                  AND n.nspname = 'hr'
            ) THEN
                CREATE TYPE hr.position_assignment_type AS ENUM (
                    'PRIMARY',
                    'ACTING',
                    'INTERIM'
                );
            END IF;
        END
        $$;
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS hr.position (
            position_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            designation_id UUID REFERENCES hr.designation(designation_id),
            parent_position_id UUID REFERENCES hr.position(position_id),
            department_id UUID REFERENCES hr.department(department_id),
            is_vacant BOOLEAN NOT NULL DEFAULT TRUE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX IF NOT EXISTS idx_hr_position_org
            ON hr.position(organization_id);
        CREATE INDEX IF NOT EXISTS idx_hr_position_org_parent
            ON hr.position(organization_id, parent_position_id);
        CREATE INDEX IF NOT EXISTS idx_hr_position_org_department
            ON hr.position(organization_id, department_id);
        CREATE INDEX IF NOT EXISTS idx_hr_position_org_designation
            ON hr.position(organization_id, designation_id);
        CREATE INDEX IF NOT EXISTS idx_hr_position_active
            ON hr.position(organization_id, is_active);
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS hr.position_assignment (
            position_assignment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            employee_id UUID NOT NULL REFERENCES hr.employee(employee_id),
            position_id UUID NOT NULL REFERENCES hr.position(position_id),
            start_date DATE NOT NULL,
            end_date DATE,
            assignment_type hr.position_assignment_type NOT NULL DEFAULT 'PRIMARY',
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX IF NOT EXISTS idx_hr_position_assignment_org
            ON hr.position_assignment(organization_id);
        CREATE INDEX IF NOT EXISTS idx_hr_position_assignment_employee_active
            ON hr.position_assignment(organization_id, employee_id, end_date);
        CREATE INDEX IF NOT EXISTS idx_hr_position_assignment_position_active
            ON hr.position_assignment(organization_id, position_id, end_date);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_hr_position_assignment_active_primary_employee
            ON hr.position_assignment(organization_id, employee_id)
            WHERE assignment_type = 'PRIMARY' AND end_date IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_hr_position_assignment_active_primary_position
            ON hr.position_assignment(organization_id, position_id)
            WHERE assignment_type = 'PRIMARY' AND end_date IS NULL;
    """)

    op.execute("""
        CREATE TEMP TABLE tmp_hr_position_backfill (
            employee_id UUID PRIMARY KEY,
            position_id UUID NOT NULL DEFAULT gen_random_uuid()
        ) ON COMMIT DROP;

        INSERT INTO tmp_hr_position_backfill (employee_id)
        SELECT e.employee_id
        FROM hr.employee e
        WHERE e.status != 'TERMINATED'
          AND NOT EXISTS (
              SELECT 1
              FROM hr.position_assignment pa
              WHERE pa.organization_id = e.organization_id
                AND pa.employee_id = e.employee_id
                AND pa.assignment_type = 'PRIMARY'
                AND pa.end_date IS NULL
          );

        INSERT INTO hr.position (
            position_id,
            organization_id,
            designation_id,
            department_id,
            is_vacant,
            created_by_id,
            updated_by_id,
            created_at,
            updated_at
        )
        SELECT
            t.position_id,
            e.organization_id,
            e.designation_id,
            e.department_id,
            FALSE,
            e.created_by_id,
            e.updated_by_id,
            NOW(),
            NOW()
        FROM tmp_hr_position_backfill t
        JOIN hr.employee e ON e.employee_id = t.employee_id;

        INSERT INTO hr.position_assignment (
            organization_id,
            employee_id,
            position_id,
            start_date,
            assignment_type,
            created_by_id,
            updated_by_id,
            created_at,
            updated_at
        )
        SELECT
            e.organization_id,
            e.employee_id,
            t.position_id,
            COALESCE(e.date_of_joining, CURRENT_DATE),
            'PRIMARY'::hr.position_assignment_type,
            e.created_by_id,
            e.updated_by_id,
            NOW(),
            NOW()
        FROM hr.employee e
        JOIN tmp_hr_position_backfill t ON t.employee_id = e.employee_id;
    """)

    op.execute("""
        UPDATE hr.position child_position
        SET parent_position_id = parent_assignment.position_id,
            updated_at = NOW()
        FROM hr.position_assignment child_assignment
        JOIN hr.employee child_employee
          ON child_employee.employee_id = child_assignment.employee_id
         AND child_employee.organization_id = child_assignment.organization_id
        JOIN hr.position_assignment parent_assignment
          ON parent_assignment.employee_id = child_employee.reports_to_id
         AND parent_assignment.organization_id = child_employee.organization_id
         AND parent_assignment.assignment_type = 'PRIMARY'
         AND parent_assignment.end_date IS NULL
        WHERE child_position.position_id = child_assignment.position_id
          AND child_position.organization_id = child_assignment.organization_id
          AND child_assignment.assignment_type = 'PRIMARY'
          AND child_assignment.end_date IS NULL
          AND child_employee.reports_to_id IS NOT NULL
          AND child_position.parent_position_id IS NULL;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS hr.position_assignment CASCADE")
    op.execute("DROP TABLE IF EXISTS hr.position CASCADE")
    op.execute("DROP TYPE IF EXISTS hr.position_assignment_type CASCADE")
