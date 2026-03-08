"""
Tests for Reconciliation Match Rules — rule CRUD, condition evaluation,
match logging, and system rule seeding.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.models.finance.banking.bank_statement import StatementLineType
from app.services.finance.banking.reconciliation_rule_service import (
    SYSTEM_RULES,
    ReconciliationRuleService,
)
from tests.ifrs.banking.conftest import MockBankStatementLine

# ── Helpers ──────────────────────────────────────────────────────────


class MockMatchRule:
    """Mock ReconciliationMatchRule for condition evaluation."""

    def __init__(
        self,
        rule_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        name: str = "Test Rule",
        source_doc_type: str = "CUSTOMER_PAYMENT",
        priority: int = 100,
        is_system: bool = False,
        is_active: bool = True,
        conditions: list | None = None,
        match_debit: bool = True,
        match_credit: bool = True,
        amount_tolerance_cents: int | None = None,
        date_window_days: int | None = None,
        action_type: str = "MATCH",
        min_confidence: int = 90,
        writeoff_account_id: uuid.UUID | None = None,
        journal_label_template: str | None = None,
        match_count: int = 0,
        last_matched_at: datetime | None = None,
    ):
        self.rule_id = rule_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.name = name
        self.source_doc_type = source_doc_type
        self.priority = priority
        self.is_system = is_system
        self.is_active = is_active
        self.conditions = conditions or []
        self.match_debit = match_debit
        self.match_credit = match_credit
        self.amount_tolerance_cents = amount_tolerance_cents
        self.date_window_days = date_window_days
        self.action_type = action_type
        self.min_confidence = min_confidence
        self.writeoff_account_id = writeoff_account_id
        self.journal_label_template = journal_label_template
        self.match_count = match_count
        self.last_matched_at = last_matched_at


# ── Condition Evaluation Tests ───────────────────────────────────────


class TestConditionEvaluation:
    """Test the rule condition evaluation logic."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.service = ReconciliationRuleService(self.db)

    def test_no_conditions_matches_any_line(self) -> None:
        """A rule with no conditions matches any line (direction-filtered)."""
        rule = MockMatchRule(conditions=[])
        line = MockBankStatementLine(
            transaction_type=StatementLineType.credit,
            description="Any transaction",
        )
        assert self.service.evaluate_conditions(rule, line) is True

    def test_direction_filter_blocks_debit_when_match_debit_false(self) -> None:
        """Rule with match_debit=False should not match debit lines."""
        rule = MockMatchRule(match_debit=False, match_credit=True, conditions=[])
        line = MockBankStatementLine(transaction_type=StatementLineType.debit)
        assert self.service.evaluate_conditions(rule, line) is False

    def test_direction_filter_blocks_credit_when_match_credit_false(self) -> None:
        """Rule with match_credit=False should not match credit lines."""
        rule = MockMatchRule(match_debit=True, match_credit=False, conditions=[])
        line = MockBankStatementLine(transaction_type=StatementLineType.credit)
        assert self.service.evaluate_conditions(rule, line) is False

    def test_direction_filter_allows_matching_direction(self) -> None:
        """Rule should match when direction filter allows it."""
        rule = MockMatchRule(match_debit=True, match_credit=False, conditions=[])
        line = MockBankStatementLine(transaction_type=StatementLineType.debit)
        assert self.service.evaluate_conditions(rule, line) is True

    def test_regex_condition_matches(self) -> None:
        """REGEX operator matches paystack references."""
        rule = MockMatchRule(
            conditions=[
                {
                    "field": "REFERENCE",
                    "operator": "REGEX",
                    "value": r"PSTK_[a-zA-Z0-9]+",
                }
            ]
        )
        line = MockBankStatementLine(
            reference="PSTK_abc123xyz",
            transaction_type=StatementLineType.credit,
        )
        assert self.service.evaluate_conditions(rule, line) is True

    def test_regex_condition_no_match(self) -> None:
        """REGEX operator returns False when pattern doesn't match."""
        rule = MockMatchRule(
            conditions=[
                {
                    "field": "REFERENCE",
                    "operator": "REGEX",
                    "value": r"PSTK_[a-zA-Z0-9]+",
                }
            ]
        )
        line = MockBankStatementLine(
            reference="FLWV3-abc123",
            transaction_type=StatementLineType.credit,
        )
        assert self.service.evaluate_conditions(rule, line) is False

    def test_contains_condition(self) -> None:
        """CONTAINS operator checks for substring match (case-insensitive)."""
        rule = MockMatchRule(
            conditions=[
                {"field": "DESCRIPTION", "operator": "CONTAINS", "value": "bank charge"}
            ]
        )
        line = MockBankStatementLine(
            description="Monthly Bank Charge Fee",
            transaction_type=StatementLineType.debit,
        )
        assert self.service.evaluate_conditions(rule, line) is True

    def test_equals_condition(self) -> None:
        """EQUALS operator checks exact match (case-insensitive, trimmed)."""
        rule = MockMatchRule(
            conditions=[
                {"field": "BANK_CATEGORY", "operator": "EQUALS", "value": "bank_fee"}
            ]
        )
        line = MockBankStatementLine(
            bank_category="BANK_FEE",
            transaction_type=StatementLineType.debit,
        )
        assert self.service.evaluate_conditions(rule, line) is True

    def test_starts_with_condition(self) -> None:
        """STARTS_WITH operator checks prefix match."""
        rule = MockMatchRule(
            conditions=[
                {"field": "REFERENCE", "operator": "STARTS_WITH", "value": "NIP/"}
            ]
        )
        line = MockBankStatementLine(
            reference="NIP/12345/SETTLEMENT",
            transaction_type=StatementLineType.credit,
        )
        assert self.service.evaluate_conditions(rule, line) is True

    def test_multiple_conditions_all_must_match(self) -> None:
        """Multiple conditions use AND logic — all must pass."""
        rule = MockMatchRule(
            conditions=[
                {"field": "DESCRIPTION", "operator": "CONTAINS", "value": "paystack"},
                {"field": "REFERENCE", "operator": "REGEX", "value": r"PSTK_.*"},
            ]
        )
        # Both conditions match
        line = MockBankStatementLine(
            description="Paystack settlement transfer",
            reference="PSTK_abc123",
            transaction_type=StatementLineType.credit,
        )
        assert self.service.evaluate_conditions(rule, line) is True

        # Only one condition matches (reference doesn't match)
        line2 = MockBankStatementLine(
            description="Paystack settlement transfer",
            reference="FLW_xyz789",
            transaction_type=StatementLineType.credit,
        )
        assert self.service.evaluate_conditions(rule, line2) is False

    def test_between_operator(self) -> None:
        """BETWEEN operator for amount range checking."""
        result = ReconciliationRuleService._check_condition(
            "500.00", "BETWEEN", "100,1000"
        )
        assert result is True

        result = ReconciliationRuleService._check_condition(
            "50.00", "BETWEEN", "100,1000"
        )
        assert result is False

    def test_greater_than_operator(self) -> None:
        """GREATER_THAN operator for amount threshold."""
        result = ReconciliationRuleService._check_condition(
            "500.00", "GREATER_THAN", "100"
        )
        assert result is True

        result = ReconciliationRuleService._check_condition(
            "50.00", "GREATER_THAN", "100"
        )
        assert result is False

    def test_less_than_operator(self) -> None:
        """LESS_THAN operator for amount ceiling."""
        result = ReconciliationRuleService._check_condition("50.00", "LESS_THAN", "100")
        assert result is True

        result = ReconciliationRuleService._check_condition(
            "500.00", "LESS_THAN", "100"
        )
        assert result is False

    def test_invalid_regex_returns_false(self) -> None:
        """Invalid regex pattern should not crash, returns False."""
        result = ReconciliationRuleService._check_condition(
            "test", "REGEX", "[invalid("
        )
        assert result is False

    def test_empty_line_value_returns_false(self) -> None:
        """Empty field value should not match any condition."""
        result = ReconciliationRuleService._check_condition("", "CONTAINS", "test")
        assert result is False

    def test_unknown_operator_returns_false(self) -> None:
        """Unknown operator should not crash, returns False."""
        result = ReconciliationRuleService._check_condition(
            "test", "UNKNOWN_OP", "test"
        )
        assert result is False


