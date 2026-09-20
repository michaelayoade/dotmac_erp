"""Add configurable departmental KPI management and measurement audit.

Revision ID: 20260920_departmental_kpis
Revises: 20260920_calendar_talk
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260920_departmental_kpis"
down_revision = "20260920_calendar_talk"
branch_labels = None
depends_on = None

SCHEMA = "perf"
PERMISSIONS = {
    "performance:kpi:dashboard:view": "View the departmental KPI dashboard",
    "performance:kpi:dashboard:view_all_departments": "View KPI dashboards for every department",
    "performance:kpi:manage": "Create and maintain KPI configurations",
    "performance:kpi:measure": "Submit KPI measurements",
    "performance:kpi:approve": "Approve KPI measurements",
    "performance:kpi:export": "Export KPI reports",
}


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table}
        USING (
            organization_id = current_setting('app.current_organization_id')::uuid
        )
        WITH CHECK (
            organization_id = current_setting('app.current_organization_id')::uuid
        )
        """
    )


def upgrade() -> None:
    table = "department_performance_template"
    # A display name is not an identity: role- and employee-specific assignments
    # may intentionally reuse the same KPI name. The stable tenant-scoped code
    # added below becomes the configuration identity instead.
    op.drop_constraint(
        "uq_dept_perf_template_kpi", table, schema=SCHEMA, type_="unique"
    )
    op.add_column(table, sa.Column("kpi_code", sa.String(50)), schema=SCHEMA)
    op.add_column(table, sa.Column("category", sa.String(100)), schema=SCHEMA)
    op.add_column(
        table,
        sa.Column("measurement_type", sa.String(20), server_default="NUMBER"),
        schema=SCHEMA,
    )
    op.add_column(
        table,
        sa.Column("direction", sa.String(20), server_default="HIGHER_IS_BETTER"),
        schema=SCHEMA,
    )
    op.add_column(
        table,
        sa.Column("frequency", sa.String(20), server_default="MONTHLY"),
        schema=SCHEMA,
    )
    op.add_column(
        table,
        sa.Column("calculation_method", sa.String(30), server_default="RATIO"),
        schema=SCHEMA,
    )
    op.add_column(
        table,
        sa.Column(
            "green_threshold",
            sa.Numeric(5, 2),
            server_default="95.00",
        ),
        schema=SCHEMA,
    )
    op.add_column(
        table,
        sa.Column(
            "amber_threshold",
            sa.Numeric(5, 2),
            server_default="80.00",
        ),
        schema=SCHEMA,
    )
    op.add_column(table, sa.Column("band_min_value", sa.Numeric(12, 2)), schema=SCHEMA)
    op.add_column(table, sa.Column("band_max_value", sa.Numeric(12, 2)), schema=SCHEMA)
    op.add_column(
        table,
        sa.Column("assignment_scope", sa.String(20), server_default="DEPARTMENT"),
        schema=SCHEMA,
    )
    op.add_column(table, sa.Column("designation_id", _uuid()), schema=SCHEMA)
    op.add_column(table, sa.Column("position_id", _uuid()), schema=SCHEMA)
    op.add_column(table, sa.Column("employee_id", _uuid()), schema=SCHEMA)
    op.add_column(table, sa.Column("effective_start", sa.Date()), schema=SCHEMA)
    op.add_column(table, sa.Column("effective_end", sa.Date()), schema=SCHEMA)
    op.add_column(
        table,
        sa.Column("config_version", sa.Integer(), server_default="1"),
        schema=SCHEMA,
    )
    op.execute(
        """
        UPDATE perf.department_performance_template
        SET kpi_code = 'KPI-' || upper(substr(md5(template_id::text), 1, 12)),
            direction = CASE
                WHEN lower_is_better THEN 'LOWER_IS_BETTER'
                ELSE 'HIGHER_IS_BETTER'
            END
        """
    )
    for column in (
        "kpi_code",
        "measurement_type",
        "direction",
        "frequency",
        "calculation_method",
        "green_threshold",
        "amber_threshold",
        "assignment_scope",
        "config_version",
    ):
        op.alter_column(table, column, nullable=False, schema=SCHEMA)
    op.create_unique_constraint(
        "uq_dept_perf_template_code",
        table,
        ["organization_id", "kpi_code"],
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_dept_perf_template_weight",
        table,
        "weightage >= 0 AND weightage <= 100",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_dept_perf_template_threshold_order",
        table,
        "green_threshold >= amber_threshold",
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_dept_perf_template_designation",
        table,
        "designation",
        ["designation_id"],
        ["designation_id"],
        source_schema=SCHEMA,
        referent_schema="hr",
    )
    op.create_foreign_key(
        "fk_dept_perf_template_position",
        table,
        "position",
        ["position_id"],
        ["position_id"],
        source_schema=SCHEMA,
        referent_schema="hr",
    )
    op.create_foreign_key(
        "fk_dept_perf_template_employee",
        table,
        "employee",
        ["employee_id"],
        ["employee_id"],
        source_schema=SCHEMA,
        referent_schema="hr",
    )

    op.add_column("kpi", sa.Column("department_template_id", _uuid()), schema=SCHEMA)
    op.add_column(
        "kpi", sa.Column("config_snapshot", postgresql.JSONB()), schema=SCHEMA
    )
    op.create_foreign_key(
        "fk_kpi_department_template",
        "kpi",
        table,
        ["department_template_id"],
        ["template_id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_index(
        "ix_perf_kpi_department_template_id",
        "kpi",
        ["department_template_id"],
        schema=SCHEMA,
    )
    op.execute(
        """
        UPDATE perf.kpi AS k
        SET department_template_id = t.template_id,
            config_snapshot = jsonb_build_object(
                'template_id', t.template_id::text,
                'version', t.config_version,
                'kpi_code', t.kpi_code,
                'kpi_name', t.kpi_name,
                'target_value', k.target_value::text,
                'weightage', k.weightage::text,
                'measurement_type', t.measurement_type,
                'unit_of_measure', k.unit_of_measure,
                'direction', t.direction,
                'calculation_method', t.calculation_method,
                'green_threshold', t.green_threshold::text,
                'amber_threshold', t.amber_threshold::text,
                'band_min_value', t.band_min_value::text,
                'band_max_value', t.band_max_value::text,
                'metric_source_key', t.metric_source_key
            )
        FROM hr.employee AS e
        JOIN perf.department_performance_template AS t
          ON t.organization_id = e.organization_id
         AND t.department_id = e.department_id
        WHERE k.organization_id = e.organization_id
          AND k.employee_id = e.employee_id
          AND lower(k.kpi_name) = lower(t.kpi_name)
          AND k.department_template_id IS NULL
        """
    )

    op.create_table(
        "kpi_measurement_history",
        sa.Column(
            "history_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("kpi_id", _uuid(), nullable=False),
        sa.Column("department_template_id", _uuid()),
        sa.Column("employee_id", _uuid(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_actual_value", sa.Numeric(12, 2)),
        sa.Column("actual_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("score", sa.Numeric(7, 2)),
        sa.Column("weighted_score", sa.Numeric(7, 2)),
        sa.Column("performance_status", sa.String(20)),
        sa.Column("measurement_mode", sa.String(20), nullable=False),
        sa.Column("source_key", sa.String(100)),
        sa.Column("evidence", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("approval_status", sa.String(20), nullable=False),
        sa.Column("submitted_by_id", _uuid()),
        sa.Column("approved_by_id", _uuid()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("is_recalculation", sa.Boolean(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by_id", _uuid()),
        sa.Column("updated_by_id", _uuid()),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["kpi_id"], ["perf.kpi.kpi_id"]),
        sa.ForeignKeyConstraint(
            ["department_template_id"],
            ["perf.department_performance_template.template_id"],
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["approved_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "organization_id",
            "kpi_id",
            "revision",
            name="uq_kpi_measurement_history_revision",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_perf_kpi_measurement_history_organization_id",
        "kpi_measurement_history",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_kpi_measurement_history_period",
        "kpi_measurement_history",
        ["organization_id", "department_template_id", "period_start", "period_end"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_kpi_measurement_history_employee",
        "kpi_measurement_history",
        ["organization_id", "employee_id", "recorded_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "kpi_configuration_audit",
        sa.Column(
            "audit_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("template_id", _uuid(), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("previous_values", postgresql.JSONB()),
        sa.Column("new_values", postgresql.JSONB(), nullable=False),
        sa.Column("changed_by_id", _uuid()),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["template_id"], ["perf.department_performance_template.template_id"]
        ),
        sa.ForeignKeyConstraint(["changed_by_id"], ["people.id"]),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_perf_kpi_configuration_audit_organization_id",
        "kpi_configuration_audit",
        ["organization_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_kpi_configuration_audit_entity",
        "kpi_configuration_audit",
        ["organization_id", "template_id", "changed_at"],
        schema=SCHEMA,
    )
    _enable_tenant_rls("kpi_measurement_history")
    _enable_tenant_rls("kpi_configuration_audit")

    for key, description in PERMISSIONS.items():
        escaped = description.replace("'", "''")
        op.execute(
            f"""
            INSERT INTO permissions (id, key, description, is_active, created_at, updated_at)
            VALUES (gen_random_uuid(), '{key}', '{escaped}', TRUE, NOW(), NOW())
            ON CONFLICT (key) DO UPDATE
            SET description = EXCLUDED.description,
                is_active = TRUE,
                updated_at = NOW()
            """
        )
    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM roles r
        CROSS JOIN permissions p
        WHERE lower(r.name) IN (
            'admin', 'super_admin', 'system_admin', 'hr_manager', 'hr_director'
        )
          AND p.key LIKE 'performance:kpi:%'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM roles r
        CROSS JOIN permissions p
        WHERE lower(r.name) = 'department_manager'
          AND p.key IN (
              'performance:kpi:dashboard:view',
              'performance:kpi:measure'
          )
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions rp
        USING permissions p
        WHERE rp.permission_id = p.id
          AND p.key LIKE 'performance:kpi:%'
        """
    )
    for key in PERMISSIONS:
        op.execute(
            sa.text("DELETE FROM permissions WHERE key = :key").bindparams(key=key)
        )

    for table in ("kpi_configuration_audit", "kpi_measurement_history"):
        op.execute(
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {SCHEMA}.{table}"
        )
        op.drop_table(table, schema=SCHEMA)

    op.drop_index("ix_perf_kpi_department_template_id", table_name="kpi", schema=SCHEMA)
    op.drop_constraint(
        "fk_kpi_department_template", "kpi", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_column("kpi", "config_snapshot", schema=SCHEMA)
    op.drop_column("kpi", "department_template_id", schema=SCHEMA)

    table = "department_performance_template"
    op.drop_constraint(
        "fk_dept_perf_template_employee", table, schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_dept_perf_template_position", table, schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_dept_perf_template_designation", table, schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint(
        "ck_dept_perf_template_threshold_order", table, schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        "ck_dept_perf_template_weight", table, schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        "uq_dept_perf_template_code", table, schema=SCHEMA, type_="unique"
    )
    op.create_unique_constraint(
        "uq_dept_perf_template_kpi",
        table,
        ["organization_id", "department_id", "kra_name", "kpi_name"],
        schema=SCHEMA,
    )
    for column in (
        "config_version",
        "effective_end",
        "effective_start",
        "employee_id",
        "position_id",
        "designation_id",
        "assignment_scope",
        "band_max_value",
        "band_min_value",
        "amber_threshold",
        "green_threshold",
        "calculation_method",
        "frequency",
        "direction",
        "measurement_type",
        "category",
        "kpi_code",
    ):
        op.drop_column(table, column, schema=SCHEMA)
