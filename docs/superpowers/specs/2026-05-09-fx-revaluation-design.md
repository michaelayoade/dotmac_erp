# FX Revaluation — Design Spec

**Date:** 2026-05-09
**Status:** Approved, ready for implementation plan
**Audit reference:** `docs/2026_correctness_audit_findings.md` — P0 #3 (silent IFRS non-conformance)
**Estimated effort:** 1–2 weeks of focused engineering

---

## Context

DotMac records foreign-currency invoices, supplier bills, and bank balances at their posting-date exchange rate and never revalues them at period end. Under IFRS, monetary items denominated in foreign currency must be revalued at the closing spot rate at every period-end with the gain/loss recognised in P&L. The 2026-05-09 correctness audit confirmed zero implementation: `FXService.lookup_spot_rate(...)` exists but no caller uses it for period-end revaluation; the only `revaluation` reference in services is fixed-asset specific.

This affects every tenant with non-functional-currency monetary items — material for any USD-denominated SaaS revenue, importer/exporter, or USD bank holding. The misstatement scales with FX volatility and exposure size.

This spec defines a service that closes the gap: discovers monetary items in non-functional currencies, computes the closing-rate revaluation, and posts an auto-reversing journal pair (period-end posting + day-1-of-next-period reversal) atomically.

## Goals

1. Tenants with non-functional-currency AR invoices, AP invoices, and bank balances see correct period-end carrying values on the balance sheet, with FX gain/loss recognised in P&L.
2. Revaluation runs as an explicit human action by a finance admin, with preview-then-confirm UX, fitting the "Audit-Ready Close" tier positioning.
3. Re-runs of a period (legitimate accountant workflow when a closing rate was wrong, a backdated invoice surfaced, or config was wrong) are supported with full audit trail — voided journals stay visible.
4. The service is auditor-defensible: trial balance shows two named accounts (FX Gain, FX Loss); each revaluation journal includes the per-currency closing rate used and a supporting line-item schedule.

## Non-goals (deferred)

- Customer prepayments, supplier prepayments, advances paid — out of scope for v1; their allocation logic is a separate concern.
- Loans receivable / payable in foreign currency — uncommon at the NG SME ICP size; skip.
- Intercompany balances — consolidation territory; separate spec.
- Auto-discovery of FX gain/loss accounts by name pattern — silent-wrong risk; rejected.
- Auto-creating FX accounts in tenant chart of accounts — assertive write to tenant CoA; rejected.
- Scheduled / Celery-driven revaluation — accountant workflow expects deliberate human action.
- Coupling revaluation to period hard-close as a blocking gate — adds dependency on FX-account configuration that not every tenant needs.
- UI for configuring the FX gain/loss accounts — the settings page extension is a separate (small) follow-up; this spec covers the service that *reads* the settings.

## Architecture decisions

### D1 — Scope: AR + AP + cash, all monetary item classes at once

Full IFRS-conformant coverage in one PR. Tenants with mixed exposure (USD invoices + USD bank balance) get correct numbers in a single deploy. Larger blast radius than phased rollout, mitigated by TDD coverage of each class.

### D2 — Account discovery: per-org `DomainSetting`, hard-fail when unset

Two settings under a new `SettingDomain.gl` value:
- `fx_gain_account_id: UUID` — account that receives credit-side gain postings
- `fx_loss_account_id: UUID` — account that receives debit-side loss postings

Service hard-fails with admin-actionable error if either is unset. No name-pattern auto-discovery (silent-wrong risk). No auto-creation of accounts in tenant CoA (assertive write to CoA rejected). Two-account pattern preserves auditor legibility — trial balance shows named gain and loss accounts separately.

### D3 — Trigger: manual button on fiscal period detail page

Admin clicks "Run FX Revaluation" on a period. Service shows preview (per-currency rate, per-account delta, total proposed journals). Admin confirms. Service posts atomically. No Celery, no scheduling, no period-close coupling. Matches Xero/QuickBooks premium-accountant workflow — the close is a deliberate human action.

### D4 — Re-run: reverse prior pair + post new pair in one transaction, mandatory reason

Service detects existing posted FX revaluation journals for the period. If found, reverses both (the period-end posting and its day-1 reversal) using `ReversalService.create_reversal`, recording the admin-supplied reason on each reversal. Then posts a fresh pair. The original journals remain POSTED with `status = REVERSED`; the reversals show as their own posted entries. Reason is required iff prior pair exists.

