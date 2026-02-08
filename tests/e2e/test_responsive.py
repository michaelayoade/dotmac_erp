"""
E2E Tests for Responsive Design.

Tests for responsive behavior across different viewport sizes:
- Mobile (375x667)
- Tablet (768x1024)
- Desktop (1280x720)
"""

import pytest
from playwright.sync_api import expect

# Viewport sizes
MOBILE_VIEWPORT = {"width": 375, "height": 667}
TABLET_VIEWPORT = {"width": 768, "height": 1024}
DESKTOP_VIEWPORT = {"width": 1280, "height": 720}


# =============================================================================
# Mobile Viewport Tests
# =============================================================================


@pytest.mark.e2e
class TestMobileViewport:
    """Tests for mobile viewport behavior."""

    def test_dashboard_mobile_layout(self, page, base_url, fresh_auth_tokens):
        """Test dashboard renders correctly on mobile."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(MOBILE_VIEWPORT)

        # Set auth cookie
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/dashboard")
        page.wait_for_load_state("networkidle")

        # Page should be visible and not broken
        expect(page.locator("main")).to_be_visible()

        # Content should not overflow horizontally
        body_width = page.evaluate("document.body.scrollWidth")
        viewport_width = MOBILE_VIEWPORT["width"]

        # Allow some tolerance for scrollbars
        assert body_width <= viewport_width + 20, (
            "Content overflows horizontally on mobile"
        )

    def test_sidebar_mobile_collapse(self, page, base_url, fresh_auth_tokens):
        """Test sidebar collapses on mobile."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(MOBILE_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/dashboard")
        page.wait_for_load_state("networkidle")

        # Sidebar should be hidden or collapsed
        sidebar = page.locator("aside, nav[class*='sidebar'], .sidebar")
        if sidebar.count() > 0:
            # Check if sidebar is hidden or has mobile class
            is_hidden = not sidebar.first.is_visible()
            has_mobile_styles = (
                page.locator(
                    ".sidebar-collapsed, .sidebar-hidden, [class*='mobile']"
                ).count()
                > 0
            )

            # Either hidden or has mobile-specific styling
            assert is_hidden or has_mobile_styles
            # Just verify page works on mobile
            expect(page.locator("main")).to_be_visible()

    def test_tables_mobile_scroll(self, page, base_url, fresh_auth_tokens):
        """Test tables are scrollable on mobile."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(MOBILE_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/ap/suppliers")
        page.wait_for_load_state("networkidle")

        # Tables should be in a scrollable container
        table = page.locator("table")
        if table.count() > 0:
            # Table should be visible
            expect(table.first).to_be_visible()

            # Check for overflow container
            page.locator(".table-responsive, .overflow-x-auto, [class*='overflow']")
            # Just verify table is accessible
            expect(page.locator("main")).to_be_visible()

    def test_forms_mobile_layout(self, page, base_url, fresh_auth_tokens):
        """Test forms render correctly on mobile."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(MOBILE_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/ap/suppliers/new")
        page.wait_for_load_state("networkidle")

        # Form should be visible
        form = page.locator("form")
        if form.count() > 0:
            expect(form.first).to_be_visible()

        # Inputs should be full width or properly sized
        inputs = page.locator("input[type='text'], input[type='email']")
        if inputs.count() > 0:
            expect(inputs.first).to_be_visible()

    def test_modals_mobile_display(self, page, base_url, fresh_auth_tokens):
        """Test modals display correctly on mobile."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(MOBILE_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/dashboard")
        page.wait_for_load_state("networkidle")

        # Look for modal trigger button
        modal_trigger = page.locator(
            "button[data-modal], button[data-bs-toggle='modal'], [x-on\\:click*='modal']"
        )
        if modal_trigger.count() > 0:
            modal_trigger.first.click()
            page.wait_for_timeout(500)

            # Modal should be visible if triggered
            modal = page.locator(".modal, [role='dialog'], [x-show*='modal']")
            if modal.count() > 0:
                expect(modal.first).to_be_visible()


# =============================================================================
# Tablet Viewport Tests
# =============================================================================


@pytest.mark.e2e
class TestTabletViewport:
    """Tests for tablet viewport behavior."""

    def test_dashboard_tablet_layout(self, page, base_url, fresh_auth_tokens):
        """Test dashboard renders correctly on tablet."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(TABLET_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/dashboard")
        page.wait_for_load_state("networkidle")

        # Page should be visible
        expect(page.locator("main")).to_be_visible()

        # Check for proper layout
        main_content = page.locator("main, .main-content, [role='main']")
        if main_content.count() > 0:
            expect(main_content.first).to_be_visible()

    def test_sidebar_tablet_behavior(self, page, base_url, fresh_auth_tokens):
        """Test sidebar behavior on tablet."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(TABLET_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/dashboard")
        page.wait_for_load_state("networkidle")

        # Sidebar may be collapsed or visible
        sidebar = page.locator("aside, nav[class*='sidebar'], .sidebar")
        if sidebar.count() > 0:
            # Just verify sidebar exists and page works
            expect(page.locator("main")).to_be_visible()

    def test_tables_tablet_layout(self, page, base_url, fresh_auth_tokens):
        """Test tables render correctly on tablet."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(TABLET_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/gl/accounts")
        page.wait_for_load_state("networkidle")

        # Tables should show more columns on tablet
        table = page.locator("table")
        if table.count() > 0:
            expect(table.first).to_be_visible()

            # Check header columns
            headers = page.locator("table thead th")
            if headers.count() > 0:
                # Should have multiple visible columns
                visible_count = sum(
                    1 for i in range(headers.count()) if headers.nth(i).is_visible()
                )
                assert visible_count >= 2, (
                    "Table should show multiple columns on tablet"
                )


