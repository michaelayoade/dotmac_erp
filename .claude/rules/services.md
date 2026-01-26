# Service Layer Guidelines

## Location
Services live in `app/services/` organized by domain:
```
app/services/
├── finance/
│   ├── gl/          # General Ledger
│   ├── ar/          # Accounts Receivable
│   ├── ap/          # Accounts Payable
│   ├── banking/     # Bank reconciliation
│   ├── tax/         # Tax management
│   └── reminder/    # Financial reminders (NEW)
├── people/
│   ├── hr/          # HR management
│   ├── payroll/     # Payroll processing
│   └── leave/       # Leave management
├── pm/              # Project management
├── expense/         # Expense claims
└── notification.py  # Cross-cutting notification service
```

## Service Class Pattern

```python
"""
Brief description of what this service handles.
"""

import logging
from datetime import date, timedelta
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.some_module import SomeModel

logger = logging.getLogger(__name__)


class SomeService:
    """Service for managing [domain] operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, id: UUID) -> Optional[SomeModel]:
        """Get a single record by ID."""
        return self.db.get(SomeModel, id)

    def list_for_org(
        self,
        organization_id: UUID,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[SomeModel]:
        """List records for an organization with optional filters."""
        stmt = select(SomeModel).where(
            SomeModel.organization_id == organization_id
        )

        if status:
            stmt = stmt.where(SomeModel.status == status)

        stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def create(self, data: SomeCreateSchema) -> SomeModel:
        """Create a new record."""
        record = SomeModel(**data.model_dump())
        self.db.add(record)
        self.db.flush()  # Get generated ID
        logger.info("Created %s: %s", SomeModel.__name__, record.id)
        return record
```

## Key Rules

1. **Receive db in __init__** - Service owns the session reference
2. **Don't commit** - Let the caller (route/task) handle commit
3. **Use flush() for IDs** - When you need generated IDs before commit
4. **Log important operations** - Creates, updates, deletes
5. **Type hint everything** - All parameters and return types
6. **Multi-tenant filtering** - Always filter by organization_id

## Querying Pattern

```python
# Use select() with scalars() for lists
stmt = select(Invoice).where(
    Invoice.organization_id == org_id,
    Invoice.status == InvoiceStatus.OPEN,
)
invoices = list(self.db.scalars(stmt).all())

# Use db.get() for single record by PK
invoice = self.db.get(Invoice, invoice_id)

# Use scalar() for single result from query
stmt = select(Invoice).where(Invoice.invoice_number == number)
invoice = self.db.scalar(stmt)

# Eager loading for relationships
from sqlalchemy.orm import joinedload

stmt = select(Invoice).options(
    joinedload(Invoice.customer),
    joinedload(Invoice.line_items),
).where(Invoice.invoice_id == id)
```

## Error Handling

```python
from app.errors import NotFoundError, ValidationError

def get_or_404(self, id: UUID) -> SomeModel:
    """Get record or raise NotFoundError."""
    record = self.db.get(SomeModel, id)
    if not record:
        raise NotFoundError(f"Record {id} not found")
    return record

def validate_status_transition(
    self, current: Status, new: Status
) -> None:
    """Validate status transition or raise ValidationError."""
    allowed = VALID_TRANSITIONS.get(current, [])
    if new not in allowed:
        raise ValidationError(
            f"Cannot transition from {current} to {new}"
        )
```

## Reminder Service Pattern

For bookkeeping reminders, create a dedicated service:

```python
# app/services/finance/reminder_service.py
class FinanceReminderService:
    """Service for identifying items needing reminders."""

    def __init__(self, db: Session):
        self.db = db

    def get_periods_closing_soon(
        self, days_before: int = 7
    ) -> List[FiscalPeriod]:
        """Get fiscal periods closing within N days."""
        cutoff = date.today() + timedelta(days=days_before)
        stmt = select(FiscalPeriod).where(
            FiscalPeriod.status == PeriodStatus.OPEN,
            FiscalPeriod.end_date <= cutoff,
        )
        return list(self.db.scalars(stmt).all())

    def get_overdue_reconciliations(self) -> List[BankAccount]:
        """Get bank accounts with overdue reconciliation."""
        # Accounts not reconciled in 30+ days
        cutoff = date.today() - timedelta(days=30)
        stmt = select(BankAccount).where(
            BankAccount.is_active == True,
            or_(
                BankAccount.last_reconciled_date.is_(None),
                BankAccount.last_reconciled_date < cutoff,
            ),
        )
        return list(self.db.scalars(stmt).all())
```
