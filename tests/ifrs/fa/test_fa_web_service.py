"""
Tests for FixedAssetWebService.
"""

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.web.deps import WebAuthContext


class TestFAWebServiceHelpers:
    """Tests for FA web service helper functions."""

    def test_format_date_with_value(self):
        """Test date formatting with valid date."""
        from app.services.fixed_assets.web import _format_date

        result = _format_date(date(2024, 1, 15))
        assert result == "2024-01-15"

    def test_format_date_none(self):
        """Test date formatting with None."""
        from app.services.fixed_assets.web import _format_date

        result = _format_date(None)
        assert result == ""

    def test_format_currency_usd(self):
        """Test currency formatting for USD."""
        from app.services.fixed_assets.web import _format_currency

        result = _format_currency(Decimal("1234.56"), "USD")
        assert result == "USD 1,234.56"

    def test_format_currency_other(self):
        """Test currency formatting for other currencies."""
        from app.services.fixed_assets.web import _format_currency

        result = _format_currency(Decimal("1234.56"), "EUR")
        assert result == "EUR 1,234.56"

    def test_format_currency_none(self):
        """Test currency formatting with None."""
        from app.services.fixed_assets.web import _format_currency

        result = _format_currency(None)
        assert result == ""

    def test_parse_status_valid(self):
        """Test status parsing with valid value."""
        from app.models.fixed_assets.asset import AssetStatus
        from app.services.fixed_assets.web import _parse_status

        result = _parse_status("IN_USE")
        assert result == AssetStatus.IN_USE

    def test_parse_status_lowercase(self):
        """Test status parsing with lowercase value."""
        from app.models.fixed_assets.asset import AssetStatus
        from app.services.fixed_assets.web import _parse_status

        result = _parse_status("in_use")
        assert result == AssetStatus.IN_USE

    def test_parse_status_none(self):
        """Test status parsing with None."""
        from app.services.fixed_assets.web import _parse_status

        result = _parse_status(None)
        assert result is None

    def test_parse_status_invalid(self):
        """Test status parsing with invalid value."""
        from app.services.fixed_assets.web import _parse_status

        result = _parse_status("INVALID_STATUS")
        assert result is None

    def test_try_uuid_valid(self):
        """Test UUID parsing with valid value."""
        from app.services.fixed_assets.web import _try_uuid

        test_uuid = uuid.uuid4()
        result = _try_uuid(str(test_uuid))
        assert result == test_uuid

    def test_try_uuid_none(self):
        """Test UUID parsing with None."""
        from app.services.fixed_assets.web import _try_uuid

        result = _try_uuid(None)
        assert result is None

    def test_try_uuid_invalid(self):
        """Test UUID parsing with invalid value."""
        from app.services.fixed_assets.web import _try_uuid

        result = _try_uuid("not-a-uuid")
        assert result is None


class MockAsset:
    """Mock Asset for testing."""

    def __init__(self, **kwargs):
        from app.models.fixed_assets.asset import AssetStatus

        self.asset_id = kwargs.get("asset_id", uuid.uuid4())
        self.organization_id = kwargs.get("organization_id", uuid.uuid4())
        self.asset_number = kwargs.get("asset_number", "FA-0001")
        self.asset_name = kwargs.get("asset_name", "Office Computer")
        self.category_id = kwargs.get("category_id", uuid.uuid4())
        self.acquisition_date = kwargs.get("acquisition_date", date.today())
        self.acquisition_cost = kwargs.get("acquisition_cost", Decimal("5000.00"))
        self.net_book_value = kwargs.get("net_book_value", Decimal("4000.00"))
        self.currency_code = kwargs.get("currency_code", "USD")
        self.status = kwargs.get("status", AssetStatus.IN_USE)
        self.serial_number = kwargs.get("serial_number")
        self.barcode = kwargs.get("barcode")
        self.custodian_employee_id = kwargs.get("custodian_employee_id")


class MockAssetCategory:
    """Mock AssetCategory for testing."""

    def __init__(self, **kwargs):
        self.category_id = kwargs.get("category_id", uuid.uuid4())
        self.organization_id = kwargs.get("organization_id", uuid.uuid4())
        self.category_code = kwargs.get("category_code", "EQUIPMENT")
        self.category_name = kwargs.get("category_name", "Office Equipment")
        self.is_active = kwargs.get("is_active", True)


class MockDepreciationRun:
    """Mock DepreciationRun for testing."""

    def __init__(self, **kwargs):
        from app.models.fixed_assets.depreciation_run import DepreciationRunStatus

        self.run_id = kwargs.get("run_id", uuid.uuid4())
        self.organization_id = kwargs.get("organization_id", uuid.uuid4())
        self.run_number = kwargs.get("run_number", "DEP-2024-01")
        self.run_description = kwargs.get("run_description", "January Depreciation")
        self.fiscal_period_id = kwargs.get("fiscal_period_id", uuid.uuid4())
        self.status = kwargs.get("status", DepreciationRunStatus.DRAFT)
        self.assets_processed = kwargs.get("assets_processed", 10)
        self.total_depreciation = kwargs.get("total_depreciation", Decimal("1000.00"))
        self.journal_entry_id = kwargs.get("journal_entry_id")
        self.created_by_user_id = kwargs.get("created_by_user_id", uuid.uuid4())
        self.calculation_started_at = kwargs.get("calculation_started_at")
        self.calculation_completed_at = kwargs.get("calculation_completed_at")
        self.posted_at = kwargs.get("posted_at")
        self.created_at = kwargs.get("created_at", datetime.now(UTC))


class MockFiscalPeriod:
    """Mock FiscalPeriod for testing."""

    def __init__(self, **kwargs):
        self.fiscal_period_id = kwargs.get("fiscal_period_id", uuid.uuid4())
        self.period_name = kwargs.get("period_name", "January 2024")
        self.start_date = kwargs.get("start_date", date(2024, 1, 1))
        self.end_date = kwargs.get("end_date", date(2024, 1, 31))


