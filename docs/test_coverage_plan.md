# Test Coverage Plan: 64% → 90%

## Executive Summary

**Current Coverage:** 64% (approximately 21,852 statements covered)
**Target Coverage:** 90% (30,878 statements)
**Gap:** ~9,026 statements to cover

## Priority Analysis

Coverage gaps are categorized by:
1. **Statement Impact** - How many uncovered statements
2. **Business Criticality** - Core business logic vs supporting features
3. **Test Complexity** - Difficulty of writing meaningful tests

---

## Phase 1: High-Impact Core Services (Priority: CRITICAL)

### 1.1 AR/AP Posting Adapters (~300 statements)

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `ar/ar_posting_adapter.py` | 24% | 96 | Critical |
| `ap/ap_posting_adapter.py` | 23% | 112 | Critical |
| `cons/cons_posting_adapter.py` | 25% | 87 | Critical |

**Tests Needed:**
```
tests/ifrs/ar/test_ar_posting_adapter.py (~30 tests)
- test_post_invoice_simple
- test_post_invoice_with_tax
- test_post_invoice_multicurrency
- test_post_credit_note
- test_post_invoice_with_discount
- test_post_revenue_recognition
- test_validation_errors
- test_idempotency
- test_reversal_posting

tests/ifrs/ap/test_ap_posting_adapter.py (~30 tests)
- test_post_supplier_invoice
- test_post_invoice_with_withholding_tax
- test_post_invoice_multicurrency
- test_post_credit_note
- test_post_goods_receipt
- test_expense_allocation
- test_validation_errors
- test_idempotency

tests/ifrs/cons/test_cons_posting_adapter.py (~25 tests)
- test_elimination_journal
- test_intercompany_transaction
- test_currency_translation
- test_minority_interest
- test_consolidation_adjustments
```

### 1.2 Financial Instrument Posting (~170 statements)

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `fin_inst/fin_inst_posting_adapter.py` | 16% | 168 | Critical |

**Tests Needed:**
```
tests/ifrs/fin_inst/test_fin_inst_posting_adapter.py (~35 tests)
- test_post_bond_purchase
- test_post_loan_disbursement
- test_post_interest_accrual
- test_post_fair_value_adjustment
- test_post_impairment
- test_post_hedge_effectiveness
- test_post_derivative_settlement
- test_post_forex_gain_loss
```

### 1.3 Inventory Transaction Service (~130 statements)

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `inv/transaction.py` | 59% | 127 | Critical |
| `inv/inv_posting_adapter.py` | 47% | 87 | Critical |

**Tests Needed:**
```
tests/ifrs/inv/test_transaction_service.py (~40 tests)
- test_record_receipt_fifo
- test_record_issue_fifo
- test_transfer_between_warehouses
- test_adjustment_positive
- test_adjustment_negative
- test_cost_layer_consumption
- test_multicurrency_transaction
- test_lot_tracked_item
- test_serial_tracked_item
- test_negative_inventory_prevention

tests/ifrs/inv/test_inv_posting_adapter.py (~25 tests)
- test_post_inventory_receipt
- test_post_inventory_issue
- test_post_adjustment
- test_post_transfer
- test_post_scrap
- test_post_revaluation
- test_cogs_posting
```

---

## Phase 2: AR/AP Business Services (~700 statements)

### 2.1 Sales Order & Quote Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `ar/sales_order.py` | 17% | 212 | High |
| `ar/quote.py` | 22% | 156 | High |
| `ar/contract.py` | 31% | 184 | High |

