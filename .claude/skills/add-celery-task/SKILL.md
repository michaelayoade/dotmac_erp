---
name: add-celery-task
description: Create a new Celery background task with proper patterns
arguments:
  - name: task_info
    description: "Task name and purpose (e.g. 'send_overdue_reminders for finance invoices')"
---

# Add Celery Task

Create a new background task for the DotMac ERP.

## Steps

### 1. Determine the module
Parse `$ARGUMENTS` to identify:
- Task name
- Domain module (finance, people, expense, etc.)
- Whether it's periodic (needs beat schedule) or on-demand

### 2. Read reference pattern
Read `app/tasks/finance.py` or `app/tasks/hr.py` for the established pattern.

### 3. Create the task
Add to `app/tasks/{module}.py`:

```python
@shared_task
def task_name() -> dict:
    """
    Brief description.

    Returns:
        Dict with processing statistics
    """
    logger.info("Starting task_name")

    results = {"processed": 0, "errors": []}

    with SessionLocal() as db:
        # Import service inside task (avoid circular imports)
        from app.services.module import SomeService

        service = SomeService(db)
        items = service.get_items_to_process()

        for item in items:
            try:
                service.process_item(item)
                results["processed"] += 1
            except Exception as e:
                logger.exception("Failed to process %s", item.id)
                results["errors"].append(str(e))

        db.commit()

    logger.info("Completed: %d processed, %d errors",
                results["processed"], len(results["errors"]))
    return results
```

### 4. Key rules
- **Services do the work** — task only orchestrates
- **Use `with SessionLocal() as db:`** — ensures cleanup
- **Import inside task** — avoids circular imports at module load
- **Return statistics dict** — for monitoring
- **Log at start/end** — with counts
- **Catch exceptions per item** — one failure shouldn't stop the batch
- **One commit at end** — not per item

### 5. Add beat schedule (if periodic)
In the Celery beat configuration:
```python
'task-name': {
    'task': 'app.tasks.module.task_name',
    'schedule': crontab(hour=8, minute=0),  # Daily at 8 AM
}
```

### 6. For notification tasks
Use the NotificationService pattern:
```python
from app.services.notification import NotificationService
from app.models.notification import EntityType, NotificationType, NotificationChannel

notification_service = NotificationService()
notification_service.create(
    db, org_id, recipient_id,
    entity_type=EntityType.SYSTEM,
    entity_id=entity_uuid,
    notification_type=NotificationType.REMINDER,
    title="Title", message="Message",
    channel=NotificationChannel.BOTH,
)
```
Always check for duplicate notifications before sending (check if one was sent today).

### 7. Test
```bash
# Test the task function directly (not through Celery)
poetry run pytest tests/tasks/ -k "task_name" -v

# Or run manually in a Python shell
poetry run python -c "from app.tasks.module import task_name; print(task_name())"
```