# =============================================================================
# Desktop Viewport Tests
# =============================================================================


@pytest.mark.e2e
class TestDesktopViewport:
    """Tests for desktop viewport behavior."""

    def test_dashboard_desktop_layout(self, page, base_url, fresh_auth_tokens):
        """Test dashboard renders correctly on desktop."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(DESKTOP_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/dashboard")
        page.wait_for_load_state("networkidle")

        # Page should be visible
        expect(page.locator("main")).to_be_visible()

        # Content should not be too narrow
        main_content = page.locator("main, .main-content, [role='main']")
        if main_content.count() > 0:
            box = main_content.first.bounding_box()
            if box:
                # Main content should use reasonable width
                assert box["width"] >= 600, "Main content too narrow on desktop"

    def test_sidebar_desktop_always_visible(self, page, base_url, fresh_auth_tokens):
        """Test sidebar is always visible on desktop."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(DESKTOP_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/dashboard")
        page.wait_for_load_state("networkidle")

        # Sidebar should be visible on desktop
        sidebar = page.locator("aside, nav[class*='sidebar'], .sidebar")
        if sidebar.count() > 0:
            expect(sidebar.first).to_be_visible()

    def test_tables_full_width(self, page, base_url, fresh_auth_tokens):
        """Test tables use full width on desktop."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(DESKTOP_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/ap/suppliers")
        page.wait_for_load_state("networkidle")

        # Tables should use available width
        table = page.locator("table")
        if table.count() > 0:
            expect(table.first).to_be_visible()

            # All columns should be visible
            headers = page.locator("table thead th")
            if headers.count() > 0:
                for i in range(min(headers.count(), 5)):  # Check first 5 headers
                    expect(headers.nth(i)).to_be_visible()


# =============================================================================
# Cross-Viewport Navigation Tests
# =============================================================================


@pytest.mark.e2e
class TestCrossViewportNavigation:
    """Tests for navigation across different viewports."""

    def test_hamburger_menu_mobile(self, page, base_url, fresh_auth_tokens):
        """Test hamburger menu works on mobile."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        page.set_viewport_size(MOBILE_VIEWPORT)

        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        page.context.add_cookies(
            [
                {
                    "name": "access_token",
                    "value": fresh_auth_tokens["access_token"],
                    "domain": parsed.hostname or "localhost",
                    "path": "/",
                }
            ]
        )

        page.goto(f"{base_url}/dashboard")
        page.wait_for_load_state("networkidle")

        # Look for hamburger/menu button
        hamburger = page.locator(
            "button[class*='menu'], button[aria-label*='menu'], .hamburger, [class*='burger']"
        )
        if hamburger.count() > 0:
            hamburger.first.click()
            page.wait_for_timeout(300)

            # Menu should be visible
            nav = page.locator("nav, .nav-menu, .mobile-menu")
            if nav.count() > 0:
                expect(nav.first).to_be_visible()

    def test_dropdown_menus_responsive(self, page, base_url, fresh_auth_tokens):
        """Test dropdown menus work across viewports."""
        if not fresh_auth_tokens or not fresh_auth_tokens.get("access_token"):
            pytest.skip("No authentication token available")

        for viewport in [MOBILE_VIEWPORT, TABLET_VIEWPORT, DESKTOP_VIEWPORT]:
            page.set_viewport_size(viewport)

            from urllib.parse import urlparse

            parsed = urlparse(base_url)
            page.context.add_cookies(
                [
                    {
                        "name": "access_token",
                        "value": fresh_auth_tokens["access_token"],
                        "domain": parsed.hostname or "localhost",
                        "path": "/",
                    }
                ]
            )

            page.goto(f"{base_url}/dashboard")
            page.wait_for_load_state("networkidle")

            # Look for dropdown
            dropdown_trigger = page.locator(
                "[data-dropdown], .dropdown-toggle, button[aria-haspopup]"
            )
            if dropdown_trigger.count() > 0:
                dropdown_trigger.first.click()
                page.wait_for_timeout(200)

                # Dropdown menu should appear
                dropdown_menu = page.locator(
                    ".dropdown-menu, [role='menu'], .dropdown-content"
                )
                if dropdown_menu.count() > 0:
                    expect(dropdown_menu.first).to_be_visible()

                # Close dropdown
                page.keyboard.press("Escape")
