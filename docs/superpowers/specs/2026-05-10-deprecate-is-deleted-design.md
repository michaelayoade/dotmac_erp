# Deprecate `is_deleted` — Design Spec

**Audit reference**: `docs/2026_correctness_audit_findings.md`, P1 #4 — Soft-delete filter coverage is inconsistent.

**Date**: 2026-05-10
**Status**: Approved (architecture only); ready for implementation plan

---

## Problem

The audit found 174 of 238 `select(Employee/Designation)` queries didn't filter `is_deleted` at all. The dual-mechanism architecture (soft-delete column + status enum on the same model) means a developer reading the model expects `is_deleted` to gate visibility, but the actual code expects them to filter on `status`. Soft-deleted employees can surface in self-service tax-profile lookups, leave dropdowns, headcount reports.

Codebase has grown since the audit. Current state on `origin/main` (2026-05-10): `is_deleted` lives in **16 model files across 5 modules** — HR (6 files), PM (2), Support (3), Fleet (2), plus the mixin definitions (3). The audit only called out HR, but the architectural inconsistency is module-wide.

The audit recommended Option A (targeted filters this sprint) or Option C (deprecate `is_deleted` next quarter). With current scope, Option A is multi-sprint grunt work that leaves the inconsistency in place. Option C is a focused, structural fix.

## Goal

Remove `is_deleted` and `deleted_at` from all 13 model files plus the `SoftDeleteMixin` definitions. Each model adopts a single lifecycle indicator: an existing status enum where one fits, an existing `is_active: bool` where one exists, or a new `is_active: bool` field where neither exists.

After this lands, soft-delete is a status transition, not a parallel mechanism. Future models cannot accidentally pick up the inconsistent dual-mechanism pattern.

## Decisions

### D1 — Scope: all 16 files, eliminate the inconsistency entirely

Drop `is_deleted` and `deleted_at` from every model that has them, AND remove `SoftDeleteMixin` from `app/models/mixins.py` and `app/models/people/base.py`. Future models can't adopt the deprecated pattern.

**Rationale**: The audit's literal scope was HR, but the same architectural inconsistency applies across PM, Support, Fleet. Touching only HR leaves the pattern available to new development. With 200-400 estimated call sites already needing updates, the marginal cost of fixing the additional 7 models in PM/Support/Fleet is small relative to the architectural value of eliminating the pattern.

### D2 — Migration strategy: migrate-then-drop (preserve soft-deleted state)

For every row currently marked `is_deleted=true`, set the new lifecycle field to its terminal value before dropping the column. Nothing is lost.

**Rationale**: Hard-deleting soft-deleted rows would lose audit trail data. Dropping the column without migrating would silently revive soft-deleted rows in lists and dropdowns — exactly the failure mode the audit flagged. The migrate-then-drop path is unambiguous about intent and reversible enough for development testing.

### D3 — Per-model lifecycle mapping

Each model gets one canonical lifecycle indicator. Where multiple options exist (e.g., a status enum AND `is_active`), pick the one that's already used for visibility filtering in service code:

| Model | Lifecycle replacement | Migration rule |
|---|---|---|
| `Employee` | `status=EmployeeStatus.TERMINATED` | `UPDATE … SET status='TERMINATED' WHERE is_deleted=true` |
| `Department` | `is_active=False` | `UPDATE … SET is_active=false WHERE is_deleted=true` |
| `Designation` | `is_active=False` | Same shape |
| `EmployeeExtended` | `is_active=False` | Same shape |
| `JobDescription` | `is_active=False` | Same shape |
| `DisciplineCase` | terminal `CaseStatus` (verify exact name) | Same shape |
| `Task` | terminal `TaskStatus` (verify exact name) | Same shape |
| `Ticket` | terminal `TicketStatus` (verify exact name) | Same shape |
| `Vehicle` | terminal `VehicleStatus` (verify exact name) | Same shape |
| `pm/Comment` | NEW `is_active: bool` | Add column default true; UPDATE WHERE is_deleted=true |
| `support/Attachment` | NEW `is_active: bool` | Same shape |
| `support/Comment` | NEW `is_active: bool` | Same shape |
| `VehicleIncident` | NEW `is_active: bool` | Same shape |

Verifications happen during the implementation plan's first task — read each status enum's actual members and confirm the terminal name. Do not assume.

