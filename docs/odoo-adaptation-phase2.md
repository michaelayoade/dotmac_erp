# Phase 2: Intelligence Layer — Configurable Reconciliation Rules & Field-Level Change Tracking

**Timeline**: ~22 days | **Depends on**: Phase 1 (Banking Settings UI pattern, BalanceInvalidation pattern)

---

## Overview

Phase 2 adds intelligence and auditability:

1. **Configurable Reconciliation Rules Engine** (12 days) — User-definable matching rules that replace hard-coded passes
2. **Field-Level Change Tracking** (10 days) — Automatic old→new value logging on key financial fields

---

## 1. Configurable Reconciliation Rules Engine

### Problem

The 7-pass auto-reconciliation in `auto_reconciliation.py` is highly effective for DotMac's current payment ecosystem (Paystack, Splynx, inter-bank). But it's **entirely hard-coded**:

- Adding support for a new payment processor requires a code change
- Custom matching patterns (e.g., "salary payments always come from GTBank with label SALARY-*") require developer intervention
- The order of passes is fixed — customers cannot prioritize one matching strategy over another
- No visibility into WHY a match was made (confidence scoring, explanation)

### Odoo Pattern Being Adapted

Odoo's `account.reconcile.model` with rule types (invoice_matching, writeoff_suggestion, writeoff_button), match conditions (label regex, amount range, partner), and configurable counterpart entries.

### Design Principles

1. **Existing passes become seed rules** — the 7 passes are migrated to `ReconciliationRule` records, preserving current behavior
2. **Rules are additive** — new rules extend matching, they don't replace the core engine
3. **Rules are ordered by priority** — lower number = tried first
4. **Rules produce explanations** — every match includes a human-readable reason
5. **Dry-run mode** — preview what rules would match without committing

### Data Model

#### `ReconciliationRule` (new table: `reconciliation_rule`)

```python
# app/models/finance/banking/reconciliation_rule.py
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, Numeric,
    String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RuleType(str, enum.Enum):
    INVOICE_MATCH = "INVOICE_MATCH"       # Match statement line to open invoice/bill
    PAYMENT_MATCH = "PAYMENT_MATCH"       # Match to existing payment record
    REFERENCE_MATCH = "REFERENCE_MATCH"   # Match by reference/description pattern
    FEE_WRITEOFF = "FEE_WRITEOFF"         # Auto-create GL entry for fees
    SETTLEMENT_MATCH = "SETTLEMENT_MATCH" # Cross-account settlement matching
    CUSTOM = "CUSTOM"                     # User-defined condition → action


class MatchField(str, enum.Enum):
    AMOUNT = "AMOUNT"
    REFERENCE = "REFERENCE"
    DESCRIPTION = "DESCRIPTION"
    DATE = "DATE"
    PARTNER = "PARTNER"
    PAYMENT_METHOD = "PAYMENT_METHOD"


class MatchOperator(str, enum.Enum):
    EQUALS = "EQUALS"
    CONTAINS = "CONTAINS"
    STARTS_WITH = "STARTS_WITH"
    REGEX = "REGEX"
    BETWEEN = "BETWEEN"
    GREATER_THAN = "GREATER_THAN"
    LESS_THAN = "LESS_THAN"


class ReconciliationRule(Base):
    __tablename__ = "reconciliation_rule"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False, index=True
    )

    # Identity
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_type: Mapped[str] = mapped_column(
        Enum(RuleType, name="reconciliation_rule_type", create_type=False),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    # System rules = migrated from 7-pass (can be disabled, not deleted)

    # Match conditions (evaluated in order, ALL must match)
    conditions: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    # Format: [
    #   {"field": "DESCRIPTION", "operator": "REGEX", "value": "PAYSTACK FEE.*"},
    #   {"field": "AMOUNT", "operator": "LESS_THAN", "value": "500"},
    # ]

    # Transaction direction filter
    match_direction: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # "DEBIT", "CREDIT", or NULL (both)

    # Amount tolerance
    amount_tolerance: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )  # Override global tolerance for this rule

    # Date window
    date_window_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Override global date buffer for this rule

    # Action: what to do on match
    action_type: Mapped[str] = mapped_column(
        String(30), default="MATCH"
    )  # MATCH, CREATE_JOURNAL, SUGGEST

    # For FEE_WRITEOFF rules: which account to post to
    writeoff_account_code: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    writeoff_journal_label: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )  # Template: "Bank fee - {date} - {description}"

    # Metadata
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    # Incremented on each successful match (for analytics)
    last_matched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
```

