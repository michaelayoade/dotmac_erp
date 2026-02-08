"""Add pfa_directory table for pension fund administrator lookups.

Revision ID: 20260130_add_pfa_directory
Revises: 20260130_add_bank_directory
Create Date: 2026-01-30

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260130_add_pfa_directory"
down_revision = "20260130_add_bank_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create pfa_directory table
    op.create_table(
        "pfa_directory",
        sa.Column(
            "pfa_code",
            sa.String(10),
            primary_key=True,
            comment="PenCom-assigned PFA code",
        ),
        sa.Column(
            "pfa_name",
            sa.String(150),
            nullable=False,
            unique=True,
            comment="Official PFA name",
        ),
        sa.Column(
            "short_name",
            sa.String(50),
            nullable=True,
            comment="Common short name or abbreviation",
        ),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.String(100)),
            nullable=True,
            comment="Alternative names for fuzzy matching",
        ),
        sa.Column("website", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="core_org",
    )

    # Create index on pfa_name for lookups
    op.create_index(
        "ix_pfa_directory_pfa_name",
        "pfa_directory",
        ["pfa_name"],
        schema="core_org",
    )

    # Seed with Nigerian PFAs (PenCom licensed as of 2025)
    op.execute("""
        INSERT INTO core_org.pfa_directory (pfa_code, pfa_name, short_name, aliases)
        VALUES
            ('001', 'ARM Pension Managers Limited', 'ARM Pensions', ARRAY['ARM', 'ARM Pension']),
            ('002', 'Crusader Sterling Pensions Limited', 'Crusader Pensions', ARRAY['Crusader', 'Crusader Sterling']),
            ('003', 'First Guarantee Pension Limited', 'First Guarantee', ARRAY['FGPL', 'First Guarantee Pension']),
            ('004', 'FCMB Pensions Limited', 'FCMB Pensions', ARRAY['FCMB Pension']),
            ('005', 'Fidelity Pension Managers Limited', 'Fidelity Pensions', ARRAY['Fidelity Pension']),
            ('006', 'IEI-Anchor Pension Managers Limited', 'IEI-Anchor', ARRAY['IEI Anchor', 'Anchor Pensions']),
            ('007', 'Leadway Pensure PFA Limited', 'Leadway Pensure', ARRAY['Leadway', 'Pensure']),
            ('008', 'NLPC Pension Fund Administrators Limited', 'NLPC Pensions', ARRAY['NLPC', 'NLPC Pension']),
            ('009', 'NPF Pensions Limited', 'NPF Pensions', ARRAY['NPF', 'Nigerian Police Force Pension']),
            ('010', 'OAK Pensions Limited', 'OAK Pensions', ARRAY['OAK', 'Oak Pension']),
            ('011', 'Pensions Alliance Limited', 'PAL Pensions', ARRAY['PAL', 'Pensions Alliance']),
            ('012', 'Premium Pension Limited', 'Premium Pensions', ARRAY['Premium', 'Premium Pension']),
            ('013', 'Radix Pension Managers Limited', 'Radix Pensions', ARRAY['Radix', 'Radix Pension']),
            ('014', 'Sigma Pensions Limited', 'Sigma Pensions', ARRAY['Sigma', 'Sigma Pension']),
            ('015', 'Stanbic IBTC Pension Managers Limited', 'Stanbic IBTC Pensions', ARRAY['Stanbic IBTC', 'Stanbic Pension', 'IBTC Pensions']),
            ('016', 'Tangerine APT Pensions Limited', 'Tangerine Pensions', ARRAY['Tangerine', 'APT Pensions', 'Tangerine APT']),
            ('017', 'Trustfund Pensions Limited', 'Trustfund Pensions', ARRAY['Trustfund', 'Trust Fund Pension']),
            ('018', 'Veritas Glanvills Pensions Limited', 'Veritas Glanvills', ARRAY['Veritas', 'Glanvills', 'VG Pensions']),
            ('019', 'Access Pensions Limited', 'Access Pensions', ARRAY['Access Pension']),
            ('020', 'Nigerian University Pension Management Company', 'NUPEMCO', ARRAY['NUPEMCO Pension']),
            ('021', 'Investment One Pension Managers Limited', 'Investment One', ARRAY['Investment One Pension']),
            ('022', 'Norrenberger Pensions Limited', 'Norrenberger Pensions', ARRAY['Norrenberger', 'Norrenberger Pension'])
        ON CONFLICT (pfa_code) DO NOTHING;
    """)


def downgrade() -> None:
    op.drop_index(
        "ix_pfa_directory_pfa_name", table_name="pfa_directory", schema="core_org"
    )
    op.drop_table("pfa_directory", schema="core_org")
