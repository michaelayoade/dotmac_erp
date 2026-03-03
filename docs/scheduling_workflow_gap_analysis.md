# Scheduling Workflow Gap Analysis

Date: 2026-02-27
Scope: `app/services/people/scheduling/*`, `app/api/people/scheduling.py`, `app/web/people/scheduling.py`, scheduling models/schemas.

## 1) Current Service Responsibilities

### Shift Patterns
- Owns reusable pattern definitions: rotation mode, cycle length, work days, shift type mapping.
- Service: `SchedulingService` pattern CRUD.

### Pattern Assignments
- Links employee + department + pattern over effective date range.
- Service: `SchedulingService` assignment CRUD + overlap check.

### Schedules
- Generated day-level assignments (`ShiftSchedule`) grouped by `schedule_month`.
- Service split:
  - `ScheduleGenerator`: generate/publish/delete-month-drafts
  - `SchedulingService`: schedule list/get/update/delete single entry

### Swap Requests
- Employee-initiated swap request lifecycle with target acceptance and manager approval.
- Service: `SwapService`.

## 2) Current Implemented Workflow (As-Is)

1. Admin/HR creates shift patterns.
2. Admin/HR assigns patterns to employees by department and date range.
3. Admin/HR generates a month schedule for a department (creates DRAFT rows).
4. Admin/HR publishes the month schedule (moves DRAFT -> PUBLISHED).
5. Employee can request swap between published schedules.
6. Target employee accepts.
7. Manager approves/rejects.
8. If approved, shift types are swapped between the two `ShiftSchedule` rows.

## 3) Gaps / Missing Pieces

### Critical
1. Cross-department assignment overlap can cause schedule generation failure.
- Overlap checks are limited to same department only, but `ShiftSchedule` is unique by org+employee+date.
- Result: employee can be concurrently assigned in multiple departments, then generation can hit DB unique violation.
- Evidence:
  - `SchedulingService._check_overlapping_assignment` filters by `department_id`.
  - `ShiftSchedule` uniqueness includes `organization_id, employee_id, shift_date`.

2. Inactive patterns can still drive schedule generation.
- Pattern deactivation only sets `is_active=False`, but assignment generation query does not filter pattern active state.
- Result: deactivated pattern can continue producing schedules through active assignments.

### High
3. Target-decline path exists in service but is not exposed in API/web workflow.
- `SwapService.decline_swap_request()` exists, but no `/swaps/{id}/decline` endpoint in API and no web action.
- Result: target can only accept in exposed flow; practical decline is missing from user-facing workflow.

4. Swap rejection reason type is conflated.
- Target decline and manager rejection both end in `REJECTED`.
- Result: analytics/audit cannot clearly distinguish “peer declined” vs “manager rejected”.

5. Month status reporting is nondeterministic when mixed statuses exist.
- `get_schedule_status_for_month()` returns first matching status with no aggregation/order.
- Result: UI month badge may misrepresent actual mixed month state.

6. Swap approval authority traversal is capped at 3 parents.
- `_verify_manager_authorization()` hard-limits hierarchy traversal to 3 levels.
- Result: valid higher-level manager in deeper hierarchy may be blocked.

7. Notification links point to routes not present in scheduling web module.
- Notifications use `/people/self/swap-requests` and `/people/self/schedule`, but no corresponding routes in `app/web/people/scheduling.py`.
- Result: likely dead links unless implemented elsewhere.

### Medium
8. Assignment updates can bypass overlap rules.
- `update_assignment()` sets fields directly and does not re-run overlap validation.
- Result: invalid overlaps can be introduced post-create.

9. Schedule regeneration behavior is incomplete for published months.
- Generation blocks if *any* schedule exists for month/department.
- Delete-month helper only removes DRAFT.
- Result: no controlled republish/revision flow for published schedules.

10. `COMPLETED` schedule status is modeled but has no transition logic.
- Enum includes `COMPLETED`, but no service path transitions to it.

## 4) Recommended Target Workflow (To-Be)

1. Pattern lifecycle: Draft/Active/Retired semantics with assignment eligibility checks.
2. Assignment lifecycle: enforce org-wide employee overlap rule against generated schedule uniqueness.
3. Month scheduling lifecycle:
- Generate (DRAFT)
- Validate (conflicts, leave, overtime constraints)
- Publish (PUBLISHED)
- Optional controlled revision flow (new version or reopen with audit)
- Close past periods (COMPLETED)
4. Swap lifecycle:
- PENDING -> TARGET_ACCEPTED -> APPROVED/REJECTED
- PENDING -> TARGET_DECLINED (distinct terminal state)
- PENDING/TARGET_ACCEPTED -> CANCELLED
5. Self-service UX/API parity:
- Create / accept / decline / cancel for employee actions.
- Manager approve/reject endpoints.

## 5) Immediate Backlog (Suggested Order)

1. Enforce overlap against org-wide employee schedule uniqueness.
2. Filter out inactive patterns during generation and prevent assigning inactive patterns.
3. Add API + web flow for target decline.
4. Split `REJECTED` into manager vs target-declined terminal states (or add explicit decision metadata field).
5. Fix month status computation to aggregate deterministically.
6. Define and implement `COMPLETED` transition policy.
7. Add scheduling test coverage (currently minimal) for lifecycle and conflict cases.