#### `ReconciliationMatchLog` (new table: `reconciliation_match_log`)

```python
class ReconciliationMatchLog(Base):
    """Audit trail: which rule matched which statement line, and why."""
    __tablename__ = "reconciliation_match_log"

    log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reconciliation_rule.rule_id"), nullable=False
    )
    statement_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    matched_entity_type: Mapped[str] = mapped_column(String(50))
    # e.g., "customer_payment", "supplier_payment", "journal_entry"
    matched_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    confidence_score: Mapped[int] = mapped_column(Integer, default=100)
    # 100 = exact match, 90+ = high confidence, <70 = needs review
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    # e.g., "Matched by Paystack reference TXN_abc123 (exact)"

    matched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

### Service Layer

#### `ReconciliationRuleService` (new)

```python
# app/services/finance/banking/reconciliation_rule_service.py

class ReconciliationRuleService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_active_rules(self, organization_id: UUID) -> list[ReconciliationRule]:
        """Get all active rules ordered by priority."""
        stmt = (
            select(ReconciliationRule)
            .where(
                ReconciliationRule.organization_id == organization_id,
                ReconciliationRule.is_active == True,
            )
            .order_by(ReconciliationRule.priority)
        )
        return list(self.db.scalars(stmt).all())

    def evaluate_rule(
        self, rule: ReconciliationRule, statement_line: BankStatementLine,
    ) -> RuleEvalResult | None:
        """Evaluate a single rule against a statement line.

        Returns RuleEvalResult if conditions match, None otherwise.
        """
        # Check direction filter
        if rule.match_direction:
            if rule.match_direction == "DEBIT" and statement_line.amount > 0:
                return None
            if rule.match_direction == "CREDIT" and statement_line.amount < 0:
                return None

        # Evaluate each condition
        if rule.conditions:
            for condition in rule.conditions:
                if not self._evaluate_condition(condition, statement_line):
                    return None

        # All conditions passed — find matching entity
        return self._find_match(rule, statement_line)

    def _evaluate_condition(
        self, condition: dict, line: BankStatementLine,
    ) -> bool:
        """Evaluate a single condition against a statement line."""
        field = condition["field"]
        operator = condition["operator"]
        value = condition["value"]

        actual = self._get_field_value(field, line)

        if operator == "EQUALS":
            return str(actual) == str(value)
        elif operator == "CONTAINS":
            return value.lower() in str(actual).lower()
        elif operator == "STARTS_WITH":
            return str(actual).lower().startswith(value.lower())
        elif operator == "REGEX":
            return bool(re.search(value, str(actual), re.IGNORECASE))
        elif operator == "BETWEEN":
            low, high = value  # [min, max]
            return Decimal(str(low)) <= Decimal(str(actual)) <= Decimal(str(high))
        elif operator == "GREATER_THAN":
            return Decimal(str(actual)) > Decimal(str(value))
        elif operator == "LESS_THAN":
            return Decimal(str(actual)) < Decimal(str(value))
        return False

    def _find_match(
        self, rule: ReconciliationRule, line: BankStatementLine,
    ) -> RuleEvalResult | None:
        """Based on rule_type, find the matching entity."""
        if rule.rule_type == RuleType.PAYMENT_MATCH:
            return self._find_payment_match(rule, line)
        elif rule.rule_type == RuleType.INVOICE_MATCH:
            return self._find_invoice_match(rule, line)
        elif rule.rule_type == RuleType.FEE_WRITEOFF:
            return self._create_writeoff_result(rule, line)
        elif rule.rule_type == RuleType.SETTLEMENT_MATCH:
            return self._find_settlement_match(rule, line)
        elif rule.rule_type == RuleType.REFERENCE_MATCH:
            return self._find_reference_match(rule, line)
        return None


@dataclass
class RuleEvalResult:
    rule_id: UUID
    matched_entity_type: str
    matched_entity_id: UUID | None
    confidence_score: int
    explanation: str
    action_type: str  # MATCH, CREATE_JOURNAL, SUGGEST
    writeoff_details: dict | None = None