# ── Field Extraction Tests ───────────────────────────────────────────


class TestFieldExtraction:
    """Test extracting field values from statement lines."""

    def test_description_field(self) -> None:
        line = MockBankStatementLine(description="Test description")
        result = ReconciliationRuleService._get_line_field(line, "DESCRIPTION")
        assert result == "Test description"

    def test_reference_field(self) -> None:
        line = MockBankStatementLine(reference="REF-123")
        result = ReconciliationRuleService._get_line_field(line, "REFERENCE")
        assert result == "REF-123"

    def test_bank_reference_field(self) -> None:
        line = MockBankStatementLine(bank_reference="BR-456")
        result = ReconciliationRuleService._get_line_field(line, "BANK_REFERENCE")
        assert result == "BR-456"

    def test_payee_field(self) -> None:
        line = MockBankStatementLine(payee_payer="Acme Corp")
        result = ReconciliationRuleService._get_line_field(line, "PAYEE")
        assert result == "Acme Corp"

    def test_unknown_field_returns_empty(self) -> None:
        line = MockBankStatementLine()
        result = ReconciliationRuleService._get_line_field(line, "NONEXISTENT")
        assert result == ""

    def test_none_field_returns_empty(self) -> None:
        line = MockBankStatementLine(reference=None)
        result = ReconciliationRuleService._get_line_field(line, "REFERENCE")
        assert result == ""


# ── System Rules Tests ───────────────────────────────────────────────


