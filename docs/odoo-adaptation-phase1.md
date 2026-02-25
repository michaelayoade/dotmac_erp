# Phase 1: Foundations — Fiscal Positions, Banking Settings UI, Aggregate Auto-Refresh

**Timeline**: ~18 days | **Priority**: Quick wins + foundational patterns for later phases

---

## Overview

Phase 1 establishes the foundational patterns that later phases depend on:

1. **Fiscal Position Tax & Account Mapping** (8 days) — Declarative tax/account remapping by customer/supplier type
2. **Banking Settings UI** (4 days) — User-facing settings page for auto-match configuration
3. **Aggregate Auto-Refresh** (6 days) — Automatic invalidation and refresh of denormalized AccountBalance rows

---

## 1. Fiscal Position Tax & Account Mapping

### Problem

Nigerian tax compliance requires different tax treatments based on customer/supplier classification:
- Government customers → VAT exempt
- Inter-state sales → different WHT rate
- Export customers → zero-rated VAT
- IPSAS entities → completely different GL account mappings

Today this is handled **manually per transaction**. Users must remember to select the right tax codes and accounts. Errors are common and caught only during audits.

### Odoo Pattern Being Adapted

Odoo's `account.fiscal.position` with `FiscalPositionTax` and `FiscalPositionAccount` child tables. Applied automatically when a partner is selected on a transaction.

### Data Model

#### `FiscalPosition` (new table: `fiscal_position`)

```python
# app/models/finance/tax/fiscal_position.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FiscalPosition(Base):
    __tablename__ = "fiscal_position"

    fiscal_position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Auto-apply conditions
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    country_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    customer_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # e.g., "GOVERNMENT", "EXPORT", "INTERSTATE", "EXEMPT_ORG"
    supplier_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    priority: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    tax_mappings: Mapped[list[FiscalPositionTaxMap]] = relationship(
        back_populates="fiscal_position", cascade="all, delete-orphan"
    )
    account_mappings: Mapped[list[FiscalPositionAccountMap]] = relationship(
        back_populates="fiscal_position", cascade="all, delete-orphan"
    )
```

#### `FiscalPositionTaxMap` (new table: `fiscal_position_tax_map`)

```python
class FiscalPositionTaxMap(Base):
    __tablename__ = "fiscal_position_tax_map"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    fiscal_position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fiscal_position.fiscal_position_id"),
        nullable=False, index=True
    )
    tax_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_code.tax_code_id"), nullable=False
    )
    tax_dest_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_code.tax_code_id"), nullable=True
    )
    # NULL tax_dest_id means "remove this tax" (exempt)

    fiscal_position: Mapped[FiscalPosition] = relationship(back_populates="tax_mappings")
```

#### `FiscalPositionAccountMap` (new table: `fiscal_position_account_map`)

```python
class FiscalPositionAccountMap(Base):
    __tablename__ = "fiscal_position_account_map"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    fiscal_position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fiscal_position.fiscal_position_id"),
        nullable=False, index=True
    )
    account_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.account_id"), nullable=False
    )
    account_dest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.account_id"), nullable=False
    )

    fiscal_position: Mapped[FiscalPosition] = relationship(back_populates="account_mappings")
```

### Service Layer

#### `FiscalPositionService` (new: `app/services/finance/tax/fiscal_position_service.py`)

