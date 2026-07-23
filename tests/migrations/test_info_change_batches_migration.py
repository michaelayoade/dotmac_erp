from pathlib import Path


def test_info_change_batches_migration_contains_batch_table_and_child_columns():
    migration = Path("alembic/versions/20260722_add_info_change_batches.py").read_text(
        encoding="utf-8"
    )

    assert 'op.create_table(\n            "employee_info_change_batch"' in migration
    assert '"batch_id"' in migration
    assert '"batch_item_order"' in migration
    assert '"content_checksum"' in migration
    assert '"fk_info_change_request_batch_id"' in migration
