# UI/UX Audit — Wave 1 (2026-03-04)

Comprehensive audit of DotMac ERP templates and web routes against the design system
and CLAUDE.md standards.

---

## P0 — Critical (Breaks Functionality)

### 1. Double Quotes on `x-data` with `tojson` — Breaks Alpine.js

Alpine.js attributes using `tojson` inside double-quoted `x-data` will break because
the JSON output contains double quotes that terminate the HTML attribute prematurely.

| File | Line | Detail |
|------|------|--------|
| `templates/finance/reports/analysis.html` | 16 | `x-data="pivotAnalysis({{ analysis_cubes \| tojson }})"` |
| `templates/projects/templates/form.html` | 7 | `x-data="templateForm(JSON.parse({{ ... \| tojson }}))"` |

**Fix:** Change outer attribute quotes to single quotes.

---

## P1 — High (Standards Violations, Accessibility, Data Integrity)

### 2. Inline Badge HTML Instead of `status_badge()` Macro

9 files use hand-coded `<span>` badge HTML instead of the `status_badge()` macro,
causing inconsistent styling and making global badge changes impossible.

| File | Statuses Affected |
|------|-------------------|
| `templates/finance/ar/invoices.html` | Overdue (inline alongside status_badge) |
| `templates/finance/ap/invoices.html` | Overdue (same pattern) |
| `templates/finance/banking/rules/match_log.html` | Matched, Suggested, Skipped |
| `templates/finance/banking/rules/match_rule_detail.html` | System, Active, Disabled, Matched, Suggested, Skipped |
| `templates/finance/banking/rules/match_rules.html` | Active, Disabled |
| `templates/finance/automation/templates_list.html` | Active, Inactive |
| `templates/expense/reports/my_approvals.html` | Approved, Rejected |
| `templates/finance/settings/payments_index.html` | Active, Not configured (uses `bg-green-*` not `bg-emerald-*`) |
| `templates/people/onboarding/admin/templates.html` | Active, Inactive |

### 3. `<th>` Elements Missing `scope="col"` (WCAG Accessibility)

| File | Count |
|------|-------|
| `templates/expense/reports/my_approvals.html` | 6 th elements |
| `templates/people/self/scheduling_swaps.html` | 4 th elements |
| `templates/people/self/scheduling_schedules.html` | 4 th elements |
| `templates/people/scheduling/pattern_form.html` | 3 th elements |
| `templates/people/self/expenses.html` | 7 th elements |
| `templates/finance/reports/analysis.html` | 1 th element |

### 4. `| default('')` Used for Python `None` Values

`default()` only handles Jinja2 `undefined`, not Python `None`. These will render
the string "None" in the UI.

| File | Lines | Fields |
|------|-------|--------|
| `templates/finance/banking/reconciliation.html` | 259, 332 | `line.reference` |
| `templates/finance/banking/reconciliation_report.html` | 138, 175, 176, 211, 212 | `description`, `reference` |
| `templates/finance/ap/supplier_detail.html` | 95 | `supplier.address` |
| `templates/finance/gl/journal_detail.html` | 241 | `line.description` |
| `templates/finance/banking/account_detail.html` | 45 | `account.branch_name` |
| `templates/finance/banking/statement_detail.html` | 344 | `bank_gl_account_id` (JS context!) |

### 5. Financial Amounts Missing `tabular-nums`

Numbers without `tabular-nums` don't vertically align in tables.

| File | Detail |
|------|--------|
| `templates/finance/ap/invoices.html` | `total_amount` and `balance` cells have `font-mono` but no `tabular-nums` |
| `templates/finance/remita/index.html` | Uses `"{:,.2f}".format()` instead of `format_currency`, no `tabular-nums` |
| `templates/finance/remita/detail.html` | Same pattern |
| `templates/expense/reports/my_approvals.html` | `"{:,.2f}".format()`, no `tabular-nums` |
| `templates/finance/lease/contracts.html` | No `font-mono`, no `tabular-nums`, no `format_currency` |
| `templates/finance/lease/overdue.html` | Same |

### 6. `{% else %}` Clause Not Using `empty_state()` Macro

| File | Current Pattern |
|------|----------------|
| `templates/finance/lease/contracts.html` | Bare `<td>` with text |
| `templates/finance/lease/overdue.html` | Bare `<td>` with text |
| `templates/expense/reports/my_approvals.html` | Plain `<p>` text |
| `templates/finance/automation/templates_list.html` | Custom inline empty div |
| `templates/finance/automation/fields_list.html` | Custom inline empty div |
| `templates/procurement/contracts/list.html` | Bare empty row |

### 7. POST Forms Not Using `{{ request.state.csrf_form | safe }}`

| File | Lines | Pattern |
|------|-------|---------|
| `templates/finance/profile.html` | 72, 129 | Raw `<input type="hidden" name="csrf_token">` |
| `templates/people/self/discipline_detail.html` | 74, 199 | Same |

---

## P2 — Medium (Consistency, Polish)

### 8. Missing `results-container` div on List Pages

`templates/finance/gl/periods.html`, `templates/finance/lease/contracts.html`,
`templates/finance/lease/overdue.html`, `templates/finance/banking/rules/match_rules.html`,
`templates/finance/banking/rules/match_log.html`

### 9. Inline Search Forms Instead of `live_search()` Macro

`templates/expense/limits/reviewer_approvers.html`,
`templates/people/hr/employees.html`,
`templates/people/payroll/slips.html`

### 10. Missing Dark Mode Pairs

`templates/finance/two_factor.html` — `bg-white` without `dark:bg-slate-800`
`templates/finance/import_export/opening_balance.html` — no dark mode classes, uses `bg-green-*`

### 11. Routes with `db.commit()` (Should Be Service Layer)

| File | Count |
|------|-------|
| `app/web/finance/tax.py` | 3 routes |
| `app/web/finance/banking.py` | 5 routes |
| `app/web/people/hr/info_changes.py` | 2 routes |
| `app/web/finance/exp_limits.py` | 1 route |

### 12. Routes with Business Logic (Should Be Web Services)

| File | Severity |
|------|----------|
| `app/web/projects.py` (4278 lines) | Critical — ORM queries, db.add, db.flush, pagination math inline |
| `app/web/procurement.py` (2651 lines) | Critical — 300+ line import handler, export queries inline |

### 13. Legacy `db.query()` in deps.py

5 occurrences of SQLAlchemy 1.x `db.query()` calls in `app/web/deps.py`.

---

## Wave 1 Implementation Scope

**This wave fixes P0 + top P1 items (template-level, low-risk):**

1. Fix double-quote `x-data` with `tojson` (P0)
2. Add `scope="col"` to all `<th>` elements missing it (P1)
3. Replace inline badge HTML with `status_badge()` macro (P1, high-traffic pages)
4. Fix `| default('')` → ternary for None values (P1)
5. Add `tabular-nums` to financial amounts missing it (P1)

**Deferred to Wave 2:** Route refactoring (P2-12), empty_state replacements, results-container additions, live_search migration.