```

#### Refactored `AutoReconciliationService`

The existing `auto_reconciliation.py` is refactored to use rules:

```python
class AutoReconciliationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rule_service = ReconciliationRuleService(db)

    def run(
        self,
        organization_id: UUID,
        reconciliation_id: UUID,
        *,
        dry_run: bool = False,
    ) -> AutoMatchResult:
        """Run all active rules against unmatched statement lines."""
        rules = self.rule_service.get_active_rules(organization_id)
        unmatched = self._get_unmatched_lines(reconciliation_id)

        result = AutoMatchResult()
        match_log: list[ReconciliationMatchLog] = []

        for line in unmatched:
            for rule in rules:
                eval_result = self.rule_service.evaluate_rule(rule, line)
                if eval_result is None:
                    continue

                if eval_result.confidence_score >= 90:
                    if not dry_run:
                        self._apply_match(line, eval_result, rule)
                    result.matched += 1

                    match_log.append(ReconciliationMatchLog(
                        organization_id=organization_id,
                        rule_id=rule.rule_id,
                        statement_line_id=line.statement_line_id,
                        matched_entity_type=eval_result.matched_entity_type,
                        matched_entity_id=eval_result.matched_entity_id,
                        confidence_score=eval_result.confidence_score,
                        explanation=eval_result.explanation,
                    ))

                    # Update rule stats
                    rule.match_count += 1
                    rule.last_matched_at = datetime.utcnow()

                    break  # Line matched, move to next

                elif eval_result.confidence_score >= 70:
                    result.contra_suggestions.append({
                        "line_id": str(line.statement_line_id),
                        "suggestion": eval_result.explanation,
                        "confidence": eval_result.confidence_score,
                        "rule_name": rule.name,
                    })

        if not dry_run:
            self.db.add_all(match_log)
            self.db.flush()

        return result
```

### Seed Data: Migrating 7 Passes to Rules

```python
SYSTEM_RULES = [
    {
        "name": "Paystack Payment Intent",
        "rule_type": "PAYMENT_MATCH",
        "priority": 10,
        "is_system": True,
        "conditions": [
            {"field": "REFERENCE", "operator": "REGEX", "value": r"PSTK_[a-zA-Z0-9]+"},
        ],
        "match_direction": None,
        "description": "Match DotMac-initiated Paystack transfers by paystack_reference",
    },
    {
        "name": "Splynx Payment by Reference",
        "rule_type": "PAYMENT_MATCH",
        "priority": 20,
        "is_system": True,
        "conditions": [
            {"field": "DESCRIPTION", "operator": "REGEX", "value": r"[0-9a-f]{12,14}"},
        ],
        "match_direction": "CREDIT",
        "description": "Extract Paystack transaction ID from Splynx payment descriptions",
    },
    {
        "name": "Date + Amount Fallback",
        "rule_type": "PAYMENT_MATCH",
        "priority": 30,
        "is_system": True,
        "conditions": [],  # No conditions — uses date/amount matching
        "match_direction": "CREDIT",
        "description": "Match unmatched Splynx payments by exact date and amount",
    },
    {
        "name": "AP Supplier Payments",
        "rule_type": "PAYMENT_MATCH",
        "priority": 40,
        "is_system": True,
        "conditions": [],
        "match_direction": "DEBIT",
        "description": "Match cleared supplier payments by reference or date+amount",
    },
    {
        "name": "AR Customer Payments",
        "rule_type": "PAYMENT_MATCH",
        "priority": 50,
        "is_system": True,
        "conditions": [],
        "match_direction": "CREDIT",
        "description": "Match non-Splynx customer receipts by reference or date+amount",
    },
    {
        "name": "Paystack Bank Fees",
        "rule_type": "FEE_WRITEOFF",
        "priority": 60,
        "is_system": True,
        "conditions": [
            {"field": "DESCRIPTION", "operator": "REGEX", "value": r"(?i)(paystack fee|transaction fee|bank charge)"},
        ],
        "match_direction": "DEBIT",
        "writeoff_account_code": "6080",
        "writeoff_journal_label": "Bank fee - {date} - {description}",
        "description": "Auto-detect Paystack fees and create Finance Cost GL journal",
    },
    {
        "name": "Inter-Bank Settlement",
        "rule_type": "SETTLEMENT_MATCH",
        "priority": 70,
        "is_system": True,
        "conditions": [
            {"field": "DESCRIPTION", "operator": "REGEX", "value": r"(?i)(settlement|transfer|NIP|NIBSS)"},
        ],
        "match_direction": None,
        "date_window_days": 10,
        "description": "Cross-bank transfer matching within settlement window",
    },
]
```

### UI

#### Rule List Page: `/finance/banking/rules`

```
[Topbar: "Reconciliation Rules" + "New Rule" button]
[Table]
  Priority | Name | Type | Direction | Status | Matches | Last Match
  10       | Paystack Payment Intent | Payment | Both | Active | 1,234 | 2 hrs ago
  20       | Splynx by Reference     | Payment | Credit | Active | 892 | 3 hrs ago
  ...
  100      | Salary Payments (custom)| Reference| Credit | Active | 45 | Yesterday
