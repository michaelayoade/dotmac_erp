# E2E Test Coverage Plan - 90% Target

## Executive Summary

**Current State:**
- 18 test files with ~350 tests
- Coverage focused on page loading, form structure, and basic navigation
- Minimal coverage for workflows, data mutations, and error scenarios

**Target State:**
- Comprehensive test coverage for all critical user journeys
- Full CRUD operations testing
- Error handling and validation testing
- Cross-module integration workflows

---

## Current Coverage Analysis

### Coverage by Module

| Module | Current Tests | Routes | Current Coverage | Target Coverage |
|--------|---------------|--------|------------------|-----------------|
| Dashboard | 20 | 2 | 85% | 95% |
| Admin | 60+ | 86 | 40% | 90% |
| AP (Payables) | 50+ | 37 | 55% | 90% |
| AR (Receivables) | 55+ | 33 | 55% | 90% |
| GL (General Ledger) | 65+ | 19 | 65% | 90% |
| Banking | 25+ | 20+ | 45% | 90% |
| Financial Reports | 18 | 10+ | 60% | 90% |
| Fixed Assets | 2 | 15+ | 10% | 85% |
| Financial Instruments | 2 | 10+ | 10% | 85% |
| Inventory | 2 | 15+ | 10% | 85% |
| Lease (IFRS 16) | 6 | 8 | 50% | 90% |
| Tax | 10 | 15 | 50% | 90% |
| Settings | 0 | 20+ | 0% | 85% |
| Automation | 0 | 25+ | 0% | 85% |
| Quotes | 0 | 10+ | 0% | 85% |
| Sales Orders | 0 | 10+ | 0% | 85% |
| Expenses | 0 | 10+ | 0% | 85% |
| Auth | 0 | 5 | 0% | 90% |

---

## Phase 1: Critical Infrastructure (Priority: HIGH)

### 1.1 Authentication & Session Tests
**File:** `tests/e2e/test_auth.py`

```python
class TestLoginFlow:
    - test_login_page_loads
    - test_login_with_valid_credentials
    - test_login_with_invalid_credentials
    - test_login_with_empty_fields
    - test_login_shows_validation_errors
    - test_logout_clears_session
    - test_protected_routes_redirect_to_login
    - test_session_expiry_handling

class TestPasswordReset:
    - test_forgot_password_page_loads
    - test_forgot_password_form_submission
    - test_reset_password_page_with_valid_token
    - test_reset_password_page_with_invalid_token
    - test_password_reset_success
```

**Estimated Tests:** 13
**Priority:** P0 (Blocker for other tests)

### 1.2 Settings Module Tests
**File:** `tests/e2e/test_settings.py`

```python
class TestSettingsIndex:
    - test_settings_page_loads
    - test_settings_sections_visible
    - test_settings_navigation_links

class TestOrganizationSettings:
    - test_organization_page_loads
    - test_organization_form_fields
    - test_organization_update_success
    - test_organization_update_with_validation_errors
    - test_organization_timezone_selection
    - test_organization_currency_fields

class TestEmailSettings:
    - test_email_settings_page_loads
    - test_email_form_fields_visible
    - test_email_settings_update
    - test_email_password_field_masked

class TestAutomationSettings:
    - test_automation_settings_page_loads
    - test_automation_form_fields
    - test_automation_settings_update

class TestReportSettings:
    - test_report_settings_page_loads
    - test_report_format_selection
    - test_report_settings_update

class TestFeatureFlags:
    - test_features_page_loads
    - test_feature_toggle_enable
    - test_feature_toggle_disable
    - test_all_features_listed
```

**Estimated Tests:** 24
**Priority:** P1

---

## Phase 2: Core CRUD Operations (Priority: HIGH)

### 2.1 Enhanced Admin Module Tests
**File:** `tests/e2e/test_admin_crud.py`