```python
class FiscalPositionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_for_partner(
        self, organization_id: UUID, partner_type: str, partner_classification: str | None,
        country_code: str | None = None, state_code: str | None = None,
    ) -> FiscalPosition | None:
        """Auto-detect fiscal position for a partner.

        Priority: exact match on all conditions > partial match > None.
        """
        stmt = (
            select(FiscalPosition)
            .where(
                FiscalPosition.organization_id == organization_id,
                FiscalPosition.auto_apply == True,
                FiscalPosition.is_active == True,
            )
            .order_by(FiscalPosition.priority)
        )
        positions = list(self.db.scalars(stmt).all())

        for pos in positions:
            if self._matches(pos, partner_type, partner_classification, country_code, state_code):
                return pos
        return None

    def map_taxes(
        self, fiscal_position: FiscalPosition, tax_code_ids: list[UUID],
    ) -> list[UUID]:
        """Remap tax codes through fiscal position.

        Returns: list of remapped tax_code_ids (may be shorter if some are exempted).
        """
        result: list[UUID] = []
        mapping = {m.tax_source_id: m.tax_dest_id for m in fiscal_position.tax_mappings}

        for tax_id in tax_code_ids:
            if tax_id in mapping:
                dest = mapping[tax_id]
                if dest is not None:  # None = exempt (remove tax)
                    result.append(dest)
            else:
                result.append(tax_id)  # No mapping = pass through
        return result

    def map_account(
        self, fiscal_position: FiscalPosition, account_id: UUID,
    ) -> UUID:
        """Remap a GL account through fiscal position."""
        for m in fiscal_position.account_mappings:
            if m.account_source_id == account_id:
                return m.account_dest_id
        return account_id  # No mapping = pass through
```

### Integration Points

#### AR Invoice Creation

In `app/services/finance/ar/invoice.py` — when creating an invoice:

```python
# After customer is selected, before tax calculation:
from app.services.finance.tax.fiscal_position_service import FiscalPositionService

fp_service = FiscalPositionService(self.db)
fiscal_position = fp_service.get_for_partner(
    organization_id=org_id,
    partner_type="customer",
    partner_classification=customer.customer_type,  # e.g., "GOVERNMENT"
    country_code=customer.country_code,
    state_code=customer.state_code,
)

if fiscal_position:
    # Remap line-level taxes
    for line in invoice_lines:
        line.tax_code_ids = fp_service.map_taxes(fiscal_position, line.tax_code_ids)
    # Remap revenue account
    for line in invoice_lines:
        line.account_id = fp_service.map_account(fiscal_position, line.account_id)
```

#### AP Supplier Invoice

Same pattern in `app/services/finance/ap/supplier_invoice.py` with `partner_type="supplier"`.

#### Posting Adapters

Account mappings flow through to posting adapters naturally — the invoice already has the correct GL accounts by the time posting happens.

### Migration

```python
# alembic/versions/XXXX_add_fiscal_position.py
def upgrade():
    if not inspector.has_table("fiscal_position"):
        op.create_table(
            "fiscal_position",
            sa.Column("fiscal_position_id", UUID(as_uuid=True), primary_key=True),
            sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("auto_apply", sa.Boolean, default=False),
            sa.Column("country_code", sa.String(3), nullable=True),
            sa.Column("state_code", sa.String(10), nullable=True),
            sa.Column("customer_type", sa.String(50), nullable=True),
            sa.Column("supplier_type", sa.String(50), nullable=True),
            sa.Column("priority", sa.Integer, default=10),
            sa.Column("is_active", sa.Boolean, default=True),
            sa.Column("created_at", sa.DateTime, default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime, default=sa.func.now()),
        )
    # fiscal_position_tax_map and fiscal_position_account_map similarly
```

### Seed Data (Nigerian Defaults)

```python
# Suggested pre-installed fiscal positions per org:
NIGERIAN_DEFAULTS = [
    {
        "name": "Government Entity (VAT Exempt)",
        "customer_type": "GOVERNMENT",
        "auto_apply": True,
        "priority": 1,
        "tax_mappings": [
            {"source": "VAT-7.5", "dest": None},  # Remove VAT
        ],
    },
    {
        "name": "Export Customer (Zero-Rated)",
        "customer_type": "EXPORT",
        "auto_apply": True,
        "priority": 2,
        "tax_mappings": [
            {"source": "VAT-7.5", "dest": "VAT-0"},  # Zero-rate
        ],
    },
    {
        "name": "WHT-Eligible Supplier",
        "supplier_type": "WHT_APPLICABLE",
        "auto_apply": True,
        "priority": 5,
        "tax_mappings": [
            # Add WHT deduction
        ],
    },
]
```