class MockDepreciationSchedule:
    """Mock DepreciationSchedule for testing."""

    def __init__(self, **kwargs):
        self.schedule_id = kwargs.get("schedule_id", uuid.uuid4())
        self.run_id = kwargs.get("run_id", uuid.uuid4())
        self.asset_id = kwargs.get("asset_id", uuid.uuid4())
        self.depreciation_amount = kwargs.get("depreciation_amount", Decimal("250.00"))
        self.net_book_value_opening = kwargs.get(
            "net_book_value_opening", Decimal("5000.00")
        )
        self.net_book_value_closing = kwargs.get(
            "net_book_value_closing", Decimal("4750.00")
        )
        self.accumulated_depreciation_opening = kwargs.get(
            "accumulated_depreciation_opening", Decimal("1000.00")
        )
        self.accumulated_depreciation_closing = kwargs.get(
            "accumulated_depreciation_closing", Decimal("1250.00")
        )
        self.expense_account_id = kwargs.get("expense_account_id", uuid.uuid4())
        self.accumulated_depreciation_account_id = kwargs.get(
            "accumulated_depreciation_account_id", uuid.uuid4()
        )
        self.remaining_life_months_opening = kwargs.get(
            "remaining_life_months_opening", 24
        )
        self.remaining_life_months_closing = kwargs.get(
            "remaining_life_months_closing", 23
        )


class MockAccount:
    """Mock GL account for testing."""

    def __init__(self, **kwargs):
        self.account_id = kwargs.get("account_id", uuid.uuid4())
        self.organization_id = kwargs.get("organization_id", uuid.uuid4())
        self.account_code = kwargs.get("account_code", "6000")
        self.account_name = kwargs.get("account_name", "Depreciation Expense")


class TestFAWebServiceListAssets:
    """Tests for list_assets_context method."""

    def test_list_assets_context_success(self):
        """Test successful assets list context."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_asset = MockAsset(organization_id=org_id)
        mock_category = MockAssetCategory(organization_id=org_id)
        summary_result = MagicMock()
        summary_result.one.return_value = SimpleNamespace(
            total_assets=1,
            total_cost=Decimal("5000.00"),
            total_nbv=Decimal("4000.00"),
            active_count=1,
        )
        rows_result = MagicMock()
        rows_result.all.return_value = [(mock_asset, mock_category)]

        # Mock SA2 patterns: db.scalar() for count, db.execute().all() for rows
        mock_db.scalar.return_value = 1
        mock_db.execute.side_effect = [summary_result, rows_result]

        result = FixedAssetWebService.list_assets_context(
            mock_db,
            str(org_id),
            search=None,
            category=None,
            status=None,
            location=None,
            page=1,
        )

        assert "assets" in result
        assert len(result["assets"]) == 1
        assert result["page"] == 1
        assert result["total_count"] == 1
        assert result["active_count"] == 1
        assert result["total_cost"] == "USD 5,000.00"
        assert result["total_nbv"] == "USD 4,000.00"

    def test_list_assets_context_with_search(self):
        """Test assets list context with search filter."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_db.scalar.return_value = 0
        mock_db.execute.return_value.all.return_value = []

        result = FixedAssetWebService.list_assets_context(
            mock_db,
            str(org_id),
            search="computer",
            category=None,
            status=None,
            location=None,
            page=1,
        )

        assert result["search"] == "computer"
        assert result["assets"] == []

    def test_list_assets_context_with_status(self):
        """Test assets list context with status filter."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_db.scalar.return_value = 0
        mock_db.execute.return_value.all.return_value = []

        result = FixedAssetWebService.list_assets_context(
            mock_db,
            str(org_id),
            search=None,
            category=None,
            status="IN_USE",
            location=None,
            page=1,
        )

        assert result["status"] == "IN_USE"


class TestFAWebServiceAssetDetail:
    """Tests for asset detail context formatting."""

    def test_asset_detail_uses_stored_carrying_amount_fields(self):
        """NBV should use the stored carrying amount, not a recomputed shortcut."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        category_id = uuid.uuid4()
        asset = MockAsset(
            asset_id=asset_id,
            organization_id=org_id,
            category_id=category_id,
            acquisition_cost=Decimal("5000.00"),
            net_book_value=Decimal("3600.00"),
        )
        asset.accumulated_depreciation = Decimal("1000.00")
        asset.revalued_amount = Decimal("4200.00")
        asset.impairment_loss = Decimal("600.00")
        asset.useful_life_months = 60
        asset.residual_value = Decimal("0.00")
        category = MockAssetCategory(
            category_id=category_id,
            organization_id=org_id,
            category_name="ICT Equipment",
        )
        auth = WebAuthContext(
            is_authenticated=True,
            person_id=uuid.uuid4(),
            organization_id=org_id,
            user_name="Test User",
            user_initials="TU",
        )
        request = MagicMock()

        mock_db.get.side_effect = [asset, category]

        captured: dict[str, object] = {}

        def _capture_template_response(_request, _template_name, context):
            captured["context"] = context
            return context

        with (
            patch("app.services.fixed_assets.web.base_context", return_value={}),
            patch(
                "app.services.fixed_assets.web.templates.TemplateResponse",
                side_effect=_capture_template_response,
            ),
        ):
            FixedAssetWebService().asset_detail_response(
                request,
                auth,
                mock_db,
                str(asset_id),
            )

        asset_view = captured["context"]["asset"]
        assert asset_view["acquisition_cost"] == "USD 5,000.00"
        assert asset_view["revalued_amount"] == "USD 4,200.00"
        assert asset_view["accumulated_depreciation"] == "USD 1,000.00"
        assert asset_view["impairment_loss"] == "USD 600.00"
        assert asset_view["net_book_value"] == "USD 3,600.00"