```python
class TestUserCRUD:
    - test_user_list_pagination
    - test_user_list_search
    - test_user_create_full_form
    - test_user_create_validation_errors
    - test_user_edit_page_loads
    - test_user_edit_form_prefilled
    - test_user_update_success
    - test_user_delete_confirmation
    - test_user_delete_success

class TestRoleCRUD:
    - test_role_list_displays
    - test_role_create_with_permissions
    - test_role_edit_permissions
    - test_role_delete_cascade_warning

class TestPermissionCRUD:
    - test_permission_list_displays
    - test_permission_create_unique_key
    - test_permission_duplicate_key_error
    - test_permission_edit_success

class TestOrganizationCRUD:
    - test_organization_create_full
    - test_organization_edit_success
    - test_organization_delete_with_dependencies_warning

class TestSettingsCRUD:
    - test_setting_create_different_types
    - test_setting_edit_value_type_change
    - test_setting_delete_success

class TestScheduledTasksCRUD:
    - test_task_create_with_schedule
    - test_task_edit_schedule
    - test_task_disable_enable
```

**Estimated Tests:** 32
**Priority:** P0

### 2.2 AP Module Full CRUD
**File:** `tests/e2e/test_ap_crud.py`

```python
class TestSupplierCRUD:
    - test_supplier_list_with_search
    - test_supplier_list_with_status_filter
    - test_supplier_create_minimal
    - test_supplier_create_full_details
    - test_supplier_create_validation_errors
    - test_supplier_edit_page_loads_data
    - test_supplier_update_contact_info
    - test_supplier_update_payment_terms
    - test_supplier_delete_without_invoices
    - test_supplier_delete_with_invoices_warning

class TestAPInvoiceCRUD:
    - test_invoice_list_with_supplier_filter
    - test_invoice_list_with_date_range
    - test_invoice_list_with_status_filter
    - test_invoice_create_single_line
    - test_invoice_create_multiple_lines
    - test_invoice_create_validation_errors
    - test_invoice_create_auto_calculates_totals
    - test_invoice_detail_shows_lines
    - test_invoice_delete_draft_only

class TestAPPaymentCRUD:
    - test_payment_create_for_invoice
    - test_payment_create_partial
    - test_payment_create_overpayment_warning
    - test_payment_list_by_supplier
    - test_payment_detail_shows_allocations

class TestPurchaseOrderCRUD:
    - test_po_list_displays
    - test_po_create_full_form
    - test_po_create_with_lines
    - test_po_edit_draft
    - test_po_submit_for_approval
    - test_po_approve_workflow
    - test_po_cancel_workflow

class TestGoodsReceiptCRUD:
    - test_gr_list_displays
    - test_gr_create_from_po
    - test_gr_partial_receipt
    - test_gr_inspect_workflow
    - test_gr_accept_all_workflow
```

**Estimated Tests:** 40
**Priority:** P0

### 2.3 AR Module Full CRUD
**File:** `tests/e2e/test_ar_crud.py`

```python
class TestCustomerCRUD:
    - test_customer_list_with_search
    - test_customer_list_with_status_filter
    - test_customer_create_minimal
    - test_customer_create_full_details
    - test_customer_create_with_credit_limit
    - test_customer_edit_page_loads_data
    - test_customer_update_contact_info
    - test_customer_update_credit_limit
    - test_customer_delete_without_invoices
    - test_customer_delete_with_invoices_warning

class TestARInvoiceCRUD:
    - test_invoice_list_with_customer_filter
    - test_invoice_list_with_date_range
    - test_invoice_list_with_status_filter
    - test_invoice_create_single_line
    - test_invoice_create_multiple_lines
    - test_invoice_create_with_tax
    - test_invoice_create_validation_errors
    - test_invoice_auto_calculates_totals
    - test_invoice_detail_shows_lines
    - test_invoice_detail_shows_payments

class TestARReceiptCRUD:
    - test_receipt_create_for_invoice
    - test_receipt_create_partial
    - test_receipt_create_overpayment
    - test_receipt_list_by_customer
    - test_receipt_detail_shows_allocations
    - test_receipt_payment_method_selection

class TestCreditNoteCRUD:
    - test_credit_note_list_displays
    - test_credit_note_create_full_credit
    - test_credit_note_create_partial_credit
    - test_credit_note_requires_reason
    - test_credit_note_linked_to_invoice
```

**Estimated Tests:** 36
**Priority:** P0

### 2.4 GL Module Full CRUD
**File:** `tests/e2e/test_gl_crud.py`

