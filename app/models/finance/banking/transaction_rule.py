"""
Bank Transaction Rule Model.

Stores rules for auto-categorizing bank transactions to GL accounts.
"""

import enum
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RuleType(str, enum.Enum):
    """Type of categorization rule."""
    PAYEE_MATCH = "PAYEE_MATCH"           # Match by payee name/pattern
    DESCRIPTION_CONTAINS = "DESCRIPTION_CONTAINS"  # Description contains text
    DESCRIPTION_REGEX = "DESCRIPTION_REGEX"        # Regex pattern match
    AMOUNT_RANGE = "AMOUNT_RANGE"          # Amount within range
    REFERENCE_MATCH = "REFERENCE_MATCH"    # Reference number pattern
    COMBINED = "COMBINED"                   # Multiple conditions


class RuleAction(str, enum.Enum):
    """Action to take when rule matches."""
    CATEGORIZE = "CATEGORIZE"       # Assign to GL account
    FLAG_REVIEW = "FLAG_REVIEW"     # Flag for manual review
    SPLIT = "SPLIT"                 # Split across accounts
    IGNORE = "IGNORE"               # Mark as non-reconciling


class TransactionRule(Base):
    """
    Transaction categorization rule.

    Defines conditions for auto-categorizing bank transactions
    and the actions to take when matched.
    """

    __tablename__ = "transaction_rule"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "rule_name",
            name="uq_rule_name",
        ),
        {"schema": "banking"},
    )

    rule_id: Mapped[uuid4] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    organization_id: Mapped[uuid4] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    # Rule identification
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Rule type and conditions
    rule_type: Mapped[RuleType] = mapped_column(
        Enum(RuleType, name="rule_type", schema="banking"),
        nullable=False,
    )

    # Matching conditions (stored as JSON for flexibility)
    conditions: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="""
        Conditions based on rule_type:
        - PAYEE_MATCH: {"patterns": ["AMAZON", "AMZN"], "case_sensitive": false}
        - DESCRIPTION_CONTAINS: {"text": "DIRECT DEBIT", "case_sensitive": false}
        - DESCRIPTION_REGEX: {"pattern": "^DD\\s+\\d+"}
        - AMOUNT_RANGE: {"min": 0, "max": 100, "transaction_type": "debit"}
        - REFERENCE_MATCH: {"pattern": "INV-\\d+"}
        - COMBINED: {"operator": "AND", "rules": [...]}
        """,
    )

    # Optional: Bank account filter
    bank_account_id: Mapped[Optional[uuid4]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("banking.bank_accounts.bank_account_id"),
        nullable=True,
        comment="If set, rule only applies to this bank account",
    )

    # Transaction type filter (credit/debit)
    applies_to_credits: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    applies_to_debits: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Action configuration
    action: Mapped[RuleAction] = mapped_column(
        Enum(RuleAction, name="rule_action", schema="banking"),
        nullable=False,
        default=RuleAction.CATEGORIZE,
    )

    # Target account for categorization
    target_account_id: Mapped[Optional[uuid4]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
    )

    # Optional tax code
    tax_code_id: Mapped[Optional[uuid4]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Split configuration (for SPLIT action)
    split_config: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="""
        Split configuration for SPLIT action:
        {
            "lines": [
                {"account_id": "uuid", "percentage": 60},
                {"account_id": "uuid", "percentage": 40}
            ]
        }
        """,
    )

    # Linked payee (optional)
    payee_id: Mapped[Optional[uuid4]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("banking.payee.payee_id"),
        nullable=True,
    )

    # Priority (higher = checked first)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    # Auto-apply or suggest
    auto_apply: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If true, automatically apply; if false, suggest for review",
    )

    # Confidence threshold (0-100)
    min_confidence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=80,
        comment="Minimum match confidence to apply/suggest this rule",
    )

    # Usage statistics
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_matched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Times user accepted the suggestion",
    )
    reject_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Times user rejected the suggestion",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by: Mapped[Optional[uuid4]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    @property
    def success_rate(self) -> float:
        """Calculate the success rate of this rule."""
        total = self.success_count + self.reject_count
        if total == 0:
            return 0.0
        return (self.success_count / total) * 100
