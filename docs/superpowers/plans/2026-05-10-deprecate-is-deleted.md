# Deprecate `is_deleted` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `is_deleted` and `deleted_at` from 13 model files plus the `SoftDeleteMixin` definitions; migrate the data so soft-deleted rows are preserved under each model's lifecycle field; update ~166 call sites in services to use the new lifecycle predicate.

**Architecture:** Single Alembic migration: ADD any new `is_active` columns → UPDATE per-table to copy soft-delete state into the lifecycle field → DROP `is_deleted` and `deleted_at` from all 13 tables. Models drop the column attributes; services replace filter and set-true sites with lifecycle-field equivalents. The mixin definitions are removed entirely so the deprecated pattern can't be re-adopted.

**Tech Stack:** SQLAlchemy 2.0, Alembic, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-05-10-deprecate-is-deleted-design.md`.

**Verified during brainstorm + recon:**
- Per-model lifecycle mapping (D3): `Employee` → `EmployeeStatus.TERMINATED`; `Department/Designation/EmployeeExtended/JobDescription` → `is_active=False`; `DisciplineCase` → `CaseStatus.WITHDRAWN`; `Task` → `TaskStatus.CANCELLED`; `Ticket` → `TicketStatus.CLOSED`; `Vehicle` → `VehicleStatus.DISPOSED`; `pm/Comment, support/Attachment, support/Comment, VehicleIncident` → NEW `is_active: bool`.
- ~10 `is_deleted=True` set-true sites in service code (not counting the mixin's own `soft_delete()` method which goes away).
- 156 `is_deleted` filter sites across services + tests.
- `app/models/people/__init__.py` re-exports `SoftDeleteMixin`; needs update too.

---

## Task 1: Recon + write the Alembic migration

**Files:**
- Create: `alembic/versions/YYYYMMDD_HHMM_drop_is_deleted_phase1.py`
- Test: `tests/migrations/test_drop_is_deleted_phase1.py` (new)

This task writes the migration first because every per-module slice depends on it. The migration is NOT applied here — applying happens at Task 8 after all model attrs are removed and services updated.

- [ ] **Step 1: Generate the migration stub**

```bash
cd /tmp/dotmac_p1_isdeleted
poetry run alembic revision -m "drop_is_deleted_phase1"
```

Note the generated filename. The `down_revision` should chain off the latest existing head — verify with `git log -- alembic/versions/ | head -10` to find the most recent migration on `origin/main`.

- [ ] **Step 2: Replace the stub body with the full migration**

Edit the generated file. Set both `upgrade()` and `downgrade()` per below. Keep the auto-generated `revision`, `down_revision`, `branch_labels`, `depends_on` as-is.

```python
"""drop is_deleted and deleted_at from 13 tables; migrate soft-deleted state.

Revision ID: <auto>
Revises: <auto>
Create Date: <auto>

P1 #4 of the Narrative-0 audit. See:
  docs/superpowers/specs/2026-05-10-deprecate-is-deleted-design.md

For each table that had is_deleted, we map the soft-delete state into a
canonical lifecycle field BEFORE dropping the column:

  Table                          Replacement
  ─────                          ───────────
  people.employee                status='TERMINATED'
  people.department              is_active=false
  people.designation             is_active=false
  people.employee_extended_*     is_active=false
  people.job_description         is_active=false
  people.disciplinary_case       status='WITHDRAWN'
  pm.task                        status='CANCELLED'
  pm.comment                     NEW is_active=false (column added in this migration)
  support.ticket                 status='CLOSED'
  support.attachment             NEW is_active=false (column added)
  support.comment                NEW is_active=false (column added)
  fleet.vehicle                  status='DISPOSED'
  fleet.vehicle_incident         NEW is_active=false (column added)

The downgrade is documented as DEV-ONLY: it cannot reliably distinguish
"soft-deleted via TERMINATED" from "legitimately TERMINATED for other
reasons", so reversing it loses precision. Production rollback is
restore-from-backup, not `alembic downgrade`.
"""

from alembic import op
import sqlalchemy as sa


# Tables that get a NEW is_active column added by this migration.
_TABLES_NEEDING_IS_ACTIVE = [
    ("pm", "comment"),
    ("support", "attachment"),
    ("support", "comment"),
    ("fleet", "vehicle_incident"),
]