**Note on terminology:** `JournalService.void_journal` only operates on DRAFT/SUBMITTED journals. POSTED journals (which our period-end revaluation pair will be) are undone via `ReversalService.create_reversal`. The audit trail thus shows: original P-end posting (POSTED→REVERSED), original reversal (POSTED→REVERSED), reversal of original P-end (POSTED), reversal of original reversal (POSTED), new P-end posting (POSTED), new reversal (POSTED) — six entries net for a single re-run, which is the auditor-defensible record of the change.

### Decisions baked in from accounting convention (not user-decided)

- **Closing rate**: spot rate at the period's `end_date`, looked up via existing `FXService.lookup_spot_rate(currency, period.end_date)`.
- **Revaluation base**: outstanding balance as-of `period.end_date`, not original amount. A USD invoice originally for $1,000 with $400 received before period-end is revalued on the remaining $600 only. Allocations posted *after* period-end are ignored — the balance is a snapshot at the period boundary.
- **Period-of-issue scope**: every monetary item still on the balance sheet at period-end is revalued, regardless of which period it was issued in. A USD invoice issued in 2023 still gets revalued at every 2024 period-end until it's paid.
- **Auto-reversing**: post period-end + post day-1-of-next-period reversal in the same transaction. Atomic. The revaluation entry cannot exist without its reversal.
- **Journal granularity**:
  - On the asset/liability control-account side: one line per `(control_account_id, currency_code)`. Each line's `currency_code` is the foreign currency; the amount is the NGN delta of the revaluation. Sub-ledger detail (per customer, per supplier, per bank account) lives in the journal description as a supporting schedule.
  - On the gain/loss side: one aggregated line per gain/loss account, in functional currency (NGN). Total gain across all currencies and control accounts collapses into a single credit to `fx_gain_account_id`; total loss into a single debit to `fx_loss_account_id`. Lines with zero net are omitted.
  - This keeps GL postings at control-account granularity, matches how the rest of the AR/AP architecture works, and produces an auditor-legible trial balance.
- **Decimal precision**: all internal arithmetic in `Decimal`. Final journal-line amounts rounded to 2 decimal places (NGN convention). No `float` introduced.
- **Functional-currency items**: skipped entirely (no FX exposure → nothing to revalue).

## File layout

```
app/services/finance/gl/
  fx_revaluation.py                              ← FXRevaluationService

app/services/finance/gl/web/
  fx_revaluation_web.py                          ← FXRevaluationWebService

app/web/finance/gl.py
  + GET  /periods/{period_id}/fx-revaluation     ← preview page
  + POST /periods/{period_id}/fx-revaluation     ← confirm + post

templates/finance/gl/
  period_fx_revaluation.html                     ← preview + confirm form (new)
  period_detail.html                             ← + "Run FX Revaluation" button

app/models/domain_settings.py
  + SettingDomain.gl                             ← enum extension

alembic/versions/
  YYYYMMDD_add_gl_setting_domain.py              ← migration for the enum value

tests/ifrs/gl/
  test_fx_revaluation_service.py                 ← service-level TDD (primary coverage)
  test_fx_revaluation_web_service.py             ← web-service-level (preview/post)
```

## Public service contract

