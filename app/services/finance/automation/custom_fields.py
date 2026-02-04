"""
Custom Fields Service.

Manages custom field definitions and validates field values.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.finance.automation import (
    CustomFieldDefinition,
    CustomFieldEntityType,
    CustomFieldType,
)

logger = logging.getLogger(__name__)


@dataclass
class CustomFieldInput:
    """Input for creating a custom field definition."""

    entity_type: CustomFieldEntityType
    field_code: str
    field_name: str
    field_type: CustomFieldType
    description: Optional[str] = None
    field_options: Optional[Dict[str, Any]] = None
    is_required: bool = False
    default_value: Optional[str] = None
    validation_regex: Optional[str] = None
    validation_message: Optional[str] = None
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    max_length: Optional[int] = None
    display_order: int = 0
    section_name: Optional[str] = None
    placeholder: Optional[str] = None
    help_text: Optional[str] = None
    show_in_list: bool = False
    show_in_form: bool = True
    show_in_detail: bool = True
    show_in_print: bool = False


class CustomFieldsService:
    """Service for managing custom field definitions."""

    def create_field(
        self,
        db: Session,
        organization_id: UUID,
        input_data: CustomFieldInput,
        created_by: UUID,
    ) -> CustomFieldDefinition:
        """Create a new custom field definition."""
        # Check for duplicate field code
        existing = db.execute(
            select(CustomFieldDefinition).where(
                and_(
                    CustomFieldDefinition.organization_id == organization_id,
                    CustomFieldDefinition.entity_type == input_data.entity_type,
                    CustomFieldDefinition.field_code == input_data.field_code,
                )
            )
        ).scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Field with code '{input_data.field_code}' already exists for {input_data.entity_type.value}",
            )

        # Validate field code format
        if not input_data.field_code.isidentifier():
            raise HTTPException(
                status_code=400,
                detail="Field code must be a valid identifier (letters, numbers, underscores)",
            )

        field = CustomFieldDefinition(
            organization_id=organization_id,
            entity_type=input_data.entity_type,
            field_code=input_data.field_code,
            field_name=input_data.field_name,
            description=input_data.description,
            field_type=input_data.field_type,
            field_options=input_data.field_options,
            is_required=input_data.is_required,
            default_value=input_data.default_value,
            validation_regex=input_data.validation_regex,
            validation_message=input_data.validation_message,
            min_value=input_data.min_value,
            max_value=input_data.max_value,
            max_length=input_data.max_length,
            display_order=input_data.display_order,
            section_name=input_data.section_name,
            placeholder=input_data.placeholder,
            help_text=input_data.help_text,
            show_in_list=input_data.show_in_list,
            show_in_form=input_data.show_in_form,
            show_in_detail=input_data.show_in_detail,
            show_in_print=input_data.show_in_print,
            created_by=created_by,
        )

        db.add(field)
        db.flush()
        return field

    def get(self, db: Session, field_id: UUID) -> Optional[CustomFieldDefinition]:
        """Get a field definition by ID."""
        return db.get(CustomFieldDefinition, field_id)

    def get_by_code(
        self,
        db: Session,
        organization_id: UUID,
        entity_type: CustomFieldEntityType,
        field_code: str,
    ) -> Optional[CustomFieldDefinition]:
        """Get a field definition by code."""
        return db.execute(
            select(CustomFieldDefinition).where(
                and_(
                    CustomFieldDefinition.organization_id == organization_id,
                    CustomFieldDefinition.entity_type == entity_type,
                    CustomFieldDefinition.field_code == field_code,
                )
            )
        ).scalar_one_or_none()

    def list_for_entity(
        self,
        db: Session,
        organization_id: UUID,
        entity_type: CustomFieldEntityType,
        is_active: bool = True,
    ) -> List[CustomFieldDefinition]:
        """List all custom fields for an entity type."""
        query = select(CustomFieldDefinition).where(
            and_(
                CustomFieldDefinition.organization_id == organization_id,
                CustomFieldDefinition.entity_type == entity_type,
            )
        )

        if is_active:
            query = query.where(CustomFieldDefinition.is_active == True)

        query = query.order_by(
            CustomFieldDefinition.section_name,
            CustomFieldDefinition.display_order,
            CustomFieldDefinition.field_name,
        )

        return list(db.execute(query).scalars().all())

    def list_all(
        self,
        db: Session,
        organization_id: UUID,
        is_active: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[CustomFieldDefinition]:
        """List all custom fields."""
        query = select(CustomFieldDefinition).where(
            CustomFieldDefinition.organization_id == organization_id
        )

        if is_active is not None:
            query = query.where(CustomFieldDefinition.is_active == is_active)

        query = query.order_by(
            CustomFieldDefinition.entity_type,
            CustomFieldDefinition.display_order,
        )
        query = query.offset(offset).limit(limit)

        return list(db.execute(query).scalars().all())

    def validate_custom_fields(
        self,
        db: Session,
        organization_id: UUID,
        entity_type: CustomFieldEntityType,
        field_values: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        """
        Validate custom field values against their definitions.

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        definitions = self.list_for_entity(db, organization_id, entity_type)
        errors = []

        definitions_by_code = {d.field_code: d for d in definitions}

        # Check required fields
        for defn in definitions:
            if defn.is_required:
                value = field_values.get(defn.field_code)
                if value is None or value == "":
                    errors.append(f"{defn.field_name} is required")

        # Validate provided values
        for field_code, value in field_values.items():
            defn_for_field = definitions_by_code.get(field_code)
            if not defn_for_field:
                # Unknown field - could ignore or error
                continue

            is_valid, error = defn_for_field.validate_value(value)
            if not is_valid and error:
                errors.append(error)

        return len(errors) == 0, errors

    def merge_with_defaults(
        self,
        db: Session,
        organization_id: UUID,
        entity_type: CustomFieldEntityType,
        field_values: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Merge provided values with defaults from field definitions.

        Returns:
            Dictionary with field codes as keys and values
        """
        definitions = self.list_for_entity(db, organization_id, entity_type)
        result = {}

        for defn in definitions:
            if defn.default_value is not None:
                result[defn.field_code] = defn.default_value

        if field_values:
            result.update(field_values)

        return result

    def get_form_schema(
        self,
        db: Session,
        organization_id: UUID,
        entity_type: CustomFieldEntityType,
    ) -> List[Dict[str, Any]]:
        """
        Get field definitions formatted for form rendering.

        Returns:
            List of field schemas suitable for dynamic form generation
        """
        definitions = self.list_for_entity(db, organization_id, entity_type)

        # Group by section
        sections: Dict[str, List[Dict[str, Any]]] = {}

        for defn in definitions:
            if not defn.show_in_form:
                continue

            section = defn.section_name or "Additional Information"
            if section not in sections:
                sections[section] = []

            field_schema = {
                "field_id": str(defn.field_id),
                "field_code": defn.field_code,
                "field_name": defn.field_name,
                "field_type": defn.field_type.value,
                "is_required": defn.is_required,
                "default_value": defn.default_value,
                "placeholder": defn.placeholder,
                "help_text": defn.help_text,
                "css_class": defn.css_class,
                "display_order": defn.display_order,
            }

            if defn.field_options:
                field_schema["options"] = defn.field_options.get("options", [])

            if defn.max_length:
                field_schema["max_length"] = defn.max_length
            if defn.min_value:
                field_schema["min_value"] = defn.min_value
            if defn.max_value:
                field_schema["max_value"] = defn.max_value
            if defn.validation_regex:
                field_schema["pattern"] = defn.validation_regex
                field_schema["pattern_message"] = defn.validation_message

            sections[section].append(field_schema)

        # Convert to list format
        result: List[Dict[str, Any]] = []
        for section_name, fields in sections.items():
            field_list: List[Dict[str, Any]] = fields
            result.append(
                {
                    "section_name": section_name,
                    "fields": sorted(field_list, key=lambda f: f["display_order"]),
                }
            )

        return result

    def update_field(
        self,
        db: Session,
        field_id: UUID,
        updates: Dict[str, Any],
        updated_by: UUID,
    ) -> CustomFieldDefinition:
        """Update a custom field definition."""
        field = db.get(CustomFieldDefinition, field_id)
        if not field:
            raise HTTPException(status_code=404, detail="Custom field not found")

        # Don't allow changing entity_type or field_code
        updates.pop("entity_type", None)
        updates.pop("field_code", None)

        for key, value in updates.items():
            if hasattr(field, key):
                setattr(field, key, value)

        field.updated_by = updated_by
        db.flush()
        return field

    def delete(self, db: Session, field_id: UUID) -> bool:
        """Delete a custom field definition."""
        field = db.get(CustomFieldDefinition, field_id)
        if not field:
            return False

        # Soft delete - set inactive
        field.is_active = False
        db.flush()
        return True

    def hard_delete(self, db: Session, field_id: UUID) -> bool:
        """Permanently delete a custom field definition."""
        field = db.get(CustomFieldDefinition, field_id)
        if not field:
            return False

        db.delete(field)
        db.flush()
        return True


# Singleton instance
custom_fields_service = CustomFieldsService()
