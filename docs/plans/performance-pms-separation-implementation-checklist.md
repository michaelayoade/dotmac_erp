# Performance vs PMS Separation Implementation Checklist

## Goal
- Keep both modules:
  - `Performance` for private-sector workflows
  - `PMS (OHCSF)` for government/public-sector workflows
- Reuse shared building blocks (cycles, templates, core scoring utilities) safely.
- Prevent wrong-module access, mixed data, and UI confusion.

## Scope Rules
- Do not remove existing Performance or PMS features.
- Do not break current tenant data.
- Prefer additive changes with backward compatibility first.

## Phase 0: Baseline and Safety
- [ ] Confirm current behavior with a smoke test:
  - [ ] `/people/perf` works
  - [ ] `/people/perf/pms/dashboard` works
  - [ ] PMS sidebar section follows `pms_ohcsf_enabled`
- [ ] Capture baseline screenshots (private org + PMS-enabled org).
- [ ] Add temporary feature flag for rollout control (optional but recommended).

## Phase 1: Introduce Explicit Mode
- [x] Add org-level mode field (example: `performance_mode`) with enum:
  - [x] `PRIVATE`
  - [x] `GOVERNMENT_PMS`
  - [x] `HYBRID` (optional)
- [x] Add Alembic migration for new field with safe default.
- [x] Add schema/model updates and admin settings form support.
- [x] Keep `pms_ohcsf_enabled` read-compatible for transition window.

## Phase 2: Backward-Compatible Mapping
- [x] Add migration/backfill logic:
  - [x] `pms_ohcsf_enabled = true` => `GOVERNMENT_PMS`
  - [x] `pms_ohcsf_enabled = false` => `PRIVATE`
- [x] Add runtime fallback:
  - [x] If mode missing, infer from legacy flag.
- [x] Add one-way sync policy during transition and document it.

## Phase 3: Enforce Route Guards by Mode
- [x] Add dependency guards in `app/web/deps.py`:
  - [x] `require_private_performance_mode`
  - [x] `require_government_pms_mode`
- [x] Apply guards to route groups:
  - [x] Private-only pages under `app/web/people/perf.py`
  - [x] Government PMS pages under `app/web/people/pms.py`
- [x] Return clear 403 messages for wrong mode.
- [x] Keep HR access guard in place (mode guard is additional, not replacement).

## Phase 4: Navigation and UX Separation
- [x] Update People sidebar in `templates/people/base_people.html`:
  - [x] Show `Performance (Private)` in private mode
  - [x] Show `PMS (Government)` in government mode
  - [x] Show both only in hybrid mode
- [x] Update landing pages and breadcrumbs to remove ambiguity.
- [x] Ensure module labels are consistent across templates and topbar.

## Phase 5: Shared Template Strategy
- [x] Add `template_profile` (or equivalent) to performance templates:
  - [x] `PRIVATE`
  - [x] `PMS`
  - [x] `BOTH`
- [x] Filter template pickers by org mode/profile.
- [x] Keep shared template engine and data model where practical.
- [x] Prevent wrong-profile template assignment at service layer.

## Phase 6: Service-Layer Safety Checks
- [x] Add service-level validation to stop cross-mode writes:
  - [x] PMS-only entities cannot be created in private mode
  - [x] Private-only flows cannot be triggered in PMS-only mode
- [x] Keep checks tenant-scoped and RBAC-safe.
- [x] Add structured error messages for UI display.

## Phase 6.5: Remove PMS Hardcoding (Policy-Driven)
- [x] Introduce a policy profile config for mode-specific rules:
  - [x] `PRIVATE` profile
  - [x] `GOVERNMENT_PMS` profile