### UI

- **List page**: `/finance/tax/fiscal-positions` — table of fiscal positions with status badges
- **Form page**: `/finance/tax/fiscal-positions/new` and `/finance/tax/fiscal-positions/{id}/edit`
  - Name, auto-apply toggle, conditions (customer_type dropdown, country, state)
  - Tax mappings table: Source Tax → Destination Tax (or "Exempt")
  - Account mappings table: Source Account → Destination Account
- **Detail page**: Shows fiscal position with all mappings and linked partners

### Testing

```python
# tests/ifrs/tax/test_fiscal_position.py

def test_government_customer_vat_exempt(db, org_id):
    """Government customers should have VAT removed."""
    fp = create_fiscal_position(db, org_id, customer_type="GOVERNMENT")
    add_tax_mapping(db, fp, source=vat_7_5, dest=None)  # Exempt

    service = FiscalPositionService(db)
    result = service.map_taxes(fp, [vat_7_5.tax_code_id])
    assert result == []  # VAT removed

def test_export_customer_zero_rated(db, org_id):
    """Export customers should get zero-rated VAT."""
    fp = create_fiscal_position(db, org_id, customer_type="EXPORT")
    add_tax_mapping(db, fp, source=vat_7_5, dest=vat_0)

    service = FiscalPositionService(db)
    result = service.map_taxes(fp, [vat_7_5.tax_code_id])
    assert result == [vat_0.tax_code_id]

def test_auto_detect_by_customer_type(db, org_id):
    """Fiscal position auto-selected based on customer classification."""
    create_fiscal_position(db, org_id, customer_type="GOVERNMENT", auto_apply=True)

    service = FiscalPositionService(db)
    fp = service.get_for_partner(org_id, "customer", "GOVERNMENT")
    assert fp is not None

def test_no_mapping_passes_through(db, org_id):
    """Unmapped taxes pass through unchanged."""
    fp = create_fiscal_position(db, org_id, customer_type="GOVERNMENT")
    # No mapping for stamp_duty

    service = FiscalPositionService(db)
    result = service.map_taxes(fp, [stamp_duty.tax_code_id])
    assert result == [stamp_duty.tax_code_id]

def test_multi_tenant_isolation(db, org_a, org_b):
    """Fiscal positions are org-scoped."""
    create_fiscal_position(db, org_a, name="Org A Position")

    service = FiscalPositionService(db)
    assert service.get_for_partner(org_b, "customer", "GOVERNMENT") is None
```

### Deliverables

| Item | File | Type |
|------|------|------|
| Model: FiscalPosition + maps | `app/models/finance/tax/fiscal_position.py` | New |
| Service: FiscalPositionService | `app/services/finance/tax/fiscal_position_service.py` | New |
| Web service: FP form context | `app/services/finance/tax/fiscal_position_web.py` | New |
| Web routes | `app/web/finance/tax.py` | Edit |
| Templates: list, detail, form | `templates/finance/tax/fiscal_positions/` | New (3 files) |
| Migration | `alembic/versions/XXXX_add_fiscal_position.py` | New |
| AR integration | `app/services/finance/ar/invoice.py` | Edit |
| AP integration | `app/services/finance/ap/supplier_invoice.py` | Edit |
| Tests | `tests/ifrs/tax/test_fiscal_position.py` | New |

---

## 2. Banking Settings UI

### Problem

The 11 banking auto-match settings exist in `settings_spec.py` with full specs (labels, descriptions, min/max, defaults), and the backend routes exist in `app/web/finance/banking.py`. But the template `templates/finance/banking/auto_match_settings.html` needs to be completed and tested.

### Current State

- **Backend complete**: `get_auto_match_settings_context()` and `update_auto_match_settings()` methods in `settings_web.py` (lines 833–896)
- **Route complete**: `GET /finance/banking/settings` and `POST /finance/banking/settings` in `app/web/finance/banking.py`
- **Template exists**: `templates/finance/banking/auto_match_settings.html` is in the untracked files
- **Specs complete**: All 11 settings have labels, descriptions, min/max, and defaults

