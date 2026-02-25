"""
Reconciliation Match Rule Service.

CRUD operations, condition evaluation, match logging, and system
rule seeding for configurable bank statement matching rules.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC as _UTC
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.banking.bank_statement import (
    BankStatementLine,
    StatementLineType,
)
from app.models.finance.banking.reconciliation_match_rule import (
    ReconciliationMatchLog,
    ReconciliationMatchRule,
)

logger = logging.getLogger(__name__)

# System rules mirror the 7 hard-coded passes in AutoReconciliationService
SYSTEM_RULES: list[dict[str, Any]] = [
    {
        "name": "Paystack Payment Intent",
        "source_doc_type": "PAYMENT_INTENT",
        "priority": 10,
        "match_credit": True,
        "match_debit": True,
        "conditions": [
            {"field": "REFERENCE", "operator": "REGEX", "value": r"PSTK_[a-zA-Z0-9]+"}
        ],
        "description": "Match Paystack-initiated transfers by paystack_reference",
    },
    {
        "name": "Splynx Payment by Reference",
        "source_doc_type": "CUSTOMER_PAYMENT",
        "priority": 20,
        "match_credit": True,
        "match_debit": False,
        "conditions": [
            {"field": "DESCRIPTION", "operator": "REGEX", "value": r"[0-9a-f]{12,14}"}
        ],
        "description": "Match Splynx payments by Paystack transaction ID in description",
    },
    {
        "name": "Date + Amount Fallback",
        "source_doc_type": "CUSTOMER_PAYMENT",
        "priority": 30,
        "match_credit": True,
        "match_debit": False,
        "conditions": [],
        "description": "Match remaining Splynx payments by exact date and amount",
    },
    {
        "name": "AP Supplier Payments",
        "source_doc_type": "SUPPLIER_PAYMENT",
        "priority": 40,
        "match_credit": False,
        "match_debit": True,
        "conditions": [],
        "description": "Match supplier payments by reference or date+amount",
    },
    {
        "name": "AR Customer Payments",
        "source_doc_type": "CUSTOMER_PAYMENT",
        "priority": 50,
        "match_credit": True,
        "match_debit": False,
        "conditions": [],
        "description": "Match non-Splynx customer receipts by reference or date+amount",
    },
    {
        "name": "Bank Fees",
        "source_doc_type": "BANK_FEE",
        "priority": 60,
        "match_credit": False,
        "match_debit": True,
        "conditions": [
            {
                "field": "DESCRIPTION",
                "operator": "REGEX",
                "value": r"(?i)(paystack fee|transaction fee|bank charge)",
            }
        ],
        "action_type": "CREATE_JOURNAL",
        "journal_label_template": "Bank fee - {date} - {description}",
        "description": "Auto-detect bank fees and create Finance Cost GL journal",
    },
    {
        "name": "Inter-Bank Settlement",
        "source_doc_type": "INTER_BANK",
        "priority": 70,
        "match_credit": True,
        "match_debit": True,
        "conditions": [
            {
                "field": "DESCRIPTION",
                "operator": "REGEX",
                "value": r"(?i)(settlement|transfer|NIP|NIBSS)",
            }
        ],
        "date_window_days": 10,
        "description": "Cross-bank transfer matching within settlement window",
    },
]


class ReconciliationRuleService:
    """Service for reconciliation match rule management."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── CRUD ──────────────────────────────────────────────────────

    def list_for_org(
        self,
        org_id: UUID,
        *,
        is_active: bool | None = None,
    ) -> list[ReconciliationMatchRule]:
        """List rules for an organization, ordered by priority."""
        stmt = (
            select(ReconciliationMatchRule)
            .where(ReconciliationMatchRule.organization_id == org_id)
            .order_by(ReconciliationMatchRule.priority)
        )
        if is_active is not None:
            stmt = stmt.where(ReconciliationMatchRule.is_active == is_active)
        return list(self.db.scalars(stmt).all())

    def get_by_id(self, rule_id: UUID) -> ReconciliationMatchRule | None:
        """Get a rule by its ID."""
        return self.db.get(ReconciliationMatchRule, rule_id)

    def create(self, org_id: UUID, data: dict[str, Any]) -> ReconciliationMatchRule:
        """Create a new match rule."""
        rule = ReconciliationMatchRule(
            organization_id=org_id,
            name=data["name"],
            description=data.get("description"),
            source_doc_type=data["source_doc_type"],
            priority=int(data.get("priority", 100)),
            is_system=bool(data.get("is_system", False)),
            is_active=bool(data.get("is_active", True)),
            conditions=data.get("conditions", []),
            match_debit=bool(data.get("match_debit", True)),
            match_credit=bool(data.get("match_credit", True)),
            amount_tolerance_cents=(
                int(data["amount_tolerance_cents"])
                if data.get("amount_tolerance_cents")
                else None
            ),
            date_window_days=(
                int(data["date_window_days"]) if data.get("date_window_days") else None
            ),
            action_type=data.get("action_type", "MATCH"),
            min_confidence=int(data.get("min_confidence", 90)),
            writeoff_account_id=data.get("writeoff_account_id"),
            journal_label_template=data.get("journal_label_template"),
        )
        self.db.add(rule)
        self.db.flush()
        logger.info("Created match rule: %s (org=%s)", rule.name, org_id)
        return rule

    def update(self, rule_id: UUID, data: dict[str, Any]) -> ReconciliationMatchRule:
        """Update an existing match rule."""
        rule = self.db.get(ReconciliationMatchRule, rule_id)
        if not rule:
            raise ValueError(f"Rule {rule_id} not found")

        for field in (
            "name",
            "description",
            "source_doc_type",
            "priority",
            "is_active",
            "conditions",
            "match_debit",
            "match_credit",
            "amount_tolerance_cents",
            "date_window_days",
            "action_type",
            "min_confidence",
            "writeoff_account_id",
            "journal_label_template",
        ):
            if field in data:
                setattr(rule, field, data[field])

        self.db.flush()
        logger.info("Updated match rule: %s", rule.name)
        return rule

    def delete(self, rule_id: UUID) -> None:
        """Delete a rule. Raises ValueError for system rules."""
        rule = self.db.get(ReconciliationMatchRule, rule_id)
        if not rule:
            raise ValueError(f"Rule {rule_id} not found")
        if rule.is_system:
            raise ValueError("System rules cannot be deleted. Disable them instead.")
        self.db.delete(rule)
        self.db.flush()
        logger.info("Deleted match rule: %s", rule.name)

    # ── Evaluation ────────────────────────────────────────────────

    def get_active_rules(self, org_id: UUID) -> list[ReconciliationMatchRule]:
        """Active rules ordered by priority ASC (lowest first)."""
        return self.list_for_org(org_id, is_active=True)

    def evaluate_conditions(
        self,
        rule: ReconciliationMatchRule,
        line: BankStatementLine,
    ) -> bool:
        """Check if all conditions match a statement line.

        Also checks direction filter (match_debit / match_credit).
        Returns True if all conditions are satisfied (AND logic).
        """
        # Direction filter
        if line.transaction_type == StatementLineType.debit and not rule.match_debit:
            return False
        if line.transaction_type == StatementLineType.credit and not rule.match_credit:
            return False

        conditions = rule.conditions or []
        if not conditions:
            # No conditions = matches any line (direction-filtered only)
            return True

        for cond in conditions:
            field = str(cond.get("field", "")).upper()
            operator = str(cond.get("operator", ""))
            value = str(cond.get("value", ""))

            line_value = self._get_line_field(line, field)
            if not self._check_condition(line_value, operator, value):
                return False

        return True

    @staticmethod
    def _get_line_field(line: BankStatementLine, field: str) -> str:
        """Extract a field value from a statement line."""
        field_map: dict[str, str | None] = {
            "DESCRIPTION": line.description,
            "REFERENCE": line.reference,
            "BANK_REFERENCE": line.bank_reference,
            "PAYEE": line.payee_payer,
            "BANK_CATEGORY": line.bank_category,
            "BANK_CODE": line.bank_code,
        }
        return field_map.get(field) or ""

    @staticmethod
    def _check_condition(line_value: str, operator: str, pattern: str) -> bool:
        """Evaluate a single condition against a line field value."""
        if not line_value:
            return False

        if operator == "EQUALS":
            return line_value.strip().lower() == pattern.strip().lower()
        if operator == "CONTAINS":
            return pattern.lower() in line_value.lower()
        if operator == "STARTS_WITH":
            return line_value.lower().startswith(pattern.lower())
        if operator == "REGEX":
            try:
                return bool(re.search(pattern, line_value))
            except re.error:
                logger.warning("Invalid regex in match rule: %s", pattern)
                return False
        if operator == "BETWEEN":
            # value format: "min,max" for amount ranges
            parts = pattern.split(",")
            if len(parts) != 2:
                return False
            try:
                val = Decimal(line_value)
                return Decimal(parts[0]) <= val <= Decimal(parts[1])
            except (ValueError, ArithmeticError):
                return False
        if operator == "GREATER_THAN":
            try:
                return Decimal(line_value) > Decimal(pattern)
            except (ValueError, ArithmeticError):
                return False
        if operator == "LESS_THAN":
            try:
                return Decimal(line_value) < Decimal(pattern)
            except (ValueError, ArithmeticError):
                return False

        return False

    # ── Logging ───────────────────────────────────────────────────

    def log_match(
        self,
        org_id: UUID,
        *,
        rule_id: UUID | None,
        line_id: UUID,
        source_doc_type: str,
        source_doc_id: UUID | None,
        journal_line_id: UUID | None,
        confidence: int,
        explanation: str,
        action: str,
    ) -> ReconciliationMatchLog:
        """Record a match in the audit log."""
        log = ReconciliationMatchLog(
            organization_id=org_id,
            rule_id=rule_id,
            statement_line_id=line_id,
            source_doc_type=source_doc_type,
            source_doc_id=source_doc_id,
            journal_line_id=journal_line_id,
            confidence_score=confidence,
            explanation=explanation,
            action_taken=action,
        )
        self.db.add(log)

        # Update rule stats if rule_id provided
        if rule_id:
            rule = self.db.get(ReconciliationMatchRule, rule_id)
            if rule:
                rule.match_count = (rule.match_count or 0) + 1
                rule.last_matched_at = datetime.now(tz=_UTC)

        return log

    def get_match_log(
        self,
        org_id: UUID,
        *,
        rule_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReconciliationMatchLog]:
        """Get match log entries for an organization."""
        stmt = (
            select(ReconciliationMatchLog)
            .where(ReconciliationMatchLog.organization_id == org_id)
            .order_by(ReconciliationMatchLog.matched_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if rule_id:
            stmt = stmt.where(ReconciliationMatchLog.rule_id == rule_id)
        return list(self.db.scalars(stmt).all())

    def count_match_log(
        self,
        org_id: UUID,
        *,
        rule_id: UUID | None = None,
    ) -> int:
        """Count match log entries for pagination."""
        stmt = select(func.count(ReconciliationMatchLog.log_id)).where(
            ReconciliationMatchLog.organization_id == org_id
        )
        if rule_id:
            stmt = stmt.where(ReconciliationMatchLog.rule_id == rule_id)
        return self.db.scalar(stmt) or 0

    # ── Seed ──────────────────────────────────────────────────────

    @staticmethod
    def seed_system_rules(db: Session, org_id: UUID) -> int:
        """Create system rules for an organization. Idempotent."""
        created = 0
        for rule_data in SYSTEM_RULES:
            existing = db.scalar(
                select(ReconciliationMatchRule).where(
                    ReconciliationMatchRule.organization_id == org_id,
                    ReconciliationMatchRule.name == rule_data["name"],
                    ReconciliationMatchRule.is_system.is_(True),
                )
            )
            if existing:
                continue

            rule = ReconciliationMatchRule(
                organization_id=org_id,
                name=rule_data["name"],
                description=rule_data.get("description"),
                source_doc_type=rule_data["source_doc_type"],
                priority=rule_data["priority"],
                is_system=True,
                is_active=True,
                conditions=rule_data.get("conditions", []),
                match_debit=rule_data.get("match_debit", True),
                match_credit=rule_data.get("match_credit", True),
                date_window_days=rule_data.get("date_window_days"),
                action_type=rule_data.get("action_type", "MATCH"),
                journal_label_template=rule_data.get("journal_label_template"),
            )
            db.add(rule)
            created += 1

        if created:
            db.flush()
            logger.info("Seeded %d system match rules for org %s", created, org_id)
        return created
