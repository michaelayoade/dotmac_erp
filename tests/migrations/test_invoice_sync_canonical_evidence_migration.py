"""The rename+freeze+recreate migration touches zero rows.

A naive substring check on the bare keywords ``INSERT``/``UPDATE``/``DELETE``
would false-positive on this migration's legitimate
``REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE ... FROM app_user``
statements, where those words are a GRANT/REVOKE privilege list, not a DML
statement. The detector below matches the actual statement-opening forms
(``INSERT INTO``, ``UPDATE <table> SET``, ``DELETE FROM``) instead, so it can
tell a privilege clause from a real mutation.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("alembic/versions/20260918_invoice_sync_canonical_evidence.py")

# Real DML statement-opening forms, case-insensitive. Deliberately NOT a bare
# `"INSERT" in source` check — see the module docstring and
# test_the_dml_detector_is_sensitive below.
_INSERT_INTO = re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE)
_UPDATE_SET = re.compile(r"\bUPDATE\s+[\w.\"]+\s+SET\b", re.IGNORECASE)
_DELETE_FROM = re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE)


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_extends_the_real_current_head() -> None:
    source = _source()
    assert 'revision = "20260918_invoice_sync_canonical_evidence"' in source
    assert 'down_revision = "20260915_merge_workforce_kpi"' in source


def test_migration_contains_no_real_dml_statements() -> None:
    source = _source()
    assert not _INSERT_INTO.search(source), "found a real INSERT INTO statement"
    assert not _UPDATE_SET.search(source), "found a real UPDATE ... SET statement"
    assert not _DELETE_FROM.search(source), "found a real DELETE FROM statement"


def test_the_dml_detector_is_sensitive() -> None:
    """Sensitivity proof (ADR-0018): prove the detector on both sides.

    A defect: a real DML statement is present and the detector must catch it.
    A near-miss: the migration's actual REVOKE privilege list, which contains
    the bare words INSERT/UPDATE/DELETE and must NOT be flagged.
    """
    planted_defect = "op.execute('DELETE FROM ar.dotmac_sub_invoice_sync_outcome')"
    assert _DELETE_FROM.search(planted_defect), (
        "the detector fails to catch a planted real DELETE FROM statement"
    )

    near_miss = (
        "op.execute(\n"
        "    'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE '\n"
        "    'ar.dotmac_sub_invoice_sync_outcome_legacy FROM app_user'\n"
        ")"
    )
    assert not _INSERT_INTO.search(near_miss)
    assert not _UPDATE_SET.search(near_miss)
    assert not _DELETE_FROM.search(near_miss)

    # And the real migration text does contain that exact privilege-list
    # near-miss, which a naive bare-keyword check would have flagged.
    source = _source()
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE" in source


def test_migration_renames_both_live_tables_to_the_frozen_archive() -> None:
    source = _source()
    assert 'LEGACY_OUTCOME_TABLE = "dotmac_sub_invoice_sync_outcome_legacy"' in source
    assert 'LEGACY_ISSUE_TABLE = "dotmac_sub_invoice_sync_issue_legacy"' in source
    assert "RENAME TO {LEGACY_OUTCOME_TABLE}" in source
    assert "RENAME TO {LEGACY_ISSUE_TABLE}" in source


def test_migration_creates_the_fresh_canonical_tables_with_digest_version() -> None:
    source = _source()
    assert '"digest_version"' in source
    assert "sa.SmallInteger()" in source
    assert "uq_sub_invoice_outcome_org_revision_digest" in source
    assert "ck_sub_invoice_outcome_digest_version" in source
    assert "projection_fingerprint = lower(projection_fingerprint)" in source


def test_migration_freezes_the_archive_and_documents_why() -> None:
    source = _source()
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE" in source
    assert "FROZEN_COMMENT" in source
    assert "COMMENT ON TABLE" in source


def test_downgrade_restores_original_names_and_regrants_privileges() -> None:
    source = _source()
    # downgrade() must exist and re-GRANT — the REVOKE in upgrade() does not
    # auto-reverse on rename.
    assert "def downgrade() -> None:" in source
    downgrade_source = source.split("def downgrade() -> None:", 1)[1]
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE" in downgrade_source
    assert "RENAME TO {OUTCOME_TABLE}" in downgrade_source
    assert "RENAME TO {ISSUE_TABLE}" in downgrade_source
    assert "COMMENT ON TABLE" in downgrade_source