### Implementation

#### Template: `templates/finance/banking/auto_match_settings.html`

Structure:
```
{% extends "finance/base_finance.html" %}
{% from "components/macros.html" import topbar, status_badge %}

Page layout:
  [Topbar: "Auto-Match Settings" with breadcrumb to Banking]

  [Success banner if ?saved=1]

  <form method="POST">
    {{ request.state.csrf_form | safe }}

    [Card: Matching Passes]
      For each pass_key in pass_keys:
        Toggle switch (checkbox) with label and description
        Shows pass number (1-7) and what it matches

    [Card: Parameters]
      Amount Tolerance: number input (min=0, max=100, step=1) with "kobo" suffix
      Date Buffer: number input (min=0, max=30) with "days" suffix
      Settlement Window: number input (min=0, max=30) with "days" suffix
      Finance Cost Account: text input with account code lookup

    [Form Actions: Cancel + Save]
  </form>
```

#### Validation Enhancements

Add client-side validation with Alpine.js:
- Toggle switches for boolean settings
- Range sliders with numeric display for integer settings
- Account code validation (check against chart of accounts)

#### Impact Preview (stretch goal)

After saving, show a summary: "With these settings, X of Y unmatched statement lines would be matched." This requires running the auto-reconciliation in dry-run mode against current unmatched lines.

### Deliverables

| Item | File | Type |
|------|------|------|
| Template | `templates/finance/banking/auto_match_settings.html` | New/Edit |
| Sidebar link | `templates/finance/base_finance.html` | Edit (add Banking > Settings link) |
| Tests | `tests/ifrs/banking/test_settings_ui.py` | New |

---

## 3. Aggregate Auto-Refresh

### Problem

`AccountBalance` rows are denormalized from `PostedLedgerLine` for query performance (dashboard stat cards, trial balance, financial statements). Currently, updates are triggered manually. If a journal posts and the balance refresh fails or isn't called, dashboards show stale data.

### Current State

- **AccountBalance model**: Stores opening/period/closing debit/credit per (org, account, period, balance_type, dimensions)
- **Updates**: Called explicitly after ledger posting (not guaranteed)
- **No staleness tracking**: No way to know if a balance is current
- **No invalidation**: No mechanism to mark balances as needing refresh

### Data Model Changes

#### Add staleness tracking to `AccountBalance`

```python
# Add to AccountBalance model:
is_stale: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
stale_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
refresh_count: Mapped[int] = mapped_column(Integer, default=0)
```

#### New: `BalanceRefreshQueue` table (lightweight queue)

```python
class BalanceRefreshQueue(Base):
    __tablename__ = "balance_refresh_queue"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invalidated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        # Deduplicate: only one pending refresh per (org, account, period)
        UniqueConstraint("organization_id", "account_id", "fiscal_period_id",
                         name="uq_balance_refresh_pending"),
    )
```

### Service Layer

#### `BalanceInvalidationService` (new)

```python
# app/services/finance/gl/balance_invalidation.py

class BalanceInvalidationService:
    """Marks account balances as stale when ledger lines change."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def invalidate(
        self, organization_id: UUID, account_id: UUID, fiscal_period_id: UUID,
    ) -> None:
        """Mark balance as stale and queue for refresh.

        Called after LedgerPostingService.post() inserts PostedLedgerLine rows.
        Uses INSERT ... ON CONFLICT DO NOTHING for idempotency.
        """
        # 1. Mark existing AccountBalance as stale
        stmt = (
            update(AccountBalance)
            .where(
                AccountBalance.organization_id == organization_id,
                AccountBalance.account_id == account_id,
                AccountBalance.fiscal_period_id == fiscal_period_id,
                AccountBalance.is_stale == False,
            )
            .values(is_stale=True, stale_since=datetime.utcnow())
        )
        self.db.execute(stmt)

        # 2. Queue refresh (idempotent — ON CONFLICT DO NOTHING)
        queue_entry = BalanceRefreshQueue(
            organization_id=organization_id,
            account_id=account_id,
            fiscal_period_id=fiscal_period_id,
        )
        self.db.add(queue_entry)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()  # Already queued, skip

    def invalidate_batch(
        self, entries: list[tuple[UUID, UUID, UUID]],
    ) -> int:
        """Bulk invalidate: list of (org_id, account_id, period_id) tuples."""
        count = 0
        for org_id, account_id, period_id in entries:
            self.invalidate(org_id, account_id, period_id)
            count += 1
        return count
```

