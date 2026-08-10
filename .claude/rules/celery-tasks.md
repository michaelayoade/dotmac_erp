# Celery Background Tasks

## Location
All Celery tasks go in `app/tasks/` organized by domain:
- `app/tasks/hr.py` - HR module tasks
- `app/tasks/expense.py` - Expense module tasks
- `app/tasks/finance.py` - Finance module tasks (reminders, aging, etc.)
- `app/tasks/sync.py` - ERPNext sync tasks
- `app/tasks/performance.py` - Performance review automation

## Tenant context — NEVER open a raw `SessionLocal()` (enforced)

Tasks run outside the HTTP request lifecycle, so nothing primes tenant
context for them. A raw `SessionLocal()` sets **neither** the ORM-listener
layer (`session.info["organization_id"]`) **nor** the PostgreSQL RLS GUC
(`SET LOCAL app.current_organization_id`). That is a silent multi-tenant bug:
queries raise `MissingOrgContextError`, return zero rows under DB-RLS, or
leak/write across tenants.

Always open sessions through the canonical context managers in
`app.db.session_context` — they set **both** layers and clean up:

- `session_for_org(org_id)` — single-tenant work (one session per org).
- `cross_org_session()` — genuine cross-tenant batch (list rows across all
  orgs, then process each under its own `session_for_org`).

`scripts/check_session_context.py` enforces this: CI, pre-commit, and the
PostToolUse hook all fail on a raw `SessionLocal()` in `app/tasks/`,
`app/tools/` or `scripts/` — every entry point outside the request lifecycle.

`scripts/` came under the guard late and carries a backlog, so it has a
ratchet: `scripts/session_context_legacy.txt` lists each grandfathered file
with its exact number of raw sessions. The count must not move in either
direction without editing that file, so a legacy script can be retired but
never extended, and every fix is recorded. Removing a line is the goal —
archive the script to `scripts/archive/` if it has already run, move the
decision into a service invoked from `app/tasks/` if it recurs, or open the
session through the canonical helpers if it must still be run by hand.

That list is **not** an approval, and is not interchangeable with the
per-line `# session-context: allow` marker, which means "reviewed and correct
here". For a batch script an unprimed session is not fail-closed but
fail-*silent*: zero rows, exit 0, "job succeeded".

## Task Pattern (single org)

```python
"""
Module Background Tasks - Celery tasks for [module] workflows.

Handles:
- [List of responsibilities]
"""

import logging
from uuid import UUID

from celery import shared_task

from app.db.session_context import session_for_org

logger = logging.getLogger(__name__)


@shared_task
def process_something(org_id: str) -> dict:
    """Brief description. Returns a dict with processing statistics."""
    logger.info("Processing something for org %s", org_id)

    results = {"processed": 0, "errors": []}

    # session_for_org primes BOTH tenant layers and closes on exit.
    with session_for_org(UUID(org_id)) as db:
        # Import service inside task to avoid circular imports
        from app.services.some_module import SomeService

        service = SomeService(db)
        for item in service.get_items_to_process():  # delegate ALL logic
            try:
                service.process_item(item)
                results["processed"] += 1
            except Exception as e:
                logger.exception("Failed to process %s", item.id)
                results["errors"].append(str(e))

        db.commit()  # commit once at the end (do NOT commit-and-continue)

    logger.info(
        "Completed: %s processed, %s errors",
        results["processed"],
        len(results["errors"]),
    )
    return results
```

## Task Pattern (all orgs — fan out)

```python
from sqlalchemy import select

from app.db.session_context import cross_org_session, session_for_org
from app.models.people import Organization


@shared_task
def process_all_orgs() -> dict:
    # Read the org list under a deliberately cross-tenant session...
    with cross_org_session() as cross_db:
        org_ids = list(cross_db.scalars(select(Organization.organization_id)).all())

    # ...then do per-org work under one tenant-scoped session each.
    results = {"orgs": 0, "errors": []}
    for org_id in org_ids:
        try:
            with session_for_org(org_id) as db:
                SomeService(db).run()
                db.commit()
            results["orgs"] += 1
        except Exception as e:
            logger.exception("Org %s failed", org_id)
            results["errors"].append(str(e))
    return results
```

`SET LOCAL` is transaction-scoped — a commit *inside* a `session_for_org`
block silently un-sets the RLS GUC. Prefer one session per org, commit once.
If you must commit-and-continue, re-prime explicitly (the call site owns that
contract).

## Key Rules

1. **Open sessions via `session_for_org` / `cross_org_session`** — never a raw
   `SessionLocal()` (enforced by `check_session_context.py`).
2. **Services do the work** - Tasks orchestrate, never contain business logic
3. **Import inside task** - Avoid circular imports by importing services inside
4. **Return statistics** - Always return a dict with counts/errors for monitoring
5. **Log at start/end** - Log when starting and when complete with counts
6. **Catch exceptions per item** - Don't let one failure stop the batch
7. **Commit at end** - One commit after all processing, not per item; avoid
   commit-and-continue inside a tenant session

## Notification Tasks Pattern

```python
@shared_task
def process_overdue_notifications() -> dict:
    """Send notifications for overdue items."""
    from app.services.finance.reminder_service import ReminderService
    from app.services.notification import NotificationService

    results = {"notifications_sent": 0, "errors": []}

    with SessionLocal() as db:
        reminder_service = ReminderService(db)
        notification_service = NotificationService()

        overdue_items = reminder_service.get_overdue_items()

        for item in overdue_items:
            try:
                # Get recipients from service
                recipients = reminder_service.get_recipients_for_item(item)

                for recipient_id in recipients:
                    notification_service.create(
                        db,
                        organization_id=item.organization_id,
                        recipient_id=recipient_id,
                        entity_type=EntityType.SYSTEM,
                        entity_id=item.id,
                        notification_type=NotificationType.OVERDUE,
                        title="Item Overdue",
                        message=f"Item {item.name} is overdue",
                        channel=NotificationChannel.BOTH,
                    )
                    results["notifications_sent"] += 1
            except Exception as e:
                logger.exception("Failed to notify for %s", item.id)
                results["errors"].append(str(e))

        db.commit()

    return results
```

## Registering Tasks

Tasks are auto-discovered. Register schedules in Celery beat config:

```python
# app/celery_config.py or similar
beat_schedule = {
    'process-overdue-notifications': {
        'task': 'app.tasks.finance.process_overdue_notifications',
        'schedule': crontab(hour=8, minute=0),  # Daily at 8 AM
    },
}
```
