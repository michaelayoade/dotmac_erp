"""
E2E Tests for Settings Module.

Tests for organization profile, email configuration, automation settings,
report settings, feature flags, and numbering sequences.
"""

import re
import pytest
from playwright.sync_api import expect


@pytest.mark.e2e
class TestSettingsIndex:
    """Tests for the settings index page."""

    def test_settings_page_loads(self, authenticated_page, base_url):
        """Test that settings page loads correctly."""
        response = authenticated_page.goto(f"{base_url}/settings")
        assert response.ok, f"Settings page failed to load: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")
        # Page has data-testid="page-title" from base template
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Settings")

    def test_settings_has_organization_section(self, authenticated_page, base_url):
        """Test that settings page has organization profile section."""
        authenticated_page.goto(f"{base_url}/settings")
        authenticated_page.wait_for_load_state("networkidle")

        org_link = authenticated_page.locator("a[href*='/settings/organization']")
        expect(org_link).to_be_visible()

    def test_settings_has_numbering_section(self, authenticated_page, base_url):
        """Test that settings page has numbering sequences section."""
        authenticated_page.goto(f"{base_url}/settings")
        authenticated_page.wait_for_load_state("networkidle")

        numbering_link = authenticated_page.locator("a[href*='/settings/numbering']")
        expect(numbering_link).to_be_visible()

    def test_settings_has_email_section(self, authenticated_page, base_url):
        """Test that settings page has email configuration section."""
        authenticated_page.goto(f"{base_url}/settings")
        authenticated_page.wait_for_load_state("networkidle")

        email_link = authenticated_page.locator("a[href*='/settings/email']")
        expect(email_link).to_be_visible()

    def test_settings_has_automation_section(self, authenticated_page, base_url):
        """Test that settings page has automation settings section."""
        authenticated_page.goto(f"{base_url}/settings")
        authenticated_page.wait_for_load_state("networkidle")

        automation_link = authenticated_page.locator("a[href*='/settings/automation']")
        expect(automation_link).to_be_visible()

    def test_settings_has_reports_section(self, authenticated_page, base_url):
        """Test that settings page has report settings section."""
        authenticated_page.goto(f"{base_url}/settings")
        authenticated_page.wait_for_load_state("networkidle")

        reports_link = authenticated_page.locator("a[href*='/settings/reports']")
        expect(reports_link).to_be_visible()

    def test_settings_has_features_section(self, authenticated_page, base_url):
        """Test that settings page has feature flags section."""
        authenticated_page.goto(f"{base_url}/settings")
        authenticated_page.wait_for_load_state("networkidle")

        features_link = authenticated_page.locator("a[href*='/settings/features']")
        expect(features_link).to_be_visible()

    def test_settings_navigation_to_organization(self, authenticated_page, base_url):
        """Test navigation from settings index to organization."""
        authenticated_page.goto(f"{base_url}/settings")
        authenticated_page.wait_for_load_state("networkidle")

        authenticated_page.locator("a[href*='/settings/organization']").click()
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page).to_have_url(re.compile(r".*/settings/organization.*"))