```python
class TestAccountCRUD:
    - test_account_list_with_search
    - test_account_list_by_category
    - test_account_create_all_types
    - test_account_create_with_parent
    - test_account_create_validation_errors
    - test_account_code_unique
    - test_account_edit_page_loads_data
    - test_account_update_success
    - test_account_delete_without_transactions
    - test_account_delete_with_transactions_warning

class TestJournalEntryCRUD:
    - test_journal_list_with_search
    - test_journal_list_by_date_range
    - test_journal_list_by_status
    - test_journal_create_balanced_entry
    - test_journal_create_unbalanced_error
    - test_journal_create_multi_line
    - test_journal_edit_draft_only
    - test_journal_detail_shows_lines
    - test_journal_delete_draft_only

class TestFiscalPeriodCRUD:
    - test_period_list_displays
    - test_period_create_new_year
    - test_period_status_indicators
    - test_period_close_checklist
    - test_period_close_execution
    - test_period_reopen_closed
```

**Estimated Tests:** 26
**Priority:** P0

---

## Phase 3: Underserved Modules (Priority: MEDIUM-HIGH)

### 3.1 Fixed Assets Module
**File:** `tests/e2e/test_fixed_assets.py`

```python
class TestAssetList:
    - test_assets_page_loads
    - test_assets_list_with_search
    - test_assets_list_by_category
    - test_assets_list_by_status

class TestAssetCRUD:
    - test_asset_create_page_loads
    - test_asset_create_full_form
    - test_asset_create_with_depreciation_method
    - test_asset_detail_page
    - test_asset_edit_page
    - test_asset_update_success
    - test_asset_dispose_workflow
    - test_asset_revalue_workflow

class TestDepreciation:
    - test_depreciation_schedule_page
    - test_depreciation_run_button
    - test_depreciation_calculation_display
    - test_depreciation_journal_generation

class TestAssetCategories:
    - test_categories_list
    - test_category_create
    - test_category_edit
```

**Estimated Tests:** 20
**Priority:** P1

### 3.2 Inventory Module
**File:** `tests/e2e/test_inventory.py`

```python
class TestItemsList:
    - test_items_page_loads
    - test_items_list_with_search
    - test_items_list_by_category
    - test_items_list_by_status

class TestItemCRUD:
    - test_item_create_page_loads
    - test_item_create_full_form
    - test_item_create_with_unit_of_measure
    - test_item_detail_page
    - test_item_edit_page
    - test_item_update_success

class TestInventoryTransactions:
    - test_transactions_list_page
    - test_transaction_create_receipt
    - test_transaction_create_issue
    - test_transaction_create_adjustment
    - test_transaction_detail_page

class TestStockLevels:
    - test_stock_levels_page
    - test_stock_by_location
    - test_stock_valuation
```

**Estimated Tests:** 19
**Priority:** P1

### 3.3 Financial Instruments Module
**File:** `tests/e2e/test_financial_instruments.py`

```python
class TestInstrumentsList:
    - test_instruments_page_loads
    - test_instruments_list_with_search
    - test_instruments_list_by_type

class TestInstrumentCRUD:
    - test_instrument_create_page_loads
    - test_instrument_create_debt
    - test_instrument_create_equity
    - test_instrument_create_derivative
    - test_instrument_detail_page
    - test_instrument_valuation_page

class TestHedgeAccounting:
    - test_hedge_page_loads
    - test_hedge_create_new
    - test_hedge_effectiveness_test
    - test_hedge_documentation
```

**Estimated Tests:** 14
**Priority:** P1

### 3.4 Banking Module Enhanced
**File:** `tests/e2e/test_banking_full.py`

```python
class TestBankAccountCRUD:
    - test_account_create_full
    - test_account_edit_page
    - test_account_update_success
    - test_account_deactivate

class TestBankStatements:
    - test_statement_list_with_filters
    - test_statement_import_page
    - test_statement_import_csv_format
    - test_statement_detail_page
    - test_statement_transactions_list

class TestBankReconciliation:
    - test_reconciliation_create
    - test_reconciliation_load_unmatched
    - test_reconciliation_manual_match
    - test_reconciliation_auto_match
    - test_reconciliation_create_adjustment
    - test_reconciliation_complete
    - test_reconciliation_detail_page
```

**Estimated Tests:** 17
**Priority:** P1

---