- [x] Externalize PMS-specific calculations/rules from hardcoded literals:
  - [x] Weight model (currently objectives=70, process=10, competencies=20)
  - [x] Objective policy (min/max objective count, required total weight)
  - [x] Competency policy (required competency count, required development-focus count)
  - [x] Rating scale mapping (labels, score bands, threshold cutoffs)
  - [x] Workflow transition map (OHCSF stages and allowed transitions)
  - [x] Deadline/phase enforcement rules
  - [x] Governance requirements (appeals/mediation/committee requirements)
  - [x] Mandatory report-pack definitions (OHCSF post-appraisal reports)
- [x] Keep one shared engine, but make it profile-driven:
  - [x] Pass profile config into scoring/validation services
  - [x] Block PMS profile values from being used in private mode unintentionally
- [x] Add safe defaults and strict validation for missing/invalid policy values.
- [x] Hardcoding audit for split-sensitive PMS assumptions:
  - [x] Dashboard/report cycle selector assumptions (`ACTIVE` + `ANNUAL`)
  - [x] OHCSF appraisal transition chain and terminal-state rules
  - [x] Contract planning limits (objective count, weight totals)
  - [x] Competency selection limits (count and development-focus count)
  - [x] Governance role mapping constants (`OHCSF_PMD`, `MDA_HRM`, etc.)
  - [x] Appeal/committee stage behavior and decision routing rules
  - [x] Mandatory OHCSF report set definitions and ordering
  - [x] OHCSF seed constants (competency framework + institutional criteria weights)
  - [x] PMS-specific labels/messages currently embedded in templates/services
  - [x] Confirm pagination defaults are intentionally shared (not split-specific policy)

## Phase 7: Data and Seed Behavior
- [x] Restrict OHCSF seeding (`PMSConfigService`) to government mode only.
- [x] Ensure seeding is idempotent (already true, keep verified).
- [x] Add guard to avoid accidental reseed/mis-seed on mode flips.

## Phase 8: Test Matrix (Must Pass)
- [x] Unit tests:
  - [x] Mode resolution logic
  - [x] Route guard dependencies
  - [x] Template profile filtering
  - [x] Policy profile loading/validation
  - [x] Scoring calculations by profile (including PMS 70/10/20 baseline)
- [x] Integration tests:
  - [x] Private org cannot access PMS endpoints
  - [x] Government org cannot access private-only endpoints (if enforced)
  - [x] Hybrid org can access both
  - [x] Private mode cannot invoke PMS-specific workflow transitions
- [x] UI tests:
  - [x] Sidebar entries by mode
  - [x] Correct landing pages by mode
- [x] Regression tests for existing perf and PMS workflows.

## Phase 9: Rollout Plan
- [ ] Deploy with backward compatibility first (mode + fallback only).
- [ ] Enable strict route guards after verifying production telemetry.
- [ ] Monitor:
  - [ ] 403 spikes by endpoint
  - [ ] 500 errors on perf/pms routes
  - [ ] Settings toggle failures
- [ ] Prepare rollback:
  - [ ] Keep legacy flag path until stabilization is complete.

## Phase 10: Cleanup
- [ ] Remove legacy `pms_ohcsf_enabled` UI path (after stable period).
- [ ] Remove transitional fallback logic.
- [ ] Update docs:
  - [ ] Admin settings guide
  - [ ] People module usage guide
  - [ ] Access-control notes

## Recommended File Touch Map
- `alembic/versions/*` (new migration)
- `app/models/finance/core_org/organization.py`
- `app/services/admin/settings_web.py`
- `app/web/deps.py`
- `app/web/people/perf.py`
- `app/web/people/pms.py`
- `templates/people/base_people.html`
- `app/services/people/perf/web/*` (where mode checks/profile filters are needed)
- `tests/people/perf/*` and `tests/people/*` (mode-access + UX tests)

## Definition of Done
- [ ] Clear UI separation between private and government flows.
- [ ] Shared components reused without data cross-contamination.
- [ ] Wrong-mode URL access reliably blocked.
- [ ] No regression in existing tenant behavior during rollout.