@pytest.mark.e2e
class TestOrganizationSettings:
    """Tests for organization profile settings."""

    def test_organization_page_loads(self, authenticated_page, base_url):
        """Test that organization settings page loads."""
        response = authenticated_page.goto(f"{base_url}/settings/organization")
        assert response.ok, f"Organization settings failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_organization_has_legal_name_field(self, authenticated_page, base_url):
        """Test that organization page has legal name field."""
        authenticated_page.goto(f"{base_url}/settings/organization")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#legal_name, input[name='legal_name']")
        expect(field).to_be_visible()

    def test_organization_has_trading_name_field(self, authenticated_page, base_url):
        """Test that organization page has trading name field."""
        authenticated_page.goto(f"{base_url}/settings/organization")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#trading_name, input[name='trading_name']")
        expect(field).to_be_visible()

    def test_organization_has_currency_fields(self, authenticated_page, base_url):
        """Test that organization page has currency fields."""
        authenticated_page.goto(f"{base_url}/settings/organization")
        authenticated_page.wait_for_load_state("networkidle")

        functional = authenticated_page.locator("#functional_currency_code, input[name='functional_currency_code']")
        presentation = authenticated_page.locator("#presentation_currency_code, input[name='presentation_currency_code']")

        expect(functional).to_be_visible()
        expect(presentation).to_be_visible()

    def test_organization_has_timezone_select(self, authenticated_page, base_url):
        """Test that organization page has timezone selection."""
        authenticated_page.goto(f"{base_url}/settings/organization")
        authenticated_page.wait_for_load_state("networkidle")

        timezone = authenticated_page.locator("#timezone, select[name='timezone']")
        expect(timezone).to_be_visible()

    def test_organization_has_date_format_select(self, authenticated_page, base_url):
        """Test that organization page has date format selection."""
        authenticated_page.goto(f"{base_url}/settings/organization")
        authenticated_page.wait_for_load_state("networkidle")

        date_format = authenticated_page.locator("#date_format, select[name='date_format']")
        expect(date_format).to_be_visible()

    def test_organization_has_contact_fields(self, authenticated_page, base_url):
        """Test that organization page has contact fields."""
        authenticated_page.goto(f"{base_url}/settings/organization")
        authenticated_page.wait_for_load_state("networkidle")

        email = authenticated_page.locator("#contact_email, input[name='contact_email']")
        phone = authenticated_page.locator("#contact_phone, input[name='contact_phone']")

        expect(email).to_be_visible()
        expect(phone).to_be_visible()

    def test_organization_has_address_fields(self, authenticated_page, base_url):
        """Test that organization page has address fields."""
        authenticated_page.goto(f"{base_url}/settings/organization")
        authenticated_page.wait_for_load_state("networkidle")

        address1 = authenticated_page.locator("#address_line1, input[name='address_line1']")
        city = authenticated_page.locator("#city, input[name='city']")
        country = authenticated_page.locator("#country, input[name='country']")

        expect(address1).to_be_visible()
        expect(city).to_be_visible()
        expect(country).to_be_visible()

    def test_organization_has_submit_button(self, authenticated_page, base_url):
        """Test that organization page has submit button."""
        authenticated_page.goto(f"{base_url}/settings/organization")
        authenticated_page.wait_for_load_state("networkidle")

        submit = authenticated_page.locator("button[type='submit']")
        expect(submit).to_be_visible()

    def test_organization_has_cancel_link(self, authenticated_page, base_url):
        """Test that organization page has cancel link."""
        authenticated_page.goto(f"{base_url}/settings/organization")
        authenticated_page.wait_for_load_state("networkidle")

        cancel = authenticated_page.locator("a[href='/settings']")
        expect(cancel).to_be_visible()


@pytest.mark.e2e
class TestEmailSettings:
    """Tests for email configuration settings."""

    def test_email_page_loads(self, authenticated_page, base_url):
        """Test that email settings page loads."""
        response = authenticated_page.goto(f"{base_url}/settings/email")
        assert response.ok, f"Email settings failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_email_has_smtp_host_field(self, authenticated_page, base_url):
        """Test that email page has SMTP host field."""
        authenticated_page.goto(f"{base_url}/settings/email")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#smtp_host, input[name='smtp_host']")
        expect(field).to_be_visible()

    def test_email_has_smtp_port_field(self, authenticated_page, base_url):
        """Test that email page has SMTP port field."""
        authenticated_page.goto(f"{base_url}/settings/email")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#smtp_port, input[name='smtp_port']")
        expect(field).to_be_visible()

    def test_email_has_smtp_username_field(self, authenticated_page, base_url):
        """Test that email page has SMTP username field."""
        authenticated_page.goto(f"{base_url}/settings/email")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#smtp_username, input[name='smtp_username']")
        expect(field).to_be_visible()

    def test_email_has_smtp_password_field(self, authenticated_page, base_url):
        """Test that email page has SMTP password field."""
        authenticated_page.goto(f"{base_url}/settings/email")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#smtp_password, input[name='smtp_password']")
        expect(field).to_be_visible()

    def test_email_has_tls_checkbox(self, authenticated_page, base_url):
        """Test that email page has TLS checkbox."""
        authenticated_page.goto(f"{base_url}/settings/email")
        authenticated_page.wait_for_load_state("networkidle")

        checkbox = authenticated_page.locator("input[name='smtp_use_tls']")
        expect(checkbox).to_be_visible()

    def test_email_has_ssl_checkbox(self, authenticated_page, base_url):
        """Test that email page has SSL checkbox."""
        authenticated_page.goto(f"{base_url}/settings/email")
        authenticated_page.wait_for_load_state("networkidle")

        checkbox = authenticated_page.locator("input[name='smtp_use_ssl']")
        expect(checkbox).to_be_visible()

    def test_email_has_from_fields(self, authenticated_page, base_url):
        """Test that email page has from email and name fields."""
        authenticated_page.goto(f"{base_url}/settings/email")
        authenticated_page.wait_for_load_state("networkidle")

        from_email = authenticated_page.locator("#smtp_from_email, input[name='smtp_from_email']")
        from_name = authenticated_page.locator("#smtp_from_name, input[name='smtp_from_name']")

        expect(from_email).to_be_visible()
        expect(from_name).to_be_visible()

    def test_email_has_submit_button(self, authenticated_page, base_url):
        """Test that email page has submit button."""
        authenticated_page.goto(f"{base_url}/settings/email")
        authenticated_page.wait_for_load_state("networkidle")

        submit = authenticated_page.locator("button[type='submit']")
        expect(submit).to_be_visible()