```python
@dataclass
class FXRevaluationLine:
    """One revaluation observation: a single (control_account, currency)
    pair's delta. The proposed journal is *constructed* from these — the
    asset/liability side becomes one journal line per FXRevaluationLine,
    while the gain/loss side aggregates across all observations into two
    summary lines."""
    account_id: UUID
    currency_code: str
    closing_rate: Decimal
    book_value_functional: Decimal       # current carrying amount in NGN
    revalued_value_functional: Decimal   # value at closing rate, in NGN
    delta_functional: Decimal            # revalued - book; signed
    is_gain: bool                        # True iff delta increases asset / decreases liability


@dataclass
class FXRevaluationPreview:
    """Output of preview() — no DB writes."""
    fiscal_period_id: UUID
    period_end_date: date
    next_period_start_date: date | None  # None if next period missing/closed
    lines: list[FXRevaluationLine]
    total_gain_functional: Decimal
    total_loss_functional: Decimal
    rates_used: dict[str, Decimal]       # currency_code → closing rate
    warnings: list[str]                  # missing rates, missing accounts, etc.
    prior_run_exists: bool               # UI uses this to require reason field
    prior_journal_ids: list[UUID]        # period-end + reversal, if any


@dataclass
class FXRevaluationResult:
    """Output of post() — journals have been written."""
    success: bool
    period_end_journal_id: UUID | None
    reversal_journal_id: UUID | None
    reversed_prior_journal_ids: list[UUID]   # if re-run; ids of the original journals that are now status=REVERSED
    total_gain_functional: Decimal
    total_loss_functional: Decimal
    message: str
    errors: list[str]


class FXRevaluationService:
    def __init__(self, db: Session):
        self.db = db

    def preview(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> FXRevaluationPreview:
        """Discover monetary items, look up closing rates, compute deltas.
        Read-only — no DB writes. Used to render the preview screen.

        Raises HTTPException(400) if FX gain/loss accounts unconfigured
        or period not OPEN/REOPENED."""

    def post(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
        user_id: UUID,
        reason: str | None = None,
    ) -> FXRevaluationResult:
        """Atomic post. Locks the period row (SELECT FOR UPDATE) to
        serialize concurrent admins. Voids prior pair if exists (reason
        required iff prior exists). Posts period-end + day-1-of-next
        reversal. Both posts go through PeriodGuardService.

        Raises HTTPException(400) on:
          - unconfigured FX accounts
          - period not open
          - prior pair exists but no reason supplied
          - day-1 reversal would land in a closed period
        """
```

## Data flow

```
[admin clicks "Run FX Revaluation" on Period detail]
        ↓
GET /periods/{id}/fx-revaluation
   FXRevaluationWebService.preview_response()
   → FXRevaluationService.preview()
        ↓ (no writes)
   1. Verify period exists, is OPEN or REOPENED
   2. Read DomainSetting: fx_gain_account_id, fx_loss_account_id
        → if missing: HTTPException(400) with admin path
   3. Discover monetary items in non-functional currency:
        - AR open invoices  (currency_code != functional, balance_due > 0
                             where balance_due = total_amount − amount_paid_at_period_end)
        - AP open invoices  (same)
        - BankAccount       (currency_code != functional, status=active,
                             current balance non-zero)
   4. For each currency in scope:
        rate = FXService.lookup_spot_rate(currency, period.end_date)
        if rate is None: warning, skip currency
   5. Compute per-item revalued NGN amount and delta
   6. Aggregate per (control_account_id, currency_code):
        sum(delta) for that pair
   7. Build proposed journal lines (one per non-zero aggregated pair)
   8. Detect prior run via:
        select JournalEntry where
          fiscal_period_id == this period
          source_module == "FXR"
          status == POSTED
        → if found, populate prior_journal_ids and prior_run_exists=True
        ↓
[template renders preview table; reason field shown iff prior_run_exists]
        ↓
[admin reviews, optionally enters reason, clicks Confirm]
        ↓
POST /periods/{id}/fx-revaluation  (CSRF, optional reason)
   FXRevaluationWebService.post_response()
   → FXRevaluationService.post()
        ↓ (atomic via route-level transaction — service flushes only;
           the route's db.commit() is the atomicity boundary; any raise
           inside post() unwinds via get_db's finally: db.rollback())
   1. Re-run preview internally (state may have changed since GET)
   2. Lock period row with SELECT FOR UPDATE — serializes concurrent admins
   3. If prior pair exists:
        - Validate reason field is present and non-empty
        - ReversalService.create_reversal(prior_period_end, reversal_date=period.end_date, reason=...)
        - ReversalService.create_reversal(prior_reversal,    reversal_date=next_period.start_date, reason=...)
   4. Post period-end journal:
        JournalService.create_journal(JournalInput(
          journal_type = JournalType.REVALUATION,
          entry_date   = period.end_date,
          posting_date = period.end_date,
          description  = "FX revaluation as at <period.end_date>"
                         + per-currency rate breakdown
                         + total gain/loss summary,
          source_module = "FXR",
          correlation_id = <new UUID>,   # links the pair
          lines = [
            # one line per (control_account, currency, dr/cr)
            # dr/cr determined by sign of aggregated delta
          ],
        ))
        — automatically goes through PeriodGuardService.require_open_period
   5. Resolve next-period start date:
        next_period = period for date == period.end_date + 1 day
        if next_period is None or not OPEN/REOPENED:
          rollback; raise HTTPException(400) with explanation
   6. Post day-1 reversal journal:
        JournalService.create_journal(JournalInput(
          journal_type   = JournalType.REVALUATION,
          entry_date     = next_period.start_date,
          posting_date   = next_period.start_date,
          description    = "Reversal of FX revaluation <period_end_journal.journal_number>",
          source_module  = "FXR",
          correlation_id = <same as period_end>,
          reversal_journal_id = <period_end_journal.journal_entry_id>,
          lines = mirror of step 4 (debits ↔ credits flipped),
        ))
   7. db.flush()
        ↓
[redirect to period detail with success toast:
   "FX revaluation posted: ₦<gain> gain, ₦<loss> loss across N currencies"]
```

