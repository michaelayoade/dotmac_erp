"""
AssetRevaluationService - IAS 16 asset revaluation management.

Handles fair value revaluations under the revaluation model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.finance.fa.asset import Asset, AssetStatus
from app.models.finance.fa.asset_category import AssetCategory
from app.models.finance.fa.asset_revaluation import AssetRevaluation
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class RevaluationInput:
    """Input for creating an asset revaluation."""

    asset_id: UUID
    fiscal_period_id: UUID
    revaluation_date: date
    fair_value: Decimal
    valuation_method: str
    valuer_name: Optional[str] = None
    valuer_reference: Optional[str] = None
    valuation_basis: Optional[str] = None


class AssetRevaluationService(ListResponseMixin):
    """
    Service for IAS 16 asset revaluations.

    Manages fair value revaluations under the revaluation model,
    tracking surplus/deficit recognition per IFRS.
    """

    @staticmethod
    def create_revaluation(
        db: Session,
        organization_id: UUID,
        input: RevaluationInput,
        created_by_user_id: UUID,
    ) -> AssetRevaluation:
        """
        Create a new asset revaluation.

        Calculates surplus/deficit and determines recognition
        (equity vs P&L) based on IAS 16 requirements.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Revaluation input data
            created_by_user_id: User creating the revaluation

        Returns:
            Created AssetRevaluation
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        ast_id = coerce_uuid(input.asset_id)

        # Load asset
        asset = db.get(Asset, ast_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        if asset.status != AssetStatus.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot revalue asset with status '{asset.status.value}'",
            )

        # Check category allows revaluation
        category = db.get(AssetCategory, asset.category_id)
        if not category:
            raise HTTPException(status_code=404, detail="Asset category not found")

        if not category.revaluation_model_allowed:
            raise HTTPException(
                status_code=400,
                detail="Revaluation model not allowed for this asset category",
            )

        # Current carrying amount
        carrying_amount_before = asset.net_book_value
        accumulated_dep_before = asset.accumulated_depreciation

        # Calculate surplus/deficit
        surplus_or_deficit = input.fair_value - carrying_amount_before

        # Determine recognition per IAS 16
        surplus_to_equity = Decimal("0")
        deficit_to_pl = Decimal("0")
        prior_deficit_reversed = Decimal("0")
        prior_surplus_reversed = Decimal("0")

        # Get cumulative revaluation history
        prior_revals = (
            db.query(AssetRevaluation)
            .filter(
                and_(
                    AssetRevaluation.asset_id == ast_id,
                    AssetRevaluation.revaluation_date < input.revaluation_date,
                )
            )
            .order_by(AssetRevaluation.revaluation_date.desc())
            .all()
        )

        # Calculate cumulative prior surplus in equity
        cumulative_surplus = Decimal("0")
        cumulative_deficit_in_pl = Decimal("0")

        for pr in prior_revals:
            cumulative_surplus += pr.surplus_to_equity
            cumulative_deficit_in_pl += pr.deficit_to_pl

        if surplus_or_deficit > 0:
            # Revaluation increase
            # First, reverse any prior deficit recognized in P&L
            if cumulative_deficit_in_pl > 0:
                prior_deficit_reversed = min(surplus_or_deficit, cumulative_deficit_in_pl)
                surplus_to_equity = surplus_or_deficit - prior_deficit_reversed
            else:
                surplus_to_equity = surplus_or_deficit

        elif surplus_or_deficit < 0:
            # Revaluation decrease
            deficit = abs(surplus_or_deficit)

            # First, reduce any prior surplus in equity
            if cumulative_surplus > 0:
                prior_surplus_reversed = min(deficit, cumulative_surplus)
                deficit_to_pl = deficit - prior_surplus_reversed
            else:
                deficit_to_pl = deficit

        # After revaluation, adjust accumulated depreciation
        # Common approach: eliminate accumulated dep and set asset to fair value
        accumulated_dep_after = Decimal("0")
        carrying_amount_after = input.fair_value

        revaluation = AssetRevaluation(
            asset_id=ast_id,
            fiscal_period_id=coerce_uuid(input.fiscal_period_id),
            revaluation_date=input.revaluation_date,
            carrying_amount_before=carrying_amount_before,
            accumulated_depreciation_before=accumulated_dep_before,
            fair_value=input.fair_value,
            revaluation_surplus_or_deficit=surplus_or_deficit,
            carrying_amount_after=carrying_amount_after,
            accumulated_depreciation_after=accumulated_dep_after,
            valuation_method=input.valuation_method,
            valuer_name=input.valuer_name,
            valuer_reference=input.valuer_reference,
            valuation_basis=input.valuation_basis,
            surplus_to_equity=surplus_to_equity,
            deficit_to_pl=deficit_to_pl,
            prior_deficit_reversed=prior_deficit_reversed,
            prior_surplus_reversed=prior_surplus_reversed,
            created_by_user_id=user_id,
        )

        db.add(revaluation)
        db.commit()
        db.refresh(revaluation)

        return revaluation

    @staticmethod
    def approve_revaluation(
        db: Session,
        organization_id: UUID,
        revaluation_id: UUID,
        approved_by_user_id: UUID,
    ) -> AssetRevaluation:
        """
        Approve a revaluation and update asset values.

        Args:
            db: Database session
            organization_id: Organization scope
            revaluation_id: Revaluation to approve
            approved_by_user_id: User approving

        Returns:
            Updated AssetRevaluation
        """
        org_id = coerce_uuid(organization_id)
        reval_id = coerce_uuid(revaluation_id)
        user_id = coerce_uuid(approved_by_user_id)

        revaluation = db.get(AssetRevaluation, reval_id)
        if not revaluation:
            raise HTTPException(status_code=404, detail="Revaluation not found")

        asset = db.get(Asset, revaluation.asset_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        if revaluation.approved_by_user_id:
            raise HTTPException(
                status_code=400,
                detail="Revaluation already approved",
            )

        # SoD check
        if revaluation.created_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties violation: creator cannot approve",
            )

        # Update revaluation
        revaluation.approved_by_user_id = user_id
        revaluation.approved_at = datetime.now(timezone.utc)

        # Update asset values
        asset.revalued_amount = revaluation.fair_value
        asset.net_book_value = revaluation.carrying_amount_after
        asset.accumulated_depreciation = revaluation.accumulated_depreciation_after

        db.commit()
        db.refresh(revaluation)

        return revaluation

    @staticmethod
    def post_revaluation(
        db: Session,
        organization_id: UUID,
        revaluation_id: UUID,
        posted_by_user_id: UUID,
        posting_date: Optional[date] = None,
    ) -> AssetRevaluation:
        """
        Post an approved revaluation to the GL.

        Args:
            db: Database session
            organization_id: Organization scope
            revaluation_id: Revaluation to post
            posted_by_user_id: User posting
            posting_date: Date for GL posting

        Returns:
            Updated AssetRevaluation
        """
        from app.services.finance.fa.fa_posting_adapter import FAPostingAdapter

        org_id = coerce_uuid(organization_id)
        reval_id = coerce_uuid(revaluation_id)
        user_id = coerce_uuid(posted_by_user_id)

        revaluation = db.get(AssetRevaluation, reval_id)
        if not revaluation:
            raise HTTPException(status_code=404, detail="Revaluation not found")

        asset = db.get(Asset, revaluation.asset_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        if not revaluation.approved_by_user_id:
            raise HTTPException(
                status_code=400,
                detail="Revaluation must be approved before posting",
            )

        if revaluation.journal_entry_id:
            raise HTTPException(
                status_code=400,
                detail="Revaluation already posted",
            )

        result = FAPostingAdapter.post_revaluation(
            db=db,
            organization_id=org_id,
            revaluation_id=reval_id,
            posting_date=posting_date or revaluation.revaluation_date,
            posted_by_user_id=user_id,
        )

        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)

        revaluation.journal_entry_id = result.journal_entry_id

        db.commit()
        db.refresh(revaluation)

        return revaluation

    @staticmethod
    def get(
        db: Session,
        revaluation_id: str,
    ) -> AssetRevaluation:
        """Get a revaluation by ID."""
        revaluation = db.get(AssetRevaluation, coerce_uuid(revaluation_id))
        if not revaluation:
            raise HTTPException(status_code=404, detail="Revaluation not found")
        return revaluation

    @staticmethod
    def get_asset_revaluations(
        db: Session,
        organization_id: UUID,
        asset_id: UUID,
    ) -> list[AssetRevaluation]:
        """Get all revaluations for an asset."""
        org_id = coerce_uuid(organization_id)
        ast_id = coerce_uuid(asset_id)

        asset = db.get(Asset, ast_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        return (
            db.query(AssetRevaluation)
            .filter(AssetRevaluation.asset_id == ast_id)
            .order_by(AssetRevaluation.revaluation_date.desc())
            .all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        asset_id: Optional[str] = None,
        fiscal_period_id: Optional[str] = None,
        pending_approval: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AssetRevaluation]:
        """List revaluations with optional filters."""
        query = db.query(AssetRevaluation)

        if asset_id:
            query = query.filter(
                AssetRevaluation.asset_id == coerce_uuid(asset_id)
            )
        elif organization_id:
            # Need to join to filter by org
            query = query.join(
                Asset, AssetRevaluation.asset_id == Asset.asset_id
            ).filter(Asset.organization_id == coerce_uuid(organization_id))

        if fiscal_period_id:
            query = query.filter(
                AssetRevaluation.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if pending_approval:
            query = query.filter(AssetRevaluation.approved_by_user_id.is_(None))

        query = query.order_by(AssetRevaluation.revaluation_date.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
asset_revaluation_service = AssetRevaluationService()
