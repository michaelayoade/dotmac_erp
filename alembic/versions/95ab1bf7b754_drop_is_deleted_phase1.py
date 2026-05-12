"""drop is_deleted and deleted_at from tables; migrate soft-deleted state.

Revision ID: 95ab1bf7b754
Revises: 86316c93eb91
Create Date: 2026-05-10 21:22:24.715520

P1 #4 of the Narrative-0 audit. See:
  docs/superpowers/specs/2026-05-10-deprecate-is-deleted-design.md

For each table that had is_deleted, we map the soft-delete state into a
canonical lifecycle field BEFORE dropping the column:

  Table                          Replacement
  -----                          -----------
  hr.employee                    status='TERMINATED'
  hr.department                  is_active=false (existing column)
  hr.designation                 is_active=false (existing column)
  hr.employee_document           is_active=false (NEW column)
  hr.employee_qualification      is_active=false (NEW column)
  hr.employee_certification      is_active=false (NEW column)
  hr.employee_dependent          is_active=false (NEW column)
  hr.skill                       is_active=false (existing column)
  hr.competency                  is_active=false (existing column)
  hr.job_description             status='archived'
  hr.disciplinary_case           status='WITHDRAWN'
  pm.task                        status='CANCELLED'
  pm.pm_comment                  is_active=false (NEW column)
  support.ticket                 status='CLOSED'
  support.ticket_attachment      is_active=false (NEW column)
  support.ticket_comment         is_active=false (NEW column)
  fleet.vehicle                  status='DISPOSED'
  fleet.vehicle_incident         is_active=false (NEW column)

Note on table-name discovery (vs the plan's nominal list of 13 base models):
The recon enumerated 13 model FILES, but several files contain multiple
SoftDelete-using ORM classes:
  app/models/people/hr/employee_extended.py -> 5 tables
    (employee_document, employee_qualification, employee_certification,
     employee_dependent, skill)
  app/models/people/hr/job_description.py -> 2 tables
    (competency, job_description)
The 18-entry _PER_TABLE_MIGRATION list reflects the actual SoftDelete-using
table set discovered by reading every model file.

Note on column shape: 4 tables (pm.pm_comment, support.ticket,
support.ticket_attachment, support.ticket_comment) define `is_deleted`
directly without inheriting SoftDeleteMixin, so they DO NOT have
`deleted_at` or `deleted_by_id`. _TABLES_WITH_DELETED_AT tracks which
tables actually have deleted_at so the DROP doesn't fail.

Note on `deleted_by_id`: the SoftDeleteMixin also defines a
`deleted_by_id: UUID FK -> people.id` column. Because we're deprecating
the entire soft-delete mechanism (not just the boolean flag), this
migration drops `deleted_by_id` alongside `is_deleted`/`deleted_at` for
the 14 SoftDeleteMixin-inheriting tables. The 4 tables that defined
`is_deleted` directly never had `deleted_by_id`, so they are unaffected.

The downgrade is documented as DEV-ONLY: it cannot reliably distinguish
"soft-deleted via TERMINATED" from "legitimately TERMINATED for other
reasons", so reversing it loses precision. Production rollback is
restore-from-backup, not `alembic downgrade`.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "95ab1bf7b754"
down_revision = "86316c93eb91"
branch_labels = None
depends_on = None


# Tables that get a NEW is_active column added by this migration.
# These tables did not previously have any lifecycle field other than
# is_deleted, so we add is_active=true as the new "alive" indicator.
_TABLES_NEEDING_IS_ACTIVE = [
    ("hr", "employee_document"),
    ("hr", "employee_qualification"),
    ("hr", "employee_certification"),
    ("hr", "employee_dependent"),
    ("pm", "pm_comment"),
    ("support", "ticket_attachment"),
    ("support", "ticket_comment"),
    ("fleet", "vehicle_incident"),
]

# Tables that inherit SoftDeleteMixin (and thus have both is_deleted AND
# deleted_at). The other tables have only is_deleted defined directly.
_TABLES_WITH_DELETED_AT = {
    ("hr", "employee"),
    ("hr", "department"),
    ("hr", "designation"),
    ("hr", "employee_document"),
    ("hr", "employee_qualification"),
    ("hr", "employee_certification"),
    ("hr", "employee_dependent"),
    ("hr", "skill"),
    ("hr", "competency"),
    ("hr", "job_description"),
    ("hr", "disciplinary_case"),
    ("pm", "task"),
    ("fleet", "vehicle"),
    ("fleet", "vehicle_incident"),
}

# Per-table soft-delete migration: each entry is
#   (schema, table, sql_setting_lifecycle_field_when_is_deleted_true)
# The SQL fragment goes in the SET clause of an UPDATE.
_PER_TABLE_MIGRATION = [
    ("hr", "employee", "status = 'TERMINATED'"),
    ("hr", "department", "is_active = false"),
    ("hr", "designation", "is_active = false"),
    ("hr", "employee_document", "is_active = false"),
    ("hr", "employee_qualification", "is_active = false"),
    ("hr", "employee_certification", "is_active = false"),
    ("hr", "employee_dependent", "is_active = false"),
    ("hr", "skill", "is_active = false"),
    ("hr", "competency", "is_active = false"),
    ("hr", "job_description", "status = 'archived'"),
    ("hr", "disciplinary_case", "status = 'WITHDRAWN'"),
    ("pm", "task", "status = 'CANCELLED'"),
    ("pm", "pm_comment", "is_active = false"),
    ("support", "ticket", "status = 'CLOSED'"),
    ("support", "ticket_attachment", "is_active = false"),
    ("support", "ticket_comment", "is_active = false"),
    ("fleet", "vehicle", "status = 'DISPOSED'"),
    ("fleet", "vehicle_incident", "is_active = false"),
]


def upgrade() -> None:
    # 1. Add new is_active columns where needed.
    for schema, table in _TABLES_NEEDING_IS_ACTIVE:
        op.add_column(
            table,
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            schema=schema,
        )

    # 2. Migrate is_deleted=true rows to their lifecycle terminal value.
    for schema, table, set_clause in _PER_TABLE_MIGRATION:
        op.execute(f"UPDATE {schema}.{table} SET {set_clause} WHERE is_deleted = true")

    # 3. Drop is_deleted on all tables, and deleted_at only on tables that have it.
    for schema, table, _ in _PER_TABLE_MIGRATION:
        op.drop_column(table, "is_deleted", schema=schema)
        if (schema, table) in _TABLES_WITH_DELETED_AT:
            op.drop_column(table, "deleted_at", schema=schema)

    # 4. Drop deleted_by_id (the third column of the SoftDeleteMixin) from the
    #    14 SoftDeleteMixin-inheriting tables. The 4 tables that defined
    #    is_deleted directly do not have this column.
    for schema, table in _TABLES_WITH_DELETED_AT:
        op.drop_column(table, "deleted_by_id", schema=schema)


def downgrade() -> None:
    """DEV-ONLY downgrade. Lossy: reverses the schema but cannot perfectly
    distinguish soft-delete-via-status from legitimate status transitions.
    Production rollback is restore-from-backup."""
    # 0. Re-add deleted_by_id (matches original SoftDeleteMixin shape).
    #    The original column had ForeignKey('people.id'); we omit the FK
    #    constraint here because this downgrade is dev-only and the FK
    #    target may have evolved since the upgrade ran.
    for schema, table in _TABLES_WITH_DELETED_AT:
        op.add_column(
            table,
            sa.Column(
                "deleted_by_id",
                UUID(as_uuid=True),
                nullable=True,
            ),
            schema=schema,
        )

    # 1. Re-add is_deleted on all tables; re-add deleted_at where present.
    for schema, table, _ in _PER_TABLE_MIGRATION:
        op.add_column(
            table,
            sa.Column(
                "is_deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            schema=schema,
        )
        if (schema, table) in _TABLES_WITH_DELETED_AT:
            op.add_column(
                table,
                sa.Column(
                    "deleted_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                ),
                schema=schema,
            )

    # 2. Reverse-engineer is_deleted=true from the lifecycle field
    #    (LOSSY -- see module docstring).
    op.execute("UPDATE hr.employee SET is_deleted = true WHERE status = 'TERMINATED'")
    op.execute("UPDATE hr.department SET is_deleted = true WHERE is_active = false")
    op.execute("UPDATE hr.designation SET is_deleted = true WHERE is_active = false")
    op.execute(
        "UPDATE hr.employee_document SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE hr.employee_qualification SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE hr.employee_certification SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE hr.employee_dependent SET is_deleted = true WHERE is_active = false"
    )
    op.execute("UPDATE hr.skill SET is_deleted = true WHERE is_active = false")
    op.execute("UPDATE hr.competency SET is_deleted = true WHERE is_active = false")
    op.execute(
        "UPDATE hr.job_description SET is_deleted = true WHERE status = 'archived'"
    )
    op.execute(
        "UPDATE hr.disciplinary_case SET is_deleted = true WHERE status = 'WITHDRAWN'"
    )
    op.execute("UPDATE pm.task SET is_deleted = true WHERE status = 'CANCELLED'")
    op.execute("UPDATE pm.pm_comment SET is_deleted = true WHERE is_active = false")
    op.execute("UPDATE support.ticket SET is_deleted = true WHERE status = 'CLOSED'")
    op.execute(
        "UPDATE support.ticket_attachment SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE support.ticket_comment SET is_deleted = true WHERE is_active = false"
    )
    op.execute("UPDATE fleet.vehicle SET is_deleted = true WHERE status = 'DISPOSED'")
    op.execute(
        "UPDATE fleet.vehicle_incident SET is_deleted = true WHERE is_active = false"
    )

    # 3. Drop the new is_active columns.
    for schema, table in _TABLES_NEEDING_IS_ACTIVE:
        op.drop_column(table, "is_active", schema=schema)