## Phase 4: Workflow & Integration Tests (Priority: MEDIUM)

### 4.1 Complete Business Workflows
**File:** `tests/e2e/test_business_workflows.py`

```python
class TestProcureToPay:
    - test_create_supplier_to_payment_workflow
    - test_po_to_invoice_to_payment_workflow
    - test_goods_receipt_to_invoice_matching

class TestOrderToCash:
    - test_customer_to_invoice_to_receipt_workflow
    - test_quote_to_order_to_invoice_workflow
    - test_credit_note_application_workflow

class TestPeriodClose:
    - test_period_close_full_checklist
    - test_period_close_with_open_items
    - test_year_end_close_workflow

class TestReporting:
    - test_trial_balance_after_entries
    - test_aging_report_accuracy
    - test_report_export_pdf
    - test_report_export_excel
```

**Estimated Tests:** 14
**Priority:** P2

### 4.2 Automation Module Tests
**File:** `tests/e2e/test_automation.py`

```python
class TestRecurringTransactions:
    - test_recurring_templates_list
    - test_recurring_template_create
    - test_recurring_template_edit
    - test_recurring_template_activate
    - test_recurring_template_deactivate
    - test_recurring_logs_list

class TestWorkflowTriggers:
    - test_workflow_rules_list
    - test_workflow_rule_create
    - test_workflow_rule_conditions
    - test_workflow_rule_actions
    - test_workflow_executions_list

class TestCustomFields:
    - test_custom_fields_list
    - test_custom_field_create_text
    - test_custom_field_create_number
    - test_custom_field_create_dropdown
    - test_custom_field_edit

class TestDocumentTemplates:
    - test_templates_list
    - test_template_create
    - test_template_edit
    - test_template_preview
```

**Estimated Tests:** 20
**Priority:** P1

### 4.3 Quotes Module Tests
**File:** `tests/e2e/test_quotes.py`

```python
class TestQuotesList:
    - test_quotes_page_loads
    - test_quotes_list_with_search
    - test_quotes_list_by_status

class TestQuoteCRUD:
    - test_quote_create_page
    - test_quote_create_with_lines
    - test_quote_detail_page
    - test_quote_edit_page
    - test_quote_send_to_customer
    - test_quote_accept_workflow
    - test_quote_reject_workflow
    - test_quote_convert_to_order
    - test_quote_duplicate
```

**Estimated Tests:** 13
**Priority:** P2

### 4.4 Sales Orders Module Tests
**File:** `tests/e2e/test_sales_orders.py`

```python
class TestSalesOrdersList:
    - test_orders_page_loads
    - test_orders_list_with_search
    - test_orders_list_by_status

class TestSalesOrderCRUD:
    - test_order_create_page
    - test_order_create_with_lines
    - test_order_create_from_quote
    - test_order_detail_page
    - test_order_edit_page
    - test_order_submit_workflow
    - test_order_approve_workflow
    - test_order_fulfill_workflow
    - test_order_convert_to_invoice
```

**Estimated Tests:** 13
**Priority:** P2

### 4.5 Expenses Module Tests
**File:** `tests/e2e/test_expenses.py`

```python
class TestExpensesList:
    - test_expenses_page_loads
    - test_expenses_list_with_search
    - test_expenses_list_by_category
    - test_expenses_list_by_status

class TestExpenseCRUD:
    - test_expense_create_page
    - test_expense_create_with_receipt
    - test_expense_detail_page
    - test_expense_edit_page
    - test_expense_submit_for_approval
    - test_expense_approve_workflow
    - test_expense_reject_workflow
    - test_expense_reimburse_workflow
```

**Estimated Tests:** 13
**Priority:** P2

---

## Phase 5: Error Handling & Edge Cases (Priority: MEDIUM)

### 5.1 Validation & Error Tests
**File:** `tests/e2e/test_validation_errors.py`

```python
class TestFormValidation:
    - test_required_field_errors
    - test_email_format_validation
    - test_number_format_validation
    - test_date_format_validation
    - test_unique_constraint_errors
    - test_foreign_key_constraint_errors

class TestBusinessRuleValidation:
    - test_unbalanced_journal_error
    - test_duplicate_document_number_error
    - test_closed_period_entry_error
    - test_insufficient_stock_error
    - test_credit_limit_exceeded_warning
    - test_overpayment_warning

class TestNotFoundHandling:
    - test_404_page_for_invalid_id
    - test_404_page_for_deleted_record
    - test_graceful_error_messages

class TestPermissionErrors:
    - test_unauthorized_access_redirect
    - test_insufficient_permissions_message
```