```

#### Rule Form Page: `/finance/banking/rules/new`

```
[Name] [Priority] [Type dropdown]
[Direction: Both / Debit only / Credit only]

[Conditions Builder]
  Field: [Description ▾]  Operator: [Contains ▾]  Value: [SALARY]  [+ Add]
  Field: [Amount ▾]       Operator: [Between ▾]   Value: [100000, 500000]  [+ Add]

[Tolerance Override] (optional, falls back to global)
[Date Window Override] (optional, falls back to global)

[For FEE_WRITEOFF type:]
  Account Code: [6080]
  Journal Label: [Bank fee - {date}]

[Cancel] [Save Rule]
```

#### Match Log Page: `/finance/banking/match-log`

```
[Date Range filter]
[Table]
  Date | Statement Line | Rule | Matched Entity | Confidence | Explanation
  07 Feb | ₦150,000 credit | Paystack PI | REC-00421 | 100% | Exact reference match
  07 Feb | ₦2,500 debit | Bank Fees | JE-2026-089 | 95% | Pattern: "Paystack Fee"
```

### Testing

```python
# tests/ifrs/banking/test_reconciliation_rules.py

def test_regex_condition_matches(db, org_id):
    """Rule with regex condition matches statement line."""
    rule = create_rule(db, org_id, conditions=[
        {"field": "DESCRIPTION", "operator": "REGEX", "value": r"PAYSTACK FEE.*"},
    ])
    line = create_statement_line(db, description="PAYSTACK FEE - TXN123")

    service = ReconciliationRuleService(db)
    result = service.evaluate_rule(rule, line)
    assert result is not None

def test_amount_between_condition(db, org_id):
    """Amount BETWEEN condition filters correctly."""
    rule = create_rule(db, org_id, conditions=[
        {"field": "AMOUNT", "operator": "BETWEEN", "value": [1000, 5000]},
    ])
    line_match = create_statement_line(db, amount=Decimal("3000"))
    line_no_match = create_statement_line(db, amount=Decimal("10000"))

    service = ReconciliationRuleService(db)
    assert service.evaluate_rule(rule, line_match) is not None
    assert service.evaluate_rule(rule, line_no_match) is None

def test_rule_priority_ordering(db, org_id):
    """Higher-priority rules (lower number) are tried first."""
    rule_low = create_rule(db, org_id, priority=10, name="First")
    rule_high = create_rule(db, org_id, priority=100, name="Last")

    service = ReconciliationRuleService(db)
    rules = service.get_active_rules(org_id)
    assert rules[0].name == "First"

def test_dry_run_does_not_commit(db, org_id):
    """Dry run returns matches without applying them."""
    setup_unmatched_lines(db, org_id)

    service = AutoReconciliationService(db)
    result = service.run(org_id, recon_id, dry_run=True)
    assert result.matched > 0
    # But lines are still unmatched
    assert count_unmatched(db, recon_id) == original_count

def test_match_log_created(db, org_id):
    """Successful matches create audit log entries."""
    service = AutoReconciliationService(db)
    service.run(org_id, recon_id)

    logs = get_match_logs(db, org_id)
    assert len(logs) > 0
    assert logs[0].explanation  # Has human-readable explanation

def test_system_rules_cannot_be_deleted(db, org_id):
    """System rules can be disabled but not deleted."""
    rule = get_system_rule(db, org_id, "Paystack Payment Intent")
    assert rule.is_system is True
    # Disable is OK
    rule.is_active = False
    db.flush()

def test_multi_tenant_isolation(db, org_a, org_b):
    """Rules are org-scoped."""
    create_rule(db, org_a, name="Org A Rule")
    service = ReconciliationRuleService(db)
    rules = service.get_active_rules(org_b)
    assert not any(r.name == "Org A Rule" for r in rules)
