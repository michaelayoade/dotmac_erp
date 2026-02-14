"""
Tests for 3-tier Splynx sync tasks.

Covers:
- Tier 1: Incremental sync (customers + recent invoices/payments/credit notes)
- Tier 2: Daily reconciliation (unpaid/partial invoices, 30-day payment window)
- Tier 3: Full reconciliation (all entities, no filters)
- Shared helpers (_build_sync_context, _get_last_sync_at)
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from app.services.splynx.sync import SyncResult
from app.tasks.splynx import (
    _DAILY_RECON_PAYMENT_DAYS,
    _INCREMENTAL_FALLBACK_DAYS,
    _INCREMENTAL_OVERLAP_HOURS,
    _build_sync_context,
    run_splynx_daily_reconciliation,
    run_splynx_full_reconciliation,
    run_splynx_incremental_sync,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

ORG_ID = uuid.uuid4()
HISTORY_ID = uuid.uuid4()
AR_ACCOUNT = uuid.uuid4()
REVENUE_ACCOUNT = uuid.uuid4()

_MODULE = "app.tasks.splynx"


def _ok_result(entity_type: str) -> SyncResult:
    return SyncResult(
        success=True, entity_type=entity_type, created=5, updated=2, skipped=10
    )


def _make_mock_org() -> MagicMock:
    org = MagicMock()
    org.is_active = True
    return org


def _make_mock_history() -> MagicMock:
    h = MagicMock()
    h.history_id = HISTORY_ID
    h.error_count = 0
    h.synced_count = 0
    h.skipped_count = 0
    h.total_records = 0
    return h


def _make_mock_service() -> MagicMock:
    svc = MagicMock()
    svc.sync_customers.return_value = _ok_result("customers")
    svc.sync_invoices.return_value = _ok_result("invoices")
    svc.sync_payments.return_value = _ok_result("payments")
    svc.sync_credit_notes.return_value = _ok_result("credit_notes")
    svc.close.return_value = None
    return svc


@contextmanager
def _mock_session_local(mock_db: MagicMock):  # type: ignore[no-untyped-def]
    """Yield a context manager that returns mock_db."""
    yield mock_db


def _setup_patches(
    mock_db: MagicMock,
    mock_service: MagicMock,
    *,
    org_id: uuid.UUID = ORG_ID,
    last_sync: datetime | None = None,
) -> dict[str, Any]:
    """Return dict of patch targets → side_effects for a typical happy-path task invocation."""
    mock_db.get.side_effect = lambda model, pk: (
        _make_mock_org() if model.__name__ == "Organization" else _make_mock_history()
    )
    mock_db.scalar.return_value = (
        AR_ACCOUNT  # first call for AR control, reused for revenue
    )

    # patch.multiple takes short attribute names (not full dotted paths)
    return {
        "SessionLocal": lambda: _mock_session_local(mock_db),
        "_resolve_org_id": lambda x: org_id,
        "SplynxConfig": MagicMock(
            from_settings=MagicMock(
                return_value=MagicMock(is_configured=MagicMock(return_value=True))
            )
        ),
        "_resolve_ar_control_account": lambda db, oid: AR_ACCOUNT,
        "_resolve_default_revenue_account": lambda db, oid: REVENUE_ACCOUNT,
        "SplynxSyncService": lambda **kwargs: mock_service,
        "_get_last_sync_at": lambda db, oid, marker: last_sync,
    }


# ---------------------------------------------------------------------------
# _build_sync_context tests
# ---------------------------------------------------------------------------


class TestBuildSyncContext:
    """Tests for the shared _build_sync_context helper."""

    def test_missing_org_returns_error(self) -> None:
        mock_db = MagicMock()
        with patch(f"{_MODULE}._resolve_org_id", return_value=None):
            result = _build_sync_context(mock_db, None, MagicMock(), ["test"])
        assert isinstance(result, dict)
        assert result["success"] is False
        assert "organization ID" in result["error"]

    def test_unconfigured_splynx_returns_error(self) -> None:
        mock_db = MagicMock()
        mock_config = MagicMock()
        mock_config.is_configured.return_value = False
        with (
            patch(f"{_MODULE}._resolve_org_id", return_value=ORG_ID),
            patch(f"{_MODULE}.SplynxConfig") as MockConfig,
        ):
            MockConfig.from_settings.return_value = mock_config
            result = _build_sync_context(mock_db, str(ORG_ID), MagicMock(), ["test"])
        assert isinstance(result, dict)
        assert result["success"] is False
        assert "not configured" in result["error"]

    def test_inactive_org_returns_error(self) -> None:
        mock_db = MagicMock()
        inactive_org = MagicMock()
        inactive_org.is_active = False
        mock_db.get.return_value = inactive_org
        mock_config = MagicMock()
        mock_config.is_configured.return_value = True
        with (
            patch(f"{_MODULE}._resolve_org_id", return_value=ORG_ID),
            patch(f"{_MODULE}.SplynxConfig") as MockConfig,
        ):
            MockConfig.from_settings.return_value = mock_config
            result = _build_sync_context(mock_db, str(ORG_ID), MagicMock(), ["test"])
        assert isinstance(result, dict)
        assert "not found or inactive" in result["error"]

    def test_missing_ar_account_returns_error(self) -> None:
        mock_db = MagicMock()
        mock_db.get.return_value = _make_mock_org()
        mock_config = MagicMock()
        mock_config.is_configured.return_value = True
        with (
            patch(f"{_MODULE}._resolve_org_id", return_value=ORG_ID),
            patch(f"{_MODULE}.SplynxConfig") as MockConfig,
            patch(f"{_MODULE}._resolve_ar_control_account", return_value=None),
        ):
            MockConfig.from_settings.return_value = mock_config
            result = _build_sync_context(mock_db, str(ORG_ID), MagicMock(), ["test"])
        assert isinstance(result, dict)
        assert "AR control account" in result["error"]

    def test_missing_revenue_account_returns_error(self) -> None:
        mock_db = MagicMock()
        mock_db.get.return_value = _make_mock_org()
        mock_config = MagicMock()
        mock_config.is_configured.return_value = True
        with (
            patch(f"{_MODULE}._resolve_org_id", return_value=ORG_ID),
            patch(f"{_MODULE}.SplynxConfig") as MockConfig,
            patch(f"{_MODULE}._resolve_ar_control_account", return_value=AR_ACCOUNT),
            patch(f"{_MODULE}._resolve_default_revenue_account", return_value=None),
        ):
            MockConfig.from_settings.return_value = mock_config
            result = _build_sync_context(mock_db, str(ORG_ID), MagicMock(), ["test"])
        assert isinstance(result, dict)
        assert "revenue account" in result["error"]


# ---------------------------------------------------------------------------
# Tier 1 — Incremental sync
# ---------------------------------------------------------------------------


class TestIncrementalSync:
    """Tests for run_splynx_incremental_sync (Tier 1)."""

    def test_uses_last_sync_time(self) -> None:
        """When prior sync exists, from_date = last_sync_at - overlap."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        last_sync_at = datetime(2026, 2, 13, 10, 0, 0, tzinfo=UTC)
        patches = _setup_patches(mock_db, mock_svc, last_sync=last_sync_at)

        with patch.multiple(_MODULE, **patches):
            result = run_splynx_incremental_sync(organization_id=str(ORG_ID))

        assert result["success"] is not None  # Task completed
        # Invoices should use from_date derived from last_sync_at
        inv_call = mock_svc.sync_invoices.call_args
        expected_from = (
            last_sync_at - timedelta(hours=_INCREMENTAL_OVERLAP_HOURS)
        ).date()
        assert inv_call.kwargs.get("date_from") == expected_from

    def test_no_date_filter_for_customers(self) -> None:
        """Customers are always fetched without date filters."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc, last_sync=datetime.now(UTC))

        with patch.multiple(_MODULE, **patches):
            run_splynx_incremental_sync(organization_id=str(ORG_ID))

        cust_call = mock_svc.sync_customers.call_args
        assert (
            cust_call.kwargs.get("date_from") is None
            or "date_from" not in cust_call.kwargs
        )

    def test_fallback_7_days_when_no_prior_sync(self) -> None:
        """When no prior sync history, uses 7-day lookback."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc, last_sync=None)

        with patch.multiple(_MODULE, **patches):
            run_splynx_incremental_sync(organization_id=str(ORG_ID))

        inv_call = mock_svc.sync_invoices.call_args
        from_date = inv_call.kwargs["date_from"]
        expected = date.today() - timedelta(days=_INCREMENTAL_FALLBACK_DAYS)
        # Allow 1 day tolerance for midnight edge cases
        assert abs((from_date - expected).days) <= 1

    def test_always_includes_credit_notes(self) -> None:
        """Tier 1 always syncs credit notes (unlike legacy task's include_credit_notes flag)."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)

        with patch.multiple(_MODULE, **patches):
            run_splynx_incremental_sync(organization_id=str(ORG_ID))

        mock_svc.sync_credit_notes.assert_called_once()

    def test_returns_synced_counts(self) -> None:
        """Result dict includes synced_count from all entity types."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)

        with patch.multiple(_MODULE, **patches):
            result = run_splynx_incremental_sync(organization_id=str(ORG_ID))

        assert "history_id" in result
        assert "synced_count" in result