**Rationale**: `is_active: bool` is the lowest-friction replacement for models that have it or need a new flag. Status enums carry domain meaning (TERMINATED, CANCELLED, DECOMMISSIONED) and should be used where they exist with a clear terminal — both because they preserve audit-trail richness and because they're already the column most code filters on.

### D4 — Single Alembic migration, transaction-wrapped

One migration file. ADD any new `is_active` columns first; UPDATE per-table to set the lifecycle field; DROP `is_deleted` and `deleted_at` from all 13 tables. Order matters: ADD → UPDATE → DROP. If any UPDATE fails (e.g., a status enum doesn't have the expected value), the whole migration rolls back via Postgres transaction semantics.

**Rationale**: Splitting the migration into multiple files makes per-table rollback possible but adds operational complexity for no proportional safety win. A single transaction-wrapped migration is atomic — either all 13 tables migrate cleanly or none do.

### D5 — Lossy `downgrade()` path, dev-only

The `downgrade()` reverses the column change but cannot perfectly distinguish "soft-deleted via TERMINATED" from "legitimately TERMINATED for other reasons". A row that's TERMINATED post-migration could have been either. We document downgrade as **dev/staging only**; production rollback should restore from backup, not from `alembic downgrade`.

**Rationale**: A truthful downgrade requires preserving the pre-migration soft-delete bit somewhere. Storing a temporary backup column is overkill for a feature already documented as deprecated; the recovery story for prod is "restore from backup", which is the standard operational answer for any irreversible schema change.

## Architecture

```
Today                                After
─────                                ─────
Employee model:                      Employee model:
  status: EmployeeStatus               status: EmployeeStatus
  is_deleted: bool                     # is_deleted removed
  deleted_at: datetime                 # deleted_at removed

soft_delete:                         soft_delete:
  obj.is_deleted = True                obj.status = EmployeeStatus.TERMINATED
  obj.deleted_at = now()             # status alone carries lifecycle

list query:                          list query:
  select(Employee).where(              select(Employee).where(
    Employee.organization_id == org,     Employee.organization_id == org,
    Employee.is_deleted.is_(False),      Employee.status != EmployeeStatus.TERMINATED,
  )                                    )
                                       # OR for lists that want all states:
                                       # no status filter; status appears in
                                       # the result for the caller to interpret.
```

The same pattern applies to every other model in scope, with the lifecycle field per D3.

## Components

| Path | Purpose | Status |
|---|---|---|
| `app/models/mixins.py` | Remove `SoftDeleteMixin` class entirely | MODIFY |
| `app/models/people/base.py` | Remove its (likely-duplicate) `SoftDeleteMixin` | MODIFY |
| `app/models/people/__init__.py` | Remove `SoftDeleteMixin` re-export | MODIFY |
| `app/models/people/hr/employee.py` | Drop `is_deleted`, `deleted_at`; keep `EmployeeStatus` | MODIFY |
| `app/models/people/hr/department.py` | Drop columns; keep `is_active` | MODIFY |
| `app/models/people/hr/designation.py` | Same | MODIFY |
| `app/models/people/hr/employee_extended.py` | Same | MODIFY |
| `app/models/people/hr/job_description.py` | Same | MODIFY |
| `app/models/people/discipline/case.py` | Drop columns; keep `CaseStatus` | MODIFY |
| `app/models/pm/task.py` | Drop columns; keep `TaskStatus` | MODIFY |
| `app/models/pm/comment.py` | Drop columns; **ADD** `is_active: bool` | MODIFY |
| `app/models/support/ticket.py` | Drop columns; keep `TicketStatus` | MODIFY |
| `app/models/support/attachment.py` | Drop columns; **ADD** `is_active: bool` | MODIFY |
| `app/models/support/comment.py` | Drop columns; **ADD** `is_active: bool` | MODIFY |
| `app/models/fleet/vehicle.py` | Drop columns; keep `VehicleStatus` | MODIFY |
| `app/models/fleet/vehicle_incident.py` | Drop columns; **ADD** `is_active: bool` | MODIFY |
| `alembic/versions/YYYYMMDD_HHMM_drop_is_deleted_phase1.py` | Single transaction-wrapped migration | NEW |
| `tests/migrations/test_drop_is_deleted_phase1.py` | Migration smoke tests (per D4) | NEW |
| Service / call-site files | Replace `is_deleted` reads/writes with lifecycle-field equivalents (~200-400 sites) | MODIFY |

## Data flow

The "soft delete" operation:
- Today: `obj.is_deleted = True; obj.deleted_at = now(); db.flush()`
- After: `obj.<lifecycle_field> = <terminal_value>; db.flush()` (per D3 mapping)

The "list non-deleted" query:
- Today: `select(Model).where(..., Model.is_deleted.is_(False))`
- After: `select(Model).where(..., <lifecycle predicate>)` per D3

The migration:
1. ADD new `is_active: bool DEFAULT true` columns to the 4 models that need them.
2. UPDATE per table to set the lifecycle field where `is_deleted=true`.
3. DROP COLUMN `is_deleted`, DROP COLUMN `deleted_at` on all 13 tables.

## Error handling

| Failure mode | Mitigation |
|---|---|
| Status enum doesn't have the expected terminal value | Verify each status enum's exact members during plan Task 1 before writing UPDATE clauses |
| Existing row has `is_deleted=true` AND non-terminal status (e.g., ACTIVE) | UPDATE is unconditional on `is_deleted=true`; that row's visible status is overwritten. Pre-migration probe documents the count for review. |
| Code somewhere constructs a soft-deleted row at insert | Grep audit before migration: `grep "is_deleted=True"`; refactor or remove dead patterns |
| Missed call site reading `obj.is_deleted` after the column is gone | Loud `AttributeError` at runtime — not silent. CI sweep is the safety net. |

## Testing strategy

**Migration smoke tests** (`tests/migrations/test_drop_is_deleted_phase1.py`, NEW):
- Seed each table with `is_deleted=true` rows (with various status combinations).
- Run migration.
- Assert: column gone from `information_schema.columns`; lifecycle field set per D3 mapping.

**Per-model unit tests**: existing test suites (`tests/people/`, `tests/pm/`, `tests/support/`, `tests/fleet/`) will fail at any site that referenced `is_deleted`. Each failure is a finding to fix in the PR.

**Integration smoke**: full `pytest tests/ --ignore=tests/integration --ignore=tests/e2e` before push. Expected: same pass count as `origin/main` (the audit found most reads were unfiltered already).

**Grep audit pre-merge**:
```bash
grep -rn "is_deleted\|deleted_at\|SoftDeleteMixin" app/ tests/ --include='*.py' | grep -v "test_drop_is_deleted_phase1"
```
Expected: empty.

**Manual per-tenant verification post-deploy**: count terminated/inactive rows before and after; deltas should match the soft-deleted count from the pre-migration probe.

## Out of scope (Phase 1)

- Hard-delete operation. Models still don't physically delete rows; they transition to a terminal lifecycle state, same as today's "soft delete".
- Status enum redesign. We use existing terminal values (TERMINATED, CANCELLED, DECOMMISSIONED, etc.) without changing or extending the enums.
- Performance optimization of the migration on large tenants. The UPDATEs are equality scans on rows that are typically a small fraction of the total table; we do not pre-add indexes specifically for the migration.
- Rollback automation. `downgrade()` exists but is documented as lossy/dev-only; production rollback is restore-from-backup.
- Lint rule blocking new `SoftDeleteMixin` adoption. The mixin is removed from the codebase; if someone re-introduces it, that's a code review failure.

## Open questions

- **R1**: What's the terminal status for `DisciplineCase`? Likely `CLOSED` or `DISMISSED`. Verify by reading `app/models/people/discipline/case.py`.
- **R2**: What's the terminal status for `Task`? Likely `CANCELLED` or `ARCHIVED`. Verify in `TaskStatus` enum.
- **R3**: What's the terminal status for `Ticket`? Likely `CLOSED` or `CANCELLED`. Verify in `TicketStatus`.
- **R4**: What's the terminal status for `Vehicle`? Likely `DECOMMISSIONED`, `RETIRED`, or `INACTIVE`. Verify in `VehicleStatus`.
- **R5**: Are there any service-layer "restore from soft-delete" code paths (set `is_deleted=False`)? If yes, they need to be replaced with a lifecycle-field reset (e.g., `status=ACTIVE`). If they don't exist, the new model is simpler — terminal status transitions are one-way.
- **R6**: Does `app/models/people/__init__.py` re-export `SoftDeleteMixin`? Verify by reading that file. If not, we drop a phantom item from D1's component list.

R1-R4 resolve in plan Task 1 (read the actual enums). R5 resolves via grep. R6 resolves by reading the file.