```

### Deliverables

| Item | File | Type |
|------|------|------|
| Model: ReconciliationRule + MatchLog | `app/models/finance/banking/reconciliation_rule.py` | New |
| Service: ReconciliationRuleService | `app/services/finance/banking/reconciliation_rule_service.py` | New |
| Refactor: AutoReconciliationService | `app/services/finance/banking/auto_reconciliation.py` | Major edit |
| Web service | `app/services/finance/banking/reconciliation_rule_web.py` | New |
| Web routes | `app/web/finance/banking.py` | Edit |
| Templates: rule list, form, match log | `templates/finance/banking/rules/` | New (3-4 files) |
| Migration | `alembic/versions/XXXX_add_reconciliation_rules.py` | New |
| Seed data migration | `alembic/versions/XXXX_seed_system_rules.py` | New |
| Tests | `tests/ifrs/banking/test_reconciliation_rules.py` | New |

---

## 2. Field-Level Change Tracking

### Problem

IFRS-regulated environments need to answer: "Who changed the invoice amount from ₦500,000 to ₦450,000, and when?" Today, DotMac has entity-level `AuditEvent` records (created/updated/deleted) but **no field-level diffs**. The `DomainSettingHistory` table tracks field-level changes for settings only.

### Odoo Pattern Being Adapted

Odoo's `tracking=True` field attribute, which declares that a field's changes should be automatically logged to the mail thread (chatter). Changes are captured in `mail.tracking.value` with `old_value_*` / `new_value_*` columns.

### Design: SQLAlchemy Event-Based Tracking

Instead of modifying every service method, use SQLAlchemy's `before_flush` event to automatically detect dirty fields on tracked models.

### Data Model

#### `FieldChangeLog` (new table: `field_change_log`)

```python
# app/models/audit_field_tracking.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class FieldChangeLog(Base):
    __tablename__ = "field_change_log"

    log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    # What entity changed
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # e.g., "JournalEntry", "Invoice", "CustomerPayment"
    entity_id: Mapped[str] = mapped_column(String(60), nullable=False)
    # UUID as string — flexible across models with different PK names

    # What field changed
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    field_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Human-readable label: "Total Amount", "Status", "Customer"

    # Old and new values (stored as strings for uniformity)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_display: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_display: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Display values for FK fields: old_value="uuid", old_display="Acme Corp"

    # Who changed it
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # Context
    change_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # "web_form", "api", "celery_task", "migration", "system"
    request_id: Mapped[str | None] = mapped_column(String(60), nullable=True)

    __table_args__ = (
        Index("ix_field_change_entity", "entity_type", "entity_id"),
        Index("ix_field_change_org_date", "organization_id", "changed_at"),
    )
```

### Tracking Configuration: Model Mixin

```python
# app/models/mixins/tracked.py
from __future__ import annotations


class TrackedMixin:
    """Mixin for models that want field-level change tracking.

    Usage:
        class Invoice(Base, TrackedMixin):
            __tracked_fields__ = {
                "status": {"label": "Status"},
                "amount_total": {"label": "Total Amount", "sensitive": False},
                "customer_id": {"label": "Customer", "display_field": "legal_name"},
                "due_date": {"label": "Due Date"},
            }
            __tracking_entity_type__ = "Invoice"
            __tracking_pk_field__ = "invoice_id"
    """

    __tracked_fields__: dict[str, dict] = {}
    __tracking_entity_type__: str = ""
    __tracking_pk_field__: str = "id"
```

### Event Listener: Automatic Change Detection

```python
# app/services/audit/field_tracker.py
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.orm import Session, UnitOfWork

from app.models.audit_field_tracking import FieldChangeLog
from app.models.mixins.tracked import TrackedMixin

logger = logging.getLogger(__name__)

# Context variable for current user (set by middleware)
from contextvars import ContextVar
_current_user_id: ContextVar[UUID | None] = ContextVar("current_user_id", default=None)
_current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)
_change_source: ContextVar[str | None] = ContextVar("change_source", default=None)


def set_tracking_context(
    user_id: UUID | None = None,
    request_id: str | None = None,
    source: str | None = None,
) -> None:
    """Set tracking context. Called by middleware before request processing."""
    if user_id is not None:
        _current_user_id.set(user_id)
    if request_id is not None:
        _current_request_id.set(request_id)
    if source is not None:
        _change_source.set(source)


