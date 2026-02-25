"""
Fiscal Position Service.

Provides auto-detection of fiscal positions based on partner attributes
and tax/account remapping for invoice creation.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.tax.fiscal_position import (
    FiscalPosition,
    FiscalPositionAccountMap,
    FiscalPositionTaxMap,
)
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


class FiscalPositionService:
    """Service for fiscal position management and tax/account remapping."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Auto-detection
    # ------------------------------------------------------------------

    def get_for_partner(
        self,
        organization_id: UUID,
        partner_type: str,
        partner_classification: str | None,
        country_code: str | None = None,
        state_code: str | None = None,
    ) -> FiscalPosition | None:
        """
        Auto-detect the best-matching fiscal position for a partner.

        Matches are scored by specificity: positions that match more
        attributes are preferred. Among equal matches, the lowest
        priority value wins.

        Args:
            organization_id: Organization scope
            partner_type: "customer" or "supplier"
            partner_classification: e.g. CustomerType.GOVERNMENT.value
            country_code: Partner country code
            state_code: Partner state code

        Returns:
            Best-matching FiscalPosition, or None if no match
        """
        org_id = coerce_uuid(organization_id)

        stmt = (
            select(FiscalPosition)
            .where(
                FiscalPosition.organization_id == org_id,
                FiscalPosition.auto_apply.is_(True),
                FiscalPosition.is_active.is_(True),
            )
            .order_by(FiscalPosition.priority.asc())
        )

        candidates = list(self.db.scalars(stmt).all())

        best: FiscalPosition | None = None
        best_score = -1

        for fp in candidates:
            score = self._match_score(
                fp, partner_type, partner_classification, country_code, state_code
            )
            if score > best_score:
                best_score = score
                best = fp

        if best and best_score > 0:
            logger.debug(
                "Matched fiscal position %s (score=%d) for %s/%s",
                best.name,
                best_score,
                partner_type,
                partner_classification,
            )
            return best

        return None

    @staticmethod
    def _match_score(
        fp: FiscalPosition,
        partner_type: str,
        partner_classification: str | None,
        country_code: str | None,
        state_code: str | None,
    ) -> int:
        """
        Score how well a fiscal position matches partner attributes.

        Returns 0 if there's a mismatch on any set criteria, otherwise
        returns the count of matching criteria (higher = more specific).
        """
        score = 0

        # Check partner type (customer_type or supplier_type on the FP)
        if partner_type == "customer" and fp.customer_type:
            if partner_classification and fp.customer_type == partner_classification:
                score += 1
            else:
                return 0  # Mismatch — this FP doesn't apply
        elif partner_type == "supplier" and fp.supplier_type:
            if partner_classification and fp.supplier_type == partner_classification:
                score += 1
            else:
                return 0

        # Check country code
        if fp.country_code:
            if country_code and fp.country_code == country_code:
                score += 1
            else:
                return 0

        # Check state code
        if fp.state_code:
            if state_code and fp.state_code == state_code:
                score += 1
            else:
                return 0

        return score

    # ------------------------------------------------------------------
    # Remapping
    # ------------------------------------------------------------------

    def map_taxes(
        self,
        fiscal_position: FiscalPosition,
        tax_code_ids: list[UUID],
    ) -> list[UUID]:
        """
        Remap tax codes through a fiscal position.

        Rules:
        - If a source tax has a destination, replace it.
        - If a source tax has dest=None, remove it (exempt).
        - Unmapped tax codes pass through unchanged.

        Args:
            fiscal_position: The fiscal position with loaded tax_maps
            tax_code_ids: Original tax code IDs

        Returns:
            Remapped list of tax code IDs
        """
        # Build lookup: source_id → dest_id (None means exempt)
        mapping: dict[UUID, UUID | None] = {
            tm.tax_source_id: tm.tax_dest_id for tm in fiscal_position.tax_maps
        }

        result: list[UUID] = []
        for tax_id in tax_code_ids:
            if tax_id in mapping:
                dest = mapping[tax_id]
                if dest is not None:
                    result.append(dest)
                # else: exempt — drop from list
            else:
                result.append(tax_id)

        return result

    def map_account(
        self,
        fiscal_position: FiscalPosition,
        account_id: UUID,
    ) -> UUID:
        """
        Remap a GL account through a fiscal position.

        Unmapped accounts pass through unchanged.

        Args:
            fiscal_position: The fiscal position with loaded account_maps
            account_id: Original GL account ID

        Returns:
            Remapped account ID (or original if no mapping)
        """
        for am in fiscal_position.account_maps:
            if am.account_source_id == account_id:
                return am.account_dest_id
        return account_id

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get_by_id(self, fiscal_position_id: UUID) -> FiscalPosition | None:
        """Get a fiscal position by ID."""
        return self.db.get(FiscalPosition, coerce_uuid(fiscal_position_id))

    def list_for_org(
        self,
        organization_id: UUID,
        *,
        is_active: bool | None = None,
        search: str | None = None,
    ) -> list[FiscalPosition]:
        """List fiscal positions for an organization."""
        org_id = coerce_uuid(organization_id)
        stmt = (
            select(FiscalPosition)
            .where(FiscalPosition.organization_id == org_id)
            .order_by(FiscalPosition.priority.asc(), FiscalPosition.name.asc())
        )

        if is_active is not None:
            stmt = stmt.where(FiscalPosition.is_active.is_(is_active))

        if search:
            stmt = stmt.where(FiscalPosition.name.ilike(f"%{search}%"))

        return list(self.db.scalars(stmt).all())

    def create(
        self,
        organization_id: UUID,
        data: dict[str, Any],
    ) -> FiscalPosition:
        """
        Create a new fiscal position with optional tax and account mappings.

        Args:
            organization_id: Organization scope
            data: Dict with name, description, auto_apply, customer_type,
                  supplier_type, country_code, state_code, priority,
                  tax_maps (list of dicts), account_maps (list of dicts)

        Returns:
            Created FiscalPosition
        """
        org_id = coerce_uuid(organization_id)

        fp = FiscalPosition(
            organization_id=org_id,
            name=str(data["name"]),
            description=data.get("description") or None,
            auto_apply=bool(data.get("auto_apply", False)),
            customer_type=data.get("customer_type") or None,
            supplier_type=data.get("supplier_type") or None,
            country_code=data.get("country_code") or None,
            state_code=data.get("state_code") or None,
            priority=int(data.get("priority", 10)),
        )
        self.db.add(fp)
        self.db.flush()

        # Add tax mappings
        for tm_data in data.get("tax_maps", []):
            if not tm_data.get("tax_source_id"):
                continue
            tm = FiscalPositionTaxMap(
                fiscal_position_id=fp.fiscal_position_id,
                tax_source_id=coerce_uuid(tm_data["tax_source_id"]),
                tax_dest_id=coerce_uuid(tm_data.get("tax_dest_id")),
            )
            self.db.add(tm)

        # Add account mappings
        for am_data in data.get("account_maps", []):
            if not am_data.get("account_source_id") or not am_data.get(
                "account_dest_id"
            ):
                continue
            am = FiscalPositionAccountMap(
                fiscal_position_id=fp.fiscal_position_id,
                account_source_id=coerce_uuid(am_data["account_source_id"]),
                account_dest_id=coerce_uuid(am_data["account_dest_id"]),
            )
            self.db.add(am)

        self.db.flush()
        logger.info("Created fiscal position '%s' (%s)", fp.name, fp.fiscal_position_id)
        return fp

    def update(
        self,
        fiscal_position_id: UUID,
        data: dict[str, Any],
    ) -> FiscalPosition:
        """
        Update a fiscal position and replace its mappings.

        Args:
            fiscal_position_id: ID of fiscal position to update
            data: Updated fields + tax_maps + account_maps

        Returns:
            Updated FiscalPosition

        Raises:
            ValueError: If fiscal position not found
        """
        fp = self.db.get(FiscalPosition, coerce_uuid(fiscal_position_id))
        if not fp:
            raise ValueError("Fiscal position not found")

        # Update scalar fields
        for field in (
            "name",
            "description",
            "auto_apply",
            "customer_type",
            "supplier_type",
            "country_code",
            "state_code",
            "priority",
            "is_active",
        ):
            if field in data:
                val = data[field]
                if field in (
                    "customer_type",
                    "supplier_type",
                    "country_code",
                    "state_code",
                ):
                    val = val or None
                if field == "auto_apply":
                    val = bool(val)
                if field == "priority":
                    val = int(val)
                setattr(fp, field, val)

        # Replace tax mappings if provided
        if "tax_maps" in data:
            # Remove existing
            for tm in list(fp.tax_maps):
                self.db.delete(tm)
            self.db.flush()

            for tm_data in data["tax_maps"]:
                if not tm_data.get("tax_source_id"):
                    continue
                tm = FiscalPositionTaxMap(
                    fiscal_position_id=fp.fiscal_position_id,
                    tax_source_id=coerce_uuid(tm_data["tax_source_id"]),
                    tax_dest_id=coerce_uuid(tm_data.get("tax_dest_id")),
                )
                self.db.add(tm)

        # Replace account mappings if provided
        if "account_maps" in data:
            for am in list(fp.account_maps):
                self.db.delete(am)
            self.db.flush()

            for am_data in data["account_maps"]:
                if not am_data.get("account_source_id") or not am_data.get(
                    "account_dest_id"
                ):
                    continue
                am = FiscalPositionAccountMap(
                    fiscal_position_id=fp.fiscal_position_id,
                    account_source_id=coerce_uuid(am_data["account_source_id"]),
                    account_dest_id=coerce_uuid(am_data["account_dest_id"]),
                )
                self.db.add(am)

        self.db.flush()
        logger.info("Updated fiscal position '%s' (%s)", fp.name, fp.fiscal_position_id)
        return fp

    def delete(self, fiscal_position_id: UUID) -> None:
        """Delete a fiscal position and its mappings (cascade)."""
        fp = self.db.get(FiscalPosition, coerce_uuid(fiscal_position_id))
        if not fp:
            raise ValueError("Fiscal position not found")

        self.db.delete(fp)
        self.db.flush()
        logger.info("Deleted fiscal position '%s' (%s)", fp.name, fp.fiscal_position_id)
