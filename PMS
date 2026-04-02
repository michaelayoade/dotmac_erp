PMS Implementation Log
Date: 2026-04-02 (UTC)

Completed Items
1) Competency-policy parity
- Enforced contract-bound competency ratings in OHCSF manager review.
- Enforced competency evidence per rating.
- Persisted development-focus flags and evidence in CompetencyAssessment.
- Added/updated tests in tests/people/perf/test_ohcsf_appraisal_workflow.py.

2) Cadence/deadline enforcement
- Added service-level deadline gates for self-assessment, manager review, calibration.
- Applied in both legacy PerformanceService and OHCSFAppraisalService.
- Added tests in tests/people/perf/test_perf_service_deadlines.py and OHCSF workflow tests.

3) Staff committee reconciliation workflow
- Added PerformanceService.reconcile_department_ratings(...) for JUNIOR/SENIOR committee reconciliation.
- Supports endorse/adjust outcomes with committee notes and metadata updates.
- Added tests in tests/people/perf/test_perf_service_reconciliation.py.

4) Post-appraisal SLA automation
- Added app/services/people/perf/dispute_sla_service.py.
- Implemented:
  - enforce_overdue_appeals()
  - enforce_overdue_grievances()
  - enforce_overdue_pips()
  - enforce_all_overdue()
  - collect_upcoming_deadline_reminders()
- Added Celery tasks:
  - process_pms_dispute_sla_enforcement
  - process_pms_dispute_deadline_reminders
- Wired exports in app/tasks/__init__.py.
- Added tests in tests/people/perf/test_dispute_sla_service.py.

Validation Snapshot
- Ruff checks passed for changed files.
- Targeted pytest suites passed after each item.

5) Reward policy depth and transparent selection workflow
- Added transparent candidate eligibility screening with explicit reasons.
- Enforced policy blockers (carryover appraisal, unresolved appeal/PIP, rating floor, duplicate nominations).
- Added audit-style action trail notes for nominate/approve/cancel events.
- Updated reward UI to show eligibility and prevent invalid nominations.
- Added/updated tests in tests/people/perf/test_reward_service.py.

6) Approved-absence documentary evidence object/audit attachment flow
- Replaced boolean documentation flag with structured `approved_absence_evidence` object on appraisal.
- Added required evidence validation for absence over 6 months:
  - document_type
  - document_reference
  - approval_reference
  - validation_reference
- Added optional audit/authority/date/notes fields with approval-date format validation.
- Updated appraisal create/update web flow and form inputs to capture structured evidence.
- Updated appraisal detail page to display captured evidence references for carryover records.
- Added Alembic migration: `20260402_pms_absence_evidence.py`.
- Added/updated tests:
  - tests/people/perf/test_perf_service_absence.py
  - tests/people/perf/test_pms_model_extensions.py

Deferred
8) Full summary-to-feature traceability matrix

7) Expanded actor-level governance touchpoints
- Replaced generic institutional governance action labels with role-specific touchpoints:
  - `MDA_INTERNAL_SUBMISSION`
  - `FMFBNP_CENTRAL_REVIEW`
  - `OHCSF_POLICY_APPROVAL`
  - `CDCU_OSGF_FINAL_SIGNOFF`
  - `CENTRAL_RETURN_FOR_REWORK`
- Added stage-to-role enforcement for workflow transitions:
  - INTERNAL_REVIEW: `MDA_PRS`/`MDA_HRM`
  - CENTRAL_REVIEW: `FMFBNP`
  - APPROVED: `OHCSF_PMD`
  - FINAL_SIGNOFF: `CDCU_OSGF`
  - RETURNED: `FMFBNP`/`OHCSF_PMD`/`CDCU_OSGF`
- Normalized legacy actor role aliases (`HRM` -> `MDA_HRM`, `OHCSF_PMS` -> `OHCSF_PMD`) for compatibility.
- Restricted governance role-assignment action to `MDA_HRM` and `OHCSF_PMD`.
- Added governance touchpoint audit actions for:
  - FCSC grievance escalation (`FCSC_GRIEVANCE_ESCALATION`)
  - SERVICOM stakeholder capture (`SERVICOM_STAKEHOLDER_FEEDBACK_CAPTURED`)
- Validated stakeholder feedback source types (`SERVICOM`, `CITIZEN`, `STAKEHOLDER`).
- Updated institutional web transition role mapping to match stage ownership.
- Added/updated tests in `tests/people/perf/test_governance_service.py`.

8) Final compliance proof (summary-to-feature traceability + executable evidence)
- Added machine-verifiable traceability matrix:
  - `docs/compliance/pms_2022_traceability_matrix.json`
  - Covers all 16 summary clauses (C01..C16) and 8 key insights (KI01..KI08).
  - Maps each clause to concrete feature files and named test cases.
- Added human-readable matrix:
  - `docs/compliance/pms_2022_traceability_matrix.md`
- Added automated compliance guard tests:
  - `tests/people/perf/test_pms_summary_traceability.py`
  - Enforces:
    - all 16 clauses present and unique
    - feature references exist
    - test references exist
    - mapped test case names exist in mapped test files
    - all 8 key insights have test evidence
- Validation:
  - `ruff check tests/people/perf/test_pms_summary_traceability.py`
  - `pytest -q tests/people/perf/test_pms_summary_traceability.py`

Notes
- Worktree contains other unrelated modified files; not reverted.
- Resume from Item 5 next session.
