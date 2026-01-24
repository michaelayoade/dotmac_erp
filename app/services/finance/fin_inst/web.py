"""
Financial instruments web view service.

Provides view-focused data for financial instrument web routes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.finance.fin_inst.financial_instrument import (
    FinancialInstrument,
    InstrumentClassification,
    InstrumentStatus,
    InstrumentType,
)
from app.models.finance.fin_inst.hedge_relationship import (
    HedgeRelationship,
    HedgeStatus,
    HedgeType,
)
from app.config import settings
from app.services.common import coerce_uuid


def _format_date(value: Optional[date]) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _format_currency(
    amount: Optional[Decimal],
    currency: str = settings.default_presentation_currency_code,
) -> str:
    if amount is None:
        return ""
    value = Decimal(str(amount))
    return f"{currency} {value:,.2f}"


def _parse_instrument_type(value: Optional[str]) -> Optional[InstrumentType]:
    if not value:
        return None
    try:
        return InstrumentType(value)
    except ValueError:
        try:
            return InstrumentType(value.upper())
        except ValueError:
            return None


def _parse_instrument_status(value: Optional[str]) -> Optional[InstrumentStatus]:
    if not value:
        return None
    try:
        return InstrumentStatus(value)
    except ValueError:
        try:
            return InstrumentStatus(value.upper())
        except ValueError:
            return None


def _parse_hedge_type(value: Optional[str]) -> Optional[HedgeType]:
    if not value:
        return None
    try:
        return HedgeType(value)
    except ValueError:
        try:
            return HedgeType(value.upper())
        except ValueError:
            return None


def _parse_hedge_status(value: Optional[str]) -> Optional[HedgeStatus]:
    if not value:
        return None
    try:
        return HedgeStatus(value)
    except ValueError:
        try:
            return HedgeStatus(value.upper())
        except ValueError:
            return None


class FinInstWebService:
    """View service for financial instruments web routes."""

    @staticmethod
    def list_instruments_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        instrument_type: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        type_value = _parse_instrument_type(instrument_type)
        status_value = _parse_instrument_status(status)

        query = db.query(FinancialInstrument).filter(
            FinancialInstrument.organization_id == org_id
        )

        if type_value:
            query = query.filter(FinancialInstrument.instrument_type == type_value)
        if status_value:
            query = query.filter(FinancialInstrument.status == status_value)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    FinancialInstrument.instrument_code.ilike(search_pattern),
                    FinancialInstrument.instrument_name.ilike(search_pattern),
                    FinancialInstrument.counterparty_name.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(FinancialInstrument.instrument_id)).scalar() or 0
        instruments = (
            query.order_by(FinancialInstrument.instrument_code)
            .limit(limit)
            .offset(offset)
            .all()
        )

        instruments_view = []
        for instrument in instruments:
            instruments_view.append(
                {
                    "instrument_id": instrument.instrument_id,
                    "instrument_code": instrument.instrument_code,
                    "instrument_name": instrument.instrument_name,
                    "instrument_type": instrument.instrument_type.value,
                    "classification": instrument.classification.value,
                    "status": instrument.status.value,
                    "carrying_amount": _format_currency(
                        instrument.carrying_amount, instrument.currency_code
                    ),
                    "currency_code": instrument.currency_code,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "instruments": instruments_view,
            "instrument_types": [t.value for t in InstrumentType],
            "instrument_statuses": [s.value for s in InstrumentStatus],
            "instrument_classifications": [c.value for c in InstrumentClassification],
            "search": search,
            "instrument_type": instrument_type,
            "status": status,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def list_hedges_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        hedge_type: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        type_value = _parse_hedge_type(hedge_type)
        status_value = _parse_hedge_status(status)

        query = (
            db.query(HedgeRelationship, FinancialInstrument)
            .join(
                FinancialInstrument,
                HedgeRelationship.hedging_instrument_id == FinancialInstrument.instrument_id,
            )
            .filter(HedgeRelationship.organization_id == org_id)
        )

        if type_value:
            query = query.filter(HedgeRelationship.hedge_type == type_value)
        if status_value:
            query = query.filter(HedgeRelationship.status == status_value)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    HedgeRelationship.hedge_code.ilike(search_pattern),
                    HedgeRelationship.hedge_name.ilike(search_pattern),
                    FinancialInstrument.instrument_code.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(HedgeRelationship.hedge_id)).scalar() or 0
        rows = (
            query.order_by(HedgeRelationship.hedge_code)
            .limit(limit)
            .offset(offset)
            .all()
        )

        hedges_view = []
        for hedge, instrument in rows:
            hedges_view.append(
                {
                    "hedge_id": hedge.hedge_id,
                    "hedge_code": hedge.hedge_code,
                    "hedge_name": hedge.hedge_name,
                    "hedge_type": hedge.hedge_type.value,
                    "status": hedge.status.value,
                    "designation_date": _format_date(hedge.designation_date),
                    "hedging_instrument_code": instrument.instrument_code,
                    "hedging_instrument_name": instrument.instrument_name,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "hedges": hedges_view,
            "hedge_types": [t.value for t in HedgeType],
            "hedge_statuses": [s.value for s in HedgeStatus],
            "search": search,
            "hedge_type": hedge_type,
            "status": status,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }


fin_inst_web_service = FinInstWebService()