**Tests Needed:**
```
tests/ifrs/ar/test_sales_order_service.py (~45 tests)
- test_create_sales_order
- test_add_order_line
- test_update_order_line
- test_approve_order
- test_convert_to_invoice
- test_partial_invoice
- test_cancel_order
- test_credit_check
- test_inventory_reservation
- test_pricing_integration
- test_discount_application

tests/ifrs/ar/test_quote_service.py (~35 tests)
- test_create_quote
- test_add_quote_line
- test_calculate_totals
- test_apply_discount
- test_convert_to_order
- test_expire_quote
- test_revision_handling
- test_approval_workflow

tests/ifrs/ar/test_contract_service.py (~40 tests)
- test_create_contract
- test_add_performance_obligation
- test_allocate_transaction_price
- test_revenue_recognition_over_time
- test_revenue_recognition_point_in_time
- test_variable_consideration
- test_contract_modification
- test_contract_termination
```

### 2.2 AP Purchasing Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `ap/purchase_order.py` | 39% | 102 | High |
| `ap/goods_receipt.py` | 33% | 126 | High |
| `ap/payment_batch.py` | 27% | 138 | High |

**Tests Needed:**
```
tests/ifrs/ap/test_purchase_order_service.py (~30 tests)
- test_create_purchase_order
- test_add_order_line
- test_approve_order
- test_send_to_supplier
- test_receive_confirmation
- test_close_order
- test_budget_check

tests/ifrs/ap/test_goods_receipt_service.py (~35 tests)
- test_create_receipt
- test_receive_full_quantity
- test_receive_partial_quantity
- test_over_receipt_handling
- test_quality_inspection
- test_inventory_update
- test_3_way_match

tests/ifrs/ap/test_payment_batch_service.py (~35 tests)
- test_create_batch
- test_add_invoices_to_batch
- test_validate_batch
- test_approve_batch
- test_execute_batch
- test_handle_failed_payment
- test_reconcile_batch
```

### 2.3 ECL & Aging Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `ar/ecl.py` | 39% | 110 | High |
| `ar/ar_aging.py` | 38% | 110 | High |
| `ap/ap_aging.py` | 42% | 88 | High |

**Tests Needed:**
```
tests/ifrs/ar/test_ecl_service.py (~30 tests)
- test_calculate_provision_rate
- test_calculate_ecl_simple
- test_calculate_ecl_forward_looking
- test_historical_loss_rate
- test_stage_classification
- test_significant_increase_credit_risk
- test_collective_assessment
- test_individual_assessment

tests/ifrs/ar/test_ar_aging_service.py (~25 tests)
- test_calculate_aging_buckets
- test_generate_aging_report
- test_aging_snapshot
- test_aging_trend_analysis

tests/ifrs/ap/test_ap_aging_service.py (~25 tests)
- test_calculate_aging_buckets
- test_cash_flow_forecast
- test_aging_by_supplier
```

---

## Phase 3: Automation & Workflow (~500 statements)

### 3.1 Automation Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `automation/recurring.py` | 22% | 192 | High |
| `automation/workflow.py` | 21% | 224 | High |
| `automation/custom_fields.py` | 32% | 94 | Medium |

**Tests Needed:**
```
tests/ifrs/automation/test_recurring_service.py (~40 tests)
- test_create_recurring_template
- test_generate_next_occurrence
- test_generate_batch
- test_pause_recurring
- test_resume_recurring
- test_end_recurring
- test_frequency_daily
- test_frequency_weekly
- test_frequency_monthly
- test_frequency_quarterly
- test_frequency_annually
- test_skip_holidays

tests/ifrs/automation/test_workflow_service.py (~45 tests)
- test_create_workflow_definition
- test_add_workflow_step
- test_start_workflow_instance
- test_advance_workflow
- test_approve_step
- test_reject_step
- test_parallel_approvals
- test_sequential_approvals
- test_escalation
- test_timeout_handling
- test_delegation

tests/ifrs/automation/test_custom_fields_service.py (~25 tests)
- test_define_custom_field
- test_validate_field_value
- test_get_field_values
- test_search_by_custom_field
```

---

## Phase 4: Banking & Tax Services (~400 statements)

### 4.1 Banking Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `banking/categorization.py` | 21% | 263 | Medium |
| `banking/bank_account.py` | 49% | 75 | Medium |