@pytest.mark.e2e
class TestAutomationSettings:
    """Tests for automation settings."""

    def test_automation_page_loads(self, authenticated_page, base_url):
        """Test that automation settings page loads."""
        response = authenticated_page.goto(f"{base_url}/settings/automation-settings")
        assert response.ok, f"Automation settings failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_automation_has_recurring_frequency_field(self, authenticated_page, base_url):
        """Test that automation page has recurring frequency field."""
        authenticated_page.goto(f"{base_url}/settings/automation-settings")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#recurring_default_frequency, select[name='recurring_default_frequency']")
        expect(field).to_be_visible()

    def test_automation_has_max_occurrences_field(self, authenticated_page, base_url):
        """Test that automation page has max occurrences field."""
        authenticated_page.goto(f"{base_url}/settings/automation-settings")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#recurring_max_occurrences, input[name='recurring_max_occurrences']")
        expect(field).to_be_visible()

    def test_automation_has_lookback_days_field(self, authenticated_page, base_url):
        """Test that automation page has lookback days field."""
        authenticated_page.goto(f"{base_url}/settings/automation-settings")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#recurring_lookback_days, input[name='recurring_lookback_days']")
        expect(field).to_be_visible()

    def test_automation_has_workflow_max_actions_field(self, authenticated_page, base_url):
        """Test that automation page has workflow max actions field."""
        authenticated_page.goto(f"{base_url}/settings/automation-settings")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#workflow_max_actions_per_event, input[name='workflow_max_actions_per_event']")
        expect(field).to_be_visible()

    def test_automation_has_async_timeout_field(self, authenticated_page, base_url):
        """Test that automation page has async timeout field."""
        authenticated_page.goto(f"{base_url}/settings/automation-settings")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#workflow_async_timeout_seconds, input[name='workflow_async_timeout_seconds']")
        expect(field).to_be_visible()

    def test_automation_has_custom_fields_limit(self, authenticated_page, base_url):
        """Test that automation page has custom fields limit field."""
        authenticated_page.goto(f"{base_url}/settings/automation-settings")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#custom_fields_max_per_entity, input[name='custom_fields_max_per_entity']")
        expect(field).to_be_visible()

    def test_automation_has_submit_button(self, authenticated_page, base_url):
        """Test that automation page has submit button."""
        authenticated_page.goto(f"{base_url}/settings/automation-settings")
        authenticated_page.wait_for_load_state("networkidle")

        submit = authenticated_page.locator("button[type='submit']")
        expect(submit).to_be_visible()


@pytest.mark.e2e
class TestReportSettings:
    """Tests for report settings."""

    def test_reports_page_loads(self, authenticated_page, base_url):
        """Test that reports settings page loads."""
        response = authenticated_page.goto(f"{base_url}/settings/reports")
        assert response.ok, f"Reports settings failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_reports_has_export_format_field(self, authenticated_page, base_url):
        """Test that reports page has export format field."""
        authenticated_page.goto(f"{base_url}/settings/reports")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#default_export_format, select[name='default_export_format']")
        expect(field).to_be_visible()

    def test_reports_has_page_size_field(self, authenticated_page, base_url):
        """Test that reports page has page size field."""
        authenticated_page.goto(f"{base_url}/settings/reports")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#report_page_size, select[name='report_page_size']")
        expect(field).to_be_visible()

    def test_reports_has_orientation_field(self, authenticated_page, base_url):
        """Test that reports page has orientation field."""
        authenticated_page.goto(f"{base_url}/settings/reports")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#report_orientation, select[name='report_orientation']")
        expect(field).to_be_visible()

    def test_reports_has_logo_checkbox(self, authenticated_page, base_url):
        """Test that reports page has include logo checkbox."""
        authenticated_page.goto(f"{base_url}/settings/reports")
        authenticated_page.wait_for_load_state("networkidle")

        checkbox = authenticated_page.locator("input[name='include_logo_in_reports']")
        expect(checkbox).to_be_visible()

    def test_reports_has_watermark_field(self, authenticated_page, base_url):
        """Test that reports page has watermark text field."""
        authenticated_page.goto(f"{base_url}/settings/reports")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#report_watermark_text, input[name='report_watermark_text']")
        expect(field).to_be_visible()

    def test_reports_has_submit_button(self, authenticated_page, base_url):
        """Test that reports page has submit button."""
        authenticated_page.goto(f"{base_url}/settings/reports")
        authenticated_page.wait_for_load_state("networkidle")

        submit = authenticated_page.locator("button[type='submit']")
        expect(submit).to_be_visible()


