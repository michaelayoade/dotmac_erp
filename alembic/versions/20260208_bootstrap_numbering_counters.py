"""Bootstrap numbering counters from existing data.

Scans existing records (customers, employees, leave applications,
expense claims, salary slips, payroll entries) and initialises
``numbering_sequence`` rows so that newly generated numbers
do not collide with existing data.

Revision ID: 20260208_bootstrap_numbering_counters
Revises: 20260208_add_numbering_sequence_types
Create Date: 2026-02-08
"""

import sqlalchemy as sa

from alembic import op

revision = "20260208_bootstrap_numbering_counters"
down_revision = "20260208_add_numbering_sequence_types"
branch_labels = None
depends_on = None


def _upsert_sequence(
    conn: sa.engine.Connection,
    org_id: str,
    seq_type: str,
    prefix: str,
    separator: str,
    min_digits: int,
    include_year: bool,
    year_format: int,
    reset_frequency: str,
    current_number: int,
    current_year: int | None,
) -> None:
    """Insert a numbering_sequence row if one does not already exist."""
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM core_config.numbering_sequence "
            "WHERE organization_id = :org_id "
            "AND sequence_type = :seq_type"
        ),
        {"org_id": org_id, "seq_type": seq_type},
    ).first()
    if exists:
        # Update current_number if the existing counter is lower
        conn.execute(
            sa.text(
                "UPDATE core_config.numbering_sequence "
                "SET current_number = GREATEST(current_number, :num), "
                "    current_year = COALESCE(:yr, current_year) "
                "WHERE organization_id = :org_id "
                "AND sequence_type = :seq_type"
            ),
            {
                "org_id": org_id,
                "seq_type": seq_type,
                "num": current_number,
                "yr": current_year,
            },
        )
        return

    conn.execute(
        sa.text(
            "INSERT INTO core_config.numbering_sequence "
            "(sequence_id, organization_id, sequence_type, prefix, suffix, "
            " separator, min_digits, include_year, include_month, year_format, "
            " current_number, current_year, current_month, "
            " reset_frequency, fiscal_year_reset, created_at) "
            "VALUES (gen_random_uuid(), :org_id, :seq_type, :prefix, '', "
            " :separator, :min_digits, :include_year, false, :year_format, "
            " :num, :yr, NULL, "
            " :reset_frequency, false, now())"
        ),
        {
            "org_id": org_id,
            "seq_type": seq_type,
            "prefix": prefix,
            "separator": separator,
            "min_digits": min_digits,
            "include_year": include_year,
            "year_format": year_format,
            "num": current_number,
            "yr": current_year,
            "reset_frequency": reset_frequency,
        },
    )


