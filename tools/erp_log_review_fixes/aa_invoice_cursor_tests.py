"""Preserve existing cursor assertions while testing the split IO boundary."""

from pathlib import Path

BRANCH = "fix/erp-invoice-quarantine-io-budget"
START = "0c083f8c49fc1f3d72c3065a91a4c8c5a7515f12"
TITLE = "test(sync): exercise quarantine fetch and persistence separately"
TESTS = ["tests/services/test_invoice_quarantine_io_boundaries.py"]
TESTS += [str(path) for path in Path("tests/services").glob("*invoice*.py") if "dotmac_sub" in path.name]


def apply(change, write, rewrite):
    path = "tests/services/test_dotmac_sub_invoice_cursor.py"

    def split_success(source):
        source = source.replace(
            '"record_blocked_invoice_accounting_revision", quarantine',
            '"record_invoice_sync_outcome", quarantine',
        )
        source = source.replace(
            "    result = harness.sync_invoices(batch_size=10)",
            '''    command = object()
    evidence = MagicMock(return_value=command)
    monkeypatch.setattr(invoices_module, "fetch_blocked_invoice_accounting_revision", evidence)

    result = harness.sync_invoices(batch_size=10)''',
        )
        old = '''    quarantine.assert_called_once_with(
        harness.db,
        harness.client,'''
        if old in source:
            source = source.replace(old, '''    quarantine.assert_called_once_with(harness.db, command)
    evidence.assert_called_once_with(
        harness.client,''')
        else:
            source = source.replace(
                "    assert quarantine.call_count == 1",
                "    quarantine.assert_called_once_with(harness.db, command)\n    assert evidence.call_count == 1",
            )
        return source

    rewrite(path, "test_invoice_mismatch_is_logged_and_quarantined_by_source_revision", split_success)
    rewrite(path, "test_invoice_quarantine_evidence_work_is_bounded", split_success)
    rewrite(
        path,
        "test_invoice_mismatch_parks_cursor_without_durable_v2_evidence",
        lambda source: source.replace(
            '"record_blocked_invoice_accounting_revision"',
            '"fetch_blocked_invoice_accounting_revision"',
        ),
    )