class TestFAWebServiceAssetCountSheets:
    """Tests for fixed asset count sheet reporting."""

    def test_asset_count_sheet_context_computes_physical_variance(self):
        """Found and discrepancy lines count as physically present; missing does not."""
        from app.models.people.assets.audit import AssetAuditLineStatus
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        plan_id = uuid.uuid4()
        category_id = uuid.uuid4()
        location_id = uuid.uuid4()
        plan = SimpleNamespace(
            audit_plan_id=plan_id,
            organization_id=org_id,
            plan_number="AAC-0001",
            title="May Count",
            planned_date=date(2026, 5, 1),
            status="COMPLETED",
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
        category = SimpleNamespace(
            category_id=category_id,
            category_code="ICT",
            category_name="IT Equipment",
        )
        location = SimpleNamespace(
            location_id=location_id,
            location_name="HQ",
        )

        def scalar_result(items):
            result = MagicMock()
            result.all.return_value = items
            return result

        mock_db.scalars.side_effect = [
            scalar_result([plan]),
            scalar_result([category]),
            scalar_result([location]),
        ]
        mock_db.get.return_value = plan
        mock_db.execute.return_value.all.return_value = [
            SimpleNamespace(
                asset_id=uuid.uuid4(),
                asset_number="FA-001",
                asset_name="Laptop",
                serial_number="SN-1",
                system_status="IN_USE",
                category_id=category_id,
                category_code="ICT",
                category_name="IT Equipment",
                location_id=location_id,
                location_code="HQ",
                location_name="HQ",
                line_status=AssetAuditLineStatus.FOUND,
                is_found=True,
                physical_check_at=datetime(2026, 5, 2, tzinfo=UTC),
                discrepancy_notes=None,
            ),
            SimpleNamespace(
                asset_id=uuid.uuid4(),
                asset_number="FA-002",
                asset_name="Monitor",
                serial_number="SN-2",
                system_status="IN_USE",
                category_id=category_id,
                category_code="ICT",
                category_name="IT Equipment",
                location_id=location_id,
                location_code="HQ",
                location_name="HQ",
                line_status=AssetAuditLineStatus.DISCREPANCY,
                is_found=True,
                physical_check_at=datetime(2026, 5, 2, tzinfo=UTC),
                discrepancy_notes="Found in another room",
            ),
            SimpleNamespace(
                asset_id=uuid.uuid4(),
                asset_number="FA-003",
                asset_name="Printer",
                serial_number="SN-3",
                system_status="IN_USE",
                category_id=category_id,
                category_code="ICT",
                category_name="IT Equipment",
                location_id=location_id,
                location_code="HQ",
                location_name="HQ",
                line_status=AssetAuditLineStatus.MISSING,
                is_found=False,
                physical_check_at=datetime(2026, 5, 2, tzinfo=UTC),
                discrepancy_notes="Not found",
            ),
        ]

        result = FixedAssetWebService.asset_count_sheet_context(
            mock_db,
            str(org_id),
            audit_plan_id=str(plan_id),
        )

        assert result["has_count_plan"] is True
        assert result["count_sheet_totals"]["system_qty"] == 3
        assert result["count_sheet_totals"]["physical_qty"] == 2
        assert result["count_sheet_totals"]["variance_qty"] == -1
        assert result["count_sheet_totals"]["variance_count"] == 1
        assert result["summary_rows"][0]["system_qty"] == 3
        assert result["summary_rows"][0]["physical_qty"] == 2
        assert result["count_sheet_rows"][2]["has_variance"] is True

    def test_export_asset_count_sheet_csv_response_returns_csv(self):
        """Asset count sheet CSV export should include rows and totals."""
        from app.services.fixed_assets.web import FixedAssetWebService

        plan = SimpleNamespace(plan_number="AAC-0001", title="May Count")
        context = {
            "selected_plan": plan,
            "count_sheet_rows": [
                {
                    "asset_number": "FA-001",
                    "asset_name": "Laptop",
                    "serial_number": "SN-1",
                    "location_name": "HQ",
                    "category_name": "ICT - IT Equipment",
                    "system_status": "IN_USE",
                    "line_status": "FOUND",
                    "system_qty": 1,
                    "physical_qty": 1,
                    "variance_qty": 0,
                    "physical_check_at": "2026-05-02",
                    "discrepancy_notes": "",
                }
            ],
            "count_sheet_totals": {
                "system_qty": 1,
                "physical_qty": 1,
                "variance_qty": 0,
                "variance_count": 0,
                "unchecked_qty": 0,
            },
        }

        with patch.object(
            FixedAssetWebService,
            "asset_count_sheet_context",
            return_value=context,
        ):
            response = FixedAssetWebService.export_asset_count_sheet_csv_response(
                MagicMock(),
                str(uuid.uuid4()),
                audit_plan_id=str(uuid.uuid4()),
            )

        assert response.media_type == "text/csv"
        assert (
            response.headers["Content-Disposition"]
            == 'attachment; filename="asset_count_sheets_aac_0001.csv"'
        )
        body = response.body.decode()
        assert "Audit Plan,Asset Number,Asset Name,Serial Number" in body
        assert "AAC-0001 - May Count,FA-001,Laptop,SN-1,HQ" in body
        assert "AAC-0001 - May Count,Total,,,,,,,1,1,0,," in body

    def test_export_asset_count_sheet_pdf_response_returns_pdf(self):
        """Asset count sheet PDF export should render through ReportPDFService."""
        from app.services.fixed_assets.web import FixedAssetWebService

        plan = SimpleNamespace(plan_number="AAC-0001", title="May Count")
        context = {
            "selected_plan": plan,
            "count_sheet_rows": [],
            "summary_rows": [],
            "count_sheet_totals": {
                "system_qty": 0,
                "physical_qty": 0,
                "variance_qty": 0,
                "variance_count": 0,
                "unchecked_qty": 0,
            },
        }
        captured: dict[str, object] = {}

        def fake_render(self, report_name, organization_id, render_context):
            captured["report_name"] = report_name
            captured["organization_id"] = organization_id
            captured["context"] = render_context
            return b"%PDF-1.4 asset count"

        org_id = str(uuid.uuid4())
        with (
            patch.object(
                FixedAssetWebService,
                "asset_count_sheet_context",
                return_value=context,
            ),
            patch("app.services.finance.rpt.pdf.ReportPDFService.render", fake_render),
        ):
            response = FixedAssetWebService.export_asset_count_sheet_pdf_response(
                MagicMock(),
                org_id,
                audit_plan_id=str(uuid.uuid4()),
            )

        assert response.media_type == "application/pdf"
        assert response.body == b"%PDF-1.4 asset count"
        assert (
            response.headers["Content-Disposition"]
            == 'attachment; filename="asset_count_sheets_aac_0001.pdf"'
        )
        assert captured["report_name"] == "asset_count_sheets"
        assert captured["organization_id"] == org_id
        assert captured["context"]["row_count"] == 0
        assert captured["context"]["plan_label"] == "AAC-0001 - May Count"


class TestFAWebServiceCountPlans:
    """Tests for fixed asset count plan web actions."""

    def test_create_count_plan_response_commits_and_redirects(self):
        """Creating a count plan should call the audit service and commit."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        plan_id = uuid.uuid4()
        plan = SimpleNamespace(audit_plan_id=plan_id)

        with patch(
            "app.services.fixed_assets.web.AssetAuditService.create_plan",
            return_value=plan,
        ) as create_mock:
            response = FixedAssetWebService.create_count_plan_response(
                mock_db,
                str(org_id),
                user_id,
                "May Count",
                "2026-05-08",
            )

        create_mock.assert_called_once()
        assert create_mock.call_args.args[0] == org_id
        assert create_mock.call_args.kwargs["title"] == "May Count"
        assert create_mock.call_args.kwargs["planned_date"] == date(2026, 5, 8)
        assert create_mock.call_args.kwargs["created_by_user_id"] == user_id
        mock_db.commit.assert_called_once()
        assert response.status_code == 303
        assert str(plan_id) in response.headers["location"]

    def test_start_count_plan_response_commits_and_redirects(self):
        """Starting a count plan should delegate to the audit service."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        plan_id = uuid.uuid4()

        with patch(
            "app.services.fixed_assets.web.AssetAuditService.start_plan"
        ) as start_mock:
            response = FixedAssetWebService.start_count_plan_response(
                mock_db,
                str(org_id),
                str(plan_id),
            )

        start_mock.assert_called_once_with(org_id, plan_id)
        mock_db.commit.assert_called_once()
        assert response.status_code == 303
        assert str(plan_id) in response.headers["location"]

    def test_check_count_plan_line_found_uses_expected_state(self):
        """Found action should record the expected location/status to avoid false variance."""
        from app.models.people.assets.audit import AssetAuditLineStatus
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        plan_id = uuid.uuid4()
        line_id = uuid.uuid4()
        expected_location_id = uuid.uuid4()
        user_id = uuid.uuid4()
        line = SimpleNamespace(
            audit_line_id=line_id,
            organization_id=org_id,
            audit_plan_id=plan_id,
            expected_location_id=expected_location_id,
            expected_status="IN_USE",
            status=AssetAuditLineStatus.PENDING,
        )
        mock_db.get.return_value = line

        with patch(
            "app.services.fixed_assets.web.AssetAuditService.record_check"
        ) as record_mock:
            response = FixedAssetWebService.check_count_plan_line_response(
                mock_db,
                str(org_id),
                user_id,
                str(plan_id),
                str(line_id),
                "found",
            )

        record_mock.assert_called_once_with(
            org_id,
            line_id,
            is_found=True,
            observed_location_id=expected_location_id,
            observed_status="IN_USE",
            discrepancy_notes=None,
            checked_by_user_id=user_id,
        )
        mock_db.commit.assert_called_once()
        assert response.status_code == 303

    def test_mark_count_plan_pending_found_records_all_pending_lines(self):
        """Bulk found action should record expected state for every pending line."""
        from app.models.people.assets.audit import AssetAuditPlanStatus
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        plan_id = uuid.uuid4()
        user_id = uuid.uuid4()
        plan = SimpleNamespace(
            audit_plan_id=plan_id,
            organization_id=org_id,
            status=AssetAuditPlanStatus.IN_PROGRESS,
        )
        loc_one = uuid.uuid4()
        loc_two = uuid.uuid4()
        lines = [
            SimpleNamespace(
                audit_line_id=uuid.uuid4(),
                expected_location_id=loc_one,
                expected_status="IN_USE",
            ),
            SimpleNamespace(
                audit_line_id=uuid.uuid4(),
                expected_location_id=loc_two,
                expected_status="IN_STORE",
            ),
        ]
        scalars_result = MagicMock()
        scalars_result.all.return_value = lines
        mock_db.get.return_value = plan
        mock_db.scalars.return_value = scalars_result

        with patch(
            "app.services.fixed_assets.web.AssetAuditService.record_check"
        ) as record_mock:
            response = FixedAssetWebService.mark_count_plan_pending_found_response(
                mock_db,
                str(org_id),
                user_id,
                str(plan_id),
            )

        assert record_mock.call_count == 2
        record_mock.assert_any_call(
            org_id,
            lines[0].audit_line_id,
            is_found=True,
            observed_location_id=loc_one,
            observed_status="IN_USE",
            discrepancy_notes=None,
            checked_by_user_id=user_id,
        )
        record_mock.assert_any_call(
            org_id,
            lines[1].audit_line_id,
            is_found=True,
            observed_location_id=loc_two,
            observed_status="IN_STORE",
            discrepancy_notes=None,
            checked_by_user_id=user_id,
        )
        mock_db.commit.assert_called_once()
        assert response.status_code == 303
        assert "Marked+2+pending+assets+as+found" in response.headers["location"]


class TestFAWebServiceDepreciationRunForm:
    """Tests for depreciation run form defaults."""

    def test_depreciation_run_form_preselects_recommended_period(self):
        """The form should default to the next due recommended fiscal period."""
        from app.models.finance.gl.fiscal_period import PeriodStatus
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        recommended_period_id = uuid.uuid4()
        fallback_period_id = uuid.uuid4()
        mock_db.execute.return_value.all.return_value = [
            (
                fallback_period_id,
                "May 2026",
                date(2026, 5, 1),
                date(2026, 5, 31),
                PeriodStatus.OPEN,
            ),
            (
                recommended_period_id,
                "April 2026",
                date(2026, 4, 1),
                date(2026, 4, 30),
                PeriodStatus.REOPENED,
            ),
        ]

        with patch(
            "app.services.fixed_assets.web.DepreciationService.get_next_automation_period",
            return_value=SimpleNamespace(fiscal_period_id=recommended_period_id),
        ):
            result = FixedAssetWebService().depreciation_run_form_context(
                mock_db,
                str(org_id),
            )

        assert result["period"] == str(recommended_period_id)
        assert result["recommended_period_id"] == str(recommended_period_id)
        assert result["fiscal_periods"][1]["is_recommended"] is True

    def test_depreciation_run_form_falls_back_to_latest_open_period(self):
        """Without a due recommendation, the latest posting-eligible period wins."""
        from app.models.finance.gl.fiscal_period import PeriodStatus
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        open_period_id = uuid.uuid4()
        closed_period_id = uuid.uuid4()
        mock_db.execute.return_value.all.return_value = [
            (
                open_period_id,
                "May 2026",
                date(2026, 5, 1),
                date(2026, 5, 31),
                PeriodStatus.OPEN,
            ),
            (
                closed_period_id,
                "April 2026",
                date(2026, 4, 1),
                date(2026, 4, 30),
                PeriodStatus.HARD_CLOSED,
            ),
        ]

        with patch(
            "app.services.fixed_assets.web.DepreciationService.get_next_automation_period",
            return_value=None,
        ):
            result = FixedAssetWebService().depreciation_run_form_context(
                mock_db,
                str(org_id),
            )

        assert result["period"] == str(open_period_id)
        assert result["recommended_period_id"] is None


class TestFAWebServiceRunDepreciation:
    """Tests for depreciation run submission behavior."""

    @pytest.mark.asyncio
    async def test_run_depreciation_calculates_without_posting(self):
        """Web submit should calculate only, leaving posting for a separate step."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        period_id = uuid.uuid4()
        auth = WebAuthContext(
            is_authenticated=True,
            person_id=user_id,
            organization_id=org_id,
            user_name="Test User",
            user_initials="TU",
        )
        request = MagicMock()
        request.form = AsyncMock(
            return_value={
                "fiscal_period_id": str(period_id),
                "posting_date": "2026-04-30",
            }
        )
        run = SimpleNamespace(run_id=uuid.uuid4())

        with (
            patch(
                "app.services.fixed_assets.web.DepreciationService.create_depreciation_run",
                return_value=run,
            ) as create_mock,
            patch(
                "app.services.fixed_assets.web.DepreciationService.calculate_run",
                return_value=run,
            ) as calculate_mock,
            patch(
                "app.services.fixed_assets.web.DepreciationService.post_run"
            ) as post_mock,
        ):
            response = await FixedAssetWebService().run_depreciation_response(
                request,
                auth,
                mock_db,
            )

        create_mock.assert_called_once()
        calculate_mock.assert_called_once_with(mock_db, org_id, run.run_id)
        post_mock.assert_not_called()
        assert response.status_code == 303
        assert str(run.run_id) in response.headers["location"]
        assert "Awaiting+posting" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_post_depreciation_run_response_posts_calculated_run(self):
        """Posting a calculated run should call the posting service and redirect back."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        run_id = uuid.uuid4()
        auth = WebAuthContext(
            is_authenticated=True,
            person_id=user_id,
            organization_id=org_id,
            user_name="Test User",
            user_initials="TU",
        )
        request = MagicMock()
        request.form = AsyncMock(return_value={"posting_date": "2026-04-30"})

        with patch(
            "app.services.fixed_assets.web.DepreciationService.post_run"
        ) as post_mock:
            response = await FixedAssetWebService().post_depreciation_run_response(
                request,
                auth,
                mock_db,
                str(run_id),
            )

        post_mock.assert_called_once_with(
            mock_db,
            org_id,
            run_id,
            user_id,
            posting_date=date(2026, 4, 30),
        )
        assert response.status_code == 303
        assert str(run_id) in response.headers["location"]
        assert "posted+successfully" in response.headers["location"]

    def test_list_assets_context_with_category_uuid(self):
        """Test assets list context with category UUID filter."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_db.scalar.return_value = 0
        mock_db.execute.return_value.all.return_value = []

        result = FixedAssetWebService.list_assets_context(
            mock_db,
            str(org_id),
            search=None,
            category=str(category_id),
            status=None,
            location=None,
            page=1,
        )

        assert result["category"] == str(category_id)

    def test_list_assets_context_with_category_code(self):
        """Test assets list context with category code filter."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_db.scalar.return_value = 0
        mock_db.execute.return_value.all.return_value = []

        result = FixedAssetWebService.list_assets_context(
            mock_db,
            str(org_id),
            search=None,
            category="EQUIPMENT",
            status=None,
            location=None,
            page=1,
        )

        assert result["category"] == "EQUIPMENT"

    def test_list_assets_context_with_location(self):
        """Location filter should be preserved in the returned context."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        location_id = uuid.uuid4()

        mock_db.scalar.return_value = 0
        mock_db.execute.return_value.all.return_value = []
        result = FixedAssetWebService.list_assets_context(
            mock_db,
            str(org_id),
            search=None,
            category=None,
            status=None,
            location=str(location_id),
            page=1,
        )

        assert result["location"] == str(location_id)

    def test_build_asset_query_filters_by_location_uuid(self):
        """Asset query should include location_id filtering when provided."""
        from app.services.fixed_assets.asset_query import build_asset_query

        org_id = uuid.uuid4()
        location_id = uuid.uuid4()

        query = build_asset_query(
            db=MagicMock(),
            organization_id=str(org_id),
            location=str(location_id),
        )
        compiled = query.compile()

        assert "asset.location_id" in str(compiled)
        assert location_id in compiled.params.values()


class TestFAWebServiceDepreciation:
    """Tests for depreciation_context method."""

    def test_depreciation_context_success(self):
        """Test successful depreciation context."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_run = MockDepreciationRun(organization_id=org_id)
        mock_period = MockFiscalPeriod()

        mock_db.scalar.return_value = 1
        mock_db.execute.return_value.all.return_value = [(mock_run, mock_period)]

        result = FixedAssetWebService.depreciation_context(
            mock_db,
            str(org_id),
            asset_id=None,
            period=None,
        )

        assert "depreciation_runs" in result
        assert len(result["depreciation_runs"]) == 1
        assert result["total_count"] == 1
        assert result["depreciation_runs"][0]["detail_url"].endswith(
            str(mock_run.run_id)
        )

    def test_depreciation_context_with_period_filter(self):
        """Test depreciation context with period filter."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        period_id = uuid.uuid4()

        mock_db.scalar.return_value = 0
        mock_db.execute.return_value.all.return_value = []

        result = FixedAssetWebService.depreciation_context(
            mock_db,
            str(org_id),
            asset_id=None,
            period=str(period_id),
        )

        assert result["period"] == str(period_id)

    def test_depreciation_context_pagination(self):
        """Test depreciation context pagination."""
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_db.scalar.return_value = 100
        mock_db.execute.return_value.all.return_value = []

        result = FixedAssetWebService.depreciation_context(
            mock_db,
            str(org_id),
            asset_id=None,
            period=None,
            page=2,
            limit=10,
        )

        assert result["page"] == 2
        assert result["limit"] == 10
        assert result["offset"] == 10
        assert result["total_count"] == 100
        assert result["total_pages"] == 10

    def test_depreciation_run_detail_context_success(self):
        """Test depreciation run detail context with schedule rows."""
        from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        run_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        category_id = uuid.uuid4()
        expense_account_id = uuid.uuid4()
        accum_account_id = uuid.uuid4()

        mock_run = MockDepreciationRun(
            run_id=run_id,
            organization_id=org_id,
            fiscal_period_id=uuid.uuid4(),
            status=DepreciationRunStatus.CALCULATED,
        )
        mock_period = MockFiscalPeriod(period_name="April 2026")
        mock_asset = MockAsset(
            asset_id=asset_id,
            organization_id=org_id,
            asset_number="FA000001",
            asset_name="All in One Desktop",
            currency_code="NGN",
            category_id=category_id,
        )
        mock_category = MockAssetCategory(
            category_id=category_id,
            organization_id=org_id,
            category_name="ICT Equipment",
        )
        mock_schedule = MockDepreciationSchedule(
            run_id=run_id,
            asset_id=asset_id,
            expense_account_id=expense_account_id,
            accumulated_depreciation_account_id=accum_account_id,
        )
        schedule_scalars = MagicMock()
        schedule_scalars.all.return_value = [mock_schedule]
        account_scalars = MagicMock()
        account_scalars.all.return_value = [
            MockAccount(
                account_id=expense_account_id,
                organization_id=org_id,
                account_code="6100",
                account_name="Depreciation Expense",
            ),
            MockAccount(
                account_id=accum_account_id,
                organization_id=org_id,
                account_code="1700",
                account_name="Accumulated Depreciation",
            ),
        ]
        mock_db.scalars.side_effect = [schedule_scalars, account_scalars]

        mock_db.get.side_effect = [mock_run, mock_period]
        mock_db.execute.return_value.all.return_value = [
            (mock_schedule, mock_asset, mock_category)
        ]

        with patch(
            "app.services.fixed_assets.web.org_context_service.get_functional_currency",
            return_value="NGN",
        ):
            result = FixedAssetWebService.depreciation_run_detail_context(
                mock_db,
                str(org_id),
                str(run_id),
            )

        assert result["run"]["run_id"] == str(run_id)
        assert result["period"]["period_name"] == "April 2026"
        assert len(result["schedules"]) == 1
        assert result["schedules"][0]["asset_number"] == "FA000001"
        assert result["schedules"][0]["category_name"] == "ICT Equipment"
        assert result["posting_preview"]["line_count"] == 2
        assert result["posting_preview"]["can_post"] is True

    def test_depreciation_run_detail_context_blocks_creator_posting(self):
        """Creators should see why they cannot post their own depreciation run."""
        from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
        from app.services.fixed_assets.web import FixedAssetWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        run_id = uuid.uuid4()
        creator_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        category_id = uuid.uuid4()
        expense_account_id = uuid.uuid4()
        accum_account_id = uuid.uuid4()

        mock_run = MockDepreciationRun(
            run_id=run_id,
            organization_id=org_id,
            fiscal_period_id=uuid.uuid4(),
            status=DepreciationRunStatus.CALCULATED,
            created_by_user_id=creator_id,
        )
        mock_period = MockFiscalPeriod(period_name="April 2026")
        mock_asset = MockAsset(
            asset_id=asset_id,
            organization_id=org_id,
            asset_number="FA000001",
            asset_name="All in One Desktop",
            currency_code="NGN",
            category_id=category_id,
        )
        mock_category = MockAssetCategory(
            category_id=category_id,
            organization_id=org_id,
            category_name="ICT Equipment",
        )
        mock_schedule = MockDepreciationSchedule(
            run_id=run_id,
            asset_id=asset_id,
            expense_account_id=expense_account_id,
            accumulated_depreciation_account_id=accum_account_id,
        )
        schedule_scalars = MagicMock()
        schedule_scalars.all.return_value = [mock_schedule]
        account_scalars = MagicMock()
        account_scalars.all.return_value = [
            MockAccount(
                account_id=expense_account_id,
                organization_id=org_id,
                account_code="6100",
                account_name="Depreciation Expense",
            ),
            MockAccount(
                account_id=accum_account_id,
                organization_id=org_id,
                account_code="1700",
                account_name="Accumulated Depreciation",
            ),
        ]
        mock_db.scalars.side_effect = [schedule_scalars, account_scalars]
        mock_db.get.side_effect = [mock_run, mock_period]
        mock_db.execute.return_value.all.return_value = [
            (mock_schedule, mock_asset, mock_category)
        ]

        with patch(
            "app.services.fixed_assets.web.org_context_service.get_functional_currency",
            return_value="NGN",
        ):
            result = FixedAssetWebService.depreciation_run_detail_context(
                mock_db,
                str(org_id),
                str(run_id),
                current_user_id=creator_id,
            )

        assert result["posting_preview"]["can_post"] is False
        assert (
            "Segregation of duties"
            in result["posting_preview"]["cannot_post_reason"]
        )


class TestFAWebServiceGLReconciliation:
    """Tests for fixed asset to GL reconciliation context."""

    def test_gl_reconciliation_totals_count_shared_gl_accounts_once(self):
        """Summary totals should not duplicate GL balances for shared accounts."""
        from app.services.fixed_assets.web import FixedAssetWebService

        org_id = uuid.uuid4()
        asset_account_id = uuid.uuid4()
        accum_account_id = uuid.uuid4()
        category_rows = [
            SimpleNamespace(
                category_id=uuid.uuid4(),
                category_code="ICT",
                category_name="ICT Equipment",
                asset_account_id=asset_account_id,
                accumulated_depreciation_account_id=accum_account_id,
                category_count=1,
                category_codes="ICT",
                category_names="ICT Equipment",
                asset_count=1,
                register_cost=Decimal("600.00"),
                register_accumulated_depreciation=Decimal("100.00"),
                register_nbv=Decimal("500.00"),
            ),
            SimpleNamespace(
                category_id=uuid.uuid4(),
                category_code="OPS",
                category_name="Operations Equipment",
                asset_account_id=asset_account_id,
                accumulated_depreciation_account_id=accum_account_id,
                category_count=1,
                category_codes="OPS",
                category_names="Operations Equipment",
                asset_count=1,
                register_cost=Decimal("400.00"),
                register_accumulated_depreciation=Decimal("50.00"),
                register_nbv=Decimal("350.00"),
            ),
        ]
        accounts = [
            SimpleNamespace(
                account_id=asset_account_id,
                account_code="1500",
                account_name="Fixed Assets",
            ),
            SimpleNamespace(
                account_id=accum_account_id,
                account_code="1590",
                account_name="Accumulated Depreciation",
            ),
        ]
        gl_rows = [
            SimpleNamespace(account_id=asset_account_id, balance=Decimal("1000.00")),
            SimpleNamespace(account_id=accum_account_id, balance=Decimal("-200.00")),
        ]
        mock_db = MagicMock()
        mock_db.execute.side_effect = [
            SimpleNamespace(all=lambda: category_rows),
            SimpleNamespace(all=lambda: gl_rows),
        ]
        mock_db.scalars.return_value = SimpleNamespace(all=lambda: accounts)

        with patch(
            "app.services.fixed_assets.web.get_currency_context",
            return_value={
                "presentation_currency_code": "NGN",
                "currencies": [{"code": "NGN", "symbol": "NGN "}],
            },
        ):
            result = FixedAssetWebService.gl_reconciliation_context(
                mock_db,
                str(org_id),
                as_of=date(2026, 4, 30),
            )

        assert result["totals"]["category_count"] == 2
        assert result["totals"]["asset_count"] == 2
        assert result["totals"]["register_nbv"] == Decimal("850.00")
        assert result["totals"]["gl_cost"] == Decimal("1000.00")
        assert result["totals"]["gl_accumulated_depreciation"] == Decimal("200.00")
        assert result["totals"]["gl_nbv"] == Decimal("800.00")
        assert result["totals"]["nbv_variance"] == Decimal("50.00")

    def test_export_gl_reconciliation_csv_response_returns_csv(self):
        """GL reconciliation CSV export should include rows and totals."""
        from app.services.fixed_assets.web import FixedAssetWebService

        context = {
            "as_of": "2026-04-30",
            "as_of_label": "2026-04-30",
            "rows": [
                {
                    "category_code": "PPE",
                    "category_names": "Property Plant Equipment",
                    "asset_count": 2,
                    "asset_account": {
                        "account_code": "1500",
                        "account_name": "Fixed Assets",
                    },
                    "accumulated_depreciation_account": {
                        "account_code": "1590",
                        "account_name": "Accumulated Depreciation",
                    },
                    "register_cost": Decimal("1000.00"),
                    "gl_cost": Decimal("1000.00"),
                    "cost_variance": Decimal("0.00"),
                    "register_accumulated_depreciation": Decimal("150.00"),
                    "gl_accumulated_depreciation": Decimal("200.00"),
                    "accumulated_depreciation_variance": Decimal("-50.00"),
                    "register_nbv": Decimal("850.00"),
                    "gl_nbv": Decimal("800.00"),
                    "nbv_variance": Decimal("50.00"),
                    "is_balanced": False,
                }
            ],
            "totals": {
                "category_count": 1,
                "asset_count": 2,
                "register_cost": Decimal("1000.00"),
                "gl_cost": Decimal("1000.00"),
                "cost_variance": Decimal("0.00"),
                "register_accumulated_depreciation": Decimal("150.00"),
                "gl_accumulated_depreciation": Decimal("200.00"),
                "accumulated_depreciation_variance": Decimal("-50.00"),
                "register_nbv": Decimal("850.00"),
                "gl_nbv": Decimal("800.00"),
                "nbv_variance": Decimal("50.00"),
            },
            "is_balanced": False,
            "currency_prefix": "NGN ",
        }

        with patch.object(
            FixedAssetWebService,
            "gl_reconciliation_context",
            return_value=context,
        ):
            response = FixedAssetWebService.export_gl_reconciliation_csv_response(
                MagicMock(),
                "00000000-0000-0000-0000-000000000001",
                as_of=date(2026, 4, 30),
            )

        assert response.media_type == "text/csv"
        assert (
            response.headers["Content-Disposition"]
            == 'attachment; filename="asset_gl_reconciliation_20260430.csv"'
        )
        body = response.body.decode()
        assert "As Of,Category Mapping,Category Names,Assets" in body
        assert (
            "2026-04-30,PPE,Property Plant Equipment,2,1500 - Fixed Assets,1590 - Accumulated Depreciation"
            in body
        )
        assert "2026-04-30,Total,,2,,," in body

    def test_export_gl_reconciliation_pdf_response_returns_pdf(self):
        """GL reconciliation PDF export should render through ReportPDFService."""
        from app.services.fixed_assets.web import FixedAssetWebService

        context = {
            "as_of": "2026-04-30",
            "as_of_label": "2026-04-30",
            "rows": [],
            "totals": {
                "asset_count": 0,
                "register_cost": Decimal("0"),
                "gl_cost": Decimal("0"),
                "cost_variance": Decimal("0"),
                "register_accumulated_depreciation": Decimal("0"),
                "gl_accumulated_depreciation": Decimal("0"),
                "accumulated_depreciation_variance": Decimal("0"),
                "register_nbv": Decimal("0"),
                "gl_nbv": Decimal("0"),
                "nbv_variance": Decimal("0"),
            },
            "is_balanced": True,
            "currency_prefix": "NGN ",
        }
        captured: dict[str, object] = {}

        def fake_render(self, report_name, organization_id, render_context):
            captured["report_name"] = report_name
            captured["organization_id"] = organization_id
            captured["context"] = render_context
            return b"%PDF-1.4 asset gl"

        with (
            patch.object(
                FixedAssetWebService,
                "gl_reconciliation_context",
                return_value=context,
            ),
            patch("app.services.finance.rpt.pdf.ReportPDFService.render", fake_render),
        ):
            response = FixedAssetWebService.export_gl_reconciliation_pdf_response(
                MagicMock(),
                "00000000-0000-0000-0000-000000000001",
                as_of=date(2026, 4, 30),
            )

        assert response.media_type == "application/pdf"
        assert response.body == b"%PDF-1.4 asset gl"
        assert (
            response.headers["Content-Disposition"]
            == 'attachment; filename="asset_gl_reconciliation_20260430.pdf"'
        )
        assert captured["report_name"] == "asset_gl_reconciliation"
        assert captured["organization_id"] == "00000000-0000-0000-0000-000000000001"
        assert captured["context"]["row_count"] == 0


class TestFAWebServiceAssetUpdate:
    """Tests for asset update response handling."""

    @pytest.mark.asyncio
    async def test_update_asset_response_filters_pre_use_only_fields_for_in_use_asset(
        self,
    ):
        """In-use assets should ignore pre-use-only form fields and still save."""
        from app.models.fixed_assets.asset import AssetStatus
        from app.services.fixed_assets.web import FixedAssetWebService

        service = FixedAssetWebService()
        org_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        location_id = uuid.uuid4()
        active_asset = MockAsset(
            asset_id=asset_id,
            organization_id=org_id,
            status=AssetStatus.IN_USE,
        )

        mock_db = MagicMock()
        mock_db.get.return_value = active_asset

        request = MagicMock()
        request.form = AsyncMock(
            return_value={
                "asset_name": "Renamed Asset",
                "category_id": str(uuid.uuid4()),
                "serial_number": "SN-200",
                "location_id": str(location_id),
                "description": "Updated description",
                "status": "UNDER_REPAIR",
                "depreciation_schedule_id": "",
                "asset_number": "FA-200",
                "currency_code": "EUR",
                "acquisition_date": "2026-04-01",
                "acquisition_cost": "9000.00",
            }
        )
        auth = WebAuthContext(
            is_authenticated=True,
            organization_id=org_id,
            person_id=uuid.uuid4(),
        )

        with patch(
            "app.services.fixed_assets.web.asset_service.update_asset"
        ) as mock_update:
            response = await service.update_asset_response(
                request=request,
                auth=auth,
                db=mock_db,
                asset_id=str(asset_id),
            )

        assert response.status_code == 303
        assert "success=Asset+updated" in response.headers["location"]
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()

        updates = mock_update.call_args.args[3]
        assert updates == {
            "serial_number": "SN-200",
            "location_id": location_id,
            "custodian_employee_id": None,
            "description": "Updated description",
            "status": AssetStatus.UNDER_REPAIR,
            "current_depreciation_schedule_id": None,
        }

    @pytest.mark.asyncio
    async def test_update_asset_response_saves_editable_form_fields_for_pre_use_asset(
        self,
    ):
        """Pre-use assets should persist asset number, currency, and schedule."""
        from app.models.fixed_assets.asset import AssetStatus
        from app.services.fixed_assets.web import FixedAssetWebService

        service = FixedAssetWebService()
        org_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        location_id = uuid.uuid4()
        schedule_id = uuid.uuid4()
        asset = MockAsset(
            asset_id=asset_id,
            organization_id=org_id,
            status=AssetStatus.NOT_IN_USE,
        )

        mock_db = MagicMock()
        mock_db.get.return_value = asset

        request = MagicMock()
        request.form = AsyncMock(
            return_value={
                "asset_number": "FA-200",
                "asset_name": "Renamed Asset",
                "category_id": str(uuid.uuid4()),
                "serial_number": "SN-200",
                "location_id": str(location_id),
                "description": "Updated description",
                "status": "IN_STORE",
                "acquisition_date": "2026-04-01",
                "acquisition_cost": "9000.00",
                "currency_code": "EUR",
                "depreciation_schedule_id": str(schedule_id),
            }
        )
        auth = WebAuthContext(
            is_authenticated=True,
            organization_id=org_id,
            person_id=uuid.uuid4(),
        )

        with patch(
            "app.services.fixed_assets.web.asset_service.update_asset"
        ) as mock_update:
            response = await service.update_asset_response(
                request=request,
                auth=auth,
                db=mock_db,
                asset_id=str(asset_id),
            )

        assert response.status_code == 303
        updates = mock_update.call_args.args[3]
        assert updates["asset_number"] == "FA-200"
        assert updates["currency_code"] == "EUR"
        assert updates["status"] == AssetStatus.IN_STORE
        assert updates["current_depreciation_schedule_id"] == schedule_id

    @pytest.mark.asyncio
    async def test_update_asset_response_saves_custodian_assignment(self):
        """Assigned employee should be forwarded in asset updates."""
        from app.models.fixed_assets.asset import AssetStatus
        from app.services.fixed_assets.web import FixedAssetWebService

        service = FixedAssetWebService()
        org_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        employee_id = uuid.uuid4()
        department_id = uuid.uuid4()
        asset = MockAsset(
            asset_id=asset_id,
            organization_id=org_id,
            status=AssetStatus.IN_USE,
        )

        mock_db = MagicMock()
        mock_db.get.return_value = asset

        request = MagicMock()
        request.form = AsyncMock(
            return_value={
                "serial_number": "SN-200",
                "location_id": "",
                "department_id": str(department_id),
                "custodian_employee_id": str(employee_id),
                "description": "",
                "status": "IN_USE",
                "depreciation_schedule_id": "",
            }
        )
        auth = WebAuthContext(
            is_authenticated=True,
            organization_id=org_id,
            person_id=uuid.uuid4(),
        )

        with (
            patch.object(
                service,
                "_validate_assignment_selection",
                return_value=(department_id, employee_id),
            ) as mock_validate,
            patch(
                "app.services.fixed_assets.web.asset_service.update_asset"
            ) as mock_update,
        ):
            response = await service.update_asset_response(
                request=request,
                auth=auth,
                db=mock_db,
                asset_id=str(asset_id),
            )

        assert response.status_code == 303
        mock_validate.assert_called_once()
        updates = mock_update.call_args.args[3]
        assert updates["custodian_employee_id"] == employee_id


class TestFAWebServiceAssetCreate:
    """Tests for asset create response handling."""

    def test_create_asset_response_commits_on_success(self):
        """Successful asset creation should commit before redirecting."""
        from app.services.fixed_assets.web import FixedAssetWebService

        service = FixedAssetWebService()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        category_id = uuid.uuid4()
        location_id = uuid.uuid4()
        department_id = uuid.uuid4()
        employee_id = uuid.uuid4()
        created_asset = MockAsset(
            organization_id=org_id,
            category_id=category_id,
            asset_name="Bill Counter",
        )

        mock_db = MagicMock()
        request = MagicMock()
        auth = WebAuthContext(
            is_authenticated=True,
            organization_id=org_id,
            person_id=user_id,
        )

        with (
            patch(
                "app.services.fixed_assets.web.asset_service.create_asset",
                return_value=created_asset,
            ) as mock_create,
            patch.object(
                service,
                "_validate_assignment_selection",
                return_value=(department_id, employee_id),
            ) as mock_validate,
        ):
            response = service.create_asset_response(
                request=request,
                auth=auth,
                asset_number=None,
                asset_name="Bill Counter",
                serial_number="106170kol57544",
                location_id=str(location_id),
                department_id=str(department_id),
                custodian_employee_id=str(employee_id),
                category_id=str(category_id),
                acquisition_date="2026-04-27",
                acquisition_cost="0",
                currency_code="NGN",
                status="IN_USE",
                description=None,
                depreciation_schedule_id=None,
                db=mock_db,
            )

        assert response.status_code == 303
        assert "success=Record+created+successfully" in response.headers["location"]
        mock_create.assert_called_once()
        mock_validate.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()
        asset_input = mock_create.call_args.args[2]
        assert asset_input.custodian_user_id == employee_id

    def test_create_asset_response_rolls_back_on_error(self):
        """Create errors should rollback the session before re-rendering the form."""
        from app.services.fixed_assets.web import FixedAssetWebService

        service = FixedAssetWebService()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_db = MagicMock()
        mock_db.get.return_value = None
        request = MagicMock()
        auth = WebAuthContext(
            is_authenticated=True,
            organization_id=org_id,
            person_id=user_id,
        )

        with (
            patch(
                "app.services.fixed_assets.web.asset_service.create_asset",
                side_effect=ValueError("boom"),
            ),
            patch(
                "app.services.fixed_assets.web.base_context",
                return_value={},
            ),
            patch.object(
                service,
                "asset_form_context",
                return_value={},
            ),
            patch(
                "app.services.fixed_assets.web.templates.TemplateResponse",
                return_value=MagicMock(),
            ),
        ):
            service.create_asset_response(
                request=request,
                auth=auth,
                asset_number=None,
                asset_name="Bill Counter",
                serial_number="106170kol57544",
                location_id=None,
                department_id=None,
                custodian_employee_id=None,
                category_id=str(category_id),
                acquisition_date="2026-04-27",
                acquisition_cost="0",
                currency_code="NGN",
                status="IN_USE",
                description=None,
                depreciation_schedule_id=None,
                db=mock_db,
            )

        mock_db.rollback.assert_called_once()