## Error handling matrix

| Condition | Behavior |
|---|---|
| `fx_gain_account_id` or `fx_loss_account_id` unset | `HTTPException(400, "FX revaluation needs both accounts configured. Visit /admin/settings/gl/fx and set 'FX Gain Account' and 'FX Loss Account'.")` |
| Period not OPEN/REOPENED | `HTTPException(400, "Period must be open to post revaluation")` |
| No closing rate available for a currency | Preview shows warning; that currency's items skipped from the run; admin can decide to abort or proceed |
| No monetary items in non-functional currency | `FXRevaluationResult(success=True, message="Nothing to revalue", *_journal_id=None)` — no journals posted, no error |
| Next-period start date is in a closed period | `HTTPException(400, "Day-1 reversal would post to a closed period. Reopen <next_period_name> or contact <admin>.")` — entire transaction rolled back |
| Prior pair exists, no reason supplied | `HTTPException(400, "Re-running requires a reason for replacing the prior revaluation")` |
| Two admins click "Run" simultaneously | `SELECT FOR UPDATE` on period row serialises. First click posts pair. Second click sees prior pair → requires reason → second admin gets the "reason required" 400 (which is the right answer). |

## Race / consistency considerations

- **Period lock**: `SELECT FOR UPDATE` on the `FiscalPeriod` row at the start of `post()` serializes concurrent calls within the same period.
- **Re-preview inside `post()`**: state could have changed between GET preview and POST confirm (a new AR invoice posted, a closing rate updated). Re-running preview inside `post()` ensures the journal posted matches the current state, not a stale snapshot.
- **Pair atomicity**: both journals (period-end + day-1 reversal) are flushed within the same route-level transaction; the route calls `db.commit()` only after `post()` returns successfully. Any exception raised inside `post()` (period closed, accounts unconfigured, reason missing, JournalService validation failure, etc.) propagates to the route, which never reaches `db.commit()`; the `get_db` dependency's `finally: db.rollback()` then unwinds. If the day-1 reversal step fails, the period-end journal's flush has not been committed and is rolled back too.
- **Idempotency token**: `correlation_id` shared between the pair lets the re-run path identify "the prior revaluation pair" deterministically — no fuzzy matching by date or description.

## Testing approach

TDD discipline (failing test first, watch fail, minimal implementation, watch pass) for all service-level behavior. Tests are pure unit — `MagicMock()` db, real Decimal arithmetic, patch `FXService.lookup_spot_rate` and `JournalService.create_journal` at their canonical class location.

```python
# tests/ifrs/gl/test_fx_revaluation_service.py

class TestPreview:
    def test_refuses_when_fx_gain_account_unconfigured(): ...
    def test_refuses_when_fx_loss_account_unconfigured(): ...
    def test_refuses_when_period_closed(): ...
    def test_returns_empty_preview_when_no_foreign_currency_items(): ...
    def test_skips_items_in_functional_currency(): ...
    def test_warns_when_currency_has_no_closing_rate(): ...
    def test_revalues_ar_invoice_outstanding_balance_not_original(): ...
    def test_revalues_ap_invoice_outstanding_balance(): ...
    def test_revalues_bank_account_balance(): ...
    def test_aggregates_lines_per_account_currency_pair(): ...
    def test_detects_prior_run_via_correlation_id(): ...

class TestPost:
    def test_posts_period_end_and_day_one_reversal_atomically(): ...
    def test_reversal_journal_lands_in_next_period(): ...
    def test_reversal_lines_mirror_period_end_lines(): ...
    def test_re_run_voids_prior_pair_before_reposting(): ...
    def test_re_run_requires_reason(): ...
    def test_re_run_records_reason_in_void_audit_trail(): ...
    def test_rolls_back_when_next_period_is_closed(): ...
    def test_concurrent_admins_serialize_via_period_lock(): ...
    def test_no_journals_posted_when_nothing_to_revalue(): ...

# tests/ifrs/gl/test_fx_revaluation_web_service.py

class TestPreviewResponse:
    def test_preview_response_includes_warnings_in_template_context(): ...
    def test_preview_response_passes_prior_run_flag_for_reason_field(): ...

class TestPostResponse:
    def test_post_response_redirects_with_success_toast(): ...
    def test_post_response_propagates_400_on_unconfigured_accounts(): ...
    def test_post_response_returns_form_with_error_when_reason_missing(): ...
```

