"""
Lease web view service.

Provides view-focused data for lease web routes.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.finance.lease.lease_asset import LeaseAsset
from app.models.finance.lease.lease_contract import (
    LeaseClassification,
    LeaseContract,
    LeaseStatus,
)
from app.models.finance.lease.lease_liability import LeaseLiability
from app.models.finance.lease.lease_payment_schedule import LeasePaymentSchedule
from app.services.common import coerce_uuid
from app.services.finance.lease import (
    lease_contract_service,
    lease_variable_payment_service,
)
from app.services.formatters import format_currency as _format_currency
from app.services.formatters import format_date as _format_date

logger = logging.getLogger(__name__)


def _parse_status(value: Optional[str]) -> Optional[LeaseStatus]:
    if not value:
        return None
    try:
        return LeaseStatus(value)
    except ValueError:
        try:
            return LeaseStatus(value.upper())
        except ValueError:
            return None


def _parse_classification(value: Optional[str]) -> Optional[LeaseClassification]:
    if not value:
        return None
    try:
        return LeaseClassification(value)
    except ValueError:
        try:
            return LeaseClassification(value.upper())
        except ValueError:
            return None


def _contract_list_view(contract: LeaseContract) -> dict:
    return {
        "lease_id": contract.lease_id,
        "lease_number": contract.lease_number,
        "lease_name": contract.lease_name,
        "lessor_name": contract.lessor_name,
        "classification": contract.classification.value,
        "status": contract.status.value,
        "commencement_date": _format_date(contract.commencement_date),
        "end_date": _format_date(contract.end_date),
        "base_payment_amount": _format_currency(
            contract.base_payment_amount,
            contract.currency_code,
        ),
        "currency_code": contract.currency_code,
    }


def _contract_detail_view(contract: LeaseContract) -> dict:
    return {
        "lease_id": contract.lease_id,
        "lease_number": contract.lease_number,
        "lease_name": contract.lease_name,
        "description": contract.description,
        "lessor_name": contract.lessor_name,
        "external_reference": contract.external_reference,
        "classification": contract.classification.value,
        "status": contract.status.value,
        "is_lessee": contract.is_lessee,
        "commencement_date": _format_date(contract.commencement_date),
        "end_date": _format_date(contract.end_date),
        "lease_term_months": int(contract.lease_term_months)
        if contract.lease_term_months is not None
        else None,
        "payment_frequency": contract.payment_frequency,
        "payment_timing": contract.payment_timing,
        "currency_code": contract.currency_code,
        "base_payment_amount": _format_currency(
            contract.base_payment_amount,
            contract.currency_code,
        ),
        "has_variable_payments": contract.has_variable_payments,
        "variable_payment_basis": contract.variable_payment_basis,
        "is_index_linked": contract.is_index_linked,
        "index_type": contract.index_type,
        "index_base_value": contract.index_base_value,
        "last_index_adjustment_date": _format_date(contract.last_index_adjustment_date),
        "incremental_borrowing_rate": contract.incremental_borrowing_rate,
        "implicit_rate": contract.implicit_rate,
        "discount_rate_used": contract.discount_rate_used,
        "has_renewal_option": contract.has_renewal_option,
        "renewal_option_term_months": contract.renewal_option_term_months,
        "renewal_reasonably_certain": contract.renewal_reasonably_certain,
        "has_purchase_option": contract.has_purchase_option,
        "purchase_option_price": contract.purchase_option_price,
        "purchase_reasonably_certain": contract.purchase_reasonably_certain,
        "has_termination_option": contract.has_termination_option,
        "termination_penalty": contract.termination_penalty,
        "asset_description": contract.asset_description,
    }


def _liability_view(liability: LeaseLiability, currency_code: str) -> dict:
    return {
        "liability_id": liability.liability_id,
        "initial_measurement_date": _format_date(liability.initial_measurement_date),
        "initial_liability_amount": _format_currency(
            liability.initial_liability_amount,
            currency_code,
        ),
        "current_liability_balance": _format_currency(
            liability.current_liability_balance,
            currency_code,
        ),
        "current_portion": _format_currency(liability.current_portion, currency_code),
        "non_current_portion": _format_currency(
            liability.non_current_portion,
            currency_code,
        ),
        "total_interest_expense": _format_currency(
            liability.total_interest_expense,
            currency_code,
        ),
        "total_payments_made": _format_currency(
            liability.total_payments_made,
            currency_code,
        ),
        "modification_adjustments": _format_currency(
            liability.modification_adjustments,
            currency_code,
        ),
        "discount_rate": liability.discount_rate,
        "last_interest_date": _format_date(liability.last_interest_date),
    }


def _asset_view(asset: LeaseAsset, currency_code: str) -> dict:
    return {
        "asset_id": asset.asset_id,
        "initial_measurement_date": _format_date(asset.initial_measurement_date),
        "initial_rou_asset_value": _format_currency(
            asset.initial_rou_asset_value,
            currency_code,
        ),
        "carrying_amount": _format_currency(asset.carrying_amount, currency_code),
        "accumulated_depreciation": _format_currency(
            asset.accumulated_depreciation,
            currency_code,
        ),
        "impairment_losses": _format_currency(
            asset.impairment_losses,
            currency_code,
        ),
        "revaluation_adjustments": _format_currency(
            asset.revaluation_adjustments,
            currency_code,
        ),
        "modification_adjustments": _format_currency(
            asset.modification_adjustments,
            currency_code,
        ),
        "depreciation_method": asset.depreciation_method,
        "useful_life_months": int(asset.useful_life_months)
        if asset.useful_life_months is not None
        else None,
        "residual_value": _format_currency(asset.residual_value, currency_code),
        "last_depreciation_date": _format_date(asset.last_depreciation_date),
    }


def _schedule_view(schedule: LeasePaymentSchedule, currency_code: str) -> dict:
    return {
        "schedule_id": schedule.schedule_id,
        "payment_number": schedule.payment_number,
        "payment_date": _format_date(schedule.payment_date),
        "total_payment": _format_currency(schedule.total_payment, currency_code),
        "principal_portion": _format_currency(
            schedule.principal_portion,
            currency_code,
        ),
        "interest_portion": _format_currency(
            schedule.interest_portion,
            currency_code,
        ),
        "variable_payment": _format_currency(
            schedule.variable_payment,
            currency_code,
        ),
        "opening_balance": _format_currency(
            schedule.opening_liability_balance,
            currency_code,
        ),
        "closing_balance": _format_currency(
            schedule.closing_liability_balance,
            currency_code,
        ),
        "index_adjustment_amount": _format_currency(
            schedule.index_adjustment_amount,
            currency_code,
        ),
        "is_index_adjusted": schedule.is_index_adjusted,
        "status": schedule.status.value,
        "actual_payment_date": _format_date(schedule.actual_payment_date),
        "actual_payment_amount": _format_currency(
            schedule.actual_payment_amount,
            currency_code,
        ),
    }


class LeaseWebService:
    """View service for lease web routes."""

    @staticmethod
    def list_contracts_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        status: Optional[str],
        lease_type: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_status(status)
        classification = _parse_classification(lease_type)

        query = db.query(LeaseContract).filter(LeaseContract.organization_id == org_id)

        if status_value:
            query = query.filter(LeaseContract.status == status_value)
        if classification:
            query = query.filter(LeaseContract.classification == classification)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    LeaseContract.lease_number.ilike(search_pattern),
                    LeaseContract.lease_name.ilike(search_pattern),
                    LeaseContract.lessor_name.ilike(search_pattern),
                )
            )

        total_count = (
            query.with_entities(func.count(LeaseContract.lease_id)).scalar() or 0
        )
        contracts = (
            query.order_by(LeaseContract.commencement_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "contracts": [_contract_list_view(contract) for contract in contracts],
            "search": search,
            "status": status,
            "lease_type": lease_type,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def contract_detail_context(
        db: Session,
        organization_id: str,
        lease_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        contract = db.get(LeaseContract, coerce_uuid(lease_id))
        if not contract or contract.organization_id != org_id:
            return {"contract": None, "liability": None, "asset": None}

        liability = lease_contract_service.get_liability(db, lease_id)
        asset = lease_contract_service.get_asset(db, lease_id)

        return {
            "contract": _contract_detail_view(contract),
            "liability": _liability_view(liability, contract.currency_code)
            if liability
            else None,
            "asset": _asset_view(asset, contract.currency_code) if asset else None,
        }

    @staticmethod
    def schedule_context(
        db: Session,
        organization_id: str,
        lease_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        contract = db.get(LeaseContract, coerce_uuid(lease_id))
        if not contract or contract.organization_id != org_id:
            return {"contract": None, "schedules": [], "summary": None}

        schedules = lease_variable_payment_service.get_scheduled_payments(
            db,
            coerce_uuid(lease_id),
            include_paid=True,
        )

        total_payment = sum(
            (Decimal(str(schedule.total_payment)) for schedule in schedules),
            Decimal("0"),
        )
        total_interest = sum(
            (Decimal(str(schedule.interest_portion)) for schedule in schedules),
            Decimal("0"),
        )
        total_principal = sum(
            (Decimal(str(schedule.principal_portion)) for schedule in schedules),
            Decimal("0"),
        )

        summary = {
            "total_payment": _format_currency(total_payment, contract.currency_code),
            "total_interest": _format_currency(total_interest, contract.currency_code),
            "total_principal": _format_currency(
                total_principal,
                contract.currency_code,
            ),
        }

        return {
            "contract": _contract_detail_view(contract),
            "schedules": [
                _schedule_view(schedule, contract.currency_code)
                for schedule in schedules
            ],
            "summary": summary,
        }


lease_web_service = LeaseWebService()