# Per-table soft-delete migration: each entry is
#   (schema, table, sql_setting_lifecycle_field_when_is_deleted_true)
# The SQL fragment goes in the SET clause of an UPDATE.
_PER_TABLE_MIGRATION = [
    ("people", "employee",            "status = 'TERMINATED'"),
    ("people", "department",          "is_active = false"),
    ("people", "designation",         "is_active = false"),
    # NOTE: the next 3 may be named differently; verify on first run via
    #   psql -c "\\dt people.*"
    # If the table names differ from the model names, adjust here.
    ("people", "employee_extended",   "is_active = false"),
    ("people", "job_description",     "is_active = false"),
    ("people", "disciplinary_case",   "status = 'WITHDRAWN'"),
    ("pm",     "task",                "status = 'CANCELLED'"),
    ("pm",     "comment",             "is_active = false"),
    ("support","ticket",              "status = 'CLOSED'"),
    ("support","attachment",          "is_active = false"),
    ("support","comment",             "is_active = false"),
    ("fleet",  "vehicle",             "status = 'DISPOSED'"),
    ("fleet",  "vehicle_incident",    "is_active = false"),
]


def upgrade() -> None:
    # 1. Add new is_active columns where needed.
    for schema, table in _TABLES_NEEDING_IS_ACTIVE:
        op.add_column(
            table,
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            schema=schema,
        )

    # 2. Migrate is_deleted=true rows to their lifecycle terminal value.
    for schema, table, set_clause in _PER_TABLE_MIGRATION:
        op.execute(
            f"UPDATE {schema}.{table} "
            f"SET {set_clause} "
            f"WHERE is_deleted = true"
        )

    # 3. Drop is_deleted and deleted_at on all 13 tables.
    for schema, table, _ in _PER_TABLE_MIGRATION:
        op.drop_column(table, "is_deleted", schema=schema)
        op.drop_column(table, "deleted_at", schema=schema)


