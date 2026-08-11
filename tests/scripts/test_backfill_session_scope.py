from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock
from uuid import UUID


@contextmanager
def _session(db):
    yield db


def test_gl_backfill_discovers_globally_then_reports_per_org(monkeypatch):
    from scripts import backfill_gl_postings

    org_ids = [
        UUID("00000000-0000-0000-0000-000000000041"),
        UUID("00000000-0000-0000-0000-000000000042"),
    ]
    cross_db = MagicMock()
    cross_db.scalars.return_value.all.return_value = org_ids
    tenant_db = MagicMock()
    captured_org_ids: list[UUID] = []

    monkeypatch.setattr(
        backfill_gl_postings.sys,
        "argv",
        ["backfill_gl_postings.py", "--dry-run"],
    )
    monkeypatch.setattr(
        backfill_gl_postings,
        "cross_org_session",
        lambda: _session(cross_db),
    )

    def session_for_org(org_id):
        captured_org_ids.append(org_id)
        return _session(tenant_db)

    monkeypatch.setattr(backfill_gl_postings, "session_for_org", session_for_org)
    monkeypatch.setattr(
        backfill_gl_postings,
        "count_missing_gl",
        lambda db, org_id: {
            entity_type: {"missing_gl": 0, "total_posted": 0}
            for entity_type in backfill_gl_postings.ENTITY_TYPES
        },
    )

    backfill_gl_postings.main()

    assert captured_org_ids == org_ids


def test_inventory_backfill_discovers_globally_then_reports_per_org(monkeypatch):
    from scripts import backfill_inventory_gl_postings

    org_ids = [
        UUID("00000000-0000-0000-0000-000000000041"),
        UUID("00000000-0000-0000-0000-000000000042"),
    ]
    cross_db = MagicMock()
    cross_db.scalars.return_value.all.return_value = org_ids
    tenant_db = MagicMock()
    captured_org_ids: list[UUID] = []

    monkeypatch.setattr(
        backfill_inventory_gl_postings.sys,
        "argv",
        ["backfill_inventory_gl_postings.py", "--dry-run"],
    )
    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "cross_org_session",
        lambda: _session(cross_db),
    )

    def session_for_org(org_id):
        captured_org_ids.append(org_id)
        return _session(tenant_db)

    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "session_for_org",
        session_for_org,
    )
    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "count_missing_inventory_gl",
        lambda db, org_id: {"total": 0, "missing_gl": 0, "zero_cost_missing": 0},
    )
    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "load_candidates",
        lambda *args, **kwargs: [],
    )

    backfill_inventory_gl_postings.main()

    assert captured_org_ids == org_ids


def test_inventory_backfill_applies_batch_size_once_across_all_orgs(monkeypatch):
    from scripts import backfill_inventory_gl_postings

    org_ids = [
        UUID("00000000-0000-0000-0000-000000000041"),
        UUID("00000000-0000-0000-0000-000000000042"),
    ]
    cross_db = MagicMock()
    cross_db.scalars.return_value.all.return_value = org_ids
    candidate_limits: list[tuple[UUID, int]] = []
    processed: list[int] = []

    monkeypatch.setattr(
        backfill_inventory_gl_postings.sys,
        "argv",
        [
            "backfill_inventory_gl_postings.py",
            "--execute",
            "--batch-size",
            "3",
        ],
    )
    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "cross_org_session",
        lambda: _session(cross_db),
    )
    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "session_for_org",
        lambda org_id: _session(MagicMock()),
    )
    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "count_missing_inventory_gl",
        lambda db, org_id: {"total": 2, "missing_gl": 2, "zero_cost_missing": 0},
    )
    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "prepare_inventory_fiscal_periods",
        lambda db, org_id, transaction_type: [],
    )

    def load_candidates(db, *, batch_size, org_id, transaction_type):
        candidate_limits.append((org_id, batch_size))
        return [object()] * min(2, batch_size)

    def process_batch(db, candidates):
        processed.append(len(candidates))
        return {
            "total": len(candidates),
            "posted": len(candidates),
            "failed": 0,
            "skipped_zero_cost": 0,
        }

    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "load_candidates",
        load_candidates,
    )
    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "process_batch",
        process_batch,
    )
    monkeypatch.setattr(
        backfill_inventory_gl_postings,
        "restore_inventory_fiscal_periods",
        lambda db, restores: None,
    )

    backfill_inventory_gl_postings.main()

    assert candidate_limits == [(org_ids[0], 3), (org_ids[1], 1)]
    assert sum(processed) == 3