**Tests Needed:**
```
tests/ifrs/banking/test_categorization_service.py (~50 tests)
- test_auto_categorize_deposit
- test_auto_categorize_withdrawal
- test_rule_based_categorization
- test_ml_categorization
- test_split_transaction
- test_recurring_pattern_detection
- test_vendor_matching
- test_customer_matching
- test_manual_override
- test_bulk_categorization

tests/ifrs/banking/test_bank_account_service.py (~20 tests)
- test_create_bank_account
- test_update_balance
- test_currency_handling
- test_account_hierarchy
```

### 4.2 Tax Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `tax/tax_transaction.py` | 36% | 143 | High |
| `tax/deferred_tax.py` | 37% | 129 | High |
| `tax/tax_reconciliation.py` | 37% | 89 | Medium |

**Tests Needed:**
```
tests/ifrs/tax/test_tax_transaction_service.py (~35 tests)
- test_create_tax_transaction
- test_vat_output
- test_vat_input
- test_withholding_tax
- test_get_vat_register
- test_tax_liability_summary
- test_bulk_create_from_invoices

tests/ifrs/tax/test_deferred_tax_service.py (~30 tests)
- test_calculate_temporary_differences
- test_calculate_deferred_tax_asset
- test_calculate_deferred_tax_liability
- test_net_deferred_tax
- test_movement_schedule
- test_rate_change_impact

tests/ifrs/tax/test_tax_reconciliation_service.py (~25 tests)
- test_statutory_to_effective_rate
- test_permanent_differences
- test_temporary_differences
- test_tax_credits
```

---

## Phase 5: Consolidation & Reporting (~400 statements)

### 5.1 Consolidation Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `cons/consolidation.py` | 32% | 160 | Medium |
| `cons/intercompany.py` | 37% | 118 | Medium |
| `cons/legal_entity.py` | 35% | 110 | Medium |
| `cons/ownership.py` | 35% | 112 | Medium |

**Tests Needed:**
```
tests/ifrs/cons/test_consolidation_service.py (~40 tests)
- test_full_consolidation
- test_proportionate_consolidation
- test_equity_method
- test_currency_translation
- test_intercompany_elimination
- test_minority_interest
- test_goodwill_calculation

tests/ifrs/cons/test_intercompany_service.py (~30 tests)
- test_record_intercompany_transaction
- test_match_intercompany_transactions
- test_generate_eliminations
- test_unmatched_transactions_report

tests/ifrs/cons/test_legal_entity_service.py (~25 tests)
- test_create_entity
- test_entity_hierarchy
- test_functional_currency
- test_reporting_currency

tests/ifrs/cons/test_ownership_service.py (~25 tests)
- test_record_investment
- test_calculate_ownership_percentage
- test_consolidation_method
- test_ownership_changes
```

### 5.2 Reporting Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `rpt/financial_statement.py` | 39% | 119 | Medium |
| `rpt/disclosure_checklist.py` | 33% | 146 | Medium |

**Tests Needed:**
```
tests/ifrs/rpt/test_financial_statement_service.py (~30 tests)
- test_generate_balance_sheet
- test_generate_income_statement
- test_generate_cash_flow
- test_generate_equity_changes
- test_comparative_periods
- test_segment_reporting

tests/ifrs/rpt/test_disclosure_checklist_service.py (~30 tests)
- test_get_applicable_disclosures
- test_check_disclosure_completeness
- test_generate_checklist
- test_mark_disclosure_complete
```

---

## Phase 6: GL & Financial Instruments (~300 statements)

### 6.1 GL Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `gl/account_balance.py` | 56% | 77 | Medium |
| `gl/journal.py` | 55% | 111 | Medium |
| `gl/ledger_posting.py` | 64% | 70 | Medium |

**Tests Needed:**
```
tests/ifrs/gl/test_account_balance_service.py (~25 tests)
- test_calculate_balance
- test_trial_balance
- test_balance_by_period
- test_balance_drill_down
- test_multicurrency_balance

tests/ifrs/gl/test_journal_service_extended.py (~30 tests)
- test_create_recurring_journal
- test_reversing_journal
- test_intercompany_journal
- test_allocation_journal
- test_bulk_journal_import
```