Smoke route tests are optional and light — the heavy correctness lives in service-level tests.

## Open questions / risks

### R1 — Bank account balance source

Where does the period-end balance for a bank account come from? Two options:
- **Last imported statement closing balance** (`BankAccount.last_statement_balance`) — current, but may be stale or empty for accounts without statement imports yet.
- **Computed from journal postings** as at `period.end_date` — always derivable but more expensive.

**Resolution**: use `BankAccount.last_statement_balance` if `last_statement_date >= period.end_date`; otherwise compute from journals (sum of debits − credits on the bank's GL account through period.end_date). Fall back gracefully; surface staleness as a preview warning.

### R2 — Multi-period rate lookup behavior

`FXService.lookup_spot_rate(currency, date)` returns the rate effective on that date. Behavior when no rate is recorded for the exact date is not defined in this spec — it falls through to whatever `FXService` does today (typically: nearest prior rate, or `None`). If `None`, the warning path triggers and that currency is skipped.

**Resolution**: trust existing `FXService` behavior; document in the spec that rate availability is the tenant's responsibility. Add tenant-visible UX in a follow-up to surface "missing rates for currencies you have exposure to" before period close.

### R3 — Auto-create vs separate migration for `SettingDomain.gl`

The enum extension and the FX-account settings UX are technically separate concerns. This spec covers the service that *reads* the settings; it doesn't cover the admin UI to *set* them. The migration to add `gl` to `SettingDomain` ships with this spec; the admin UI to set the values is a small follow-up.

**Resolution**: ship migration in this PR. Admins set values via the existing `/admin/settings` page (which dynamically lists domains) until a dedicated FX settings page lands.

### R4 — Posting date for the reversal when next period crosses fiscal year boundary

If revaluating period 12 of FY24 and next period is period 1 of FY25, the reversal lands in FY25-period-1. This is the correct accounting behavior, but it does mean an opening fiscal year carries an entry that wasn't created by the year's own activity.

**Resolution**: this is correct accounting; document in the journal description that the reversal is from the prior FY's period-end revaluation.

## Out of scope (explicit deferrals)

- Customer prepayments / supplier prepayments revaluation
- Loans receivable / payable in foreign currency
- Intercompany balance revaluation
- Scheduled / Celery-driven revaluation
- Period-close gating ("can't hard-close until revaluation runs")
- Auto-discovery of FX accounts by name
- Auto-creation of FX accounts in tenant CoA
- Dedicated FX settings admin page UI (planned follow-up)
- Surfacing "missing rates" warnings before period close (planned follow-up)

---

## Implementation summary

| | |
|---|---|
| **New service** | `FXRevaluationService` (~400 lines including helpers) |
| **New web service** | `FXRevaluationWebService` (~150 lines) |
| **Routes added** | 2 (`GET` + `POST` on `/periods/{id}/fx-revaluation`) |
| **Templates added** | 1 (`period_fx_revaluation.html`) |
| **Templates touched** | 1 (`period_detail.html` — adds button) |
| **Migrations** | 1 (extend `SettingDomain` enum with `gl`) |
| **Models touched** | 0 (re-uses `JournalEntry`, `BankAccount`, `Invoice`, `SupplierInvoice`, `DomainSetting`) |
| **Test files** | 2 (service + web service) |
| **Estimated lines of test** | ~600 |
| **Estimated total effort** | 1–2 weeks of focused engineering |

Ready for implementation plan via `writing-plans`.
