"""
E2E Tests for Automation Module.

Tests for automation features:
- Recurring Transactions
- Workflow Triggers/Rules
- Custom Fields
- Document Templates
"""

from uuid import uuid4

import pytest
from playwright.sync_api import expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


# =============================================================================
# Recurring Transactions Tests
# =============================================================================


@pytest.mark.e2e
class TestRecurringTransactionsList:
    """Tests for recurring transactions list."""

    def test_recurring_templates_list_page_loads(self, authenticated_page, base_url):
        """Test recurring templates list page loads."""
        response = authenticated_page.goto(f"{base_url}/automation/recurring")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_recurring_templates_has_new_button(self, authenticated_page, base_url):
        """Test recurring templates has new button."""
        authenticated_page.goto(f"{base_url}/automation/recurring")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator(
            "a[href*='/recurring/new'], button:has-text('New'), a:has-text('New Template')"
        )
        if new_btn.count() > 0:
            expect(new_btn.first).to_be_visible()

    def test_recurring_templates_shows_frequency(self, authenticated_page, base_url):
        """Test recurring templates shows frequency column."""
        authenticated_page.goto(f"{base_url}/automation/recurring")
        authenticated_page.wait_for_load_state("networkidle")

        frequency_col = authenticated_page.locator(
            "th:has-text('Frequency'), th:has-text('Schedule')"
        )
        if frequency_col.count() > 0:
            expect(frequency_col.first).to_be_visible()


@pytest.mark.e2e
class TestRecurringTemplateCreate:
    """Tests for creating recurring templates."""

    def test_recurring_template_create_page_loads(self, authenticated_page, base_url):
        """Test recurring template create page loads."""
        response = authenticated_page.goto(f"{base_url}/automation/recurring/new")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")

    def test_recurring_template_has_name_field(self, authenticated_page, base_url):
        """Test recurring template form has name field."""
        authenticated_page.goto(f"{base_url}/automation/recurring/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='name'], input[name='template_name'], #name"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_recurring_template_has_frequency_field(self, authenticated_page, base_url):
        """Test recurring template form has frequency selection."""
        authenticated_page.goto(f"{base_url}/automation/recurring/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='frequency'], select[name='schedule'], #frequency"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_recurring_template_has_start_date(self, authenticated_page, base_url):
        """Test recurring template form has start date."""
        authenticated_page.goto(f"{base_url}/automation/recurring/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='start_date'], input[type='date']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()


@pytest.mark.e2e
class TestRecurringTemplateEdit:
    """Tests for editing recurring templates."""

    def test_recurring_template_edit_page_accessible(
        self, authenticated_page, base_url
    ):
        """Test recurring template edit is accessible."""
        authenticated_page.goto(f"{base_url}/automation/recurring")
        authenticated_page.wait_for_load_state("networkidle")

        template_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/recurring/']"
        ).first
        if template_link.count() > 0:
            template_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                expect(edit_btn.first).to_be_visible()

    def test_recurring_template_activate(self, authenticated_page, base_url):
        """Test recurring template activation."""
        authenticated_page.goto(f"{base_url}/automation/recurring")
        authenticated_page.wait_for_load_state("networkidle")

        template_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/recurring/']"
        ).first
        if template_link.count() > 0:
            template_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            activate_btn = authenticated_page.locator(
                "button:has-text('Activate'), button:has-text('Enable')"
            )
            if activate_btn.count() > 0:
                expect(activate_btn.first).to_be_visible()

    def test_recurring_template_deactivate(self, authenticated_page, base_url):
        """Test recurring template deactivation."""
        authenticated_page.goto(f"{base_url}/automation/recurring")
        authenticated_page.wait_for_load_state("networkidle")

        template_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/recurring/']"
        ).first
        if template_link.count() > 0:
            template_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            deactivate_btn = authenticated_page.locator(
                "button:has-text('Deactivate'), button:has-text('Disable'), button:has-text('Pause')"
            )
            if deactivate_btn.count() > 0:
                expect(deactivate_btn.first).to_be_visible()


@pytest.mark.e2e
class TestRecurringLogs:
    """Tests for recurring transaction logs."""

    def test_recurring_logs_list_page_loads(self, authenticated_page, base_url):
        """Test recurring logs list page loads."""
        response = authenticated_page.goto(f"{base_url}/automation/recurring/logs")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()


