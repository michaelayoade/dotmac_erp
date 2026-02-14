# Contra Transfer Automation Plan

## Goal
Automatically detect and post inter-bank transfers (contra entries) with high precision, then match both statement lines to the posted journal lines in an idempotent way.

## Scope
- In scope:
  - Bank-to-bank transfer detection within one organization.
  - Auto-posting balanced transfer journals.
  - Auto-matching both source and destination statement lines.
  - Rule-driven candidate tagging and bank-target hints.
- Out of scope (phase 1):
  - Partial transfer splits (one line to multiple destination lines).
  - FX transfers across currencies.
  - Net settlements mixed with fees in one statement line (handled separately by settlement pass).

## Architecture
1. Categorization layer (intent only):
- Existing rule engine marks `contra_candidate` and optional `target_bank_hint`.
- No posting from this layer.

2. Contra matching layer (new pass in auto reconciliation):
- Pair outgoing and incoming lines by amount/date/reference features.
- Produce deterministic best pair with confidence score and explanation.

3. Posting/matching layer:
- Create one journal entry:
  - Debit destination bank GL
  - Credit source bank GL
- Match both statement lines to corresponding journal lines.

## Data Model Additions
Add fields in `banking.bank_statement_line_matches` (or companion table) to support traceability:
- `match_type` (`MANUAL`, `AUTO`, `CONTRA_TRANSFER`, `SETTLEMENT`, etc.).
- `match_group_id` UUID for linking both sides of one transfer.
- `match_reason` text/json (scoring evidence).
- `idempotency_key` text unique (optional if derived in service and stored elsewhere).

Migration:
- Add nullable columns first.
- Backfill existing rows with `match_type='AUTO'` and generated `match_group_id` where applicable.

## Service Changes
Primary files:
- `app/services/finance/banking/auto_reconciliation.py`
- `app/services/finance/banking/bank_reconciliation.py`
- `app/services/finance/banking/categorization.py`

### 1) New pass: `_match_contra_transfers(...)`
Insert after direct payment matching and before fallback fuzzy passes.

Input:
- `organization_id`
- current statement bank account (source context)
- unmatched lines
- matched line id sets

Logic:
- Candidate source lines: unmatched debits marked `contra_candidate` or matching transfer patterns.
- Candidate destination lines: unmatched credits on other active bank accounts.
- Date window default: 0-2 days (configurable, max 5).
- Amount tolerance default: exact to 0.01 (configurable).
- Score factors:
  - exact amount match (highest weight)
  - date proximity
  - reference overlap
  - description/token similarity
  - optional bank hint match
- Enforce 1:1 best-match assignment.

### 2) Posting
For each accepted pair:
- Validate source and destination bank GL accounts exist and active.
- Create/post journal with deterministic correlation:
  - `contra-{src_line_id}-{dst_line_id}`
- Idempotency key:
  - `org:{org_id}:contra:{src_line_id}:{dst_line_id}:v1`

### 3) Matching
Use `BankReconciliationService.match_statement_line(...)` for both lines.
Requirements:
- Keep current idempotent behavior (already added).
- Write metadata (`match_type`, `match_group_id`, reason payload).

## Rule Strategy
Extend rule conditions schema with optional hints:
- `is_contra_candidate: true`
- `target_bank_account_id` (optional)
- `target_bank_keywords` (optional)

Behavior:
- Rules do not post/match directly.
- Rules only increase candidate confidence and reduce search space.

## Config Strategy
Add central config (DB or settings) for:
- date window days
- amount tolerance
- minimum confidence for auto-apply
- bank-pair allowlist (optional hard guardrail)

Recommended defaults (phase 1):
- `date_window_days=2`
- `amount_tolerance=0.01`
- `min_confidence=90`
- `require_same_currency=true`

## Safety and Controls
- Tenant scope on every query (organization_id).
- Do not match if either line already matched to different GL.
- Do not post when candidate confidence below threshold.
- Write audit log for every auto contra match with reason and actor `SYSTEM`.
- Feature flag:
  - `BANKING_ENABLE_CONTRA_AUTOMATCH` (off in prod until verification complete).

## Rollout Plan
1. Milestone A: foundation
- Schema migration for metadata columns.
- Add scoring utility + pure unit tests.

2. Milestone B: engine (dry-run)
- Implement `_match_contra_transfers` in “suggest only” mode.
- Store suggestions + confidence, no posting.

3. Milestone C: auto-post pilot
- Enable posting/matching for high-confidence only.
- Run for one org/bank pair with feature flag targeting.

4. Milestone D: broaden coverage
- Add rule hints and bank-pair configs.
- Expand to all bank accounts.

## Test Plan
Unit:
- Scoring and pairing determinism.
- Idempotency key generation.
- Rule hint influence without overmatching.

Integration:
- Journal creation correctness (Dr/Cr accounts and amounts).
- Both lines matched and linked by same `match_group_id`.
- Re-run safety: no duplicate journal/match.
- Cross-tenant isolation.

Regression:
- Existing settlement matching unchanged.
- Existing direct paystack/splynx passes unchanged.

Suggested files:
- `tests/ifrs/banking/test_auto_reconciliation.py`
- `tests/finance/test_bank_reconciliation_service.py`
- new: `tests/ifrs/banking/test_contra_transfer_matching.py`

## Acceptance Criteria
- At least 95% precision in pilot sample (manual validation set).
- Zero duplicate journal entries on repeated runs.
- Zero cross-tenant leakage.
- All contra auto-matches include auditable reason payload.
- No regression in existing banking auto-match tests.

## Execution Backlog (Concrete)
1. Add migration for match metadata columns.
2. Add scorer/pairer module (`app/services/finance/banking/contra_matching.py`).
3. Add contra pass in `AutoReconciliationService`.
4. Extend `match_statement_line` metadata persistence.
5. Add feature flag checks and config wiring.
6. Add dry-run CLI script for historical evaluation.
7. Add unit + integration + regression tests.
8. Roll out pilot and measure precision.