class TestSystemRules:
    """Verify system rule seed data is well-formed."""

    def test_system_rules_count(self) -> None:
        """There should be 7 system rules matching the 7 passes."""
        assert len(SYSTEM_RULES) == 7

    def test_system_rules_have_unique_names(self) -> None:
        names = [r["name"] for r in SYSTEM_RULES]
        assert len(names) == len(set(names))

    def test_system_rules_have_ascending_priorities(self) -> None:
        priorities = [r["priority"] for r in SYSTEM_RULES]
        assert priorities == sorted(priorities)

    def test_system_rules_have_required_fields(self) -> None:
        for rule in SYSTEM_RULES:
            assert "name" in rule
            assert "source_doc_type" in rule
            assert "priority" in rule
            assert "match_credit" in rule
            assert "match_debit" in rule

    def test_bank_fee_rule_has_action_type(self) -> None:
        """Bank fee rule should have CREATE_JOURNAL action."""
        fee_rule = next(r for r in SYSTEM_RULES if r["name"] == "Bank Fees")
        assert fee_rule["action_type"] == "CREATE_JOURNAL"
        assert fee_rule["journal_label_template"] is not None

    def test_interbank_rule_has_date_window(self) -> None:
        """Inter-bank rule should have a date window for settlement matching."""
        ib_rule = next(r for r in SYSTEM_RULES if r["name"] == "Inter-Bank Settlement")
        assert ib_rule["date_window_days"] == 10


# ── CRUD Tests ───────────────────────────────────────────────────────


class TestRuleCRUD:
    """Test rule CRUD operations."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.service = ReconciliationRuleService(self.db)

    def test_create_rule(self) -> None:
        org_id = uuid.uuid4()
        data = {
            "name": "Custom Paystack Rule",
            "source_doc_type": "PAYMENT_INTENT",
            "priority": 100,
            "conditions": [
                {"field": "REFERENCE", "operator": "REGEX", "value": r"PSTK_.*"}
            ],
            "match_credit": True,
            "match_debit": False,
        }
        rule = self.service.create(org_id, data)
        assert rule.name == "Custom Paystack Rule"
        assert rule.source_doc_type == "PAYMENT_INTENT"
        assert rule.priority == 100
        assert rule.is_system is False
        assert rule.is_active is True
        self.db.add.assert_called_once()
        assert self.db.flush.call_count >= 1

    def test_delete_system_rule_raises(self) -> None:
        """System rules cannot be deleted."""
        rule = MagicMock()
        rule.is_system = True
        self.db.get.return_value = rule

        with pytest.raises(ValueError, match="System rules cannot be deleted"):
            self.service.delete(uuid.uuid4())

    def test_delete_custom_rule_succeeds(self) -> None:
        rule = MagicMock()
        rule.is_system = False
        rule.name = "Custom Rule"
        self.db.get.return_value = rule

        self.service.delete(uuid.uuid4())
        self.db.delete.assert_called_once_with(rule)

    def test_delete_nonexistent_rule_raises(self) -> None:
        self.db.get.return_value = None
        with pytest.raises(ValueError, match="not found"):
            self.service.delete(uuid.uuid4())

    def test_update_rule(self) -> None:
        rule = MagicMock()
        rule.name = "Old Name"
        self.db.get.return_value = rule

        self.service.update(uuid.uuid4(), {"name": "New Name", "priority": 50})
        assert rule.name == "New Name"
        assert rule.priority == 50
        assert self.db.flush.call_count >= 1

    def test_update_nonexistent_rule_raises(self) -> None:
        self.db.get.return_value = None
        with pytest.raises(ValueError, match="not found"):
            self.service.update(uuid.uuid4(), {"name": "X"})


# ── Match Logging Tests ─────────────────────────────────────────────


class TestMatchLogging:
    """Test the match audit log."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.service = ReconciliationRuleService(self.db)

    def test_log_match_creates_record(self) -> None:
        org_id = uuid.uuid4()
        log = self.service.log_match(
            org_id,
            rule_id=None,
            line_id=uuid.uuid4(),
            source_doc_type="CUSTOMER_PAYMENT",
            source_doc_id=uuid.uuid4(),
            journal_line_id=uuid.uuid4(),
            confidence=100,
            explanation="Exact reference match",
            action="MATCHED",
        )
        assert log.organization_id == org_id
        assert log.confidence_score == 100
        assert log.action_taken == "MATCHED"
        self.db.add.assert_called_once()

    def test_log_match_updates_rule_stats(self) -> None:
        """When a rule_id is provided, the rule's stats should be updated."""
        org_id = uuid.uuid4()
        rule = MagicMock()
        rule.match_count = 5
        rule.last_matched_at = None
        self.db.get.return_value = rule

        rule_id = uuid.uuid4()
        self.service.log_match(
            org_id,
            rule_id=rule_id,
            line_id=uuid.uuid4(),
            source_doc_type="CUSTOMER_PAYMENT",
            source_doc_id=None,
            journal_line_id=None,
            confidence=90,
            explanation="Date+amount fallback",
            action="MATCHED",
        )
        assert rule.match_count == 6
        assert rule.last_matched_at is not None
