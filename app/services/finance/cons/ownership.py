"""
OwnershipService - Ownership interest management.

Manages ownership interests, control determination, and NCI calculations (IFRS 10).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.cons.legal_entity import ConsolidationMethod, LegalEntity
from app.models.finance.cons.ownership_interest import OwnershipInterest
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class OwnershipInput:
    """Input for creating ownership interest."""

    investor_entity_id: UUID
    investee_entity_id: UUID
    ownership_percentage: Decimal
    voting_rights_percentage: Decimal
    effective_from: date
    shares_held: Optional[Decimal] = None
    total_shares_outstanding: Optional[Decimal] = None
    investment_cost: Optional[Decimal] = None
    nci_at_acquisition: Optional[Decimal] = None
    nci_measurement_basis: Optional[str] = None


@dataclass
class EffectiveOwnershipResult:
    """Result of effective ownership calculation."""

    entity_id: UUID
    entity_code: str
    direct_ownership: Decimal
    effective_ownership: Decimal
    nci_percentage: Decimal
    ownership_chain: list[str]
    has_control: bool
    consolidation_method: ConsolidationMethod


@dataclass
class NCISummary:
    """NCI summary for the group."""

    entity_id: UUID
    entity_name: str
    nci_percentage: Decimal
    nci_at_acquisition: Optional[Decimal]
    has_control: bool


class OwnershipService(ListResponseMixin):
    """
    Service for ownership interest management.

    Handles:
    - Ownership interest recording
    - Effective ownership calculation through chains
    - Control determination (IFRS 10)
    - NCI percentage calculation
    """

    @staticmethod
    def create_ownership(
        db: Session,
        group_id: UUID,
        input: OwnershipInput,
    ) -> OwnershipInterest:
        """
        Create ownership interest record.

        Args:
            db: Database session
            group_id: Group identifier
            input: Ownership input data

        Returns:
            Created OwnershipInterest
        """
        grp_id = coerce_uuid(group_id)

        # Validate entities
        investor = db.get(LegalEntity, input.investor_entity_id)
        if not investor or investor.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Investor entity not found")

        investee = db.get(LegalEntity, input.investee_entity_id)
        if not investee or investee.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Investee entity not found")

        # Prevent self-ownership
        if input.investor_entity_id == input.investee_entity_id:
            raise HTTPException(
                status_code=400,
                detail="Entity cannot own itself",
            )

        # Validate percentages
        if not (Decimal("0") <= input.ownership_percentage <= Decimal("100")):
            raise HTTPException(
                status_code=400,
                detail="Ownership percentage must be between 0 and 100",
            )

        # Mark previous ownership as non-current
        existing = (
            db.query(OwnershipInterest)
            .filter(
                OwnershipInterest.investor_entity_id == input.investor_entity_id,
                OwnershipInterest.investee_entity_id == input.investee_entity_id,
                OwnershipInterest.is_current == True,
            )
            .first()
        )

        if existing:
            existing.is_current = False
            existing.effective_to = input.effective_from

        # Calculate effective ownership
        effective_ownership = (
            OwnershipService._calculate_effective_ownership_for_investor(
                db, grp_id, input.investor_entity_id
            )
        )
        combined_effective = (
            effective_ownership * input.ownership_percentage / Decimal("100")
        ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

        # NCI is 100% minus effective ownership by parent
        nci_percentage = Decimal("100") - input.ownership_percentage

        # Determine control
        has_control = input.voting_rights_percentage > Decimal("50")
        has_significant_influence = (
            Decimal("20") <= input.voting_rights_percentage <= Decimal("50")
        ) and not has_control
        has_joint_control = input.voting_rights_percentage == Decimal("50")

        ownership = OwnershipInterest(
            investor_entity_id=input.investor_entity_id,
            investee_entity_id=input.investee_entity_id,
            ownership_percentage=input.ownership_percentage,
            voting_rights_percentage=input.voting_rights_percentage,
            effective_ownership_percentage=combined_effective,
            effective_from=input.effective_from,
            shares_held=input.shares_held,
            total_shares_outstanding=input.total_shares_outstanding,
            investment_cost=input.investment_cost,
            nci_percentage=nci_percentage,
            nci_at_acquisition=input.nci_at_acquisition,
            nci_measurement_basis=input.nci_measurement_basis,
            has_control=has_control,
            has_significant_influence=has_significant_influence,
            has_joint_control=has_joint_control,
            is_current=True,
        )

        db.add(ownership)
        db.commit()
        db.refresh(ownership)

        # Update investee consolidation method based on control
        OwnershipService._update_consolidation_method(db, investee, ownership)

        return ownership

    @staticmethod
    def _calculate_effective_ownership_for_investor(
        db: Session,
        group_id: UUID,
        investor_entity_id: UUID,
    ) -> Decimal:
        """Calculate effective ownership the investor has from parent."""
        investor = db.get(LegalEntity, investor_entity_id)
        if not investor:
            return Decimal("0")

        # If this is the parent, it has 100% ownership
        if investor.parent_entity_id is None:
            return Decimal("100")

        # Find ownership from its parent
        ownership = (
            db.query(OwnershipInterest)
            .filter(
                OwnershipInterest.investee_entity_id == investor_entity_id,
                OwnershipInterest.is_current == True,
            )
            .first()
        )

        if ownership:
            return ownership.effective_ownership_percentage
        return Decimal("100")

    @staticmethod
    def _update_consolidation_method(
        db: Session,
        investee: LegalEntity,
        ownership: OwnershipInterest,
    ) -> None:
        """Update consolidation method based on control determination."""
        if ownership.has_control:
            investee.consolidation_method = ConsolidationMethod.FULL
        elif ownership.has_joint_control:
            investee.consolidation_method = ConsolidationMethod.PROPORTIONATE
        elif ownership.has_significant_influence:
            investee.consolidation_method = ConsolidationMethod.EQUITY
        else:
            investee.consolidation_method = ConsolidationMethod.NOT_CONSOLIDATED

        db.commit()

    @staticmethod
    def calculate_effective_ownership(
        db: Session,
        group_id: UUID,
        as_of_date: Optional[date] = None,
    ) -> list[EffectiveOwnershipResult]:
        """
        Calculate effective ownership for all entities in group.

        Args:
            db: Database session
            group_id: Group identifier
            as_of_date: Point-in-time view

        Returns:
            List of effective ownership results
        """
        grp_id = coerce_uuid(group_id)

        # Get all entities
        entities = (
            db.query(LegalEntity)
            .filter(
                LegalEntity.group_id == grp_id,
                LegalEntity.is_active == True,
            )
            .all()
        )

        # Get current ownership interests
        ownership_query = db.query(OwnershipInterest).filter(
            OwnershipInterest.is_current == True,
        )
        if as_of_date:
            ownership_query = ownership_query.filter(
                OwnershipInterest.effective_from <= as_of_date,
                (OwnershipInterest.effective_to > as_of_date)
                | (OwnershipInterest.effective_to.is_(None)),
            )

        ownerships = ownership_query.all()

        # Build ownership map: investee -> (investor, percentage)
        ownership_map: dict[UUID, tuple[UUID, Decimal, bool]] = {}
        for o in ownerships:
            ownership_map[o.investee_entity_id] = (
                o.investor_entity_id,
                o.ownership_percentage,
                o.has_control,
            )

        results = []
        entity_map = {e.entity_id: e for e in entities}

        for entity in entities:
            # Calculate effective ownership by tracing the chain
            effective, chain = OwnershipService._trace_ownership_chain(
                entity.entity_id, entity_map, ownership_map
            )

            # Get direct ownership (from immediate parent)
            direct_ownership = Decimal("100")
            has_control = True
            if entity.entity_id in ownership_map:
                _, direct_ownership, has_control = ownership_map[entity.entity_id]

            nci = Decimal("100") - effective

            results.append(
                EffectiveOwnershipResult(
                    entity_id=entity.entity_id,
                    entity_code=entity.entity_code,
                    direct_ownership=direct_ownership,
                    effective_ownership=effective,
                    nci_percentage=nci,
                    ownership_chain=chain,
                    has_control=has_control,
                    consolidation_method=entity.consolidation_method,
                )
            )

        return results

    @staticmethod
    def _trace_ownership_chain(
        entity_id: UUID,
        entity_map: dict[UUID, LegalEntity],
        ownership_map: dict[UUID, tuple[UUID, Decimal, bool]],
    ) -> tuple[Decimal, list[str]]:
        """Trace ownership chain from entity to parent."""
        chain = []
        current_id = entity_id
        effective_ownership = Decimal("100")

        visited = set()
        while current_id in ownership_map and current_id not in visited:
            visited.add(current_id)
            investor_id, percentage, _ = ownership_map[current_id]
            effective_ownership = (
                effective_ownership * percentage / Decimal("100")
            ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

            if current_id in entity_map:
                chain.append(f"{entity_map[current_id].entity_code}({percentage}%)")

            current_id = investor_id

        # Add the ultimate parent
        if current_id in entity_map and current_id not in visited:
            chain.append(f"{entity_map[current_id].entity_code}(100%)")

        chain.reverse()
        return effective_ownership, chain

    @staticmethod
    def get_nci_summary(
        db: Session,
        group_id: UUID,
    ) -> list[NCISummary]:
        """
        Get NCI summary for all subsidiaries.

        Args:
            db: Database session
            group_id: Group identifier

        Returns:
            List of NCI summaries
        """
        grp_id = coerce_uuid(group_id)

        # Get subsidiaries with current ownership
        subsidiaries = (
            db.query(LegalEntity)
            .filter(
                LegalEntity.group_id == grp_id,
                LegalEntity.is_active == True,
                LegalEntity.consolidation_method == ConsolidationMethod.FULL,
            )
            .all()
        )

        results = []
        for sub in subsidiaries:
            ownership = (
                db.query(OwnershipInterest)
                .filter(
                    OwnershipInterest.investee_entity_id == sub.entity_id,
                    OwnershipInterest.is_current == True,
                )
                .first()
            )

            if ownership and ownership.nci_percentage > 0:
                results.append(
                    NCISummary(
                        entity_id=sub.entity_id,
                        entity_name=sub.entity_name,
                        nci_percentage=ownership.nci_percentage,
                        nci_at_acquisition=ownership.nci_at_acquisition,
                        has_control=ownership.has_control,
                    )
                )

        return results

    @staticmethod
    def update_ownership_percentage(
        db: Session,
        group_id: UUID,
        interest_id: UUID,
        new_percentage: Decimal,
        new_voting_rights: Decimal,
        effective_date: date,
    ) -> OwnershipInterest:
        """
        Update ownership percentage (step acquisition/disposal).

        Args:
            db: Database session
            group_id: Group identifier
            interest_id: Existing interest to update
            new_percentage: New ownership percentage
            new_voting_rights: New voting rights percentage
            effective_date: Effective date of change

        Returns:
            New OwnershipInterest record
        """
        grp_id = coerce_uuid(group_id)
        int_id = coerce_uuid(interest_id)

        existing = db.get(OwnershipInterest, int_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Ownership interest not found")

        # Validate the investor entity belongs to the group
        investor = db.get(LegalEntity, existing.investor_entity_id)
        if not investor or investor.group_id != grp_id:
            raise HTTPException(status_code=404, detail="Investor entity not in group")

        # Create new ownership input
        new_input = OwnershipInput(
            investor_entity_id=existing.investor_entity_id,
            investee_entity_id=existing.investee_entity_id,
            ownership_percentage=new_percentage,
            voting_rights_percentage=new_voting_rights,
            effective_from=effective_date,
            shares_held=existing.shares_held,
            total_shares_outstanding=existing.total_shares_outstanding,
            investment_cost=existing.investment_cost,
        )

        return OwnershipService.create_ownership(db, group_id, new_input)

    @staticmethod
    def get(
        db: Session,
        interest_id: str,
    ) -> OwnershipInterest:
        """Get an ownership interest by ID."""
        interest = db.get(OwnershipInterest, coerce_uuid(interest_id))
        if not interest:
            raise HTTPException(status_code=404, detail="Ownership interest not found")
        return interest

    @staticmethod
    def list(
        db: Session,
        investor_entity_id: Optional[str] = None,
        investee_entity_id: Optional[str] = None,
        is_current: Optional[bool] = None,
        has_control: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[OwnershipInterest]:
        """List ownership interests with optional filters."""
        query = db.query(OwnershipInterest)

        if investor_entity_id:
            query = query.filter(
                OwnershipInterest.investor_entity_id == coerce_uuid(investor_entity_id)
            )

        if investee_entity_id:
            query = query.filter(
                OwnershipInterest.investee_entity_id == coerce_uuid(investee_entity_id)
            )

        if is_current is not None:
            query = query.filter(OwnershipInterest.is_current == is_current)

        if has_control is not None:
            query = query.filter(OwnershipInterest.has_control == has_control)

        query = query.order_by(OwnershipInterest.effective_from.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
ownership_service = OwnershipService()
