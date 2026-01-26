# Celery Background Tasks

## Location
All Celery tasks go in `app/tasks/` organized by domain:
- `app/tasks/hr.py` - HR module tasks
- `app/tasks/expense.py` - Expense module tasks
- `app/tasks/finance.py` - Finance module tasks (reminders, aging, etc.)
- `app/tasks/sync.py` - ERPNext sync tasks
- `app/tasks/performance.py` - Performance review automation

## Task Pattern

```python
"""
Module Background Tasks - Celery tasks for [module] workflows.

Handles:
- [List of responsibilities]
"""

import logging
from celery import shared_task
from app.db import SessionLocal

logger = logging.getLogger(__name__)


@shared_task
def process_something() -> dict:
    """
    Brief description of what this task does.

    Returns:
        Dict with processing statistics
    """
    logger.info("Processing something")

    results = {
        "processed": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        # Import service inside task to avoid circular imports
        from app.services.some_module import SomeService

        service = SomeService(db)
        # Delegate ALL logic to service
        items = service.get_items_to_process()

        for item in items:
            try:
                service.process_item(item)
                results["processed"] += 1
            except Exception as e:
                logger.exception("Failed to process %s", item.id)
                results["errors"].append(str(e))

        db.commit()

    logger.info("Completed: %s processed, %s errors",
                results["processed"], len(results["errors"]))
    return results
```

## Key Rules

1. **Services do the work** - Tasks orchestrate, never contain business logic
2. **Use context manager** - `with SessionLocal() as db:` ensures cleanup
3. **Import inside task** - Avoid circular imports by importing services inside
4. **Return statistics** - Always return a dict with counts/errors for monitoring
5. **Log at start/end** - Log when starting and when complete with counts
6. **Catch exceptions per item** - Don't let one failure stop the batch
7. **Commit at end** - One commit after all processing, not per item

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