def upgrade() -> None:
    conn = op.get_bind()

    # Get all organizations
    orgs = conn.execute(
        sa.text("SELECT organization_id FROM core_org.organization")
    ).fetchall()

    for (org_id,) in orgs:
        org_str = str(org_id)

        # --- CUSTOMER: CUST-00001 (no year, NEVER reset) ---
        max_cust = conn.execute(
            sa.text(
                "SELECT COALESCE(MAX("
                "  CAST(regexp_replace(customer_code, '^CUST-', '') AS INTEGER)"
                "), 0) "
                "FROM ar.customer "
                "WHERE organization_id = :org_id "
                "AND customer_code ~ '^CUST-[0-9]+$'"
            ),
            {"org_id": org_str},
        ).scalar()
        _upsert_sequence(
            conn,
            org_str,
            "CUSTOMER",
            "CUST",
            "-",
            5,
            include_year=False,
            year_format=4,
            reset_frequency="NEVER",
            current_number=max_cust or 0,
            current_year=None,
        )

        # --- EMPLOYEE: EMP-YYYY-NNNN (yearly reset) ---
        max_emp = conn.execute(
            sa.text(
                "SELECT COALESCE(MAX("
                "  CAST(regexp_replace("
                "    employee_code, '^EMP-[0-9]{4}-', ''"
                "  ) AS INTEGER)"
                "), 0) "
                "FROM hr.employee "
                "WHERE organization_id = :org_id "
                "AND employee_code ~ ('^EMP-' || EXTRACT(YEAR FROM now())::text || '-[0-9]+$')"
            ),
            {"org_id": org_str},
        ).scalar()
        _upsert_sequence(
            conn,
            org_str,
            "EMPLOYEE",
            "EMP-",
            "-",
            4,
            include_year=True,
            year_format=4,
            reset_frequency="YEARLY",
            current_number=max_emp or 0,
            current_year=conn.execute(
                sa.text("SELECT EXTRACT(YEAR FROM now())::int")
            ).scalar(),
        )

        # --- LEAVE_APPLICATION: LV-YYYY-NNNNN (yearly reset) ---
        max_lv = conn.execute(
            sa.text(
                "SELECT COALESCE(MAX("
                "  CAST(regexp_replace("
                "    application_number, '^LV-[0-9]{4}-', ''"
                "  ) AS INTEGER)"
                "), 0) "
                "FROM leave.leave_application "
                "WHERE organization_id = :org_id "
                "AND application_number ~ ('^LV-' || EXTRACT(YEAR FROM now())::text || '-[0-9]+$')"
            ),
            {"org_id": org_str},
        ).scalar()
        current_year = conn.execute(
            sa.text("SELECT EXTRACT(YEAR FROM now())::int")
        ).scalar()
        _upsert_sequence(
            conn,
            org_str,
            "LEAVE_APPLICATION",
            "LV-",
            "-",
            5,
            include_year=True,
            year_format=4,
            reset_frequency="YEARLY",
            current_number=max_lv or 0,
            current_year=current_year,
        )

        # --- EXPENSE: EXP-YYYY-NNNNN (yearly reset) ---
        # Check PG sequence value first, then compare with table MAX
        max_exp_table = conn.execute(
            sa.text(
                "SELECT COALESCE(MAX("
                "  CAST(regexp_replace("
                "    claim_number, '^EXP-[0-9]{4}-', ''"
                "  ) AS INTEGER)"
                "), 0) "
                "FROM expense.expense_claim "
                "WHERE organization_id = :org_id "
                "AND claim_number ~ ('^EXP-' || EXTRACT(YEAR FROM now())::text || '-[0-9]+$')"
            ),
            {"org_id": org_str},
        ).scalar()
        # Also check raw PG sequence if it exists
        max_exp_seq = 0
        try:
            max_exp_seq = (
                conn.execute(
                    sa.text("SELECT last_value FROM expense.expense_claim_number_seq")
                ).scalar()
                or 0
            )
        except sa.exc.ProgrammingError:
            pass  # Sequence may not exist
        max_exp = max(max_exp_table or 0, max_exp_seq)
        _upsert_sequence(
            conn,
            org_str,
            "EXPENSE",
            "EXP-",
            "-",
            5,
            include_year=True,
            year_format=4,
            reset_frequency="YEARLY",
            current_number=max_exp,
            current_year=current_year,
        )

        # --- EXPENSE_INVOICE: EXP-INV-YYYY-NNNNN (yearly reset) ---
        max_einv_table = conn.execute(
            sa.text(
                "SELECT COALESCE(MAX("
                "  CAST(regexp_replace("
                "    invoice_number, '^EXP-INV-[0-9]{4}-', ''"
                "  ) AS INTEGER)"
                "), 0) "
                "FROM ap.supplier_invoice "
                "WHERE organization_id = :org_id "
                "AND invoice_number ~ ('^EXP-INV-' || EXTRACT(YEAR FROM now())::text || '-[0-9]+$')"
            ),
            {"org_id": org_str},
        ).scalar()
        max_einv_seq = 0
        try:
            max_einv_seq = (
                conn.execute(
                    sa.text(
                        "SELECT last_value FROM expense.expense_supplier_invoice_number_seq"
                    )
                ).scalar()
                or 0
            )
        except sa.exc.ProgrammingError:
            pass
        max_einv = max(max_einv_table or 0, max_einv_seq)
        _upsert_sequence(
            conn,
            org_str,
            "EXPENSE_INVOICE",
            "EXP-INV-",
            "-",
            5,
            include_year=True,
            year_format=4,
            reset_frequency="YEARLY",
            current_number=max_einv,
            current_year=current_year,
        )

        # --- SALARY_SLIP: SLIP-YYYY-NNNNN (yearly reset) ---
        max_slip = conn.execute(
            sa.text(
                "SELECT COALESCE(MAX(sequence_number), 0) "
                "FROM people.payroll_number_sequence "
                "WHERE organization_id = :org_id "
                "AND prefix = 'SLIP' "
                "AND year = EXTRACT(YEAR FROM now())::int"
            ),
            {"org_id": org_str},
        ).scalar()
        _upsert_sequence(
            conn,
            org_str,
            "SALARY_SLIP",
            "SLIP-",
            "-",
            5,
            include_year=True,
            year_format=4,
            reset_frequency="YEARLY",
            current_number=max_slip or 0,
            current_year=current_year,
        )

        # --- PAYROLL_ENTRY: PAY-YYYY-NNNN (yearly reset) ---
        max_pay = conn.execute(
            sa.text(
                "SELECT COALESCE(MAX(sequence_number), 0) "
                "FROM people.payroll_number_sequence "
                "WHERE organization_id = :org_id "
                "AND prefix = 'PAY' "
                "AND year = EXTRACT(YEAR FROM now())::int"
            ),
            {"org_id": org_str},
        ).scalar()
        _upsert_sequence(
            conn,
            org_str,
            "PAYROLL_ENTRY",
            "PAY-",
            "-",
            4,
            include_year=True,
            year_format=4,
            reset_frequency="YEARLY",
            current_number=max_pay or 0,
            current_year=current_year,
        )


def downgrade() -> None:
    conn = op.get_bind()
    # Remove the bootstrapped rows for the new types only
    for seq_type in [
        "CUSTOMER",
        "EMPLOYEE",
        "LEAVE_APPLICATION",
        "SALARY_SLIP",
        "PAYROLL_ENTRY",
        "EXPENSE_INVOICE",
    ]:
        conn.execute(
            sa.text(
                "DELETE FROM core_config.numbering_sequence "
                "WHERE sequence_type = :seq_type"
            ),
            {"seq_type": seq_type},
        )