@pytest.mark.e2e
class TestFeatureFlags:
    """Tests for feature flags settings."""

    def test_features_page_loads(self, authenticated_page, base_url):
        """Test that features page loads."""
        response = authenticated_page.goto(f"{base_url}/settings/features")
        assert response.ok, f"Features settings failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_features_has_multi_currency_toggle(self, authenticated_page, base_url):
        """Test that features page has multi-currency toggle."""
        authenticated_page.goto(f"{base_url}/settings/features")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for multi-currency feature toggle or text
        multi_currency = authenticated_page.locator("text=Multi Currency, text=multi_currency, text=Multi-Currency")
        expect(multi_currency.first).to_be_visible()

    def test_features_has_budgeting_toggle(self, authenticated_page, base_url):
        """Test that features page has budgeting toggle."""
        authenticated_page.goto(f"{base_url}/settings/features")
        authenticated_page.wait_for_load_state("networkidle")

        budgeting = authenticated_page.locator("text=Budgeting, text=budgeting")
        expect(budgeting.first).to_be_visible()

    def test_features_has_inventory_toggle(self, authenticated_page, base_url):
        """Test that features page has inventory toggle."""
        authenticated_page.goto(f"{base_url}/settings/features")
        authenticated_page.wait_for_load_state("networkidle")

        inventory = authenticated_page.locator("text=Inventory, text=inventory")
        expect(inventory.first).to_be_visible()

    def test_features_has_fixed_assets_toggle(self, authenticated_page, base_url):
        """Test that features page has fixed assets toggle."""
        authenticated_page.goto(f"{base_url}/settings/features")
        authenticated_page.wait_for_load_state("networkidle")

        assets = authenticated_page.locator("text=Fixed Assets, text=fixed_assets")
        expect(assets.first).to_be_visible()

    def test_features_has_leases_toggle(self, authenticated_page, base_url):
        """Test that features page has leases toggle."""
        authenticated_page.goto(f"{base_url}/settings/features")
        authenticated_page.wait_for_load_state("networkidle")

        leases = authenticated_page.locator("text=Leases, text=leases")
        expect(leases.first).to_be_visible()

    def test_features_toggles_are_buttons(self, authenticated_page, base_url):
        """Test that feature toggles have clickable buttons."""
        authenticated_page.goto(f"{base_url}/settings/features")
        authenticated_page.wait_for_load_state("networkidle")

        # Should have toggle buttons (role=switch or button with toggle styling)
        toggles = authenticated_page.locator("button[role='switch'], button[class*='toggle'], form button[type='submit']")
        expect(toggles.first).to_be_visible()


@pytest.mark.e2e
class TestNumberingSequences:
    """Tests for numbering sequences settings."""

    def test_numbering_page_loads(self, authenticated_page, base_url):
        """Test that numbering page loads."""
        response = authenticated_page.goto(f"{base_url}/settings/numbering")
        assert response.ok, f"Numbering settings failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_numbering_has_sequences_list(self, authenticated_page, base_url):
        """Test that numbering page has sequences list."""
        authenticated_page.goto(f"{base_url}/settings/numbering")
        authenticated_page.wait_for_load_state("networkidle")

        # Should have sequence type labels
        sequences = authenticated_page.locator("text=Invoice, text=Credit Note, text=Payment, text=Journal")
        expect(sequences.first).to_be_visible()

    def test_numbering_sequences_are_clickable(self, authenticated_page, base_url):
        """Test that numbering sequences are clickable for editing."""
        authenticated_page.goto(f"{base_url}/settings/numbering")
        authenticated_page.wait_for_load_state("networkidle")

        # Sequences should be links or have edit buttons
        edit_links = authenticated_page.locator("a[href*='/settings/numbering/']")
        if edit_links.count() > 0:
            expect(edit_links.first).to_be_visible()


