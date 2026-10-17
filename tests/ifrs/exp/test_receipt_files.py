"""Expense receipt staging policy and entry parsing.

The staging call itself requires PostgreSQL (the cleanup-key
reservation lock), so it is exercised by the PostgreSQL integration
lane; these tests pin the policy equivalence with the legacy upload
config and the receipt-entry parsing the download path relies on.
"""

from __future__ import annotations

import uuid

from app.services.expense.receipt_files import receipt_file_id, receipt_policy
from app.services.file_upload import get_expense_receipt_upload


def test_receipt_policy_matches_the_legacy_upload_config() -> None:
    config = get_expense_receipt_upload().config
    policy = receipt_policy()

    assert policy.max_bytes == config.max_size_bytes
    assert policy.allowed_extensions == frozenset(config.allowed_extensions)
    assert policy.allowed_media_types == frozenset(config.allowed_content_types)


def test_receipt_file_id_parses_only_bare_uuid_entries() -> None:
    file_id = uuid.uuid4()

    assert receipt_file_id(str(file_id)) == file_id
    assert receipt_file_id(f"  {file_id}  ") == file_id
    assert receipt_file_id("expense_receipts/org/receipt.jpg") is None
    assert receipt_file_id("/app/uploads/expense_receipts/receipt.jpg") is None
    assert receipt_file_id("https://example.com/receipt.jpg") is None
    assert receipt_file_id("") is None
