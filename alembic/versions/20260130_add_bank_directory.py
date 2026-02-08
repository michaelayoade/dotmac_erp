"""Add bank_directory table for bank code lookups.

Revision ID: 20260130_add_bank_directory
Revises: 20260130_add_scheduling_audit_ids
Create Date: 2026-01-30

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260130_add_bank_directory"
down_revision = "20260130_merge_statutory_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create bank_directory table
    op.create_table(
        "bank_directory",
        sa.Column(
            "bank_code",
            sa.String(10),
            primary_key=True,
            comment="CBN/NIBSS bank code",
        ),
        sa.Column(
            "bank_name",
            sa.String(100),
            nullable=False,
            unique=True,
            comment="Official bank name",
        ),
        sa.Column(
            "nibss_code",
            sa.String(20),
            nullable=True,
            comment="NIBSS institution code if different from bank_code",
        ),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.String(100)),
            nullable=True,
            comment="Alternative names for fuzzy matching",
        ),
        sa.Column(
            "sort_code_prefix",
            sa.String(10),
            nullable=True,
            comment="Bank sort code prefix",
        ),
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

    # Create index on bank_name for lookups
    op.create_index(
        "ix_bank_directory_bank_name",
        "bank_directory",
        ["bank_name"],
        schema="core_org",
    )

    # Seed with major Nigerian banks
    op.execute("""
        INSERT INTO core_org.bank_directory (bank_code, bank_name, nibss_code, aliases, sort_code_prefix)
        VALUES
            ('044', 'Access Bank', '044', ARRAY['Access', 'Access Bank Plc', 'Access Bank Nigeria'], '044'),
            ('023', 'Citibank Nigeria', '023', ARRAY['Citibank', 'Citi'], '023'),
            ('050', 'Ecobank Nigeria', '050', ARRAY['Ecobank', 'Eco Bank'], '050'),
            ('084', 'Enterprise Bank', '084', ARRAY['Enterprise'], '084'),
            ('070', 'Fidelity Bank', '070', ARRAY['Fidelity', 'Fidelity Bank Plc'], '070'),
            ('011', 'First Bank of Nigeria', '011', ARRAY['First Bank', 'FBN', 'FirstBank'], '011'),
            ('214', 'First City Monument Bank', '214', ARRAY['FCMB', 'First City Monument'], '214'),
            ('058', 'Guaranty Trust Bank', '058', ARRAY['GTBank', 'GT Bank', 'GTB', 'Guaranty Trust'], '058'),
            ('030', 'Heritage Bank', '030', ARRAY['Heritage'], '030'),
            ('301', 'Jaiz Bank', '301', ARRAY['Jaiz'], '301'),
            ('082', 'Keystone Bank', '082', ARRAY['Keystone'], '082'),
            ('076', 'Polaris Bank', '076', ARRAY['Polaris', 'Skye Bank'], '076'),
            ('101', 'Providus Bank', '101', ARRAY['Providus'], '101'),
            ('221', 'Stanbic IBTC Bank', '221', ARRAY['Stanbic IBTC', 'Stanbic', 'IBTC'], '221'),
            ('068', 'Standard Chartered Bank', '068', ARRAY['Standard Chartered', 'StanChart'], '068'),
            ('232', 'Sterling Bank', '232', ARRAY['Sterling', 'Sterling Bank Plc'], '232'),
            ('100', 'Suntrust Bank', '100', ARRAY['Suntrust', 'Sun Trust'], '100'),
            ('032', 'Union Bank of Nigeria', '032', ARRAY['Union Bank', 'Union'], '032'),
            ('033', 'United Bank for Africa', '033', ARRAY['UBA', 'United Bank'], '033'),
            ('215', 'Unity Bank', '215', ARRAY['Unity'], '215'),
            ('035', 'Wema Bank', '035', ARRAY['Wema', 'ALAT'], '035'),
            ('057', 'Zenith Bank', '057', ARRAY['Zenith', 'Zenith Bank Plc'], '057'),
            ('304', 'Stanbic Mobile', '304', ARRAY['Stanbic Mobile Money'], NULL),
            ('305', 'Paycom', '305', ARRAY['Opay', 'OPay'], NULL),
            ('306', 'eTranzact', '306', ARRAY['eTranzact'], NULL),
            ('307', 'Ecobank Xpress Account', '307', ARRAY['Ecobank Xpress'], NULL),
            ('309', 'FBN Mobile', '309', ARRAY['FirstMobile', 'FBN Mobile'], NULL),
            ('311', 'Parkway ReadyCash', '311', ARRAY['ReadyCash', 'Parkway'], NULL),
            ('322', 'Zenith Mobile', '322', ARRAY['Zenith eaZymoney'], NULL),
            ('323', 'Access Mobile', '323', ARRAY['Access Money'], NULL),
            ('401', 'ASO Savings', '401', ARRAY['ASO Savings and Loans'], NULL),
            ('403', 'Jubilee Life', '403', ARRAY['Jubilee Life Mortgage Bank'], NULL),
            ('415', 'Imperial Homes Mortgage', '415', ARRAY['Imperial Homes'], NULL),
            ('501', 'Fortis Microfinance Bank', '501', ARRAY['Fortis MFB'], NULL),
            ('090110', 'VFD Microfinance Bank', '090110', ARRAY['VFD MFB', 'VFD'], NULL),
            ('090267', 'Kuda Microfinance Bank', '090267', ARRAY['Kuda', 'Kuda Bank', 'Kuda MFB'], NULL),
            ('090405', 'Moniepoint Microfinance Bank', '090405', ARRAY['Moniepoint', 'Moniepoint MFB'], NULL),
            ('090303', 'PalmPay', '090303', ARRAY['Palm Pay'], NULL),
            ('999991', 'Carbon', '999991', ARRAY['Carbon MFB', 'Paylater'], NULL),
            ('999992', 'FairMoney', '999992', ARRAY['Fair Money', 'FairMoney MFB'], NULL)
        ON CONFLICT (bank_code) DO NOTHING;
    """)


def downgrade() -> None:
    op.drop_index(
        "ix_bank_directory_bank_name", table_name="bank_directory", schema="core_org"
    )
    op.drop_table("bank_directory", schema="core_org")