### 6.2 Financial Instruments

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `fin_inst/interest_accrual.py` | 39% | 79 | Medium |
| `fin_inst/valuation.py` | 37% | 85 | Medium |

**Tests Needed:**
```
tests/ifrs/fin_inst/test_interest_accrual_service.py (~25 tests)
- test_accrue_interest_simple
- test_accrue_interest_compound
- test_effective_interest_method
- test_amortization_schedule
- test_bulk_accrual

tests/ifrs/fin_inst/test_valuation_service.py (~25 tests)
- test_fair_value_level1
- test_fair_value_level2
- test_fair_value_level3
- test_amortized_cost
- test_impairment_calculation
```

---

## Phase 7: Additional Services (~400 statements)

### 7.1 FA Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `fa/depreciation.py` | 60% | 83 | Medium |
| `fa/disposal.py` | 54% | 76 | Medium |
| `fa/revaluation.py` | 70% | 44 | Low |

### 7.2 Lease Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `lease/lease_calculation.py` | 66% | 53 | Medium |
| `lease/lease_modification.py` | 68% | 42 | Medium |
| `lease/lease_variable_payment.py` | 71% | 37 | Low |

### 7.3 Other Services

| Service | Current | Missed | Priority |
|---------|---------|--------|----------|
| `common/numbering.py` | 15% | 155 | Medium |
| `exp/expense.py` | 21% | 96 | Medium |
| `auth.py` | 52% | 167 | Medium |
| `auth_dependencies.py` | 51% | 131 | Medium |

---

## Phase 8: Web Services (Optional for 90%)

Web services account for ~3,000 missed statements but are primarily view context builders. These can be tested with integration tests if time permits.

| Service | Current | Missed |
|---------|---------|--------|
| `admin/web.py` | 12% | 782 |
| `rpt/web.py` | 11% | 333 |
| `gl/web.py` | 25% | 351 |
| `ap/web.py` | 36% | 385 |
| `ar/web.py` | 37% | 335 |
| `automation/web.py` | 23% | 182 |
| `banking/web.py` | 19% | 246 |

---

## Estimated Test Count Summary

| Phase | Focus Area | New Tests | Statements |
|-------|------------|-----------|------------|
| 1 | Posting Adapters | ~185 | ~600 |
| 2 | AR/AP Business Services | ~300 | ~700 |
| 3 | Automation & Workflow | ~110 | ~500 |
| 4 | Banking & Tax | ~160 | ~400 |
| 5 | Consolidation & Reporting | ~180 | ~400 |
| 6 | GL & Financial Instruments | ~105 | ~300 |
| 7 | Additional Services | ~100 | ~400 |
| **Total** | | **~1,140** | **~3,300** |

**Note:** Completing Phases 1-6 (~940 tests) should achieve ~85% coverage. Adding Phase 7 tests should reach the 90% target.

---

## Implementation Priority Order

1. **Week 1:** Phase 1 - Posting Adapters (Critical path for all modules)
2. **Week 2:** Phase 2 - AR/AP Services (Core business operations)
3. **Week 3:** Phase 3 - Automation (Supports recurring operations)
4. **Week 4:** Phase 4 - Banking & Tax (Compliance critical)
5. **Week 5:** Phase 5-6 - Consolidation, Reporting, GL, Fin Inst
6. **Week 6:** Phase 7 - Remaining services and gap filling

---

## Testing Best Practices

1. **Mock Database Sessions** - Use `MagicMock` for `db` parameter
2. **Patch External Dependencies** - Mock service dependencies with `patch.object()`
3. **Test Edge Cases** - Include validation errors, boundary conditions
4. **Test Error Handling** - Verify proper exception raising
5. **Use Fixtures** - Create reusable mock objects
6. **Parameterized Tests** - Use `@pytest.mark.parametrize` for variations
