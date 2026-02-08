"""
Custom Field Definition Model.

Allows organizations to define custom fields on entities.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CustomFieldEntityType(str, enum.Enum):
    """Entity types that support custom fields."""

    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    INVOICE = "INVOICE"
    BILL = "BILL"
    EXPENSE = "EXPENSE"
    QUOTE = "QUOTE"
    SALES_ORDER = "SALES_ORDER"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    ITEM = "ITEM"
    PROJECT = "PROJECT"
    ASSET = "ASSET"
    JOURNAL = "JOURNAL"
    PAYMENT = "PAYMENT"


class CustomFieldType(str, enum.Enum):
    """Data types for custom fields."""

    TEXT = "TEXT"
    TEXTAREA = "TEXTAREA"
    NUMBER = "NUMBER"
    DECIMAL = "DECIMAL"
    DATE = "DATE"
    DATETIME = "DATETIME"
    BOOLEAN = "BOOLEAN"
    SELECT = "SELECT"
    MULTISELECT = "MULTISELECT"
    EMAIL = "EMAIL"
    URL = "URL"
    PHONE = "PHONE"
    CURRENCY = "CURRENCY"


class CustomFieldDefinition(Base):
    """
    Custom field definition.

    Defines a custom field that can be added to entities.
    Field values are stored in the entity's custom_fields JSONB column.
    """

    __tablename__ = "custom_field_definition"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "entity_type", "field_code", name="uq_custom_field_code"
        ),
        Index("idx_custom_field_org", "organization_id"),
        Index("idx_custom_field_entity", "entity_type"),
        Index("idx_custom_field_active", "is_active"),
        {"schema": "automation"},
    )

    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    # Field identification
    entity_type: Mapped[CustomFieldEntityType] = mapped_column(
        Enum(CustomFieldEntityType, name="custom_field_entity_type"),
        nullable=False,
    )
    field_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Internal code used as key in custom_fields JSON",
    )
    field_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name for the field",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Field type and configuration
    field_type: Mapped[CustomFieldType] = mapped_column(
        Enum(CustomFieldType, name="custom_field_type"),
        nullable=False,
    )
    field_options: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Options for SELECT/MULTISELECT: [{value, label}]",
    )

    # Validation
    is_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    default_value: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    validation_regex: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Regex pattern for validation",
    )
    validation_message: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Error message when validation fails",
    )
    min_value: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Minimum value for NUMBER/DECIMAL/DATE",
    )
    max_value: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Maximum value for NUMBER/DECIMAL/DATE",
    )
    max_length: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Max length for TEXT/TEXTAREA",
    )

    # Display settings
    display_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    section_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Group fields into sections",
    )
    placeholder: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    help_text: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    css_class: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # Visibility
    show_in_list: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Show in list/table views",
    )
    show_in_form: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Show in create/edit forms",
    )
    show_in_detail: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Show in detail views",
    )
    show_in_print: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Show in print/PDF output",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Audit
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    def validate_value(self, value: Any) -> tuple[bool, str | None]:
        """Validate a value against this field's rules."""
        import re
        from decimal import Decimal, InvalidOperation

        # Required check
        if self.is_required and (value is None or value == ""):
            return False, f"{self.field_name} is required"

        if value is None or value == "":
            return True, None

        # Type-specific validation
        if self.field_type == CustomFieldType.NUMBER:
            try:
                int(value)
            except (ValueError, TypeError):
                return False, f"{self.field_name} must be a number"

        elif self.field_type == CustomFieldType.DECIMAL:
            try:
                Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError):
                return False, f"{self.field_name} must be a decimal number"

        elif self.field_type == CustomFieldType.EMAIL:
            email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
            if not re.match(email_pattern, str(value)):
                return False, f"{self.field_name} must be a valid email address"

        elif self.field_type == CustomFieldType.SELECT:
            if self.field_options:
                valid_values = [
                    opt.get("value") for opt in self.field_options.get("options", [])
                ]
                if str(value) not in valid_values:
                    return (
                        False,
                        f"{self.field_name} must be one of the allowed options",
                    )

        # Regex validation
        if self.validation_regex:
            if not re.match(self.validation_regex, str(value)):
                return (
                    False,
                    self.validation_message or f"{self.field_name} format is invalid",
                )

        # Length validation
        if self.max_length and len(str(value)) > self.max_length:
            return (
                False,
                f"{self.field_name} must be at most {self.max_length} characters",
            )

        return True, None
