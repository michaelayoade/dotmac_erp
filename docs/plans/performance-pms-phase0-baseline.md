# Phase 0 Baseline Report: Performance vs PMS

Date: 2026-04-02

## Objective
- Establish pre-change baseline for:
  - route structure
  - core perf/PMS behavior
  - feature-flag/sidebar linkage

## Checks Performed

### 1. Route structure (static verification)
- Verified Performance router prefix:
  - `app/web/people/perf.py` uses `APIRouter(prefix="/perf", ...)`
- Verified PMS router prefix:
  - `app/web/people/pms.py` uses `APIRouter(prefix="/pms", ...)`
- Verified PMS is nested under Performance:
  - `app/web/people/perf.py` includes `router.include_router(pms_router)`

Route decorator counts (for baseline size reference):
- `app/web/people/perf.py`: 69 `@router.get/@router.post` handlers
- `app/web/people/pms.py`: 61 `@router.get/@router.post` handlers

### 2. Sidebar + feature-flag linkage
- Verified template-level PMS sidebar section is conditional:
  - `templates/people/base_people.html` contains `{% if pms_ohcsf_enabled %}`
- Verified context injection of PMS flag:
  - `app/web/deps.py` sets `pms_ohcsf_enabled` from organization.

### 3. Focused smoke test run (behavior baseline)
Command executed:

```bash
pytest -q \
  tests/people/perf/test_scoring_engine.py \
  tests/people/perf/test_contract_service.py \
  tests/people/perf/test_ohcsf_appraisal_workflow.py \
  tests/people/perf/test_ohcsf_reporting_service.py
```

Result:
- All selected tests passed (`exit code 0`).

## Baseline Outcome
- Performance and PMS route layers are in place and nested as expected.
- PMS sidebar visibility is currently controlled by `pms_ohcsf_enabled`.
- Core OHCSF scoring/contract/workflow/reporting tests are currently green.

## Known Gaps (Phase 0 remaining item)
- Manual UI screenshot capture for before-state (private vs PMS-enabled org) is still pending and should be completed in a browser session.