def _extract_changes(instance: TrackedMixin) -> list[dict]:
    """Extract field-level changes from a dirty instance."""
    changes: list[dict] = []
    tracked = instance.__tracked_fields__
    mapper = instance.__class__.__mapper__

    for field_name, config in tracked.items():
        attr = mapper.attrs.get(field_name)
        if attr is None:
            continue

        history = attr.load_history()
        if not history.has_changes():
            continue

        old_val = history.deleted[0] if history.deleted else None
        new_val = history.added[0] if history.added else None

        # Skip if values are actually the same (SQLAlchemy may report false changes)
        if old_val == new_val:
            continue

        # Sensitive fields: log the change but not the values
        if config.get("sensitive"):
            old_str = "***"
            new_str = "***"
        else:
            old_str = str(old_val) if old_val is not None else None
            new_str = str(new_val) if new_val is not None else None

        changes.append({
            "field_name": field_name,
            "field_label": config.get("label", field_name),
            "old_value": old_str,
            "new_value": new_str,
            # Display values resolved later for FK fields
        })

    return changes


@event.listens_for(Session, "before_flush")
def track_field_changes(session: Session, flush_context: UnitOfWork, instances: object) -> None:
    """SQLAlchemy event: capture field changes before flush."""
    change_logs: list[FieldChangeLog] = []

    for instance in session.dirty:
        if not isinstance(instance, TrackedMixin):
            continue
        if not instance.__tracked_fields__:
            continue

        changes = _extract_changes(instance)
        if not changes:
            continue

        entity_type = instance.__tracking_entity_type__ or instance.__class__.__name__
        pk_field = instance.__tracking_pk_field__
        entity_id = str(getattr(instance, pk_field))
        org_id = getattr(instance, "organization_id", None)

        for change in changes:
            change_logs.append(FieldChangeLog(
                organization_id=org_id,
                entity_type=entity_type,
                entity_id=entity_id,
                field_name=change["field_name"],
                field_label=change["field_label"],
                old_value=change["old_value"],
                new_value=change["new_value"],
                changed_by_user_id=_current_user_id.get(None),
                changed_at=datetime.utcnow(),
                change_source=_change_source.get(None),
                request_id=_current_request_id.get(None),
            ))

    for log in change_logs:
        session.add(log)
```

### Models to Track (Initial Rollout)

Priority models for IFRS compliance:

```python
# app/models/finance/gl/journal_entry.py
class JournalEntry(Base, TrackedMixin):
    __tracked_fields__ = {
        "status": {"label": "Status"},
        "journal_date": {"label": "Journal Date"},
        "description": {"label": "Description"},
        "submitted_by_user_id": {"label": "Submitted By"},
        "approved_by_user_id": {"label": "Approved By"},
        "posted_by_user_id": {"label": "Posted By"},
    }
    __tracking_entity_type__ = "JournalEntry"
    __tracking_pk_field__ = "journal_entry_id"

# app/models/finance/ar/invoice.py
class Invoice(Base, TrackedMixin):
    __tracked_fields__ = {
        "status": {"label": "Status"},
        "amount_total": {"label": "Total Amount"},
        "customer_id": {"label": "Customer"},
        "due_date": {"label": "Due Date"},
        "payment_terms": {"label": "Payment Terms"},
    }
    __tracking_entity_type__ = "Invoice"
    __tracking_pk_field__ = "invoice_id"

# app/models/finance/banking/bank_reconciliation.py
class BankReconciliation(Base, TrackedMixin):
    __tracked_fields__ = {
        "status": {"label": "Status"},
        "reconciliation_date": {"label": "Reconciliation Date"},
        "statement_ending_balance": {"label": "Statement Balance"},
    }
    __tracking_entity_type__ = "BankReconciliation"
    __tracking_pk_field__ = "reconciliation_id"
```

### Middleware Integration

```python
# In app/web/deps.py or middleware:
from app.services.audit.field_tracker import set_tracking_context

# In base_context() or auth middleware:
set_tracking_context(
    user_id=auth.user_id,
    request_id=request.state.request_id,
    source="web_form",
)
```

### UI: Change Timeline on Detail Pages

```python
# In web services (e.g., journal_web.py):
def get_change_history(
    self, entity_type: str, entity_id: str, limit: int = 50,
) -> list[FieldChangeLog]:
    stmt = (
        select(FieldChangeLog)
        .where(
            FieldChangeLog.entity_type == entity_type,
            FieldChangeLog.entity_id == entity_id,
        )
        .order_by(FieldChangeLog.changed_at.desc())
        .limit(limit)
    )
    return list(self.db.scalars(stmt).all())