@pytest.mark.e2e
class TestNumberingSequenceEdit:
    """Tests for editing numbering sequence."""

    def test_numbering_edit_page_loads(self, authenticated_page, base_url):
        """Test that clicking a sequence opens edit page."""
        authenticated_page.goto(f"{base_url}/settings/numbering")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first sequence edit link
        edit_links = authenticated_page.locator("a[href*='/settings/numbering/']")
        if edit_links.count() > 0:
            edit_links.first.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Should be on edit page
            expect(authenticated_page).to_have_url(re.compile(r".*/settings/numbering/.*"))

    def test_numbering_edit_has_prefix_field(self, authenticated_page, base_url):
        """Test that numbering edit has prefix field."""
        authenticated_page.goto(f"{base_url}/settings/numbering")
        authenticated_page.wait_for_load_state("networkidle")

        edit_links = authenticated_page.locator("a[href*='/settings/numbering/']")
        if edit_links.count() > 0:
            edit_links.first.click()
            authenticated_page.wait_for_load_state("networkidle")

            prefix = authenticated_page.locator("#prefix, input[name='prefix']")
            expect(prefix).to_be_visible()

    def test_numbering_edit_has_min_digits_field(self, authenticated_page, base_url):
        """Test that numbering edit has min digits field."""
        authenticated_page.goto(f"{base_url}/settings/numbering")
        authenticated_page.wait_for_load_state("networkidle")

        edit_links = authenticated_page.locator("a[href*='/settings/numbering/']")
        if edit_links.count() > 0:
            edit_links.first.click()
            authenticated_page.wait_for_load_state("networkidle")

            min_digits = authenticated_page.locator("#min_digits, select[name='min_digits']")
            expect(min_digits).to_be_visible()

    def test_numbering_edit_has_reset_frequency_field(self, authenticated_page, base_url):
        """Test that numbering edit has reset frequency field."""
        authenticated_page.goto(f"{base_url}/settings/numbering")
        authenticated_page.wait_for_load_state("networkidle")

        edit_links = authenticated_page.locator("a[href*='/settings/numbering/']")
        if edit_links.count() > 0:
            edit_links.first.click()
            authenticated_page.wait_for_load_state("networkidle")

            reset_freq = authenticated_page.locator("#reset_frequency, select[name='reset_frequency']")
            expect(reset_freq).to_be_visible()

    def test_numbering_edit_has_preview(self, authenticated_page, base_url):
        """Test that numbering edit has live preview."""
        authenticated_page.goto(f"{base_url}/settings/numbering")
        authenticated_page.wait_for_load_state("networkidle")

        edit_links = authenticated_page.locator("a[href*='/settings/numbering/']")
        if edit_links.count() > 0:
            edit_links.first.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Should have a preview section
            preview = authenticated_page.locator("text=Preview, text=preview")
            expect(preview.first).to_be_visible()


@pytest.mark.e2e
class TestSettingsResponsive:
    """Tests for settings pages responsive design."""

    def test_settings_mobile_layout(self, authenticated_page, base_url):
        """Test settings page on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})

        authenticated_page.goto(f"{base_url}/settings")
        authenticated_page.wait_for_load_state("networkidle")

        # Page should still be accessible - check page-title
        expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

    def test_organization_mobile_layout(self, authenticated_page, base_url):
        """Test organization settings on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})

        authenticated_page.goto(f"{base_url}/settings/organization")
        authenticated_page.wait_for_load_state("networkidle")

        # Form should still be accessible
        form = authenticated_page.locator("form")
        expect(form).to_be_visible()

    def test_email_mobile_layout(self, authenticated_page, base_url):
        """Test email settings on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})

        authenticated_page.goto(f"{base_url}/settings/email")
        authenticated_page.wait_for_load_state("networkidle")

        # Form should still be accessible
        form = authenticated_page.locator("form")
        expect(form).to_be_visible()

    def test_features_mobile_layout(self, authenticated_page, base_url):
        """Test features page on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})

        authenticated_page.goto(f"{base_url}/settings/features")
        authenticated_page.wait_for_load_state("networkidle")

        # Feature toggles should still be visible
        toggles = authenticated_page.locator("button[role='switch'], form button[type='submit']")
        if toggles.count() > 0:
            expect(toggles.first).to_be_visible()
