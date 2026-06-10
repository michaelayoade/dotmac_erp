"""
Tests for the mobile-facing /me additions:

- attendance check-in/out geolocation pass-through
- GET/POST /me/notifications JSON endpoints
- GET /me/approvals aggregator (RBAC gating, sorting, resilience)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.approvals_aggregator import (
    ApprovalsAggregatorService,
    _amount_str,
    _sort_ts,
)

ORG_ID = uuid4()
PERSON_ID = uuid4()
EMPLOYEE_ID = uuid4()


def _auth(roles: list[str] | None = None, scopes: list[str] | None = None) -> dict:
    return {
        "organization_id": str(ORG_ID),
        "person_id": str(PERSON_ID),
        "roles": roles or [],
        "scopes": scopes or [],
    }


@pytest.fixture()
def me_app():
    from app.api.deps import get_db_with_org, require_tenant_auth
    from app.api.me import router as me_router

    app = FastAPI()
    app.include_router(me_router, prefix="/api/v1")
    app.dependency_overrides[require_tenant_auth] = _auth
    app.dependency_overrides[get_db_with_org] = lambda: MagicMock()
    return app


@pytest.fixture()
def me_client(me_app):
    return TestClient(me_app)


# =============================================================================
# Attendance geolocation pass-through
# =============================================================================


def _fake_attendance() -> SimpleNamespace:
    return SimpleNamespace(
        attendance_id=uuid4(),
        organization_id=ORG_ID,
        employee_id=EMPLOYEE_ID,
        attendance_date=date(2026, 6, 10),
        status="PRESENT",
        check_in=datetime(2026, 6, 10, 8, 0),
        check_out=None,
        shift_type_id=None,
        working_hours=None,
        late_entry=False,
        early_exit=False,
        overtime_hours=Decimal("0"),
        late_entry_minutes=0,
        early_exit_minutes=0,
        marked_by="SYSTEM",
        notes=None,
        created_at=datetime(2026, 6, 10, 8, 0),
        updated_at=None,
    )


class TestAttendanceGeoPassThrough:
    @patch("app.api.me._get_employee_id", return_value=EMPLOYEE_ID)
    @patch("app.api.me.AttendanceService")
    def test_check_in_passes_lat_long_to_service(
        self, svc_cls, _get_emp, me_client
    ) -> None:
        svc_cls.return_value.check_in.return_value = _fake_attendance()

        resp = me_client.post(
            "/api/v1/me/attendance/check-in",
            json={"notes": "on site", "latitude": 6.5244, "longitude": 3.3792},
        )

        assert resp.status_code == 201
        kwargs = svc_cls.return_value.check_in.call_args.kwargs
        assert kwargs["latitude"] == pytest.approx(6.5244)
        assert kwargs["longitude"] == pytest.approx(3.3792)

    @patch("app.api.me._get_employee_id", return_value=EMPLOYEE_ID)
    @patch("app.api.me.AttendanceService")
    def test_check_out_passes_lat_long_to_service(
        self, svc_cls, _get_emp, me_client
    ) -> None:
        svc_cls.return_value.check_out.return_value = _fake_attendance()

        resp = me_client.post(
            "/api/v1/me/attendance/check-out",
            json={"latitude": 6.45, "longitude": 3.39},
        )

        assert resp.status_code == 201
        kwargs = svc_cls.return_value.check_out.call_args.kwargs
        assert kwargs["latitude"] == pytest.approx(6.45)
        assert kwargs["longitude"] == pytest.approx(3.39)

    @patch("app.api.me._get_employee_id", return_value=EMPLOYEE_ID)
    @patch("app.api.me.AttendanceService")
    def test_check_in_geo_defaults_to_none(self, svc_cls, _get_emp, me_client) -> None:
        svc_cls.return_value.check_in.return_value = _fake_attendance()

        resp = me_client.post("/api/v1/me/attendance/check-in", json={})

        assert resp.status_code == 201
        kwargs = svc_cls.return_value.check_in.call_args.kwargs
        assert kwargs["latitude"] is None
        assert kwargs["longitude"] is None


# =============================================================================
# Notifications
# =============================================================================


def _fake_notification(is_read: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        notification_id=uuid4(),
        entity_type="SYSTEM",
        entity_id=uuid4(),
        notification_type="REMINDER",
        channel="IN_APP",
        title="Period closing soon",
        message="Q2 closes in 3 days.",
        action_url="/finance/gl/periods",
        is_read=is_read,
        read_at=None,
        created_at=datetime(2026, 6, 10, 9, 0),
    )


class TestMyNotifications:
    @patch("app.api.me.NotificationService")
    def test_list_returns_items_and_unread_count(self, svc_cls, me_client) -> None:
        svc = svc_cls.return_value
        svc.list_notifications.return_value = [_fake_notification() for _ in range(3)]
        svc.get_unread_count.return_value = 7

        resp = me_client.get("/api/v1/me/notifications")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 3
        assert body["unread_count"] == 7
        assert body["has_more"] is False
        item = body["items"][0]
        assert item["title"] == "Period closing soon"
        assert item["action_url"] == "/finance/gl/periods"

    @patch("app.api.me.NotificationService")
    def test_list_has_more_uses_limit_plus_one(self, svc_cls, me_client) -> None:
        svc = svc_cls.return_value
        svc.list_notifications.return_value = [_fake_notification() for _ in range(3)]
        svc.get_unread_count.return_value = 3

        resp = me_client.get("/api/v1/me/notifications", params={"limit": 2})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["has_more"] is True
        # service is asked for limit + 1 to detect the next page
        assert svc.list_notifications.call_args.kwargs["limit"] == 3

    @patch("app.api.me.NotificationService")
    def test_list_empty(self, svc_cls, me_client) -> None:
        svc = svc_cls.return_value
        svc.list_notifications.return_value = []
        svc.get_unread_count.return_value = 0

        resp = me_client.get("/api/v1/me/notifications")

        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["unread_count"] == 0
        assert body["has_more"] is False

    @patch("app.api.me.NotificationService")
    def test_mark_read_success(self, svc_cls, me_client) -> None:
        svc_cls.return_value.mark_read.return_value = True

        resp = me_client.post(f"/api/v1/me/notifications/{uuid4()}/read")

        assert resp.status_code == 200
        assert resp.json() == {"marked_read": True}

    @patch("app.api.me.NotificationService")
    def test_mark_read_not_found(self, svc_cls, me_client) -> None:
        svc_cls.return_value.mark_read.return_value = False

        resp = me_client.post(f"/api/v1/me/notifications/{uuid4()}/read")

        assert resp.status_code == 404

    @patch("app.api.me.NotificationService")
    def test_mark_all_read(self, svc_cls, me_client) -> None:
        svc_cls.return_value.mark_all_read.return_value = 5

        resp = me_client.post("/api/v1/me/notifications/mark-all-read")

        assert resp.status_code == 200
        assert resp.json() == {"marked_read": 5}


# =============================================================================
# Approvals aggregator — service-level
# =============================================================================


def _stub_item(type_key: str, submitted: datetime) -> dict:
    return {
        "type": type_key,
        "id": str(uuid4()),
        "reference": f"{type_key.upper()}-001",
        "title": f"{type_key} item",
        "requester": None,
        "amount": None,
        "currency": None,
        "status": "SUBMITTED",
        "submitted_at": submitted,
        "_sort_ts": _sort_ts(submitted),
    }


ALL_CATEGORIES = {
    "leave",
    "expense",
    "cash_advance",
    "ap_invoice",
    "ap_payment_batch",
    "requisition",
}


@pytest.fixture()
def stubbed_aggregator(monkeypatch):
    """Aggregator whose per-category fetchers return one stub item each."""
    db = MagicMock()
    # The _has_permission DB fallback must DENY by default in tests —
    # a bare MagicMock would return truthy rows and grant everything.
    db.scalar.return_value = None
    svc = ApprovalsAggregatorService(db)
    base = datetime(2026, 6, 10, 12, 0)
    fetcher_names = {
        "leave": "_leave_items",
        "expense": "_expense_claim_items",
        "cash_advance": "_cash_advance_items",
        "ap_invoice": "_ap_invoice_items",
        "ap_payment_batch": "_ap_payment_batch_items",
        "requisition": "_requisition_items",
    }
    for offset, (key, attr) in enumerate(fetcher_names.items()):
        submitted = base.replace(hour=offset + 1)
        monkeypatch.setattr(
            svc,
            attr,
            lambda org, person, limit, k=key, s=submitted: [_stub_item(k, s)],
        )
    return svc


class TestApprovalsAggregatorGating:
    def test_admin_sees_all_categories(self, stubbed_aggregator) -> None:
        result = stubbed_aggregator.list_pending(
            organization_id=ORG_ID,
            person_id=PERSON_ID,
            roles={"admin"},
            scopes=set(),
        )
        assert set(result["counts"]) == ALL_CATEGORIES
        assert result["total"] == len(ALL_CATEGORIES)

    def test_leave_tier_scope_sees_only_leave(self, stubbed_aggregator) -> None:
        result = stubbed_aggregator.list_pending(
            organization_id=ORG_ID,
            person_id=PERSON_ID,
            roles=set(),
            scopes={"leave:applications:approve:tier1"},
        )
        assert set(result["counts"]) == {"leave"}
        assert result["total"] == 1

    def test_expense_tier1_scope_sees_only_expense(self, stubbed_aggregator) -> None:
        # Mirrors the API approve endpoint, which requires exactly tier1.
        result = stubbed_aggregator.list_pending(
            organization_id=ORG_ID,
            person_id=PERSON_ID,
            roles=set(),
            scopes={"expense:claims:approve:tier1"},
        )
        assert set(result["counts"]) == {"expense"}

    def test_expense_tier2_scope_does_not_grant_api_inbox(
        self, stubbed_aggregator
    ) -> None:
        # tier2/tier3 are honored only by web routes; the JSON approve
        # endpoint requires tier1, so the inbox must not show the category.
        result = stubbed_aggregator.list_pending(
            organization_id=ORG_ID,
            person_id=PERSON_ID,
            roles=set(),
            scopes={"expense:claims:approve:tier2"},
        )
        assert "expense" not in result["counts"]

    def test_db_role_grant_unlocks_category_without_scope(
        self, stubbed_aggregator, monkeypatch
    ) -> None:
        # require_tenant_permission falls back to a DB role->permission
        # lookup; the inbox mirrors that.
        monkeypatch.setattr(
            stubbed_aggregator,
            "_has_permission",
            lambda person, roles, scopes, key: key == "expense:claims:approve:tier1",
        )
        result = stubbed_aggregator.list_pending(
            organization_id=ORG_ID,
            person_id=PERSON_ID,
            roles=set(),
            scopes=set(),
        )
        assert set(result["counts"]) == {"expense"}

    def test_ap_permissions_gate_exactly(self, stubbed_aggregator) -> None:
        result = stubbed_aggregator.list_pending(
            organization_id=ORG_ID,
            person_id=PERSON_ID,
            roles=set(),
            scopes={"ap:invoices:approve", "ap:payment_batches:approve"},
        )
        assert set(result["counts"]) == {"ap_invoice", "ap_payment_batch"}

    def test_no_permissions_sees_nothing(self, stubbed_aggregator) -> None:
        result = stubbed_aggregator.list_pending(
            organization_id=ORG_ID,
            person_id=PERSON_ID,
            roles=set(),
            scopes={"some:other:permission"},
        )
        assert result["items"] == []
        assert result["counts"] == {}
        assert result["total"] == 0

    def test_requisitions_are_admin_only(self, stubbed_aggregator) -> None:
        result = stubbed_aggregator.list_pending(
            organization_id=ORG_ID,
            person_id=PERSON_ID,
            roles=set(),
            scopes={
                "leave:applications:approve:tier1",
                "expense:claims:approve:tier1",
                "ap:invoices:approve",
            },
        )
        assert "requisition" not in result["counts"]


class TestApprovalsAggregatorBehavior:
    def test_items_sorted_newest_first_and_sort_key_stripped(
        self, stubbed_aggregator
    ) -> None:
        result = stubbed_aggregator.list_pending(
            organization_id=ORG_ID,
            person_id=PERSON_ID,
            roles={"admin"},
            scopes=set(),
        )
        submitted = [i["submitted_at"] for i in result["items"]]
        assert submitted == sorted(submitted, reverse=True)
        assert all("_sort_ts" not in i for i in result["items"])

    def test_failing_category_is_skipped_not_fatal(
        self, stubbed_aggregator, monkeypatch
    ) -> None:
        def boom(org, person, limit):
            raise RuntimeError("category exploded")

        monkeypatch.setattr(stubbed_aggregator, "_ap_invoice_items", boom)

        result = stubbed_aggregator.list_pending(
            organization_id=ORG_ID,
            person_id=PERSON_ID,
            roles={"admin"},
            scopes=set(),
        )
        assert "ap_invoice" not in result["counts"]
        assert set(result["counts"]) == ALL_CATEGORIES - {"ap_invoice"}


class TestAppendReceiptUrl:
    """attach_receipt must append to the single-or-JSON-array convention."""

    def _append(self, existing, new):
        from app.services.expense.service_claims import ExpenseClaimMixin

        return ExpenseClaimMixin._append_receipt_url(existing, new)

    def test_first_receipt_stored_as_plain_string(self) -> None:
        assert self._append(None, "a/b.jpg") == "a/b.jpg"
        assert self._append("", "a/b.jpg") == "a/b.jpg"

    def test_second_receipt_upgrades_to_json_array(self) -> None:
        import json

        result = self._append("a/first.jpg", "a/second.jpg")
        assert json.loads(result) == ["a/first.jpg", "a/second.jpg"]

    def test_appends_to_existing_json_array(self) -> None:
        import json

        existing = json.dumps(["a/1.jpg", "a/2.jpg"])
        result = self._append(existing, "a/3.jpg")
        assert json.loads(result) == ["a/1.jpg", "a/2.jpg", "a/3.jpg"]

    def test_malformed_json_treated_as_single_url(self) -> None:
        import json

        result = self._append("[not-json", "a/new.jpg")
        assert json.loads(result) == ["[not-json", "a/new.jpg"]


class TestPaymentBatchSegregationOfDuties:
    def test_own_batches_are_filtered_out(self, monkeypatch) -> None:
        from types import SimpleNamespace as NS

        svc = ApprovalsAggregatorService(MagicMock())
        mine = NS(
            batch_id=uuid4(),
            batch_number="PB-001",
            total_payments=3,
            total_amount=Decimal("100"),
            currency_code="NGN",
            status=SimpleNamespace(value="DRAFT"),
            created_at=datetime(2026, 6, 10, 9, 0),
            batch_date=date(2026, 6, 10),
            created_by_user_id=PERSON_ID,
        )
        theirs = NS(
            **{
                **mine.__dict__,
                "batch_id": uuid4(),
                "batch_number": "PB-002",
                "created_by_user_id": uuid4(),
            }
        )

        import app.services.finance.ap.payment_batch as pb_module

        monkeypatch.setattr(
            pb_module.payment_batch_service,
            "list",
            lambda *a, **k: [mine, theirs],
        )

        items = svc._ap_payment_batch_items(ORG_ID, PERSON_ID, 10)

        assert [i["reference"] for i in items] == ["PB-002"]


class TestAggregatorHelpers:
    def test_sort_ts_handles_datetime_date_and_none(self) -> None:
        dt = datetime(2026, 6, 10, 14, 30)
        assert _sort_ts(dt) == dt
        assert _sort_ts(date(2026, 6, 10)) == datetime(2026, 6, 10, 0, 0)
        assert _sort_ts(None) == datetime.min

    def test_amount_str_formats_two_decimals(self) -> None:
        assert _amount_str(Decimal("48375")) == "48375.00"
        assert _amount_str(Decimal("1234.567")) == "1234.57"
        assert _amount_str(None) is None


# =============================================================================
# Approvals aggregator — route wiring
# =============================================================================


class TestMyApprovalsRoute:
    @patch("app.api.me.ApprovalsAggregatorService")
    def test_route_passes_auth_context(self, svc_cls, me_client) -> None:
        svc_cls.return_value.list_pending.return_value = {
            "items": [],
            "counts": {},
            "total": 0,
        }

        resp = me_client.get("/api/v1/me/approvals", params={"limit_per_type": 5})

        assert resp.status_code == 200
        assert resp.json() == {"items": [], "counts": {}, "total": 0}
        kwargs = svc_cls.return_value.list_pending.call_args.kwargs
        assert kwargs["organization_id"] == ORG_ID
        assert kwargs["person_id"] == PERSON_ID
        assert kwargs["limit_per_type"] == 5

    @patch("app.api.me.ApprovalsAggregatorService")
    def test_limit_per_type_validation(self, svc_cls, me_client) -> None:
        resp = me_client.get("/api/v1/me/approvals", params={"limit_per_type": 0})
        assert resp.status_code == 422


# =============================================================================
# Create / submit own expense claim
# =============================================================================


def _fake_claim(claim_status: str = "DRAFT") -> SimpleNamespace:
    from app.schemas.people.expense import ExpenseClaimRead

    claim = SimpleNamespace(
        claim_id=uuid4(),
        organization_id=ORG_ID,
        claim_number="EXP-00042",
        employee_id=EMPLOYEE_ID,
        claim_date=date(2026, 6, 10),
        expense_period_start=None,
        expense_period_end=None,
        purpose="Site visit fuel",
        project_id=None,
        ticket_id=None,
        task_id=None,
        currency_code="NGN",
        cost_center_id=None,
        recipient_bank_code=None,
        recipient_bank_name=None,
        recipient_account_number=None,
        recipient_name=None,
        requested_approver_id=None,
        notes=None,
        status=claim_status,
        total_claimed_amount=Decimal("15000.00"),
        total_approved_amount=None,
        submitted_at=None,
        approved_at=None,
        approver_id=None,
        rejection_reason=None,
        paid_at=None,
        payment_reference=None,
        advance_adjusted=Decimal("0"),
        created_at=datetime(2026, 6, 10, 9, 0),
        updated_at=None,
        items=[],
        employee=None,
        approver=None,
    )
    # Sanity: fail fast here (not in the route) if the fake drifts from schema
    ExpenseClaimRead.model_validate(claim)
    return claim


class TestCreateMyExpenseClaim:
    @patch("app.api.me._get_employee_id", return_value=EMPLOYEE_ID)
    @patch("app.api.me.ExpenseService")
    def test_create_resolves_employee_from_token(
        self, svc_cls, _get_emp, me_client
    ) -> None:
        svc_cls.return_value.create_claim.return_value = _fake_claim()

        resp = me_client.post(
            "/api/v1/me/expenses/claims",
            json={
                "claim_date": "2026-06-10",
                "purpose": "Site visit fuel",
                "items": [
                    {
                        "expense_date": "2026-06-10",
                        "category_id": str(uuid4()),
                        "description": "Fuel",
                        "claimed_amount": "15000.00",
                    }
                ],
            },
        )

        assert resp.status_code == 201
        kwargs = svc_cls.return_value.create_claim.call_args.kwargs
        assert kwargs["employee_id"] == EMPLOYEE_ID
        assert kwargs["created_by_id"] == PERSON_ID
        assert len(kwargs["items"]) == 1

    @patch("app.api.me._get_employee_id", return_value=EMPLOYEE_ID)
    @patch("app.api.me.ExpenseService")
    def test_create_service_error_maps_to_400(
        self, svc_cls, _get_emp, me_client
    ) -> None:
        from app.services.expense.service_common import ExpenseServiceError

        svc_cls.return_value.create_claim.side_effect = ExpenseServiceError(
            "Claimed amount exceeds category limit"
        )

        resp = me_client.post(
            "/api/v1/me/expenses/claims",
            json={"claim_date": "2026-06-10", "purpose": "x", "items": []},
        )

        assert resp.status_code == 400


class TestSubmitMyExpenseClaim:
    @patch("app.api.me._get_employee_id", return_value=EMPLOYEE_ID)
    @patch("app.api.me.ExpenseService")
    def test_submit_own_claim(self, svc_cls, _get_emp, me_client) -> None:
        claim = _fake_claim()
        svc_cls.return_value.get_claim.return_value = claim
        svc_cls.return_value.submit_claim.return_value = SimpleNamespace(
            claim=_fake_claim("SUBMITTED")
        )

        resp = me_client.post(f"/api/v1/me/expenses/claims/{claim.claim_id}/submit")

        assert resp.status_code == 200
        assert resp.json()["status"] == "SUBMITTED"

    @patch("app.api.me._get_employee_id", return_value=EMPLOYEE_ID)
    @patch("app.api.me.ExpenseService")
    def test_submit_other_employees_claim_forbidden(
        self, svc_cls, _get_emp, me_client
    ) -> None:
        other = _fake_claim()
        other.employee_id = uuid4()
        svc_cls.return_value.get_claim.return_value = other

        resp = me_client.post(f"/api/v1/me/expenses/claims/{other.claim_id}/submit")

        assert resp.status_code == 403
        svc_cls.return_value.submit_claim.assert_not_called()


# =============================================================================
# Receipt upload
# =============================================================================


class TestReceiptUpload:
    @patch("app.api.me._get_employee_id", return_value=EMPLOYEE_ID)
    @patch("app.api.me.ExpenseService")
    def test_upload_attaches_receipt(self, svc_cls, _get_emp, me_client) -> None:
        item_id = uuid4()
        svc_cls.return_value.attach_receipt.return_value = SimpleNamespace(
            item_id=item_id,
            receipt_url=f"expense-receipts/{ORG_ID}/abc.jpg",
        )

        resp = me_client.post(
            f"/api/v1/me/expenses/claims/{uuid4()}/items/{item_id}/receipt",
            files={"receipt": ("fuel.jpg", b"\xff\xd8\xff fake-jpeg", "image/jpeg")},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["item_id"] == str(item_id)
        assert body["receipt_url"].endswith("abc.jpg")
        kwargs = svc_cls.return_value.attach_receipt.call_args.kwargs
        assert kwargs["employee_id"] == EMPLOYEE_ID
        assert kwargs["content_type"] == "image/jpeg"
        assert kwargs["original_filename"] == "fuel.jpg"

    @patch("app.api.me._get_employee_id", return_value=EMPLOYEE_ID)
    @patch("app.api.me.ExpenseService")
    def test_upload_invalid_file_maps_to_400(
        self, svc_cls, _get_emp, me_client
    ) -> None:
        from app.services.file_upload import FileUploadError

        svc_cls.return_value.attach_receipt.side_effect = FileUploadError(
            "File too large"
        )

        resp = me_client.post(
            f"/api/v1/me/expenses/claims/{uuid4()}/items/{uuid4()}/receipt",
            files={"receipt": ("big.jpg", b"x" * 10, "image/jpeg")},
        )

        assert resp.status_code == 400
        assert "File too large" in resp.json()["detail"]

    @patch("app.api.me._get_employee_id", return_value=EMPLOYEE_ID)
    @patch("app.api.me.ExpenseService")
    def test_upload_on_submitted_claim_maps_to_400(
        self, svc_cls, _get_emp, me_client
    ) -> None:
        from app.services.expense.service_common import ExpenseClaimStatusError

        svc_cls.return_value.attach_receipt.side_effect = ExpenseClaimStatusError(
            "SUBMITTED", "attach receipt"
        )

        resp = me_client.post(
            f"/api/v1/me/expenses/claims/{uuid4()}/items/{uuid4()}/receipt",
            files={"receipt": ("r.jpg", b"x", "image/jpeg")},
        )

        assert resp.status_code == 400
