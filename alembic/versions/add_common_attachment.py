"""Add common schema and attachment table.

Revision ID: add_common_attachment
Revises: add_expense_cost_allocation
Create Date: 2025-01-10
"""

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "add_common_attachment"
down_revision = "add_expense_cost_allocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind,
        "attachment_category",
        "INVOICE",
        "RECEIPT",
        "CONTRACT",
        "PURCHASE_ORDER",
        "GOODS_RECEIPT",
        "PAYMENT",
        "QUOTE",
        "CREDIT_NOTE",
        "EXPENSE",
        "JOURNAL",
        "BANK_STATEMENT",
        "TAX_DOCUMENT",
        "SUPPLIER",
        "CUSTOMER",
        "OTHER",
    )

    statements = [
        """CREATE SCHEMA IF NOT EXISTS common;""",
        """CREATE TABLE common.attachment (
	attachment_id UUID DEFAULT gen_random_uuid() NOT NULL, 
	organization_id UUID NOT NULL, 
	entity_type VARCHAR(50) NOT NULL, 
	entity_id UUID NOT NULL, 
	file_name VARCHAR(255) NOT NULL, 
	file_path VARCHAR(500) NOT NULL, 
	file_size BIGINT NOT NULL, 
	content_type VARCHAR(100) NOT NULL, 
	category attachment_category NOT NULL, 
	description TEXT, 
	storage_provider VARCHAR(20) NOT NULL, 
	checksum VARCHAR(64), 
	uploaded_by UUID NOT NULL, 
	uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (attachment_id)
);""",
        """COMMENT ON COLUMN common.attachment.entity_type IS 'Type of entity: SUPPLIER_INVOICE, PURCHASE_ORDER, GOODS_RECEIPT, etc.';""",
        """COMMENT ON COLUMN common.attachment.entity_id IS 'ID of the related entity';""",
        """COMMENT ON COLUMN common.attachment.file_name IS 'Original file name';""",
        """COMMENT ON COLUMN common.attachment.file_path IS 'Storage path (relative path or S3 key)';""",
        """COMMENT ON COLUMN common.attachment.file_size IS 'File size in bytes';""",
        """COMMENT ON COLUMN common.attachment.content_type IS 'MIME type';""",
        """COMMENT ON COLUMN common.attachment.description IS 'User-provided description';""",
        """COMMENT ON COLUMN common.attachment.storage_provider IS 'Storage backend: LOCAL, S3, AZURE_BLOB, GCS';""",
        """COMMENT ON COLUMN common.attachment.checksum IS 'SHA-256 hash for integrity verification';""",
        """CREATE INDEX idx_attachment_category ON common.attachment (organization_id, category);""",
        """CREATE INDEX idx_attachment_entity ON common.attachment (organization_id, entity_type, entity_id);""",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """DROP TABLE IF EXISTS common.attachment CASCADE;""",
        """DROP TYPE IF EXISTS attachment_category CASCADE;""",
        """DROP SCHEMA IF EXISTS common CASCADE;""",
    ]
    for statement in statements:
        op.execute(statement)
