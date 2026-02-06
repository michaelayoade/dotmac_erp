"""
Transaction Categorization Service.

Provides auto-categorization of bank transactions using payee matching
and configurable rules.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    StatementLineType,
)
from app.models.finance.banking.payee import Payee, PayeeType
from app.models.finance.banking.transaction_rule import (
    RuleAction,
    RuleType,
    TransactionRule,
)
from app.services.common import coerce_uuid


@dataclass
class CategorizationSuggestion:
    """A suggested categorization for a transaction."""

    account_id: Optional[UUID] = None
    account_name: Optional[str] = None
    tax_code_id: Optional[UUID] = None
    payee_id: Optional[UUID] = None
    payee_name: Optional[str] = None
    rule_id: Optional[UUID] = None
    rule_name: Optional[str] = None
    confidence: int = 0  # 0-100
    match_reason: str = ""
    action: RuleAction = RuleAction.CATEGORIZE
    split_config: Optional[dict] = None


@dataclass
class CategorizationResult:
    """Result of categorizing a single transaction."""

    line_id: UUID
    suggestions: List[CategorizationSuggestion] = field(default_factory=list)
    is_duplicate: bool = False
    duplicate_of: Optional[UUID] = None

    @property
    def best_suggestion(self) -> Optional[CategorizationSuggestion]:
        """Get the highest confidence suggestion."""
        if not self.suggestions:
            return None
        return max(self.suggestions, key=lambda s: s.confidence)

    @property
    def has_high_confidence_match(self) -> bool:
        """Check if there's a high confidence match (>80%)."""
        return any(s.confidence >= 80 for s in self.suggestions)


@dataclass
class BatchCategorizationResult:
    """Result of categorizing multiple transactions."""

    total_lines: int = 0
    categorized_count: int = 0
    high_confidence_count: int = 0
    low_confidence_count: int = 0
    no_match_count: int = 0
    duplicate_count: int = 0
    results: List[CategorizationResult] = field(default_factory=list)