# ---------------------------------------------------------------------------
# Tier 2 — Daily reconciliation
# ---------------------------------------------------------------------------


class TestDailyReconciliation:
    """Tests for run_splynx_daily_reconciliation (Tier 2)."""

    def test_fetches_unpaid_invoices(self) -> None:
        """Tier 2 calls sync_invoices with status='unpaid'."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)
        # Remove _get_last_sync_at — Tier 2 doesn't use it
        patches.pop("_get_last_sync_at", None)

        with patch.multiple(_MODULE, **patches):
            run_splynx_daily_reconciliation(organization_id=str(ORG_ID))

        calls = mock_svc.sync_invoices.call_args_list
        statuses = [c.kwargs.get("status") for c in calls]
        assert "unpaid" in statuses
        assert "partially_paid" in statuses

    def test_payments_30_day_window(self) -> None:
        """Tier 2 syncs payments with a 30-day lookback."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)
        patches.pop("_get_last_sync_at", None)

        with patch.multiple(_MODULE, **patches):
            run_splynx_daily_reconciliation(organization_id=str(ORG_ID))

        pay_call = mock_svc.sync_payments.call_args
        from_date = pay_call.kwargs["date_from"]
        expected = date.today() - timedelta(days=_DAILY_RECON_PAYMENT_DAYS)
        assert abs((from_date - expected).days) <= 1

    def test_no_customer_sync(self) -> None:
        """Tier 2 does NOT sync customers (Tier 1 handles that)."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)
        patches.pop("_get_last_sync_at", None)

        with patch.multiple(_MODULE, **patches):
            run_splynx_daily_reconciliation(organization_id=str(ORG_ID))

        mock_svc.sync_customers.assert_not_called()

    def test_no_credit_note_sync(self) -> None:
        """Tier 2 does NOT sync credit notes."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)
        patches.pop("_get_last_sync_at", None)

        with patch.multiple(_MODULE, **patches):
            run_splynx_daily_reconciliation(organization_id=str(ORG_ID))

        mock_svc.sync_credit_notes.assert_not_called()

    def test_batch_size_passed(self) -> None:
        """Tier 2 passes batch_size to all sync calls to prevent OOM."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)
        patches.pop("_get_last_sync_at", None)

        with patch.multiple(_MODULE, **patches):
            run_splynx_daily_reconciliation(organization_id=str(ORG_ID))

        for call in mock_svc.sync_invoices.call_args_list:
            assert call.kwargs.get("batch_size") == 5000
        assert mock_svc.sync_payments.call_args.kwargs.get("batch_size") == 5000


# ---------------------------------------------------------------------------
# Tier 3 — Full reconciliation
# ---------------------------------------------------------------------------


class TestFullReconciliation:
    """Tests for run_splynx_full_reconciliation (Tier 3)."""

    def test_no_date_filters(self) -> None:
        """Tier 3 fetches all entities without date filters."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)
        patches.pop("_get_last_sync_at", None)

        with patch.multiple(_MODULE, **patches):
            run_splynx_full_reconciliation(organization_id=str(ORG_ID))

        for method_name in [
            "sync_customers",
            "sync_invoices",
            "sync_payments",
            "sync_credit_notes",
        ]:
            call = getattr(mock_svc, method_name).call_args
            assert (
                call.kwargs.get("date_from") is None or "date_from" not in call.kwargs
            )
            assert call.kwargs.get("date_to") is None or "date_to" not in call.kwargs

    def test_no_status_filter(self) -> None:
        """Tier 3 does NOT use status filters on invoices."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)
        patches.pop("_get_last_sync_at", None)

        with patch.multiple(_MODULE, **patches):
            run_splynx_full_reconciliation(organization_id=str(ORG_ID))

        inv_call = mock_svc.sync_invoices.call_args
        assert inv_call.kwargs.get("status") is None or "status" not in inv_call.kwargs

    def test_syncs_all_entity_types(self) -> None:
        """Tier 3 syncs customers, invoices, payments, AND credit notes."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)
        patches.pop("_get_last_sync_at", None)

        with patch.multiple(_MODULE, **patches):
            run_splynx_full_reconciliation(organization_id=str(ORG_ID))

        mock_svc.sync_customers.assert_called_once()
        mock_svc.sync_invoices.assert_called_once()
        mock_svc.sync_payments.assert_called_once()
        mock_svc.sync_credit_notes.assert_called_once()

    def test_skip_unchanged_enabled(self) -> None:
        """Tier 3 uses skip_unchanged=True for efficiency."""
        mock_db = MagicMock()
        mock_svc = _make_mock_service()
        patches = _setup_patches(mock_db, mock_svc)
        patches.pop("_get_last_sync_at", None)

        with patch.multiple(_MODULE, **patches):
            run_splynx_full_reconciliation(organization_id=str(ORG_ID))

        for method_name in [
            "sync_customers",
            "sync_invoices",
            "sync_payments",
            "sync_credit_notes",
        ]:
            call = getattr(mock_svc, method_name).call_args
            assert call.kwargs.get("skip_unchanged") is True


# ---------------------------------------------------------------------------
# Legacy alias
# ---------------------------------------------------------------------------


class TestLegacyAlias:
    """Test that run_scheduled_splynx_sync delegates to the new incremental task."""

    def test_delegates_to_incremental(self) -> None:
        with patch(f"{_MODULE}.run_splynx_incremental_sync") as mock_inc:
            mock_inc.return_value = {"success": True}
            from app.tasks.splynx import run_scheduled_splynx_sync

            result = run_scheduled_splynx_sync(
                organization_id=str(ORG_ID),
                lookback_days=5,
                batch_size=1000,
                include_credit_notes=False,
            )
        mock_inc.assert_called_once_with(
            organization_id=str(ORG_ID),
            batch_size=1000,
        )
        assert result == {"success": True}
