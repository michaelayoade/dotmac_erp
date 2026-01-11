"""
E2E Tests for Dashboard.

Tests the dashboard UI flows using Playwright.
"""

import re

import pytest
from playwright.sync_api import Page, expect


class TestDashboardNavigation:
    """Test dashboard navigation and page loads."""

    @pytest.mark.e2e
    def test_dashboard_loads(self, dashboard_page: Page):
        """Test that dashboard page loads successfully."""
        # Verify we're on the dashboard
        expect(dashboard_page).to_have_url(re.compile(r".*/dashboard.*"))
        expect(dashboard_page.get_by_test_id("page-title")).to_contain_text("Dashboard")

    @pytest.mark.e2e
    def test_dashboard_has_title(self, dashboard_page: Page):
        """Test that dashboard has a title."""
        expect(dashboard_page.get_by_test_id("page-title")).to_be_visible()

    @pytest.mark.e2e
    def test_dashboard_navigation_links(self, dashboard_page: Page, base_url: str):
        """Test that navigation links work."""
        nav = dashboard_page.get_by_test_id("sidebar-nav")
        expect(nav).to_be_visible()


class TestDashboardStats:
    """Test dashboard statistics display."""

    @pytest.mark.e2e
    def test_dashboard_displays_stats(self, dashboard_page: Page):
        """Test that dashboard shows statistics."""
        expect(dashboard_page.get_by_test_id("dashboard-stats")).to_be_visible()


class TestDashboardInteractions:
    """Test dashboard interactive elements."""

    @pytest.mark.e2e
    def test_dashboard_responsive(self, authenticated_page: Page, base_url: str):
        """Test dashboard is responsive."""
        # Test mobile viewport
        authenticated_page.set_viewport_size({"width": 375, "height": 667})
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

        # Test tablet viewport
        authenticated_page.set_viewport_size({"width": 768, "height": 1024})
        authenticated_page.reload()
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

        # Test desktop viewport
        authenticated_page.set_viewport_size({"width": 1920, "height": 1080})
        authenticated_page.reload()
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()


class TestDashboardMetrics:
    """Test dashboard metrics display and verification."""

    @pytest.mark.e2e
    def test_dashboard_has_financial_metrics(self, authenticated_page: Page, base_url: str):
        """Test dashboard displays financial metrics."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("dashboard-stat-total-revenue")).to_be_visible()
        expect(authenticated_page.get_by_test_id("dashboard-stat-total-expenses")).to_be_visible()
        expect(authenticated_page.get_by_test_id("dashboard-stat-net-income")).to_be_visible()

    @pytest.mark.e2e
    def test_dashboard_cash_flow_summary(self, authenticated_page: Page, base_url: str):
        """Test dashboard shows cash flow summary."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        cash_flow = authenticated_page.get_by_test_id("dashboard-cash-flow")
        expect(cash_flow).to_be_visible()
        expect(cash_flow.get_by_text("Net Cash Flow")).to_be_visible()

    @pytest.mark.e2e
    def test_dashboard_aging_overview(self, authenticated_page: Page, base_url: str):
        """Test dashboard shows aging overview."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        aging_overview = authenticated_page.get_by_test_id("dashboard-aging-overview")
        expect(aging_overview).to_be_visible()
        expect(aging_overview.get_by_text("Aging Overview")).to_be_visible()

    @pytest.mark.e2e
    def test_dashboard_open_invoices_metric(self, authenticated_page: Page, base_url: str):
        """Test dashboard shows open invoices."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("dashboard-stat-open-invoices")).to_be_visible()


class TestDashboardDrillDown:
    """Test dashboard drill-down navigation."""

    @pytest.mark.e2e
    def test_metric_click_navigates(self, authenticated_page: Page, base_url: str):
        """Test clicking a metric navigates to detail view."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        link = authenticated_page.get_by_test_id("dashboard-link-journals")
        expect(link).to_be_visible()
        link.click()
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/journals.*"))

    @pytest.mark.e2e
    def test_ar_aging_drilldown(self, authenticated_page: Page, base_url: str):
        """Test AR aging drill-down from dashboard."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        ar_link = authenticated_page.get_by_test_id("dashboard-link-ar-aging")
        expect(ar_link).to_be_visible()
        ar_link.click()
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/aging.*"))

    @pytest.mark.e2e
    def test_ap_aging_drilldown(self, authenticated_page: Page, base_url: str):
        """Test AP aging drill-down from dashboard."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        ap_link = authenticated_page.get_by_test_id("dashboard-link-ap-aging")
        expect(ap_link).to_be_visible()
        ap_link.click()
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/aging.*"))


class TestDashboardCharts:
    """Test dashboard charts and visualizations."""

    @pytest.mark.e2e
    def test_dashboard_has_charts(self, authenticated_page: Page, base_url: str):
        """Test dashboard displays charts."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        charts = authenticated_page.get_by_test_id("dashboard-cash-flow").locator("svg")
        assert charts.count() > 0

    @pytest.mark.e2e
    def test_dashboard_cash_flow_period_label(self, authenticated_page: Page, base_url: str):
        """Test cash flow period label displays."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        cash_flow = authenticated_page.get_by_test_id("dashboard-cash-flow")
        expect(cash_flow.get_by_text("Last 30 days")).to_be_visible()


class TestDashboardQuickActions:
    """Test dashboard quick action buttons."""

    @pytest.mark.e2e
    def test_quick_actions_exist(self, authenticated_page: Page, base_url: str):
        """Test quick action buttons exist."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("dashboard-quick-actions")).to_be_visible()

    @pytest.mark.e2e
    def test_new_invoice_quick_action(self, authenticated_page: Page, base_url: str):
        """Test new invoice quick action."""
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        quick_actions = authenticated_page.get_by_test_id("dashboard-quick-actions")
        new_invoice = quick_actions.locator("a[href='/ar/invoices/new']").first
        expect(new_invoice).to_be_visible()
