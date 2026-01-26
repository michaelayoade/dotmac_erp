# Notification System

## Overview
The notification system handles in-app and email notifications across all modules.

## Core Components

### EntityType (what triggered the notification)
```python
class EntityType(str, enum.Enum):
    TICKET = "TICKET"
    EXPENSE = "EXPENSE"
    LEAVE = "LEAVE"
    ATTENDANCE = "ATTENDANCE"
    PAYROLL = "PAYROLL"
    EMPLOYEE = "EMPLOYEE"
    APPROVAL = "APPROVAL"
    SYSTEM = "SYSTEM"
    # Finance module entity types
    FISCAL_PERIOD = "FISCAL_PERIOD"      # Fiscal period close reminders
    TAX_PERIOD = "TAX_PERIOD"            # Tax filing reminders
    BANK_RECONCILIATION = "BANK_RECONCILIATION"  # Reconciliation overdue
    INVOICE = "INVOICE"                  # AR collection reminders
    SUBLEDGER = "SUBLEDGER"              # GL/subledger discrepancies
```

### NotificationType (what kind of event)
```python
class NotificationType(str, enum.Enum):
    # Assignment
    ASSIGNED = "ASSIGNED"
    REASSIGNED = "REASSIGNED"

    # Status changes
    STATUS_CHANGE = "STATUS_CHANGE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"

    # Deadlines - USE FOR REMINDERS
    DUE_SOON = "DUE_SOON"     # X days before due
    OVERDUE = "OVERDUE"       # Past due date

    # System
    REMINDER = "REMINDER"     # General reminder
    ALERT = "ALERT"           # Urgent attention needed
    INFO = "INFO"             # Informational
```

### NotificationChannel
```python
class NotificationChannel(str, enum.Enum):
    IN_APP = "IN_APP"   # Show in UI only
    EMAIL = "EMAIL"     # Send email only
    BOTH = "BOTH"       # Both in-app and email
```

## Usage Pattern

```python
from app.services.notification import NotificationService
from app.models.notification import EntityType, NotificationType, NotificationChannel

notification_service = NotificationService()

# Create a reminder notification
notification_service.create(
    db,
    organization_id=org_id,
    recipient_id=user_id,
    entity_type=EntityType.SYSTEM,
    entity_id=related_entity_uuid,  # e.g., fiscal_period_id
    notification_type=NotificationType.DUE_SOON,
    title="Fiscal Period Closing Soon",
    message="Q4 2024 closes in 3 days. Complete all journal entries.",
    channel=NotificationChannel.BOTH,
    action_url="/finance/gl/periods",  # Optional - where to navigate
)
```

## Finding Recipients

For bookkeeping reminders, determine recipients by role:

```python
from app.services.rbac import get_users_with_permission

# Get all users who can close fiscal periods
recipients = get_users_with_permission(
    db, org_id, permission="finance.gl.period.close"
)

# Or get users with a specific role
from sqlalchemy import select
from app.models.rbac import RoleAssignment

stmt = select(RoleAssignment.person_id).where(
    RoleAssignment.organization_id == org_id,
    RoleAssignment.role_name.in_(["accountant", "finance_manager"]),
)
recipient_ids = db.scalars(stmt).all()
```

## Avoiding Duplicate Notifications

For recurring reminders (daily/weekly), track what's been sent:

```python
# Option 1: Check if notification exists for today
from datetime import date, datetime
from sqlalchemy import and_, func

today_start = datetime.combine(date.today(), datetime.min.time())

existing = db.scalar(
    select(func.count(Notification.notification_id))
    .where(
        Notification.entity_type == EntityType.SYSTEM,
        Notification.entity_id == period_id,
        Notification.notification_type == NotificationType.DUE_SOON,
        Notification.created_at >= today_start,
    )
)

if existing == 0:
    # Send notification
```

## Best Practices

1. **Use SYSTEM entity type** for bookkeeping reminders
2. **Use DUE_SOON/OVERDUE types** for deadline-based reminders
3. **Include action_url** so users can navigate directly
4. **Use BOTH channel** for important financial reminders
5. **Check for duplicates** before sending recurring reminders
6. **Log what's sent** for debugging and audit trails