**Estimated Tests:** 18
**Priority:** P2

### 5.2 Responsive Design Tests
**File:** `tests/e2e/test_responsive.py`

```python
class TestMobileViewport:
    - test_dashboard_mobile_layout
    - test_sidebar_mobile_collapse
    - test_tables_mobile_scroll
    - test_forms_mobile_layout
    - test_modals_mobile_display

class TestTabletViewport:
    - test_dashboard_tablet_layout
    - test_sidebar_tablet_behavior
    - test_tables_tablet_layout

class TestDesktopViewport:
    - test_dashboard_desktop_layout
    - test_sidebar_desktop_always_visible
    - test_tables_full_width
```

**Estimated Tests:** 11
**Priority:** P3

---

## Phase 6: Performance & Data Tests (Priority: LOW)

### 6.1 Pagination & Large Data Tests
**File:** `tests/e2e/test_pagination.py`

```python
class TestListPagination:
    - test_pagination_controls_visible
    - test_pagination_first_page
    - test_pagination_next_page
    - test_pagination_last_page
    - test_pagination_page_size_change
    - test_pagination_with_filters
    - test_empty_list_no_pagination
```

**Estimated Tests:** 7
**Priority:** P3

### 6.2 Search & Filter Tests
**File:** `tests/e2e/test_search_filters.py`

```python
class TestSearchFunctionality:
    - test_search_returns_results
    - test_search_no_results_message
    - test_search_clears_on_empty
    - test_search_preserves_filters
    - test_date_range_filter
    - test_status_filter
    - test_combined_filters
```

**Estimated Tests:** 7
**Priority:** P3

---

## Implementation Priority Matrix

| Phase | Priority | Tests | Effort | Timeline |
|-------|----------|-------|--------|----------|
| Phase 1 | P0/P1 | 37 | Medium | Week 1 |
| Phase 2 | P0 | 134 | High | Weeks 2-3 |
| Phase 3 | P1 | 70 | Medium | Week 4 |
| Phase 4 | P1/P2 | 73 | Medium | Week 5 |
| Phase 5 | P2/P3 | 29 | Low | Week 6 |
| Phase 6 | P3 | 14 | Low | Week 6 |

**Total New Tests:** ~357 tests
**Combined with Existing:** ~707 tests
**Target Coverage:** 90%+

---

## Test Infrastructure Improvements

### 1. Page Object Model (POM)
Create reusable page objects for common patterns:

```python
# tests/e2e/pages/base_page.py
class BasePage:
    def __init__(self, page):
        self.page = page

    def wait_for_page_load(self):
        self.page.wait_for_load_state("networkidle")

    def get_page_title(self):
        return self.page.get_by_test_id("page-title")

    def get_error_message(self):
        return self.page.locator(".alert-error, .text-red-600")

    def get_success_message(self):
        return self.page.locator(".alert-success, .text-green-600")

# tests/e2e/pages/supplier_page.py
class SupplierPage(BasePage):
    def navigate_to_list(self):
        self.page.goto("/ap/suppliers")
        self.wait_for_page_load()

    def click_new_button(self):
        self.page.get_by_test_id("new-supplier-btn").click()

    def fill_supplier_form(self, data):
        self.page.locator("#supplier_code").fill(data["code"])
        self.page.locator("#supplier_name").fill(data["name"])
        # ... etc
```

### 2. Test Data Factory
Create consistent test data generation:

```python
# tests/e2e/factories.py
from uuid import uuid4

class TestDataFactory:
    @staticmethod
    def supplier_data():
        unique_id = str(uuid4())[:8]
        return {
            "code": f"SUP-{unique_id}",
            "name": f"Test Supplier {unique_id}",
            "email": f"supplier-{unique_id}@test.com",
            "currency": "USD",
        }

    @staticmethod
    def customer_data():
        unique_id = str(uuid4())[:8]
        return {
            "code": f"CUST-{unique_id}",
            "name": f"Test Customer {unique_id}",
            # ...
        }
```

