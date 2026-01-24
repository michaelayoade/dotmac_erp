"""
PriceListService - Price list management and price resolution.

Manages price lists, pricing tiers, and resolves effective prices for items.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.finance.inv.item import Item
from app.models.finance.inv.price_list import PriceList, PriceListItem, PriceListType
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class PriceListInput:
    """Input for creating a price list."""

    price_list_code: str
    price_list_name: str
    price_list_type: PriceListType
    currency_code: str
    description: Optional[str] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    priority: int = 0
    base_price_list_id: Optional[UUID] = None
    markup_percent: Optional[Decimal] = None
    is_default: bool = False


@dataclass
class PriceListItemInput:
    """Input for adding an item to a price list."""

    item_id: UUID
    unit_price: Decimal
    currency_code: str
    min_quantity: Decimal = Decimal("1")
    discount_percent: Optional[Decimal] = None
    discount_amount: Optional[Decimal] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None


@dataclass
class ResolvedPrice:
    """Result of price resolution for an item."""

    item_id: UUID
    unit_price: Decimal
    currency_code: str
    price_list_id: Optional[UUID]
    price_list_code: Optional[str]
    discount_percent: Optional[Decimal]
    discount_amount: Optional[Decimal]
    net_price: Decimal
    quantity_break: Decimal
    source: str  # "PRICE_LIST", "ITEM_LIST_PRICE", "ITEM_AVERAGE_COST"


class PriceListService(ListResponseMixin):
    """
    Service for price list management and price resolution.

    Manages price lists and resolves effective prices based on
    customer, quantity, date, and price list priority.
    """

    @staticmethod
    def create_price_list(
        db: Session,
        organization_id: UUID,
        input: PriceListInput,
    ) -> PriceList:
        """
        Create a new price list.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Price list input data

        Returns:
            Created PriceList
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate code
        existing = db.query(PriceList).filter(
            and_(
                PriceList.organization_id == org_id,
                PriceList.price_list_code == input.price_list_code,
            )
        ).first()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Price list code '{input.price_list_code}' already exists",
            )

        # If setting as default, clear other defaults
        if input.is_default:
            db.query(PriceList).filter(
                and_(
                    PriceList.organization_id == org_id,
                    PriceList.price_list_type == input.price_list_type,
                    PriceList.is_default == True,
                )
            ).update({"is_default": False})

        price_list = PriceList(
            organization_id=org_id,
            price_list_code=input.price_list_code,
            price_list_name=input.price_list_name,
            description=input.description,
            price_list_type=input.price_list_type,
            currency_code=input.currency_code,
            effective_from=input.effective_from,
            effective_to=input.effective_to,
            priority=input.priority,
            base_price_list_id=input.base_price_list_id,
            markup_percent=input.markup_percent,
            is_default=input.is_default,
            is_active=True,
        )

        db.add(price_list)
        db.commit()
        db.refresh(price_list)

        return price_list

    @staticmethod
    def update_price_list(
        db: Session,
        organization_id: UUID,
        price_list_id: UUID,
        updates: dict,
    ) -> PriceList:
        """Update a price list."""
        org_id = coerce_uuid(organization_id)
        pl_id = coerce_uuid(price_list_id)

        price_list = db.get(PriceList, pl_id)
        if not price_list or price_list.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Price list not found")

        # If setting as default, clear other defaults
        if updates.get("is_default") and not price_list.is_default:
            db.query(PriceList).filter(
                and_(
                    PriceList.organization_id == org_id,
                    PriceList.price_list_type == price_list.price_list_type,
                    PriceList.is_default == True,
                )
            ).update({"is_default": False})

        for key, value in updates.items():
            if hasattr(price_list, key) and key not in ["price_list_id", "organization_id"]:
                setattr(price_list, key, value)

        db.commit()
        db.refresh(price_list)

        return price_list

    @staticmethod
    def add_item_price(
        db: Session,
        organization_id: UUID,
        price_list_id: UUID,
        input: PriceListItemInput,
    ) -> PriceListItem:
        """
        Add or update an item price in a price list.

        Args:
            db: Database session
            organization_id: Organization scope
            price_list_id: Price list ID
            input: Price item input data

        Returns:
            Created/updated PriceListItem
        """
        org_id = coerce_uuid(organization_id)
        pl_id = coerce_uuid(price_list_id)
        itm_id = coerce_uuid(input.item_id)

        # Validate price list
        price_list = db.get(PriceList, pl_id)
        if not price_list or price_list.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Price list not found")

        # Validate item
        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        # Check for existing item with same quantity break
        existing = db.query(PriceListItem).filter(
            and_(
                PriceListItem.price_list_id == pl_id,
                PriceListItem.item_id == itm_id,
                PriceListItem.min_quantity == input.min_quantity,
            )
        ).first()

        if existing:
            # Update existing
            existing.unit_price = input.unit_price
            existing.currency_code = input.currency_code
            existing.discount_percent = input.discount_percent
            existing.discount_amount = input.discount_amount
            existing.effective_from = input.effective_from
            existing.effective_to = input.effective_to
            existing.is_active = True
            db.commit()
            db.refresh(existing)
            return existing

        # Create new
        price_item = PriceListItem(
            price_list_id=pl_id,
            item_id=itm_id,
            unit_price=input.unit_price,
            currency_code=input.currency_code,
            min_quantity=input.min_quantity,
            discount_percent=input.discount_percent,
            discount_amount=input.discount_amount,
            effective_from=input.effective_from,
            effective_to=input.effective_to,
            is_active=True,
        )

        db.add(price_item)
        db.commit()
        db.refresh(price_item)

        return price_item

    @staticmethod
    def remove_item_price(
        db: Session,
        organization_id: UUID,
        price_list_item_id: UUID,
    ) -> bool:
        """Remove an item price from a price list."""
        org_id = coerce_uuid(organization_id)
        pli_id = coerce_uuid(price_list_item_id)

        item = db.get(PriceListItem, pli_id)
        if not item:
            raise HTTPException(status_code=404, detail="Price list item not found")

        # Verify organization
        price_list = db.get(PriceList, item.price_list_id)
        if not price_list or price_list.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Price list item not found")

        db.delete(item)
        db.commit()

        return True

    @staticmethod
    def resolve_price(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        quantity: Decimal = Decimal("1"),
        price_list_id: Optional[UUID] = None,
        price_list_type: PriceListType = PriceListType.SALES,
        as_of_date: Optional[date] = None,
        currency_code: Optional[str] = None,
    ) -> ResolvedPrice:
        """
        Resolve the effective price for an item.

        Resolution order:
        1. Specific price list (if provided)
        2. Default price list for type
        3. Other active price lists by priority
        4. Item's list_price
        5. Item's average_cost

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item to price
            quantity: Quantity for quantity break lookup
            price_list_id: Specific price list to use
            price_list_type: Type of price list (SALES/PURCHASE)
            as_of_date: Date for effective date check
            currency_code: Filter by currency

        Returns:
            ResolvedPrice with effective pricing
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)
        check_date = as_of_date or date.today()

        # Get item
        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        # Build price list query
        pl_query = db.query(PriceList).filter(
            and_(
                PriceList.organization_id == org_id,
                PriceList.price_list_type == price_list_type,
                PriceList.is_active == True,
                or_(PriceList.effective_from.is_(None), PriceList.effective_from <= check_date),
                or_(PriceList.effective_to.is_(None), PriceList.effective_to >= check_date),
            )
        )

        if currency_code:
            pl_query = pl_query.filter(PriceList.currency_code == currency_code)

        if price_list_id:
            pl_query = pl_query.filter(PriceList.price_list_id == coerce_uuid(price_list_id))
        else:
            pl_query = pl_query.order_by(PriceList.is_default.desc(), PriceList.priority.desc())

        price_lists = pl_query.all()

        # Try each price list in order
        for pl in price_lists:
            # Find matching item price with quantity break
            item_prices = db.query(PriceListItem).filter(
                and_(
                    PriceListItem.price_list_id == pl.price_list_id,
                    PriceListItem.item_id == itm_id,
                    PriceListItem.is_active == True,
                    PriceListItem.min_quantity <= quantity,
                    or_(PriceListItem.effective_from.is_(None), PriceListItem.effective_from <= check_date),
                    or_(PriceListItem.effective_to.is_(None), PriceListItem.effective_to >= check_date),
                )
            ).order_by(PriceListItem.min_quantity.desc()).first()

            if item_prices:
                # Calculate net price after discounts
                net_price = item_prices.unit_price
                if item_prices.discount_percent:
                    net_price = net_price * (1 - item_prices.discount_percent / 100)
                if item_prices.discount_amount:
                    net_price = net_price - item_prices.discount_amount
                net_price = net_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                return ResolvedPrice(
                    item_id=itm_id,
                    unit_price=item_prices.unit_price,
                    currency_code=item_prices.currency_code,
                    price_list_id=pl.price_list_id,
                    price_list_code=pl.price_list_code,
                    discount_percent=item_prices.discount_percent,
                    discount_amount=item_prices.discount_amount,
                    net_price=net_price,
                    quantity_break=item_prices.min_quantity,
                    source="PRICE_LIST",
                )

            # Check base price list with markup
            if pl.base_price_list_id and pl.markup_percent is not None:
                base_price = PriceListService._get_base_price(
                    db, pl.base_price_list_id, itm_id, quantity, check_date
                )
                if base_price:
                    marked_up = base_price * (1 + pl.markup_percent / 100)
                    marked_up = marked_up.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                    return ResolvedPrice(
                        item_id=itm_id,
                        unit_price=marked_up,
                        currency_code=pl.currency_code,
                        price_list_id=pl.price_list_id,
                        price_list_code=pl.price_list_code,
                        discount_percent=None,
                        discount_amount=None,
                        net_price=marked_up,
                        quantity_break=Decimal("1"),
                        source="PRICE_LIST",
                    )

        # Fallback to item's list price
        if item.list_price:
            return ResolvedPrice(
                item_id=itm_id,
                unit_price=item.list_price,
                currency_code=item.currency_code,
                price_list_id=None,
                price_list_code=None,
                discount_percent=None,
                discount_amount=None,
                net_price=item.list_price,
                quantity_break=Decimal("1"),
                source="ITEM_LIST_PRICE",
            )

        # Fallback to average cost
        avg_cost = item.average_cost or Decimal("0")
        return ResolvedPrice(
            item_id=itm_id,
            unit_price=avg_cost,
            currency_code=item.currency_code,
            price_list_id=None,
            price_list_code=None,
            discount_percent=None,
            discount_amount=None,
            net_price=avg_cost,
            quantity_break=Decimal("1"),
            source="ITEM_AVERAGE_COST",
        )

    @staticmethod
    def _get_base_price(
        db: Session,
        base_price_list_id: UUID,
        item_id: UUID,
        quantity: Decimal,
        check_date: date,
    ) -> Optional[Decimal]:
        """Get price from base price list."""
        item_price = db.query(PriceListItem).filter(
            and_(
                PriceListItem.price_list_id == base_price_list_id,
                PriceListItem.item_id == item_id,
                PriceListItem.is_active == True,
                PriceListItem.min_quantity <= quantity,
                or_(PriceListItem.effective_from.is_(None), PriceListItem.effective_from <= check_date),
                or_(PriceListItem.effective_to.is_(None), PriceListItem.effective_to >= check_date),
            )
        ).order_by(PriceListItem.min_quantity.desc()).first()

        return item_price.unit_price if item_price else None

    @staticmethod
    def get(
        db: Session,
        price_list_id: str,
    ) -> PriceList:
        """Get a price list by ID."""
        pl = db.get(PriceList, coerce_uuid(price_list_id))
        if not pl:
            raise HTTPException(status_code=404, detail="Price list not found")
        return pl

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        price_list_type: Optional[PriceListType] = None,
        is_active: Optional[bool] = None,
        currency_code: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PriceList]:
        """List price lists with optional filters."""
        query = db.query(PriceList)

        if organization_id:
            query = query.filter(PriceList.organization_id == coerce_uuid(organization_id))

        if price_list_type:
            query = query.filter(PriceList.price_list_type == price_list_type)

        if is_active is not None:
            query = query.filter(PriceList.is_active == is_active)

        if currency_code:
            query = query.filter(PriceList.currency_code == currency_code)

        query = query.order_by(PriceList.is_default.desc(), PriceList.priority.desc())
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def list_items(
        db: Session,
        price_list_id: str,
        item_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PriceListItem]:
        """List items in a price list."""
        pl_id = coerce_uuid(price_list_id)

        query = db.query(PriceListItem).filter(PriceListItem.price_list_id == pl_id)

        if item_id:
            query = query.filter(PriceListItem.item_id == coerce_uuid(item_id))

        query = query.order_by(PriceListItem.min_quantity.asc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
price_list_service = PriceListService()