#### `BalanceRefreshService` (new)

```python
# app/services/finance/gl/balance_refresh.py

class BalanceRefreshService:
    """Processes the balance refresh queue."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def process_queue(self, batch_size: int = 100) -> dict[str, int]:
        """Process pending balance refreshes.

        Returns: {"refreshed": N, "errors": M}
        """
        stmt = (
            select(BalanceRefreshQueue)
            .where(BalanceRefreshQueue.processed_at.is_(None))
            .order_by(BalanceRefreshQueue.invalidated_at)
            .limit(batch_size)
        )
        pending = list(self.db.scalars(stmt).all())

        results = {"refreshed": 0, "errors": 0}

        for entry in pending:
            try:
                self._refresh_balance(
                    entry.organization_id, entry.account_id, entry.fiscal_period_id
                )
                entry.processed_at = datetime.utcnow()
                results["refreshed"] += 1
            except Exception as e:
                logger.exception(
                    "Failed to refresh balance: org=%s account=%s period=%s",
                    entry.organization_id, entry.account_id, entry.fiscal_period_id,
                )
                results["errors"] += 1

        self.db.flush()
        return results

    def _refresh_balance(
        self, organization_id: UUID, account_id: UUID, fiscal_period_id: UUID,
    ) -> None:
        """Recalculate AccountBalance from PostedLedgerLine rows."""
        # Sum debits and credits from ledger lines
        stmt = (
            select(
                func.coalesce(func.sum(PostedLedgerLine.debit_amount), 0),
                func.coalesce(func.sum(PostedLedgerLine.credit_amount), 0),
                func.count(PostedLedgerLine.id),
            )
            .where(
                PostedLedgerLine.organization_id == organization_id,
                PostedLedgerLine.account_id == account_id,
                PostedLedgerLine.fiscal_period_id == fiscal_period_id,
            )
        )
        period_debit, period_credit, txn_count = self.db.execute(stmt).one()

        # Upsert AccountBalance
        # (use existing balance service pattern for opening balance calculation)
        ...

        # Clear stale flag
        update_stmt = (
            update(AccountBalance)
            .where(
                AccountBalance.organization_id == organization_id,
                AccountBalance.account_id == account_id,
                AccountBalance.fiscal_period_id == fiscal_period_id,
            )
            .values(
                period_debit=period_debit,
                period_credit=period_credit,
                is_stale=False,
                stale_since=None,
                last_updated_at=datetime.utcnow(),
                transaction_count=txn_count,
                refresh_count=AccountBalance.refresh_count + 1,
            )
        )
        self.db.execute(update_stmt)
```

### Integration: Hook into LedgerPostingService

In `app/services/finance/gl/ledger_posting.py`, after inserting `PostedLedgerLine` rows:

```python
# After posting ledger lines:
invalidation = BalanceInvalidationService(self.db)
for line in posted_lines:
    invalidation.invalidate(
        organization_id=journal_entry.organization_id,
        account_id=line.account_id,
        fiscal_period_id=journal_entry.fiscal_period_id,
    )
```

### Celery Task

```python
# app/tasks/finance.py

@shared_task
def refresh_stale_balances() -> dict:
    """Process pending balance refreshes. Runs every 30 seconds."""
    with SessionLocal() as db:
        from app.services.finance.gl.balance_refresh import BalanceRefreshService
        service = BalanceRefreshService(db)
        results = service.process_queue(batch_size=200)
        db.commit()

    if results["refreshed"] > 0:
        logger.info("Refreshed %d balances, %d errors", results["refreshed"], results["errors"])
    return results
```