### 3. Custom Fixtures
Add more specialized fixtures:

```python
# tests/e2e/conftest.py additions

@pytest.fixture
def supplier_with_invoice(authenticated_page, base_url):
    """Create a supplier and invoice for testing payments."""
    # Create supplier
    # Create invoice
    yield {"supplier_id": ..., "invoice_id": ...}
    # Cleanup if needed

@pytest.fixture
def gl_account(authenticated_page, base_url):
    """Create a GL account for testing journal entries."""
    # ...
```

---

## Test Execution Strategy

### CI/CD Integration
```yaml
# .github/workflows/e2e.yml
name: E2E Tests

on: [push, pull_request]

jobs:
  e2e:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: test
    steps:
      - uses: actions/checkout@v4
      - name: Install dependencies
        run: pip install -e ".[test]"
      - name: Install Playwright browsers
        run: playwright install chromium
      - name: Start server
        run: uvicorn app.main:app &
      - name: Run E2E tests
        run: pytest tests/e2e -v --browser chromium
```

### Parallel Execution
```bash
# Run tests in parallel for faster execution
pytest tests/e2e -n auto --dist loadgroup

# Group tests by module for isolation
pytest tests/e2e/test_ap*.py -n 4
pytest tests/e2e/test_ar*.py -n 4
```

### Test Tags
```python
# Use markers for selective execution
@pytest.mark.smoke  # Quick sanity tests
@pytest.mark.crud   # CRUD operations
@pytest.mark.workflow  # Multi-step workflows
@pytest.mark.slow   # Long-running tests
```

---

## Success Metrics

1. **Coverage Target:** 90% of routes tested
2. **Test Reliability:** <2% flaky tests
3. **Execution Time:** <15 minutes for full suite
4. **Critical Path:** 100% coverage of P0 tests

---

## Appendix: Test File Structure

```
tests/e2e/
├── conftest.py                 # Shared fixtures
├── pages/                      # Page Object Models
│   ├── __init__.py
│   ├── base_page.py
│   ├── dashboard_page.py
│   ├── supplier_page.py
│   ├── customer_page.py
│   ├── invoice_page.py
│   └── ...
├── factories.py                # Test data factories
├── helpers.py                  # Utility functions
│
├── # Auth & Settings (Phase 1)
├── test_auth.py
├── test_settings.py
│
├── # Admin (Phase 2)
├── test_admin.py               # Existing
├── test_admin_crud.py          # NEW
│
├── # AP Module (Phase 2)
├── test_ap.py                  # Existing
├── test_ap_workflow.py         # Existing
├── test_ap_crud.py             # NEW
│
├── # AR Module (Phase 2)
├── test_ar.py                  # Existing
├── test_ar_workflow.py         # Existing
├── test_ar_crud.py             # NEW
│
├── # GL Module (Phase 2)
├── test_gl.py                  # Existing
├── test_gl_workflow.py         # Existing
├── test_gl_crud.py             # NEW
│
├── # Underserved Modules (Phase 3)
├── test_fixed_assets.py        # ENHANCED
├── test_inventory.py           # ENHANCED
├── test_financial_instruments.py # ENHANCED
├── test_banking.py             # Existing
├── test_banking_full.py        # NEW
│
├── # Workflows (Phase 4)
├── test_business_workflows.py  # NEW
├── test_automation.py          # NEW
├── test_quotes.py              # NEW
├── test_sales_orders.py        # NEW
├── test_expenses.py            # NEW
│
├── # Error & Edge Cases (Phase 5)
├── test_validation_errors.py   # NEW
├── test_responsive.py          # NEW
│
├── # Performance (Phase 6)
├── test_pagination.py          # NEW
└── test_search_filters.py      # NEW
```

---

## Conclusion

This plan provides a structured approach to achieving 90% e2e test coverage:

1. **Immediate Focus (Weeks 1-3):** Auth, Settings, and full CRUD for core modules
2. **Medium Term (Weeks 4-5):** Underserved modules and business workflows
3. **Ongoing (Week 6+):** Error handling, edge cases, and performance tests

The estimated ~357 new tests combined with existing ~350 tests will provide comprehensive coverage of all critical user journeys and business logic.