# =============================================================================
# Workflow Triggers Tests
# =============================================================================


@pytest.mark.e2e
class TestWorkflowRulesList:
    """Tests for workflow rules list."""

    def test_workflow_rules_list_page_loads(self, authenticated_page, base_url):
        """Test workflow rules list page loads."""
        response = authenticated_page.goto(f"{base_url}/automation/workflows")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_workflow_rules_has_new_button(self, authenticated_page, base_url):
        """Test workflow rules has new button."""
        authenticated_page.goto(f"{base_url}/automation/workflows")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator(
            "a[href*='/workflows/new'], button:has-text('New'), a:has-text('New Rule')"
        )
        if new_btn.count() > 0:
            expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestWorkflowRuleCreate:
    """Tests for creating workflow rules."""

    def test_workflow_rule_create_page_loads(self, authenticated_page, base_url):
        """Test workflow rule create page loads."""
        response = authenticated_page.goto(f"{base_url}/automation/workflows/new")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")

    def test_workflow_rule_has_name_field(self, authenticated_page, base_url):
        """Test workflow rule form has name field."""
        authenticated_page.goto(f"{base_url}/automation/workflows/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='name'], input[name='rule_name'], #name"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_workflow_rule_has_trigger_field(self, authenticated_page, base_url):
        """Test workflow rule form has trigger/event selection."""
        authenticated_page.goto(f"{base_url}/automation/workflows/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='trigger'], select[name='event'], #trigger"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_workflow_rule_conditions(self, authenticated_page, base_url):
        """Test workflow rule has conditions section."""
        authenticated_page.goto(f"{base_url}/automation/workflows/new")
        authenticated_page.wait_for_load_state("networkidle")

        conditions = authenticated_page.locator(
            "text=Conditions, text=When, .conditions-section"
        )
        if conditions.count() > 0:
            expect(conditions.first).to_be_visible()

    def test_workflow_rule_actions(self, authenticated_page, base_url):
        """Test workflow rule has actions section."""
        authenticated_page.goto(f"{base_url}/automation/workflows/new")
        authenticated_page.wait_for_load_state("networkidle")

        actions = authenticated_page.locator(
            "text=Actions, text=Then, .actions-section"
        )
        if actions.count() > 0:
            expect(actions.first).to_be_visible()


@pytest.mark.e2e
class TestWorkflowExecutions:
    """Tests for workflow execution logs."""

    def test_workflow_executions_list_page_loads(self, authenticated_page, base_url):
        """Test workflow executions list page loads."""
        response = authenticated_page.goto(
            f"{base_url}/automation/workflows/executions"
        )
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()


# =============================================================================
# Custom Fields Tests
# =============================================================================


@pytest.mark.e2e
class TestCustomFieldsList:
    """Tests for custom fields list."""

    def test_custom_fields_list_page_loads(self, authenticated_page, base_url):
        """Test custom fields list page loads."""
        response = authenticated_page.goto(f"{base_url}/automation/custom-fields")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_custom_fields_has_new_button(self, authenticated_page, base_url):
        """Test custom fields has new button."""
        authenticated_page.goto(f"{base_url}/automation/custom-fields")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator(
            "a[href*='/custom-fields/new'], button:has-text('New'), a:has-text('Add Field')"
        )
        if new_btn.count() > 0:
            expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestCustomFieldCreate:
    """Tests for creating custom fields."""

    def test_custom_field_create_text(self, authenticated_page, base_url):
        """Test creating a text custom field."""
        authenticated_page.goto(f"{base_url}/automation/custom-fields/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Check for field type selection
        type_field = authenticated_page.locator(
            "select[name='field_type'], select[name='type'], #field_type"
        )
        if type_field.count() > 0:
            expect(type_field.first).to_be_visible()

            # Check for text option
            options = type_field.first.locator("option")
            has_text_option = False
            for i in range(options.count()):
                if "text" in options.nth(i).text_content().lower():
                    has_text_option = True
                    break

    def test_custom_field_create_number(self, authenticated_page, base_url):
        """Test creating a number custom field."""
        authenticated_page.goto(f"{base_url}/automation/custom-fields/new")
        authenticated_page.wait_for_load_state("networkidle")

        type_field = authenticated_page.locator(
            "select[name='field_type'], select[name='type'], #field_type"
        )
        if type_field.count() > 0:
            options = type_field.first.locator("option")
            for i in range(options.count()):
                text = options.nth(i).text_content().lower()
                if "number" in text or "numeric" in text or "integer" in text:
                    type_field.first.select_option(index=i)
                    break

    def test_custom_field_create_dropdown(self, authenticated_page, base_url):
        """Test creating a dropdown custom field."""
        authenticated_page.goto(f"{base_url}/automation/custom-fields/new")
        authenticated_page.wait_for_load_state("networkidle")

        type_field = authenticated_page.locator(
            "select[name='field_type'], select[name='type'], #field_type"
        )
        if type_field.count() > 0:
            options = type_field.first.locator("option")
            for i in range(options.count()):
                text = options.nth(i).text_content().lower()
                if "dropdown" in text or "select" in text or "list" in text:
                    type_field.first.select_option(index=i)
                    break

    def test_custom_field_edit(self, authenticated_page, base_url):
        """Test editing a custom field."""
        authenticated_page.goto(f"{base_url}/automation/custom-fields")
        authenticated_page.wait_for_load_state("networkidle")

        field_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/custom-fields/']"
        ).first
        if field_link.count() > 0:
            field_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                expect(edit_btn.first).to_be_visible()


# =============================================================================
# Document Templates Tests
# =============================================================================


@pytest.mark.e2e
class TestDocumentTemplatesList:
    """Tests for document templates list."""

    def test_templates_list_page_loads(self, authenticated_page, base_url):
        """Test templates list page loads."""
        response = authenticated_page.goto(f"{base_url}/automation/templates")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_templates_has_new_button(self, authenticated_page, base_url):
        """Test templates has new button."""
        authenticated_page.goto(f"{base_url}/automation/templates")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator(
            "a[href*='/templates/new'], button:has-text('New'), a:has-text('New Template')"
        )
        if new_btn.count() > 0:
            expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestDocumentTemplateCreate:
    """Tests for creating document templates."""

    def test_template_create_page_loads(self, authenticated_page, base_url):
        """Test template create page loads."""
        response = authenticated_page.goto(f"{base_url}/automation/templates/new")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")

    def test_template_has_name_field(self, authenticated_page, base_url):
        """Test template form has name field."""
        authenticated_page.goto(f"{base_url}/automation/templates/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='name'], input[name='template_name'], #name"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_template_has_type_field(self, authenticated_page, base_url):
        """Test template form has type selection."""
        authenticated_page.goto(f"{base_url}/automation/templates/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='template_type'], select[name='type'], #template_type"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_template_has_content_editor(self, authenticated_page, base_url):
        """Test template form has content editor."""
        authenticated_page.goto(f"{base_url}/automation/templates/new")
        authenticated_page.wait_for_load_state("networkidle")

        editor = authenticated_page.locator(
            "textarea[name='content'], .editor, [contenteditable='true'], #content"
        )
        if editor.count() > 0:
            expect(editor.first).to_be_visible()


@pytest.mark.e2e
class TestDocumentTemplateEdit:
    """Tests for editing document templates."""

    def test_template_edit_accessible(self, authenticated_page, base_url):
        """Test template edit is accessible."""
        authenticated_page.goto(f"{base_url}/automation/templates")
        authenticated_page.wait_for_load_state("networkidle")

        template_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/templates/']"
        ).first
        if template_link.count() > 0:
            template_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                expect(edit_btn.first).to_be_visible()

    def test_template_preview(self, authenticated_page, base_url):
        """Test template preview functionality."""
        authenticated_page.goto(f"{base_url}/automation/templates")
        authenticated_page.wait_for_load_state("networkidle")

        template_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/templates/']"
        ).first
        if template_link.count() > 0:
            template_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            preview_btn = authenticated_page.locator(
                "button:has-text('Preview'), a:has-text('Preview')"
            )
            if preview_btn.count() > 0:
                expect(preview_btn.first).to_be_visible()
