"""
LegalEntityService - Group structure management.

Manages legal entities within a consolidation group (IFRS 10).
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.cons.legal_entity import (
    ConsolidationMethod,
    EntityType,
    LegalEntity,
)
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class LegalEntityInput:
    """Input for creating a legal entity."""

    entity_code: str
    entity_name: str
    legal_name: str
    entity_type: EntityType
    consolidation_method: ConsolidationMethod
    country_code: str
    functional_currency_code: str
    reporting_currency_code: str
    parent_entity_id: UUID | None = None
    organization_id: UUID | None = None
    is_consolidating_entity: bool = False
    description: str | None = None
    incorporation_date: date | None = None
    registration_number: str | None = None
    tax_id: str | None = None
    fiscal_year_end_month: int = 12
    fiscal_year_end_day: int = 31
    acquisition_date: date | None = None
    acquisition_cost: Decimal | None = None
    goodwill_at_acquisition: Decimal | None = None
    address: dict | None = None


@dataclass
class GroupStructure:
    """Group structure hierarchy."""

    entity: LegalEntity
    children: list[GroupStructure]
    level: int
    effective_ownership: Decimal


class LegalEntityService(ListResponseMixin):
    """
    Service for legal entity management.

    Handles:
    - Legal entity CRUD
    - Group structure hierarchy
    - Consolidation method assignment
    - Goodwill tracking
    """

    @staticmethod
    def create_entity(
        db: Session,
        group_id: UUID,
        input: LegalEntityInput,
    ) -> LegalEntity:
        """
        Create a new legal entity.

        Args:
            db: Database session
            group_id: Group identifier
            input: Entity input data

        Returns:
            Created LegalEntity
        """
        grp_id = coerce_uuid(group_id)

        # Check for duplicate entity code
        existing = db.scalar(
            select(LegalEntity).where(
                LegalEntity.group_id == grp_id,
                LegalEntity.entity_code == input.entity_code,
            )
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Entity code {input.entity_code} already exists in group",
            )

        # Validate parent if specified
        if input.parent_entity_id:
            parent = db.get(LegalEntity, input.parent_entity_id)
            if not parent or parent.group_id != grp_id:
                raise HTTPException(status_code=404, detail="Parent entity not found")

        # Validate consolidation method based on entity type
        if input.entity_type == EntityType.PARENT:
            if input.consolidation_method != ConsolidationMethod.FULL:
                raise HTTPException(
                    status_code=400,
                    detail="Parent entity must use FULL consolidation method",
                )
            if input.parent_entity_id:
                raise HTTPException(
                    status_code=400,
                    detail="Parent entity cannot have a parent",
                )

        entity = LegalEntity(
            group_id=grp_id,
            organization_id=input.organization_id,
            entity_code=input.entity_code,
            entity_name=input.entity_name,
            legal_name=input.legal_name,
            description=input.description,
            entity_type=input.entity_type,
            parent_entity_id=input.parent_entity_id,
            consolidation_method=input.consolidation_method,
            is_consolidating_entity=input.is_consolidating_entity,
            country_code=input.country_code,
            incorporation_date=input.incorporation_date,
            registration_number=input.registration_number,
            tax_id=input.tax_id,
            functional_currency_code=input.functional_currency_code,
            reporting_currency_code=input.reporting_currency_code,
            fiscal_year_end_month=input.fiscal_year_end_month,
            fiscal_year_end_day=input.fiscal_year_end_day,
            acquisition_date=input.acquisition_date,
            acquisition_cost=input.acquisition_cost,
            goodwill_at_acquisition=input.goodwill_at_acquisition,
            address=input.address,
        )

        db.add(entity)
        db.commit()
        db.refresh(entity)

        return entity

    @staticmethod
    def update_consolidation_method(
        db: Session,
        group_id: UUID,
        entity_id: UUID,
        new_method: ConsolidationMethod,
    ) -> LegalEntity:
        """
        Update consolidation method for an entity.

        Args:
            db: Database session
            group_id: Group identifier
            entity_id: Entity to update
            new_method: New consolidation method

        Returns:
            Updated LegalEntity
        """
        grp_id = coerce_uuid(group_id)
        ent_id = coerce_uuid(entity_id)

        entity = db.get(LegalEntity, ent_id)
        if not entity or entity.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Validate method for entity type
        if (
            entity.entity_type == EntityType.PARENT
            and new_method != ConsolidationMethod.FULL
        ):
            raise HTTPException(
                status_code=400,
                detail="Parent entity must use FULL consolidation method",
            )

        if (
            entity.entity_type == EntityType.ASSOCIATE
            and new_method != ConsolidationMethod.EQUITY
        ):
            raise HTTPException(
                status_code=400,
                detail="Associate entities must use EQUITY method",
            )

        entity.consolidation_method = new_method
        db.commit()
        db.refresh(entity)

        return entity

    @staticmethod
    def record_disposal(
        db: Session,
        group_id: UUID,
        entity_id: UUID,
        disposal_date: date,
    ) -> LegalEntity:
        """
        Record disposal of an entity.

        Args:
            db: Database session
            group_id: Group identifier
            entity_id: Entity being disposed
            disposal_date: Date of disposal

        Returns:
            Updated LegalEntity
        """
        grp_id = coerce_uuid(group_id)
        ent_id = coerce_uuid(entity_id)

        entity = db.get(LegalEntity, ent_id)
        if not entity or entity.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Entity not found")

        if entity.entity_type == EntityType.PARENT:
            raise HTTPException(
                status_code=400,
                detail="Cannot dispose the parent entity",
            )

        entity.disposal_date = disposal_date
        entity.is_active = False
        entity.consolidation_method = ConsolidationMethod.NOT_CONSOLIDATED

        db.commit()
        db.refresh(entity)

        return entity

    @staticmethod
    def record_goodwill_impairment(
        db: Session,
        group_id: UUID,
        entity_id: UUID,
        impairment_amount: Decimal,
    ) -> LegalEntity:
        """
        Record goodwill impairment for an entity.

        Args:
            db: Database session
            group_id: Group identifier
            entity_id: Entity with goodwill
            impairment_amount: Impairment amount

        Returns:
            Updated LegalEntity
        """
        grp_id = coerce_uuid(group_id)
        ent_id = coerce_uuid(entity_id)

        entity = db.get(LegalEntity, ent_id)
        if not entity or entity.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Entity not found")

        if not entity.goodwill_at_acquisition:
            raise HTTPException(
                status_code=400,
                detail="Entity has no goodwill recorded",
            )

        current_goodwill = (
            entity.goodwill_at_acquisition - entity.accumulated_goodwill_impairment
        )
        if impairment_amount > current_goodwill:
            raise HTTPException(
                status_code=400,
                detail=f"Impairment amount {impairment_amount} exceeds carrying value {current_goodwill}",
            )

        entity.accumulated_goodwill_impairment += impairment_amount
        db.commit()
        db.refresh(entity)

        return entity

    @staticmethod
    def get_group_structure(
        db: Session,
        group_id: UUID,
        as_of_date: date | None = None,
    ) -> list[GroupStructure]:
        """
        Get hierarchical group structure.

        Args:
            db: Database session
            group_id: Group identifier
            as_of_date: Point-in-time view (defaults to current)

        Returns:
            List of GroupStructure starting from parent
        """
        grp_id = coerce_uuid(group_id)

        # Get all active entities
        stmt = select(LegalEntity).where(
            LegalEntity.group_id == grp_id,
            LegalEntity.is_active == True,
        )

        if as_of_date:
            stmt = stmt.where(
                (LegalEntity.acquisition_date <= as_of_date)
                | (LegalEntity.acquisition_date.is_(None)),
                (LegalEntity.disposal_date > as_of_date)
                | (LegalEntity.disposal_date.is_(None)),
            )

        entities = db.scalars(stmt).all()

        # Build tree structure
        {e.entity_id: e for e in entities}
        children_map: dict[UUID | None, list[LegalEntity]] = {}

        for entity in entities:
            parent_id = entity.parent_entity_id
            if parent_id not in children_map:
                children_map[parent_id] = []
            children_map[parent_id].append(entity)

        def build_tree(parent_id: UUID | None, level: int) -> list[GroupStructure]:
            result = []
            for entity in children_map.get(parent_id, []):
                children = build_tree(entity.entity_id, level + 1)
                result.append(
                    GroupStructure(
                        entity=entity,
                        children=children,
                        level=level,
                        effective_ownership=Decimal("100")
                        if level == 0
                        else Decimal("0"),
                    )
                )
            return result

        return build_tree(None, 0)

    @staticmethod
    def get_entities_for_consolidation(
        db: Session,
        group_id: UUID,
        consolidation_method: ConsolidationMethod | None = None,
    ) -> builtins.list[LegalEntity]:
        """
        Get entities to include in consolidation.

        Args:
            db: Database session
            group_id: Group identifier
            consolidation_method: Filter by method

        Returns:
            List of entities for consolidation
        """
        grp_id = coerce_uuid(group_id)

        stmt = select(LegalEntity).where(
            LegalEntity.group_id == grp_id,
            LegalEntity.is_active == True,
            LegalEntity.consolidation_method != ConsolidationMethod.NOT_CONSOLIDATED,
        )

        if consolidation_method:
            stmt = stmt.where(LegalEntity.consolidation_method == consolidation_method)

        return db.scalars(stmt.order_by(LegalEntity.entity_code)).all()

    @staticmethod
    def get_carrying_value_of_goodwill(
        db: Session,
        group_id: UUID,
        entity_id: UUID | None = None,
    ) -> Decimal:
        """
        Get carrying value of goodwill.

        Args:
            db: Database session
            group_id: Group identifier
            entity_id: Specific entity (optional)

        Returns:
            Carrying value of goodwill
        """
        grp_id = coerce_uuid(group_id)

        stmt = select(LegalEntity).where(
            LegalEntity.group_id == grp_id,
            LegalEntity.is_active == True,
            LegalEntity.goodwill_at_acquisition.isnot(None),
        )

        if entity_id:
            stmt = stmt.where(LegalEntity.entity_id == coerce_uuid(entity_id))

        entities = db.scalars(stmt).all()

        total_goodwill = Decimal("0")
        for entity in entities:
            if entity.goodwill_at_acquisition:
                carrying_value = (
                    entity.goodwill_at_acquisition
                    - entity.accumulated_goodwill_impairment
                )
                total_goodwill += carrying_value

        return total_goodwill

    @staticmethod
    def get(
        db: Session,
        entity_id: str,
        group_id: UUID | None = None,
    ) -> LegalEntity:
        """Get a legal entity by ID."""
        entity = db.get(LegalEntity, coerce_uuid(entity_id))
        if not entity:
            raise HTTPException(status_code=404, detail="Legal entity not found")
        if group_id is not None and entity.group_id != coerce_uuid(group_id):
            raise HTTPException(status_code=404, detail="Legal entity not found")
        return entity

    @staticmethod
    def list(
        db: Session,
        group_id: str | None = None,
        entity_type: EntityType | None = None,
        consolidation_method: ConsolidationMethod | None = None,
        is_active: bool | None = None,
        country_code: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[LegalEntity]:
        """List legal entities with optional filters."""
        stmt = select(LegalEntity)

        if group_id:
            stmt = stmt.where(LegalEntity.group_id == coerce_uuid(group_id))

        if entity_type:
            stmt = stmt.where(LegalEntity.entity_type == entity_type)

        if consolidation_method:
            stmt = stmt.where(LegalEntity.consolidation_method == consolidation_method)

        if is_active is not None:
            stmt = stmt.where(LegalEntity.is_active == is_active)

        if country_code:
            stmt = stmt.where(LegalEntity.country_code == country_code)

        stmt = stmt.order_by(LegalEntity.entity_code).limit(limit).offset(offset)
        return db.scalars(stmt).all()


# Module-level singleton instance
legal_entity_service = LegalEntityService()