```

Template partial (`templates/components/_change_history.html`):
```html
{% for change in change_history %}
<div class="flex items-start gap-3 py-2 border-b border-slate-100 dark:border-slate-700">
    <div class="text-xs text-slate-500 dark:text-slate-400 w-32 shrink-0">
        {{ change.changed_at | format_datetime }}
    </div>
    <div class="text-sm">
        <span class="font-medium">{{ change.field_label }}</span>
        changed
        {% if change.old_value %}
            from <span class="text-rose-600 dark:text-rose-400 line-through">{{ change.old_display or change.old_value }}</span>
        {% endif %}
        to <span class="text-emerald-600 dark:text-emerald-400 font-medium">{{ change.new_display or change.new_value }}</span>
        {% if change.changed_by_user_id %}
            <span class="text-slate-400">by {{ change.changed_by_name }}</span>
        {% endif %}
    </div>
</div>
{% endfor %}
```

### Testing

```python
# tests/services/test_field_tracking.py

def test_status_change_tracked(db, org_id):
    """Changing journal status creates a change log entry."""
    je = create_journal_entry(db, org_id, status="DRAFT")
    je.status = "SUBMITTED"
    db.flush()

    logs = get_change_logs(db, "JournalEntry", str(je.journal_entry_id))
    assert len(logs) == 1
    assert logs[0].field_name == "status"
    assert logs[0].old_value == "DRAFT"
    assert logs[0].new_value == "SUBMITTED"

def test_no_log_when_value_unchanged(db, org_id):
    """No change log if field is set to same value."""
    je = create_journal_entry(db, org_id, status="DRAFT")
    je.status = "DRAFT"  # Same value
    db.flush()

    logs = get_change_logs(db, "JournalEntry", str(je.journal_entry_id))
    assert len(logs) == 0

def test_sensitive_field_masked(db, org_id):
    """Sensitive fields log the change but not values."""
    # Assuming a model with sensitive=True field
    ...

def test_multiple_field_changes_in_one_flush(db, org_id):
    """Multiple fields changed at once create multiple log entries."""
    invoice = create_invoice(db, org_id)
    invoice.status = "POSTED"
    invoice.amount_total = Decimal("999.99")
    db.flush()

    logs = get_change_logs(db, "Invoice", str(invoice.invoice_id))
    assert len(logs) == 2
    field_names = {l.field_name for l in logs}
    assert field_names == {"status", "amount_total"}
```

### Deliverables

| Item | File | Type |
|------|------|------|
| Model: FieldChangeLog | `app/models/audit_field_tracking.py` | New |
| Mixin: TrackedMixin | `app/models/mixins/tracked.py` | New |
| Event listener: field_tracker | `app/services/audit/field_tracker.py` | New |
| Migration | `alembic/versions/XXXX_add_field_change_log.py` | New |
| Template partial | `templates/components/_change_history.html` | New |
| Model edits: add tracked_fields | Multiple model files | Edit (5-6 files) |
| Middleware integration | `app/web/deps.py` | Edit |
| Tests | `tests/services/test_field_tracking.py` | New |

---

## Phase 2 Summary

| Feature | New Files | Edited Files | Days |
|---------|-----------|-------------|------|
| Reconciliation Rules Engine | 7 | 3 | 12 |
| Field-Level Change Tracking | 5 | 7 | 10 |
| **Total** | **12** | **10** | **22** |

### Dependencies

```
Phase 1 (Banking Settings UI) → Reconciliation Rules (reuses settings patterns)
Phase 1 (Aggregate Invalidation pattern) → Field Tracker (same event-driven pattern)
Reconciliation Rules ←→ Field Tracking (independent, can parallelize)
```

### Verification Checklist

- [ ] `make lint` passes
- [ ] `mypy` passes
- [ ] Reconciliation rules: 8+ tests covering conditions, ordering, dry-run, seed data, multi-tenancy
- [ ] Field tracking: 6+ tests covering changes, no-ops, sensitive masking, multi-field
- [ ] Existing auto-reconciliation tests still pass (backward compatibility)
- [ ] Performance: field tracking adds < 5ms per flush (benchmark)
- [ ] Migration seeds system rules for all existing organizations