Celery Beat schedule:
```python
'refresh-stale-balances': {
    'task': 'app.tasks.finance.refresh_stale_balances',
    'schedule': 30.0,  # Every 30 seconds
},
```

### Dashboard Staleness Indicator

On dashboard stat cards, check if any displayed balances are stale:

```python
# In dashboard web service:
stale_count = db.scalar(
    select(func.count(AccountBalance.balance_id))
    .where(
        AccountBalance.organization_id == org_id,
        AccountBalance.is_stale == True,
    )
)
context["balances_stale"] = stale_count > 0
```

In template:
```html
{% if balances_stale %}
<div class="text-xs text-amber-600 dark:text-amber-400 flex items-center gap-1">
    <span class="animate-pulse">●</span> Refreshing balances...
</div>
{% endif %}
```

### Testing

```python
# tests/ifrs/gl/test_balance_refresh.py

def test_posting_invalidates_balance(db, org_id):
    """Posting a journal entry marks affected balances as stale."""
    # Create and post a journal entry
    post_journal(db, org_id, account_id=cash_account.account_id)

    balance = get_account_balance(db, org_id, cash_account.account_id)
    assert balance.is_stale is True

def test_refresh_clears_stale_flag(db, org_id):
    """Refresh service recalculates and clears stale flag."""
    invalidate_balance(db, org_id, account_id, period_id)

    service = BalanceRefreshService(db)
    results = service.process_queue()
    assert results["refreshed"] == 1

    balance = get_account_balance(db, org_id, account_id)
    assert balance.is_stale is False

def test_idempotent_invalidation(db, org_id):
    """Multiple invalidations for same balance create only one queue entry."""
    invalidation = BalanceInvalidationService(db)
    invalidation.invalidate(org_id, account_id, period_id)
    invalidation.invalidate(org_id, account_id, period_id)

    queue_count = count_queue_entries(db, org_id, account_id, period_id)
    assert queue_count == 1
```

### Deliverables

| Item | File | Type |
|------|------|------|
| Model changes | `app/models/finance/gl/account_balance.py` | Edit (add is_stale, stale_since, refresh_count) |
| Model: BalanceRefreshQueue | `app/models/finance/gl/balance_refresh_queue.py` | New |
| Service: BalanceInvalidationService | `app/services/finance/gl/balance_invalidation.py` | New |
| Service: BalanceRefreshService | `app/services/finance/gl/balance_refresh.py` | New |
| Integration: LedgerPostingService | `app/services/finance/gl/ledger_posting.py` | Edit |
| Celery task | `app/tasks/finance.py` | Edit (add refresh_stale_balances) |
| Migration | `alembic/versions/XXXX_add_balance_staleness.py` | New |
| Tests | `tests/ifrs/gl/test_balance_refresh.py` | New |

---

## Phase 1 Summary

| Feature | New Files | Edited Files | Days |
|---------|-----------|-------------|------|
| Fiscal Position | 6 | 4 | 8 |
| Banking Settings UI | 1 | 1 | 4 |
| Aggregate Auto-Refresh | 4 | 3 | 6 |
| **Total** | **11** | **8** | **18** |

### Dependencies Between Features

```
Fiscal Position (standalone, no deps)
Banking Settings UI (standalone, no deps)
Aggregate Auto-Refresh (standalone, no deps)
```

All three can be developed in **parallel** by different developers.

### Verification Checklist

- [ ] `make lint` passes on all new/changed files
- [ ] `mypy` passes on all new/changed files
- [ ] Tests pass: fiscal position (5+ tests), banking settings (3+ tests), balance refresh (5+ tests)
- [ ] Migration is idempotent (safe to run multiple times)
- [ ] Multi-tenancy: all queries filter by organization_id
- [ ] No business logic in routes (service layer only)
- [ ] Templates include CSRF, dark mode, accessibility
