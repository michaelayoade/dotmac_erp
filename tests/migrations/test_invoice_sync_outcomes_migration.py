from __future__ import annotations

from pathlib import Path


MIGRATION = Path("alembic/versions/20260906_invoice_sync_outcomes.py")


def test_invoice_sync_outcome_migration_extends_current_erp_head() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260906_invoice_sync_outcomes"' in source
    assert 'down_revision = "20260905_selfcare_mapping_unique"' in source


def test_both_evidence_tables_are_tenant_protected_and_granted() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "public.app_current_tenant_id()" in source
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in source
    assert 'OUTCOME_TABLE = "dotmac_sub_invoice_sync_outcome"' in source
    assert 'ISSUE_TABLE = "dotmac_sub_invoice_sync_issue"' in source


def test_migration_does_not_backfill_or_delete_financial_data() -> None:
    source = MIGRATION.read_text(encoding="utf-8").upper()
    assert "UPDATE AR.INVOICE" not in source
    assert "DELETE FROM" not in source