class TransactionCategorizationService:
    """Service for auto-categorizing bank transactions."""

    def categorize_line(
        self,
        db: Session,
        organization_id: UUID,
        line: BankStatementLine,
        check_duplicates: bool = True,
    ) -> CategorizationResult:
        """
        Categorize a single statement line.

        Args:
            db: Database session
            organization_id: Organization UUID
            line: The statement line to categorize
            check_duplicates: Whether to check for duplicate transactions

        Returns:
            CategorizationResult with suggestions
        """
        result = CategorizationResult(line_id=line.line_id)

        # Check for duplicates first
        if check_duplicates:
            duplicate = self._find_duplicate(db, organization_id, line)
            if duplicate:
                result.is_duplicate = True
                result.duplicate_of = duplicate.line_id
                return result

        # Try payee matching
        payee_suggestion = self._match_payee(db, organization_id, line)
        if payee_suggestion:
            result.suggestions.append(payee_suggestion)

        # Try rule matching
        rule_suggestions = self._match_rules(db, organization_id, line)
        result.suggestions.extend(rule_suggestions)

        # Sort by confidence (highest first)
        result.suggestions.sort(key=lambda s: s.confidence, reverse=True)

        return result

    def categorize_statement(
        self,
        db: Session,
        organization_id: UUID,
        statement_id: UUID,
        check_duplicates: bool = True,
    ) -> BatchCategorizationResult:
        """
        Categorize all unmatched lines in a statement.

        Args:
            db: Database session
            organization_id: Organization UUID
            statement_id: Statement UUID
            check_duplicates: Whether to check for duplicates

        Returns:
            BatchCategorizationResult with all results
        """
        # Get unmatched lines
        lines = (
            db.query(BankStatementLine)
            .filter(
                BankStatementLine.statement_id == statement_id,
                BankStatementLine.is_matched == False,
            )
            .order_by(BankStatementLine.line_number)
            .all()
        )

        batch_result = BatchCategorizationResult(total_lines=len(lines))

        for line in lines:
            result = self.categorize_line(db, organization_id, line, check_duplicates)
            batch_result.results.append(result)

            if result.is_duplicate:
                batch_result.duplicate_count += 1
            elif result.suggestions:
                batch_result.categorized_count += 1
                if result.has_high_confidence_match:
                    batch_result.high_confidence_count += 1
                else:
                    batch_result.low_confidence_count += 1
            else:
                batch_result.no_match_count += 1

        return batch_result

    def _find_duplicate(
        self,
        db: Session,
        organization_id: UUID,
        line: BankStatementLine,
    ) -> Optional[BankStatementLine]:
        """Check if a transaction is a duplicate."""
        # Look for same amount, date, and similar description in recent statements
        statement = db.get(BankStatement, line.statement_id)
        if not statement:
            return None

        # Find potential duplicates (same account, date, amount, type)
        duplicate = (
            db.query(BankStatementLine)
            .join(BankStatement)
            .filter(
                BankStatement.bank_account_id == statement.bank_account_id,
                BankStatementLine.line_id != line.line_id,
                BankStatementLine.transaction_date == line.transaction_date,
                BankStatementLine.amount == line.amount,
                BankStatementLine.transaction_type == line.transaction_type,
            )
            .first()
        )

        if duplicate:
            # Check if descriptions are similar (simple check)
            if line.description and duplicate.description:
                if (
                    self._similarity_score(line.description, duplicate.description)
                    > 0.8
                ):
                    return duplicate
            # If no description, match on reference
            elif line.bank_reference and duplicate.bank_reference:
                if line.bank_reference == duplicate.bank_reference:
                    return duplicate

        return None

    def _match_payee(
        self,
        db: Session,
        organization_id: UUID,
        line: BankStatementLine,
    ) -> Optional[CategorizationSuggestion]:
        """Try to match the transaction to a known payee."""
        if not line.description and not line.payee_payer:
            return None

        search_text = f"{line.payee_payer or ''} {line.description or ''}".strip()
        if not search_text:
            return None

        # Get active payees
        payees = (
            db.query(Payee)
            .filter(
                Payee.organization_id == organization_id,
                Payee.is_active == True,
            )
            .all()
        )

        best_match: Optional[Tuple[Payee, int]] = None

        for payee in payees:
            if payee.matches_name(search_text):
                # Calculate confidence based on match quality
                confidence = self._calculate_payee_confidence(payee, search_text)
                if best_match is None or confidence > best_match[1]:
                    best_match = (payee, confidence)

        if best_match:
            payee, confidence = best_match
            return CategorizationSuggestion(
                account_id=payee.default_account_id,
                tax_code_id=payee.default_tax_code_id,
                payee_id=payee.payee_id,
                payee_name=payee.payee_name,
                confidence=confidence,
                match_reason=f"Matched payee: {payee.payee_name}",
            )

        return None

    def _match_rules(
        self,
        db: Session,
        organization_id: UUID,
        line: BankStatementLine,
    ) -> List[CategorizationSuggestion]:
        """Match the transaction against categorization rules."""
        suggestions = []

        # Get active rules, ordered by priority
        statement = db.get(BankStatement, line.statement_id)
        bank_account_id = statement.bank_account_id if statement else None

        rules = (
            db.query(TransactionRule)
            .filter(
                TransactionRule.organization_id == organization_id,
                TransactionRule.is_active == True,
                or_(
                    TransactionRule.bank_account_id == None,
                    TransactionRule.bank_account_id == bank_account_id,
                ),
            )
            .order_by(TransactionRule.priority.desc())
            .all()
        )

        for rule in rules:
            # Check transaction type filter
            if line.transaction_type == StatementLineType.credit:
                if not rule.applies_to_credits:
                    continue
            else:
                if not rule.applies_to_debits:
                    continue

            # Try to match the rule
            match_result = self._evaluate_rule(rule, line)
            if match_result:
                confidence, reason = match_result

                if confidence >= rule.min_confidence:
                    suggestion = CategorizationSuggestion(
                        account_id=rule.target_account_id,
                        tax_code_id=rule.tax_code_id,
                        payee_id=rule.payee_id,
                        rule_id=rule.rule_id,
                        rule_name=rule.rule_name,
                        confidence=confidence,
                        match_reason=reason,
                        action=rule.action,
                        split_config=rule.split_config
                        if rule.action == RuleAction.SPLIT
                        else None,
                    )
                    suggestions.append(suggestion)

        return suggestions

    def _evaluate_rule(
        self,
        rule: TransactionRule,
        line: BankStatementLine,
    ) -> Optional[Tuple[int, str]]:
        """
        Evaluate if a rule matches a transaction line.

        Returns:
            Tuple of (confidence, reason) if matched, None otherwise
        """
        conditions = rule.conditions or {}

        if rule.rule_type == RuleType.PAYEE_MATCH:
            return self._eval_payee_match(conditions, line)
        elif rule.rule_type == RuleType.DESCRIPTION_CONTAINS:
            return self._eval_description_contains(conditions, line)
        elif rule.rule_type == RuleType.DESCRIPTION_REGEX:
            return self._eval_description_regex(conditions, line)
        elif rule.rule_type == RuleType.AMOUNT_RANGE:
            return self._eval_amount_range(conditions, line)
        elif rule.rule_type == RuleType.REFERENCE_MATCH:
            return self._eval_reference_match(conditions, line)
        elif rule.rule_type == RuleType.COMBINED:
            return self._eval_combined(conditions, line)

        return None

    def _eval_payee_match(
        self,
        conditions: dict,
        line: BankStatementLine,
    ) -> Optional[Tuple[int, str]]:
        """Evaluate payee pattern match."""
        patterns = conditions.get("patterns", [])
        case_sensitive = conditions.get("case_sensitive", False)

        search_text = f"{line.payee_payer or ''} {line.description or ''}"
        if not case_sensitive:
            search_text = search_text.upper()

        for pattern in patterns:
            check_pattern = pattern if case_sensitive else pattern.upper()
            if check_pattern in search_text:
                return (90, f"Payee pattern matched: {pattern}")

        return None

    def _eval_description_contains(
        self,
        conditions: dict,
        line: BankStatementLine,
    ) -> Optional[Tuple[int, str]]:
        """Evaluate description contains match."""
        text = conditions.get("text", "")
        case_sensitive = conditions.get("case_sensitive", False)

        if not line.description:
            return None

        description = line.description if case_sensitive else line.description.upper()
        check_text = text if case_sensitive else text.upper()

        if check_text in description:
            return (85, f"Description contains: {text}")

        return None

    def _eval_description_regex(
        self,
        conditions: dict,
        line: BankStatementLine,
    ) -> Optional[Tuple[int, str]]:
        """Evaluate regex pattern match."""
        pattern = conditions.get("pattern", "")

        if not line.description or not pattern:
            return None

        try:
            if re.search(pattern, line.description, re.IGNORECASE):
                return (80, f"Regex matched: {pattern}")
        except re.error as exc:
            logger.warning(
                "Invalid regex pattern in categorization rule: %r - %s",
                pattern,
                exc,
            )

        return None

    def _eval_amount_range(
        self,
        conditions: dict,
        line: BankStatementLine,
    ) -> Optional[Tuple[int, str]]:
        """Evaluate amount range match."""
        min_amount = Decimal(str(conditions.get("min", 0)))
        max_amount = Decimal(str(conditions.get("max", float("inf"))))
        trans_type = conditions.get("transaction_type")

        # Check transaction type if specified
        if trans_type:
            if (
                trans_type == "credit"
                and line.transaction_type != StatementLineType.credit
            ):
                return None
            if (
                trans_type == "debit"
                and line.transaction_type != StatementLineType.debit
            ):
                return None

        if min_amount <= line.amount <= max_amount:
            return (70, f"Amount in range: {min_amount} - {max_amount}")

        return None

    def _eval_reference_match(
        self,
        conditions: dict,
        line: BankStatementLine,
    ) -> Optional[Tuple[int, str]]:
        """Evaluate reference pattern match."""
        pattern = conditions.get("pattern", "")

        if not pattern:
            return None

        # Check various reference fields
        ref_fields = [line.reference, line.bank_reference, line.check_number]

        for ref in ref_fields:
            if ref:
                try:
                    if re.search(pattern, ref, re.IGNORECASE):
                        return (85, f"Reference matched: {pattern}")
                except re.error as exc:
                    logger.warning(
                        "Invalid regex pattern in reference match: %r - %s",
                        pattern,
                        exc,
                    )

        return None

    def _eval_combined(
        self,
        conditions: dict,
        line: BankStatementLine,
    ) -> Optional[Tuple[int, str]]:
        """Evaluate combined conditions."""
        operator = conditions.get("operator", "AND")
        sub_rules = conditions.get("rules", [])

        if not sub_rules:
            return None

        results = []
        for sub_rule in sub_rules:
            sub_type = sub_rule.get("type")
            sub_conditions = sub_rule.get("conditions", {})

            result = None
            if sub_type == "PAYEE_MATCH":
                result = self._eval_payee_match(sub_conditions, line)
            elif sub_type == "DESCRIPTION_CONTAINS":
                result = self._eval_description_contains(sub_conditions, line)
            elif sub_type == "AMOUNT_RANGE":
                result = self._eval_amount_range(sub_conditions, line)

            if result:
                results.append(result)

        if not results:
            return None

        if operator == "AND":
            if len(results) == len(sub_rules):
                avg_confidence = sum(r[0] for r in results) // len(results)
                return (avg_confidence, "All conditions matched")
        elif operator == "OR":
            best = max(results, key=lambda r: r[0])
            return best

        return None

    def _calculate_payee_confidence(self, payee: Payee, search_text: str) -> int:
        """Calculate confidence score for a payee match."""
        search_upper = search_text.upper()
        name_upper = payee.payee_name.upper()

        # Exact name match = 95%
        if name_upper in search_upper:
            # Higher confidence if it's at the start
            if search_upper.startswith(name_upper):
                return 95
            return 90

        # Pattern match = 80-85%
        if payee.name_patterns:
            patterns = [p.strip().upper() for p in payee.name_patterns.split("|")]
            for pattern in patterns:
                if pattern and pattern in search_upper:
                    return 85

        return 75

    def _similarity_score(self, s1: str, s2: str) -> float:
        """Calculate simple similarity between two strings."""
        if not s1 or not s2:
            return 0.0

        s1_upper = s1.upper()
        s2_upper = s2.upper()

        if s1_upper == s2_upper:
            return 1.0

        # Simple word overlap similarity
        words1 = set(s1_upper.split())
        words2 = set(s2_upper.split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union)

    # Payee management methods

    def create_payee(
        self,
        db: Session,
        organization_id: UUID,
        payee_name: str,
        payee_type: PayeeType = PayeeType.OTHER,
        name_patterns: Optional[str] = None,
        default_account_id: Optional[UUID] = None,
        default_tax_code_id: Optional[UUID] = None,
        supplier_id: Optional[UUID] = None,
        customer_id: Optional[UUID] = None,
        notes: Optional[str] = None,
        created_by: Optional[UUID] = None,
    ) -> Payee:
        """Create a new payee."""
        payee = Payee(
            organization_id=organization_id,
            payee_name=payee_name,
            payee_type=payee_type,
            name_patterns=name_patterns,
            default_account_id=default_account_id,
            default_tax_code_id=default_tax_code_id,
            supplier_id=supplier_id,
            customer_id=customer_id,
            notes=notes,
            created_by=created_by,
        )
        db.add(payee)
        db.flush()
        return payee

    def update_payee(
        self,
        db: Session,
        organization_id: UUID,
        payee_id: UUID,
        **kwargs,
    ) -> Optional[Payee]:
        """Update a payee."""
        org_id = coerce_uuid(organization_id)
        payee = (
            db.query(Payee)
            .filter(
                Payee.payee_id == coerce_uuid(payee_id),
                Payee.organization_id == org_id,
            )
            .first()
        )
        if not payee:
            return None

        allowed_fields = [
            "payee_name",
            "payee_type",
            "name_patterns",
            "default_account_id",
            "default_tax_code_id",
            "supplier_id",
            "customer_id",
            "notes",
            "is_active",
        ]

        for key, value in kwargs.items():
            if key in allowed_fields:
                setattr(payee, key, value)

        db.flush()
        return payee

    def list_payees(
        self,
        db: Session,
        organization_id: UUID,
        payee_type: Optional[PayeeType] = None,
        is_active: Optional[bool] = True,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Payee]:
        """List payees with filters."""
        query = db.query(Payee).filter(Payee.organization_id == organization_id)

        if payee_type:
            query = query.filter(Payee.payee_type == payee_type)
        if is_active is not None:
            query = query.filter(Payee.is_active == is_active)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Payee.payee_name.ilike(search_pattern),
                    Payee.name_patterns.ilike(search_pattern),
                )
            )

        return query.order_by(Payee.payee_name).offset(offset).limit(limit).all()

    def increment_payee_match(
        self,
        db: Session,
        organization_id: UUID,
        payee_id: UUID,
    ) -> None:
        """Increment match count for a payee."""
        org_id = coerce_uuid(organization_id)
        payee = (
            db.query(Payee)
            .filter(
                Payee.payee_id == coerce_uuid(payee_id),
                Payee.organization_id == org_id,
            )
            .first()
        )
        if payee:
            payee.match_count += 1
            payee.last_matched_at = datetime.utcnow()
            db.flush()

    # Rule management methods

    def create_rule(
        self,
        db: Session,
        organization_id: UUID,
        rule_name: str,
        rule_type: RuleType,
        conditions: dict,
        action: RuleAction = RuleAction.CATEGORIZE,
        target_account_id: Optional[UUID] = None,
        tax_code_id: Optional[UUID] = None,
        bank_account_id: Optional[UUID] = None,
        payee_id: Optional[UUID] = None,
        priority: int = 100,
        auto_apply: bool = False,
        min_confidence: int = 80,
        applies_to_credits: bool = True,
        applies_to_debits: bool = True,
        split_config: Optional[dict] = None,
        description: Optional[str] = None,
        created_by: Optional[UUID] = None,
    ) -> TransactionRule:
        """Create a new categorization rule."""
        rule = TransactionRule(
            organization_id=organization_id,
            rule_name=rule_name,
            rule_type=rule_type,
            conditions=conditions,
            action=action,
            target_account_id=target_account_id,
            tax_code_id=tax_code_id,
            bank_account_id=bank_account_id,
            payee_id=payee_id,
            priority=priority,
            auto_apply=auto_apply,
            min_confidence=min_confidence,
            applies_to_credits=applies_to_credits,
            applies_to_debits=applies_to_debits,
            split_config=split_config,
            description=description,
            created_by=created_by,
        )
        db.add(rule)
        db.flush()
        return rule

    def update_rule(
        self,
        db: Session,
        organization_id: UUID,
        rule_id: UUID,
        **kwargs,
    ) -> Optional[TransactionRule]:
        """Update a rule."""
        org_id = coerce_uuid(organization_id)
        rule = (
            db.query(TransactionRule)
            .filter(
                TransactionRule.rule_id == coerce_uuid(rule_id),
                TransactionRule.organization_id == org_id,
            )
            .first()
        )
        if not rule:
            return None

        allowed_fields = [
            "rule_name",
            "description",
            "rule_type",
            "conditions",
            "action",
            "target_account_id",
            "tax_code_id",
            "bank_account_id",
            "payee_id",
            "priority",
            "auto_apply",
            "min_confidence",
            "applies_to_credits",
            "applies_to_debits",
            "split_config",
            "is_active",
        ]

        for key, value in kwargs.items():
            if key in allowed_fields:
                setattr(rule, key, value)

        db.flush()
        return rule

    def list_rules(
        self,
        db: Session,
        organization_id: UUID,
        rule_type: Optional[RuleType] = None,
        is_active: Optional[bool] = True,
        bank_account_id: Optional[UUID] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[TransactionRule]:
        """List rules with filters."""
        query = db.query(TransactionRule).filter(
            TransactionRule.organization_id == organization_id
        )

        if rule_type:
            query = query.filter(TransactionRule.rule_type == rule_type)
        if is_active is not None:
            query = query.filter(TransactionRule.is_active == is_active)
        if bank_account_id:
            query = query.filter(
                or_(
                    TransactionRule.bank_account_id == None,
                    TransactionRule.bank_account_id == bank_account_id,
                )
            )

        return (
            query.order_by(TransactionRule.priority.desc(), TransactionRule.rule_name)
            .offset(offset)
            .limit(limit)
            .all()
        )

    def record_rule_feedback(
        self,
        db: Session,
        organization_id: UUID,
        rule_id: UUID,
        accepted: bool,
    ) -> None:
        """Record user feedback on a rule suggestion."""
        org_id = coerce_uuid(organization_id)
        rule = (
            db.query(TransactionRule)
            .filter(
                TransactionRule.rule_id == coerce_uuid(rule_id),
                TransactionRule.organization_id == org_id,
            )
            .first()
        )
        if rule:
            rule.match_count += 1
            rule.last_matched_at = datetime.utcnow()
            if accepted:
                rule.success_count += 1
            else:
                rule.reject_count += 1
            db.flush()

    def learn_from_categorization(
        self,
        db: Session,
        organization_id: UUID,
        line: BankStatementLine,
        account_id: UUID,
        created_by: Optional[UUID] = None,
    ) -> Optional[Payee]:
        """
        Learn from a manual categorization to create/update payee.

        When a user manually categorizes a transaction, we can learn
        the payee name and default account for future auto-categorization.
        """
        payee_name = line.payee_payer or line.description
        if not payee_name:
            return None

        # Clean up the name (take first significant part)
        payee_name = payee_name.strip()[:200]

        # Check if payee already exists
        existing = (
            db.query(Payee)
            .filter(
                Payee.organization_id == organization_id,
                Payee.payee_name == payee_name,
            )
            .first()
        )

        if existing:
            # Update match count and possibly default account
            existing.match_count += 1
            existing.last_matched_at = datetime.utcnow()
            if not existing.default_account_id:
                existing.default_account_id = account_id
            db.flush()
            return existing
        else:
            # Create new payee
            return self.create_payee(
                db=db,
                organization_id=organization_id,
                payee_name=payee_name,
                default_account_id=account_id,
                created_by=created_by,
            )


# Singleton instance
categorization_service = TransactionCategorizationService()
