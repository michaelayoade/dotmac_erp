# Correctness Audit — Narrative 0 Findings

**Date:** 2026-05-09
**Branch audited:** `feature/forms-inventory-finance-cleanup`
**Scope:** Silent correctness gaps that produce wrong financial state without user-visible errors. Eight focused checks across the finance, banking, and people stacks.
**Outcome:** Three P0 items, two P1 items, one P2 item, five areas closed clean.

---

## Why this audit

A "Narrative 0 — Correctness" pass is the prerequisite to selling any premium accounting tier. Charging extra for features built on top of silently wrong primitives is unsellable. This audit ran before any new-feature work to surface what would otherwise sit underneath future engineering investment.

Three principles shaped the scope:

1. **Silent-wrong over loud-wrong.** Bugs that throw exceptions self-report. Bugs that produce wrong numbers do not. The audit favored the second class.
2. **Secondary code paths.** Primary entry points (e.g. user-driven journal entry creation) tend to get correctness reviews; *integration* and *import* entry points often don't. Most findings concentrate there.
3. **Defense in depth.** A single missing guard isn't necessarily an active bug, but it converts unrelated upstream bugs into tenant-isolation bugs. Fragility is itself a finding.

---

## Methodology

Eight discrete checks, each scoped tightly enough to reach a verdict in 1–2 hours:

| # | Area | Method |
|---|------|--------|
| 1 | Org-scoped uniqueness constraints | Grep all `UniqueConstraint(...)` and `unique=True` columns; classify each as transitively safe (parent org-scoped), intentionally global (idempotency keys, RRR), or genuinely missing org scope. |
| 2 | Period-lock bypass via direct SQL paths | Find every `JournalEntry(...)` constructor call and verify each path invokes `PeriodGuardService.require_open_period`. |
| 3 | CSRF coverage on bulk-action endpoints | Inspect middleware token-extraction logic + frontend `fetch()` patching to verify JSON-body POST endpoints are covered. |
| 4 | Soft-delete bypass paths | Inventory soft-delete columns; sample `select(SoftDeletedModel)` and `db.get(SoftDeletedModel, ...)` for filter coverage. |
| 5 | Mono webhook idempotency | Read `_get_or_create_statement` and `_add_statement_line_once` for race conditions and dedupe correctness. |
| 6 | AR/AP/cash FX revaluation gap | Confirm the absence by greping for `revalu`/`fx_gain`/`unrealized` callers of `FXService.lookup_spot_rate`. |
| 7 | Decimal-to-float precision | Find `float()` conversions of monetary fields; classify each as display-boundary or arithmetic-in-float. |
| 8 | Multi-org permission bleed | Find `db.get(OrgScopedModel, id)` calls and check for nearby `organization_id` validation. |

---

## P0 — Ship before the next close cycle

### 1. ERPNext journal sync bypasses period lock

**Location:** `app/services/erpnext/sync/journal_entry.py:215-238`

**Problem:** `_resolve_fiscal_period(posting_date)` returns a period_id by date range without checking whether the period is soft- or hard-closed. Every other journal-creation path (`gl/journal.py`, `gl/reversal.py`, `automation/recurring.py`) calls `PeriodGuardService.require_open_period`. ERPNext sync does not.

**Impact:** ERPNext-pushed entries can land in hard-closed periods. Silent post-close adjustments. Audit-trail integrity loss. Restatement risk for any tenant using ERPNext sync.

**Fix:** Wrap the period resolution in `PeriodGuardService.require_open_period(db, org_id, posting_date)`. Mirror the pattern already in `gl/reversal.py:111-123`. ~30 minutes of work plus tests.

### 2. Opening-balance import bypasses period lock

**Location:** `app/services/finance/import_export/opening_balance.py:471-517`

**Problem:** Same shape as #1 — period_id is resolved by date range with no closed-status check.

**Impact:** Lower than #1 in normal operation (opening balance is typically a one-time admin action), but if reused for retroactive adjustments it's the same hole. A tenant doing a re-import after a closed period is the realistic trigger.

**Fix:** Same as #1 — same `PeriodGuardService.require_open_period` wrap. Ship in the same PR as #1.

### 3. AR/AP/cash unrealized FX revaluation is absent