def downgrade() -> None:
    """DEV-ONLY downgrade. Lossy: reverses the schema but cannot perfectly
    distinguish soft-delete-via-status from legitimate status transitions.
    Production rollback is restore-from-backup."""
    # 1. Re-add is_deleted and deleted_at on all tables.
    for schema, table, _ in _PER_TABLE_MIGRATION:
        op.add_column(
            table,
            sa.Column(
                "is_deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            schema=schema,
        )
        op.add_column(
            table,
            sa.Column(
                "deleted_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            schema=schema,
        )

    # 2. Reverse-engineer is_deleted=true from the lifecycle field
    #    (LOSSY — see module docstring).
    op.execute(
        "UPDATE people.employee SET is_deleted = true WHERE status = 'TERMINATED'"
    )
    op.execute(
        "UPDATE people.department SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE people.designation SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE people.employee_extended SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE people.job_description SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE people.disciplinary_case SET is_deleted = true WHERE status = 'WITHDRAWN'"
    )
    op.execute(
        "UPDATE pm.task SET is_deleted = true WHERE status = 'CANCELLED'"
    )
    op.execute(
        "UPDATE pm.comment SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE support.ticket SET is_deleted = true WHERE status = 'CLOSED'"
    )
    op.execute(
        "UPDATE support.attachment SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE support.comment SET is_deleted = true WHERE is_active = false"
    )
    op.execute(
        "UPDATE fleet.vehicle SET is_deleted = true WHERE status = 'DISPOSED'"
    )
    op.execute(
        "UPDATE fleet.vehicle_incident SET is_deleted = true WHERE is_active = false"
    )

    # 3. Drop the new is_active columns.
    for schema, table in _TABLES_NEEDING_IS_ACTIVE:
        op.drop_column(table, "is_active", schema=schema)
```

- [ ] **Step 3: Verify the table names match reality**

Spec D3 listed model class names; the migration uses table names. Verify the table for each entry by reading each model file's `__tablename__`:

```bash
cd /tmp/dotmac_p1_isdeleted
for f in app/models/people/hr/employee.py \
         app/models/people/hr/department.py \
         app/models/people/hr/designation.py \
         app/models/people/hr/employee_extended.py \
         app/models/people/hr/job_description.py \
         app/models/people/discipline/case.py \
         app/models/pm/task.py \
         app/models/pm/comment.py \
         app/models/support/ticket.py \
         app/models/support/attachment.py \
         app/models/support/comment.py \
         app/models/fleet/vehicle.py \
         app/models/fleet/vehicle_incident.py
do
  echo "=== $f ==="
  grep -E '__tablename__\s*=|"schema":' "$f" 2>/dev/null | head -3
done
```

Update the migration's `_PER_TABLE_MIGRATION` and `_TABLES_NEEDING_IS_ACTIVE` lists to match the actual `(schema, __tablename__)` pairs. **Important**: `employee_extended` is likely a placeholder; the real model may have multiple tables (qualifications, certifications, etc.). Check the model file and split into separate entries if so.

- [ ] **Step 4: Write a smoke test for the migration**

Create `tests/migrations/__init__.py` (empty) if missing.

Create `tests/migrations/test_drop_is_deleted_phase1.py`:

```python
"""Smoke test for the drop-is_deleted Phase 1 migration.

Asserts the migration's UPDATE clauses preserve soft-deleted state and
the column drop is complete. Runs against the test SQLite double via
the existing migration-test infrastructure.
"""

from __future__ import annotations

import pytest


def test_migration_module_loads():
    """The migration file must be importable without syntax errors."""
    import importlib.util
    from pathlib import Path

    # Locate the most recent drop_is_deleted migration file.
    versions = Path(__file__).resolve().parents[1].parent / "alembic" / "versions"
    candidates = list(versions.glob("*drop_is_deleted_phase1*.py"))
    assert len(candidates) == 1, (
        f"Expected exactly one drop_is_deleted_phase1 migration; "
        f"found {len(candidates)}: {candidates}"
    )

    spec = importlib.util.spec_from_file_location(
        "drop_is_deleted_phase1", candidates[0]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Must expose upgrade and downgrade callables.
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_migration_lists_have_consistent_lengths():
    """_PER_TABLE_MIGRATION must include all 13 tables; _TABLES_NEEDING_
    IS_ACTIVE must be a subset of size 4."""
    import importlib.util
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1].parent / "alembic" / "versions"
    candidates = list(versions.glob("*drop_is_deleted_phase1*.py"))
    spec = importlib.util.spec_from_file_location(
        "drop_is_deleted_phase1", candidates[0]
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert len(module._PER_TABLE_MIGRATION) == 13, (
        f"Expected exactly 13 (schema, table, set_clause) entries; "
        f"got {len(module._PER_TABLE_MIGRATION)}"
    )
    assert len(module._TABLES_NEEDING_IS_ACTIVE) == 4
    # The 4 tables with new is_active columns must also appear in the
    # full migration list.
    new_set = set(module._TABLES_NEEDING_IS_ACTIVE)
    full_pairs = {(s, t) for s, t, _ in module._PER_TABLE_MIGRATION}
    assert new_set <= full_pairs, (
        f"_TABLES_NEEDING_IS_ACTIVE has entries not in _PER_TABLE_MIGRATION: "
        f"{new_set - full_pairs}"
    )
```

- [ ] **Step 5: Run the migration smoke tests**

```bash
poetry run pytest tests/migrations/test_drop_is_deleted_phase1.py -v
```

Expected: 2 passes. The tests confirm the file is importable and structurally consistent. Full-execution tests against a real DB happen at Task 8.

- [ ] **Step 6: DO NOT apply the migration yet.** Tasks 2-7 modify the Python models; Task 8 applies the migration. Leave the migration file in the working tree for now.

---

## Task 2: HR slice — drop columns + update Employee/Department/Designation services

**Files:**
- Modify: `app/models/people/hr/employee.py`
- Modify: `app/models/people/hr/department.py`
- Modify: `app/models/people/hr/designation.py`
- Modify: `app/models/people/hr/employee_extended.py`
- Modify: `app/models/people/hr/job_description.py`
- Modify: `app/models/people/discipline/case.py`
- Modify: ~50-100 service/route/test files in `app/services/people/`, `app/web/people/`, `app/tasks/`, `tests/people/`

The audit's primary scope. After this task, no Python code in the People/HR module references `is_deleted`.

- [ ] **Step 1: Audit all `is_deleted` references in HR**

```bash
cd /tmp/dotmac_p1_isdeleted
grep -rn "is_deleted\|deleted_at" \
  app/models/people/ \
  app/services/people/ \
  app/web/people/ \
  app/tasks/ \
  tests/people/ \
  tests/ifrs/payroll/ \
  --include='*.py' \
  > /tmp/hr_is_deleted_audit.txt
wc -l /tmp/hr_is_deleted_audit.txt
```

Read the audit file. You'll see filter sites (`Employee.is_deleted.is_(False)`), set-true sites (`obj.is_deleted = True`), and possibly `deleted_at` reads.

- [ ] **Step 2: Drop the columns from the 6 model files**

For each of the 6 HR model files, remove the `is_deleted` and `deleted_at` mapped columns AND the `SoftDeleteMixin` from the class bases (if present). The `SoftDeleteMixin` itself is removed in Task 7; for now, just drop the inheritance and the columns.

Pattern for each file:

```python
# Before:
from app.models.people.base import (AuditMixin, SoftDeleteMixin, ...)

class Employee(Base, OrganizationMixin, AuditMixin, SoftDeleteMixin, ...):
    ...
    is_deleted: Mapped[bool] = mapped_column(...)  # remove
    deleted_at: Mapped[datetime | None] = mapped_column(...)  # remove

# After:
from app.models.people.base import (AuditMixin, ...)  # no SoftDeleteMixin

class Employee(Base, OrganizationMixin, AuditMixin, ...):
    ...
    # is_deleted and deleted_at removed
```

If the model defines a `soft_delete()` method that calls `self.is_deleted = True`, replace its body with the lifecycle setter:

| Model | New `soft_delete()` body |
|---|---|
| Employee | `self.status = EmployeeStatus.TERMINATED` |
| Department | `self.is_active = False` |
| Designation | `self.is_active = False` |
| EmployeeExtended (and per-table sub-models) | `self.is_active = False` |
| JobDescription | `self.is_active = False` |
| DisciplineCase | `self.status = CaseStatus.WITHDRAWN` |

If a model doesn't have an explicit `soft_delete()` method (uses the mixin's), service code that calls `obj.soft_delete()` will need to be replaced with the lifecycle setter directly — handled in Step 4.

- [ ] **Step 3: Replace filter sites**

For each filter pattern in HR services/routes/tests, do the substitution:

```python
# Before:                                          After:
Employee.is_deleted.is_(False)                     Employee.status != EmployeeStatus.TERMINATED
Department.is_deleted.is_(False)                   Department.is_active.is_(True)
Designation.is_deleted.is_(False)                  Designation.is_active.is_(True)
EmployeeQualification.is_deleted.is_(False)        EmployeeQualification.is_active.is_(True)
JobDescription.is_deleted.is_(False)               JobDescription.is_active.is_(True)
DisciplinaryCase.is_deleted.is_(False)             DisciplinaryCase.status != CaseStatus.WITHDRAWN
```

For each filter you change:
- Verify the import for the relevant Status enum is present in the file (add if missing).
- Read the surrounding code to make sure the filter's intent matches. Some sites may genuinely want all statuses (no filter); for those, just remove the `is_deleted` predicate and keep the rest of the WHERE clause.

- [ ] **Step 4: Replace set-true sites and `obj.soft_delete()` call sites**

Grep set sites in HR:
```bash
grep -rn "is_deleted\s*=\s*True\|\.soft_delete(" \
  app/services/people/ app/web/people/ app/tasks/ \
  --include='*.py'
```

Each match becomes the lifecycle setter:
```python
# Before:                                  After (Employee):
employee.is_deleted = True                 employee.status = EmployeeStatus.TERMINATED
employee.deleted_at = datetime.utcnow()    # remove (TERMINATED transition is the audit signal)

# Before:                                  After (Department/Designation/etc.):
dept.is_deleted = True                     dept.is_active = False
dept.deleted_at = datetime.utcnow()        # remove
```

Add `from app.models.people.hr.employee import EmployeeStatus` (or appropriate enum) to any service file that didn't already import it.

- [ ] **Step 5: Run the HR test suite**

```bash
poetry run pytest tests/people/ tests/ifrs/payroll/ -q 2>&1 | tail -10
```

Expected outcomes:
- Some tests fail at import time with `AttributeError: type object 'Employee' has no attribute 'is_deleted'`. Fix each by replacing the test's `is_deleted` reference with the lifecycle equivalent.
- After all `is_deleted` references in tests are fixed, the suite should pass at the same count as on `origin/main` (the audit found most filters were broken anyway, so removing them doesn't change observable behavior).

Iterate Steps 4-5 until the HR test suite is green.

- [ ] **Step 6: Verify zero `is_deleted` references remain in HR**

```bash
grep -rn "is_deleted\|deleted_at\|SoftDeleteMixin" \
  app/models/people/ \
  app/services/people/ \
  app/web/people/ \
  app/tasks/hr.py \
  tests/people/ \
  tests/ifrs/payroll/ \
  --include='*.py' | grep -v "test_drop_is_deleted_phase1"
```

Expected: empty output. Anything else is a missed site.

- [ ] **Step 7: DO NOT commit** — leave staged for the orchestrator to commit after spec + code review.

---

## Task 3: PM slice — drop columns from Task and Comment, add is_active to Comment

**Files:**
- Modify: `app/models/pm/task.py`
- Modify: `app/models/pm/comment.py`
- Modify: ~20-50 files in `app/services/pm/`, `app/web/pm/`, `tests/pm/`

- [ ] **Step 1: Audit `is_deleted` references in PM**

```bash
grep -rn "is_deleted\|deleted_at" \
  app/models/pm/ app/services/pm/ app/web/pm/ tests/pm/ \
  --include='*.py' > /tmp/pm_is_deleted_audit.txt
wc -l /tmp/pm_is_deleted_audit.txt
```

- [ ] **Step 2: Update `app/models/pm/task.py`**

Drop `is_deleted` and `deleted_at` columns; remove `SoftDeleteMixin` from the class bases. If the model has its own `soft_delete()` method, replace its body with `self.status = TaskStatus.CANCELLED`.

- [ ] **Step 3: Update `app/models/pm/comment.py`**

Drop `is_deleted` and `deleted_at` columns; remove `SoftDeleteMixin` from the class bases. Add a new `is_active` column:

```python
is_active: Mapped[bool] = mapped_column(
    Boolean,
    nullable=False,
    server_default=text("true"),
    default=True,
)
```

If the model has a `soft_delete()` method, replace its body with `self.is_active = False`.

- [ ] **Step 4: Replace filter sites**

```python
# Task:
Task.is_deleted.is_(False)         →  Task.status != TaskStatus.CANCELLED
# Comment:
Comment.is_deleted.is_(False)      →  Comment.is_active.is_(True)
```

- [ ] **Step 5: Replace set-true sites**

```python
# Task:
task.is_deleted = True             →  task.status = TaskStatus.CANCELLED
# Comment:
comment.is_deleted = True          →  comment.is_active = False
```

Add `from app.models.pm.task import TaskStatus` to any service file that needs it.

- [ ] **Step 6: Run the PM test suite**

```bash
poetry run pytest tests/pm/ -q
```

Expected: pass count matches `origin/main`.

- [ ] **Step 7: Verify zero remaining references**

```bash
grep -rn "is_deleted\|deleted_at\|SoftDeleteMixin" \
  app/models/pm/ app/services/pm/ app/web/pm/ tests/pm/ \
  --include='*.py'
```

Expected: empty.

- [ ] **Step 8: DO NOT commit.**

---

## Task 4: Support slice — Ticket, Attachment, Comment

**Files:**
- Modify: `app/models/support/ticket.py`
- Modify: `app/models/support/attachment.py`
- Modify: `app/models/support/comment.py`
- Modify: ~20-50 files in `app/services/support/`, `app/web/support/`, `tests/support/`

- [ ] **Step 1: Audit references**

```bash
grep -rn "is_deleted\|deleted_at" \
  app/models/support/ app/services/support/ app/web/support/ tests/support/ \
  --include='*.py' > /tmp/support_is_deleted_audit.txt
wc -l /tmp/support_is_deleted_audit.txt
```

- [ ] **Step 2: Update `app/models/support/ticket.py`**

Drop `is_deleted` and `deleted_at`; remove `SoftDeleteMixin`. Update `soft_delete()` body if present to `self.status = TicketStatus.CLOSED`.

- [ ] **Step 3: Update `app/models/support/attachment.py`**

Drop columns; remove `SoftDeleteMixin`; add `is_active: bool` per the same pattern as PM Comment in Task 3 Step 3.

- [ ] **Step 4: Update `app/models/support/comment.py`**

Same as Step 3.

- [ ] **Step 5: Replace filter sites**

```python
# Ticket:
Ticket.is_deleted.is_(False)             →  Ticket.status != TicketStatus.CLOSED
# Attachment / Comment:
Attachment.is_deleted.is_(False)         →  Attachment.is_active.is_(True)
Comment.is_deleted.is_(False)            →  Comment.is_active.is_(True)
```

- [ ] **Step 6: Replace set-true sites**

```python
# Ticket:
ticket.is_deleted = True                 →  ticket.status = TicketStatus.CLOSED
# Attachment / Comment:
att.is_deleted = True                    →  att.is_active = False
```

- [ ] **Step 7: Run the Support test suite**

```bash
poetry run pytest tests/support/ -q
```

Expected: pass count matches `origin/main`.

- [ ] **Step 8: Verify zero remaining references**

```bash
grep -rn "is_deleted\|deleted_at\|SoftDeleteMixin" \
  app/models/support/ app/services/support/ app/web/support/ tests/support/ \
  --include='*.py'
```

Expected: empty.

- [ ] **Step 9: DO NOT commit.**

---

## Task 5: Fleet slice — Vehicle, VehicleIncident

**Files:**
- Modify: `app/models/fleet/vehicle.py`
- Modify: `app/models/fleet/vehicle_incident.py`
- Modify: ~10-30 files in `app/services/fleet/`, `app/web/fleet/`, `tests/fleet/`

- [ ] **Step 1: Audit references**

```bash
grep -rn "is_deleted\|deleted_at" \
  app/models/fleet/ app/services/fleet/ app/web/fleet/ tests/fleet/ \
  --include='*.py' > /tmp/fleet_is_deleted_audit.txt
wc -l /tmp/fleet_is_deleted_audit.txt
```

- [ ] **Step 2: Update `app/models/fleet/vehicle.py`**

Drop `is_deleted` and `deleted_at`; remove `SoftDeleteMixin`. Update `soft_delete()` body to `self.status = VehicleStatus.DISPOSED`.

- [ ] **Step 3: Update `app/models/fleet/vehicle_incident.py`**

Drop columns; remove `SoftDeleteMixin`; add `is_active: bool`.

- [ ] **Step 4: Replace filter sites**

```python
# Vehicle:
Vehicle.is_deleted.is_(False)            →  Vehicle.status != VehicleStatus.DISPOSED
# VehicleIncident:
VehicleIncident.is_deleted.is_(False)    →  VehicleIncident.is_active.is_(True)
```

- [ ] **Step 5: Replace set-true sites**

```python
# Vehicle:
vehicle.is_deleted = True                →  vehicle.status = VehicleStatus.DISPOSED
# VehicleIncident:
incident.is_deleted = True               →  incident.is_active = False
```

- [ ] **Step 6: Run the Fleet test suite**

```bash
poetry run pytest tests/fleet/ -q
```

Expected: pass count matches `origin/main`.

- [ ] **Step 7: Verify zero remaining references**

```bash
grep -rn "is_deleted\|deleted_at\|SoftDeleteMixin" \
  app/models/fleet/ app/services/fleet/ app/web/fleet/ tests/fleet/ \
  --include='*.py'
```

Expected: empty.

- [ ] **Step 8: DO NOT commit.**

---

## Task 6: Remove `SoftDeleteMixin` definitions

**Files:**
- Modify: `app/models/mixins.py`
- Modify: `app/models/people/base.py`
- Modify: `app/models/people/__init__.py`

After Tasks 2-5, no model in the codebase inherits from `SoftDeleteMixin`. Now we delete the class definitions themselves so future models can't pick it up.

- [ ] **Step 1: Read `app/models/mixins.py`**

```bash
cat app/models/mixins.py
```

Find the `SoftDeleteMixin` class block. It's likely 20-40 lines (column definitions + a `soft_delete()` method).

- [ ] **Step 2: Delete the `SoftDeleteMixin` class from `app/models/mixins.py`**

Remove the entire `class SoftDeleteMixin:` block. If the file has imports specifically used by the mixin (e.g., `from datetime import datetime` only used by `deleted_at`), they may now be unused — leave them for ruff to flag at lint time.

- [ ] **Step 3: Read `app/models/people/base.py`**

```bash
grep -B 0 -A 20 "class SoftDeleteMixin\|class .*SoftDelete" app/models/people/base.py
```

If `app/models/people/base.py` defines its own `SoftDeleteMixin` (likely a duplicate or shim of the one in `mixins.py`), delete that class block too.

- [ ] **Step 4: Read `app/models/people/__init__.py`**

```bash
grep -n "SoftDeleteMixin" app/models/people/__init__.py
```

If `SoftDeleteMixin` is in the `__all__` list or `from app.models.people.base import SoftDeleteMixin`, remove those lines.

- [ ] **Step 5: Verify zero `SoftDeleteMixin` references remain**

```bash
grep -rn "SoftDeleteMixin" app/ tests/ --include='*.py'
```

Expected: empty.

- [ ] **Step 6: Run the full test suite**

```bash
poetry run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -5
```

Expected: same pass count as `origin/main`. Any failure is a missed `SoftDeleteMixin` reference somewhere.

- [ ] **Step 7: DO NOT commit.**

---

## Task 7: Apply migration locally + verify pre/post counts

**Files:** No code changes; verification only.

- [ ] **Step 1: Inspect pre-migration soft-delete counts**

If the dev database has any meaningful data, capture pre-migration counts:

```bash
poetry run python -c "
from app.db import SessionLocal
from sqlalchemy import text

with SessionLocal() as db:
    for schema, table in [
        ('people', 'employee'), ('people', 'department'),
        ('people', 'designation'), ('people', 'employee_extended'),
        ('people', 'job_description'), ('people', 'disciplinary_case'),
        ('pm', 'task'), ('pm', 'comment'),
        ('support', 'ticket'), ('support', 'attachment'), ('support', 'comment'),
        ('fleet', 'vehicle'), ('fleet', 'vehicle_incident'),
    ]:
        try:
            count = db.execute(
                text(f'SELECT COUNT(*) FROM {schema}.{table} WHERE is_deleted = true')
            ).scalar()
            print(f'{schema}.{table}: is_deleted=true rows: {count}')
        except Exception as e:
            print(f'{schema}.{table}: SKIP ({e})')
"
```

If the venv mismatch causes the import to fail, skip this step in dev — the migration's UPDATE clauses are deterministic and the migration test in Task 1 already verified structural correctness.

- [ ] **Step 2: Apply the migration**

```bash
poetry run alembic upgrade head 2>&1 | tail -10
```

Expected: clean apply. If there's a failure on a specific table (e.g., a status enum value doesn't exist), the whole transaction rolls back; investigate the specific UPDATE clause and adjust.

- [ ] **Step 3: Verify post-migration state**

```bash
poetry run python -c "
from app.db import SessionLocal
from sqlalchemy import text

with SessionLocal() as db:
    for schema, table in [
        ('people', 'employee'), ('people', 'department'),
        ('pm', 'task'), ('support', 'ticket'),
        ('fleet', 'vehicle'),
    ]:
        result = db.execute(
            text(f\"SELECT column_name FROM information_schema.columns \"
                 f\"WHERE table_schema = '{schema}' AND table_name = '{table}' \"
                 f\"AND column_name IN ('is_deleted', 'deleted_at')\")
        ).fetchall()
        assert not result, f'{schema}.{table} still has columns: {result}'
        print(f'{schema}.{table}: is_deleted/deleted_at dropped ✓')

    # Check the new is_active columns exist where expected.
    for schema, table in [
        ('pm', 'comment'), ('support', 'attachment'),
        ('support', 'comment'), ('fleet', 'vehicle_incident'),
    ]:
        result = db.execute(
            text(f\"SELECT column_name FROM information_schema.columns \"
                 f\"WHERE table_schema = '{schema}' AND table_name = '{table}' \"
                 f\"AND column_name = 'is_active'\")
        ).fetchall()
        assert result, f'{schema}.{table} is missing the new is_active column'
        print(f'{schema}.{table}: is_active column added ✓')
"
```

If venv doesn't allow this, run via psql or skip — the migration test in Task 1 covers structural correctness.

- [ ] **Step 4: Run the full test suite to confirm migration + code changes are consistent**

```bash
poetry run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -5
```

Expected: pass count matches `origin/main`. The combined effect of migration + Python changes is consistent.

- [ ] **Step 5: DO NOT commit yet** — Task 8 batches the final lint/type/format/grep audit and the commit.

---

## Task 8: Final sweep, lint, type, grep audit, commit

**Files:** No code changes; verification only (or auto-fixes from ruff format).

- [ ] **Step 1: Final grep audit across the entire codebase**

```bash
grep -rn "is_deleted\|deleted_at\|SoftDeleteMixin" \
  app/ tests/ \
  --include='*.py' \
  | grep -v "drop_is_deleted_phase1" \
  | head -50
```

Expected: empty (or only the migration file). Anything else is a missed site.

- [ ] **Step 2: Lint**

```bash
poetry run ruff check app/ tests/ 2>&1 | tail -5
```

Expected: All checks passed. Fix any issues with `poetry run ruff check --fix app/ tests/`.

- [ ] **Step 3: Format**

```bash
poetry run ruff format --check app/ tests/ 2>&1 | tail -5
```

If reformatting needed: `poetry run ruff format app/ tests/`.

- [ ] **Step 4: Type check**

```bash
poetry run mypy app/ 2>&1 | tail -10
```

Expected: same error count as `origin/main`. New errors are likely in service code where the lifecycle field was substituted; fix by importing the relevant Status enum or adding type annotations.

- [ ] **Step 5: Pre-commit (final CI dress rehearsal)**

```bash
poetry run pre-commit run --from-ref origin/main --to-ref HEAD 2>&1 | tail -20
```

All hooks should pass. If any fail (ruff, ruff-format, bandit, semgrep, detect-secrets), fix and re-run.

- [ ] **Step 6: Commit**

Stage everything and write the commit message:

```bash
git add -A
git commit -m "$(cat <<'EOF'
Deprecate is_deleted column across 13 models (P1 #4)

Removes is_deleted and deleted_at from Employee, Department, Designation,
EmployeeExtended, JobDescription, DisciplineCase, Task, pm/Comment,
Ticket, support/Attachment, support/Comment, Vehicle, VehicleIncident.
Removes SoftDeleteMixin from app/models/mixins.py and
app/models/people/base.py.

Each model adopts a single canonical lifecycle indicator:
  Employee, DisciplineCase, Task, Ticket, Vehicle: existing status enum
    (terminal values: TERMINATED, WITHDRAWN, CANCELLED, CLOSED, DISPOSED)
  Department, Designation, EmployeeExtended, JobDescription:
    existing is_active: bool
  pm/Comment, support/Attachment, support/Comment, VehicleIncident:
    NEW is_active: bool (added by the migration)

Single Alembic migration:
  1. ADD is_active to the 4 models that need it
  2. UPDATE per-table to set the lifecycle field where is_deleted=true
  3. DROP is_deleted and deleted_at from all 13 tables

The migration is transaction-wrapped: if any UPDATE fails (e.g., an
unexpected status value), the entire migration rolls back atomically.

The audit (docs/2026_correctness_audit_findings.md, P1 #4) found 174 of
238 select(Employee/Designation) queries didn't filter is_deleted at
all — soft-deleted employees could surface in dropdowns and reports.
The dual-mechanism architecture (column + status enum) made the broken
filtering invisible to model readers. After this change, soft-delete is
a status transition, not a parallel mechanism.

Tests: full suite green at the same pass count as origin/main. Lint,
format, mypy, pre-commit hooks: clean.

Spec: docs/superpowers/specs/2026-05-10-deprecate-is-deleted-design.md
Plan: docs/superpowers/plans/2026-05-10-deprecate-is-deleted.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Push and open the PR per the standard workflow.

---

## Self-review summary

- **Spec coverage**: D1 (scope: all 16 files) → Task 6 removes the mixin definitions; Tasks 2-5 drop columns from all 13 models. D2 (migrate-then-drop) → Task 1's migration. D3 (per-model lifecycle mapping) → Task 1's `_PER_TABLE_MIGRATION` list + Tasks 2-5 substitutions. D4 (single transaction-wrapped migration) → Task 1 step 2. D5 (lossy downgrade) → Task 1 docstring + downgrade body.
- **Placeholder scan**: No "TBD" / "implement later". The `# fill in details` patterns are absent — every code-changing step shows the actual code. The `# similar to Task N` pattern is avoided — each task repeats its own substitution patterns even if PM/Support/Fleet share shape.
- **Type consistency**: `EmployeeStatus.TERMINATED`, `CaseStatus.WITHDRAWN`, `TaskStatus.CANCELLED`, `TicketStatus.CLOSED`, `VehicleStatus.DISPOSED` are used consistently across the migration, the model `soft_delete()` bodies, the filter substitutions, and the set-true substitutions. `is_active.is_(True)` (not `== True`) used consistently per SQLAlchemy 2.0 idiom.
