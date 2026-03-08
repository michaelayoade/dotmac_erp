"""Corporate-card and card-transaction operations."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from app.models.expense import CardTransaction, CardTransactionStatus, CorporateCard
from app.services.common import PaginatedResult, PaginationParams
from app.services.expense.service_common import (
    CardTransactionNotFoundError,
    CorporateCardNotFoundError,
    ExpenseServiceBase,
)


class ExpenseCardMixin(ExpenseServiceBase):
    def list_cards(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        is_active: bool | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[CorporateCard]:
        query = select(CorporateCard).where(CorporateCard.organization_id == org_id)
        if employee_id:
            query = query.where(CorporateCard.employee_id == employee_id)
        if is_active is not None:
            query = query.where(CorporateCard.is_active == is_active)
        query = query.order_by(CorporateCard.assigned_date.desc().nullslast())
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)
        items = list(self.db.scalars(query).all())
        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_card(self, org_id: UUID, card_id: UUID) -> CorporateCard:
        card = self.db.scalar(
            select(CorporateCard).where(
                CorporateCard.card_id == card_id,
                CorporateCard.organization_id == org_id,
            )
        )
        if not card:
            raise CorporateCardNotFoundError(card_id)
        return card

    def create_card(
        self,
        org_id: UUID,
        *,
        card_number_last4: str,
        card_name: str,
        card_type: str,
        employee_id: UUID | None = None,
        assigned_date: date | None = None,
        issuer: str | None = None,
        expiry_date: date | None = None,
        credit_limit: Decimal | None = None,
        single_transaction_limit: Decimal | None = None,
        monthly_limit: Decimal | None = None,
        currency_code: str = "NGN",
        liability_account_id: UUID | None = None,
    ) -> CorporateCard:
        card = CorporateCard(
            organization_id=org_id,
            card_number_last4=card_number_last4,
            card_name=card_name,
            card_type=card_type,
            employee_id=employee_id,
            assigned_date=assigned_date,
            issuer=issuer,
            expiry_date=expiry_date,
            credit_limit=credit_limit,
            single_transaction_limit=single_transaction_limit,
            monthly_limit=monthly_limit,
            currency_code=currency_code,
            liability_account_id=liability_account_id,
            is_active=True,
        )
        self.db.add(card)
        self.db.flush()
        return card

    def update_card(self, org_id: UUID, card_id: UUID, **kwargs) -> CorporateCard:
        card = self.get_card(org_id, card_id)
        for key, value in kwargs.items():
            if value is not None and hasattr(card, key):
                setattr(card, key, value)
        self.db.flush()
        return card

    def deactivate_card(
        self,
        org_id: UUID,
        card_id: UUID,
        *,
        reason: str | None = None,
    ) -> CorporateCard:
        card = self.get_card(org_id, card_id)
        card.is_active = False
        card.deactivated_on = date.today()
        if reason:
            card.deactivation_reason = reason
        self.db.flush()
        return card

    def list_transactions(
        self,
        org_id: UUID,
        *,
        card_id: UUID | None = None,
        status: CardTransactionStatus | str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        unmatched_only: bool = False,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[CardTransaction]:
        query = select(CardTransaction).where(CardTransaction.organization_id == org_id)
        if card_id:
            query = query.where(CardTransaction.card_id == card_id)
        if status:
            status_value: CardTransactionStatus | None = None
            if isinstance(status, CardTransactionStatus):
                status_value = status
            elif isinstance(status, str):
                try:
                    status_value = CardTransactionStatus(status)
                except ValueError:
                    status_value = None
            if status_value:
                query = query.where(CardTransaction.status == status_value)
        if from_date:
            query = query.where(CardTransaction.transaction_date >= from_date)
        if to_date:
            query = query.where(CardTransaction.transaction_date <= to_date)
        if unmatched_only:
            query = query.where(CardTransaction.expense_claim_id.is_(None))
        query = query.order_by(CardTransaction.transaction_date.desc())
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)
        items = list(self.db.scalars(query).all())
        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_transaction(self, org_id: UUID, transaction_id: UUID) -> CardTransaction:
        transaction = self.db.scalar(
            select(CardTransaction).where(
                CardTransaction.transaction_id == transaction_id,
                CardTransaction.organization_id == org_id,
            )
        )
        if not transaction:
            raise CardTransactionNotFoundError(transaction_id)
        return transaction

    def create_transaction(
        self,
        org_id: UUID,
        *,
        card_id: UUID,
        transaction_date: date,
        merchant_name: str,
        amount: Decimal,
        posting_date: date | None = None,
        merchant_category: str | None = None,
        currency_code: str = "NGN",
        original_currency: str | None = None,
        original_amount: Decimal | None = None,
        external_reference: str | None = None,
        description: str | None = None,
        notes: str | None = None,
    ) -> CardTransaction:
        self.get_card(org_id, card_id)
        transaction = CardTransaction(
            organization_id=org_id,
            card_id=card_id,
            transaction_date=transaction_date,
            posting_date=posting_date,
            merchant_name=merchant_name,
            merchant_category=merchant_category,
            amount=amount,
            currency_code=currency_code,
            original_currency=original_currency,
            original_amount=original_amount,
            external_reference=external_reference,
            description=description,
            notes=notes,
            status=CardTransactionStatus.PENDING,
            is_personal_expense=False,
            personal_deduction_from_salary=False,
        )
        self.db.add(transaction)
        self.db.flush()
        return transaction

    def update_transaction(
        self,
        org_id: UUID,
        transaction_id: UUID,
        **kwargs,
    ) -> CardTransaction:
        transaction = self.get_transaction(org_id, transaction_id)
        for key, value in kwargs.items():
            if value is not None and hasattr(transaction, key):
                setattr(transaction, key, value)
        self.db.flush()
        return transaction

    def match_transaction(
        self,
        org_id: UUID,
        transaction_id: UUID,
        *,
        expense_claim_id: UUID,
    ) -> CardTransaction:
        transaction = self.get_transaction(org_id, transaction_id)
        transaction.expense_claim_id = expense_claim_id
        transaction.matched_on = date.today()
        transaction.status = CardTransactionStatus.MATCHED
        self.db.flush()
        return transaction

    def mark_personal(
        self,
        org_id: UUID,
        transaction_id: UUID,
        *,
        deduct_from_salary: bool = False,
    ) -> CardTransaction:
        transaction = self.get_transaction(org_id, transaction_id)
        transaction.is_personal_expense = True
        transaction.personal_deduction_from_salary = deduct_from_salary
        transaction.status = CardTransactionStatus.PERSONAL
        self.db.flush()
        return transaction