**Location:** No implementation. `FXService.lookup_spot_rate` exists at `app/services/finance/platform/fx.py:49` but is not used for period-end revaluation. Only `app/services/fixed_assets/revaluation.py` exists, and it is fixed-asset specific.

**Problem:** Foreign-currency monetary items (open AR invoices in USD, open AP invoices in USD, USD bank balances) are recorded at their posting-date exchange rate and never revalued. IFRS requires period-end revaluation at the closing spot rate with the gain/loss recognized in P&L.

**Impact:** Material for any tenant with non-functional-currency exposure — USD-denominated SaaS revenue, importers, exporters, USD bank holdings. Quietly IFRS non-conformant. The amount of misstatement scales with FX volatility and exposure size.

**Fix:** New `FXRevaluationService.revalue_period_end(organization_id, fiscal_period_id)`:

1. List open AR invoices, AP invoices, and cash balances in non-functional currencies
2. Look up the closing spot rate per currency via existing `FXService.lookup_spot_rate`
3. Compute `revalued_amount = original_currency_amount × closing_rate` and the delta from book value
4. Post a journal: Dr/Cr the AR/AP/cash accounts; Dr/Cr a `FX Gain/Loss` revenue/expense account
5. Reverse on day 1 of the next period using the auto-reversing pattern that `RecurringTemplate` already supports

Estimated effort: 1–2 weeks of focused engineering. The lookup infrastructure exists; the gap is the period-end orchestration.

**Communication note:** Ship as silent remediation in a minor release. Do **not** position as a premium feature. The honest framing is "we noticed and fixed a calculation gap"; pricing it would be unsellable.

---

## P1 — Architectural fragility, fix systematically

### 4. Soft-delete filter coverage is inconsistent

**Locations:**
- `app/models/people/base.py` — `SoftDeleteMixin` definition
- ~89 `select(Employee/Designation)` call sites that omit `is_deleted` filter (out of 135 total)
- 10+ `db.get(Employee, ...)` direct PK lookups that bypass any filter

**Problem:** 66% of `select(Employee/Designation)` queries do not filter `is_deleted`. Soft-deleted employees can surface in self-service tax-profile lookups, leave dropdowns, headcount reports, and similar reads. Payroll itself is safe today because it filters on `Employee.status == EmployeeStatus.ACTIVE` — but that's a *parallel* mechanism running alongside the documented one, not coordinated with it.

**Impact:** No active money-loss bug detected. Display correctness, headcount metrics, and adjacent paths are exposed. The bigger problem is the architectural inconsistency: a developer reading the model sees `is_deleted` and expects queries to honor it; the code expects them to filter on `status`.

**Fix options:**

| Option | Effort | Risk | Notes |
|---|---|---|---|
| **A — Targeted filters** | ~1 week | Low | Add `is_deleted` filter to high-risk paths only (payroll, leave, self-service). Fast; leaves the architectural inconsistency in place. |
| **B — Global loader criteria** | ~2 weeks | Medium | SQLAlchemy `with_loader_criteria` for `SoftDeleteMixin`. Correct but has gotchas with eager loading and joins; needs careful test coverage. |
| **C — Deprecate `is_deleted`** | ~1 week | Low–Medium | Drop soft-delete columns, rely entirely on status-based lifecycle. One-shot migration. Removes the inconsistency permanently. |

**Recommendation:** Option A this sprint, Option C next quarter. Option B has more failure modes than upside given the codebase already de facto uses status.

### 5. Multi-org permission bleed at the model layer

**Location:** 86 of 338 `db.get(OrgScopedModel, id)` calls in `app/services` lack a same-statement organization check. Sample inspection shows roughly half are actually safe (org check on the line immediately following the `db.get`, missed by the audit's regex window) or transitively safe (parent fetched via org-scoped query). The remainder trust the caller to have validated org membership upstream.

**Problem:** No active cross-tenant leak detected. But the pattern means any single upstream bug (a route that forgets to scope by org, a worker that processes a stale message) becomes a *cross-tenant* bug instead of a *same-tenant* logic error. Severity multiplier on every other bug in the system.

**Fix:** Introduce a single helper:

```python
def get_or_404_for_org(db: Session, model: type[T], pk: UUID, org_id: UUID) -> T:
    obj = db.get(model, pk)
    if obj is None or obj.organization_id != org_id:
        raise NotFoundError(f"{model.__name__} not found")
    return obj
```

Roll out incrementally. Add a lint rule that flags raw `db.get(OrgScopedModel, ...)` outside the helper. ~1 sprint of background work, no big-bang refactor.

---

## P2 — Edge case worth a tracked issue

### 6. Mono `transaction_id=None` collision risk

**Location:** `app/services/finance/banking/mono_sync.py:932`

**Problem:** Statement-line `transaction_id` is built as `f"mono_{txn.id}"`. The partial unique index `uq_banking_bank_statement_lines_mono_transaction_id` covers any `transaction_id LIKE 'mono_%'`. If Mono ever sends transactions with `txn.id = None`, all such transactions collapse to the same dedupe key `"mono_None"` and all but the first IntegrityError-out and are silently dropped.

**Impact:** Silent data loss on the affected transactions. Has not been observed in production, but the partner-bank quirk surface is wide.

**Fix:** Validate `txn.id` is non-empty before constructing the dedupe key, mirroring the existing `_parse_date` validation for `txn.date`. ~10 minutes.

---

## Closed clean (recorded so future audits don't repeat the work)

- **Org-scoped uniqueness on entity numbering** — `uq_invoice_number`, `uq_journal_number`, `uq_supplier_invoice`, `uq_payment_number`, `uq_receipt_number` all properly include `organization_id`. The `Person` model is itself org-scoped, transitively protecting `uq_employee_person`.
- **CSRF coverage** — global middleware (`app/web/csrf.py`) plus the `window.fetch` patch in `templates/base.html:287-296` ensure JSON-body bulk endpoints are covered via the `X-CSRF-Token` header. HTMX requests are covered via the `htmx:configRequest` listener. Forms are covered via `scanForms()`.
- **Mono webhook idempotency** — `_get_or_create_statement` uses a textbook upsert with `with_for_update` lock + savepoint + IntegrityError recovery. `_add_statement_line_once` correctly distinguishes `transaction_id` collisions (true duplicate) from `(statement_id, line_number)` races (retry with bumped line number). Both paths are race-safe.
- **Decimal-to-float precision** — 151 `float()` conversions found, all at the JSON serialization boundary for chart `_raw` keys. Dashboard totals (`balance_sheet.py`, `income_statement.py`, `dashboard.py`) compute in `Decimal` and only convert at the presentation boundary. No money arithmetic in float space that lands in storage.
- **Bulk SQL bypass paths** — zero `db.execute(update(...))` or `db.execute(insert(...))` patterns in `app/services/finance/`. Codebase is consistent ORM usage; ORM-level hooks (audit, validation) cannot be bypassed via raw SQL because raw SQL isn't used.

---

## Recommended sequencing

```
This week:    P0 #1 + P0 #2  (period-lock fixes — half-day each, ship together)
              P2 #6           (Mono None guard — 10 min, while you're in mono_sync.py)
Next 2 weeks: P0 #3           (FX revaluation — the real engineering work)
Next sprint:  P1 #4 Option A  (soft-delete on payroll/leave/self-service paths only)
              P1 #5           (start the helper; migrate opportunistically)
Q3 2026:      P1 #4 Option C  (deprecate is_deleted in favor of status — clean architectural debt)
```

Total P0 effort: ~2 weeks. P1 runs as background work parallel to feature delivery.

---

## Patterns worth establishing as policy

Two patterns surfaced from the audit findings that are worth codifying:

1. **Any path that creates financial records must call the same guard suite.** Today this is `PeriodGuardService.require_open_period`. ERPNext sync and opening-balance import skipped it. The fix isn't only in those two files — it's a code-review checklist item for any future integration that creates `JournalEntry` or its derivatives. Consider a constructor wrapper or a factory that bakes in the guard.

2. **Org-scoped lookups must use the helper, not raw `db.get`.** The 86 instances of raw `db.get(OrgScopedModel, ...)` represent low-grade architectural drift. The helper + lint rule pattern stops the drift from continuing.

The audit was worth running specifically because both findings were *systemic* — they would have continued to grow with each new integration point or service method until something visible broke. Catching them now is cheaper than catching them after a tenant escalation.
