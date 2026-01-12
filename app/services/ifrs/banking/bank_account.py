"""
Bank Account Service.

Provides CRUD operations for bank accounts and GL account linkage.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ifrs.banking.bank_account import (
    BankAccount,
    BankAccountStatus,
    BankAccountType,
)
from app.models.ifrs.gl.account import Account
from app.services.common import coerce_uuid


@dataclass
class BankAccountInput:
    """Input for creating/updating a bank account."""

    bank_name: str
    account_number: str
    account_name: str
    gl_account_id: UUID
    currency_code: str = settings.default_functional_currency_code
    account_type: BankAccountType = BankAccountType.checking
    bank_code: Optional[str] = None
    branch_code: Optional[str] = None
    branch_name: Optional[str] = None
    iban: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    notes: Optional[str] = None
    is_primary: bool = False
    allow_overdraft: bool = False
    overdraft_limit: Optional[Decimal] = None


class BankAccountService:
    """Service for managing bank accounts."""

    def create(
        self,
        db: Session,
        organization_id: UUID,
        input: BankAccountInput,
        created_by: Optional[UUID] = None,
    ) -> BankAccount:
        """Create a new bank account."""
        org_id = coerce_uuid(organization_id)
        # Validate GL account exists and is a cash/bank account
        gl_account = db.get(Account, input.gl_account_id)
        if not gl_account or gl_account.organization_id != org_id:
            raise HTTPException(status_code=404, detail=f"GL account {input.gl_account_id} not found")

        # Check for duplicate account number
        existing = db.execute(
            select(BankAccount).where(
                and_(
                    BankAccount.organization_id == org_id,
                    BankAccount.account_number == input.account_number,
                    BankAccount.bank_code == input.bank_code,
                )
            )
        ).scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Bank account {input.account_number} already exists for this bank",
            )

        # If this is set as primary, unset other primary accounts
        if input.is_primary:
            db.execute(
                BankAccount.__table__.update()
                .where(
                    and_(
                        BankAccount.organization_id == org_id,
                        BankAccount.currency_code == input.currency_code,
                        BankAccount.is_primary == True,
                    )
                )
                .values(is_primary=False)
            )

        bank_account = BankAccount(
            organization_id=org_id,
            bank_name=input.bank_name,
            bank_code=input.bank_code,
            branch_code=input.branch_code,
            branch_name=input.branch_name,
            account_number=input.account_number,
            account_name=input.account_name,
            account_type=input.account_type,
            iban=input.iban,
            currency_code=input.currency_code,
            gl_account_id=input.gl_account_id,
            status=BankAccountStatus.active,
            contact_name=input.contact_name,
            contact_phone=input.contact_phone,
            contact_email=input.contact_email,
            notes=input.notes,
            is_primary=input.is_primary,
            allow_overdraft=input.allow_overdraft,
            overdraft_limit=input.overdraft_limit,
            created_by=created_by,
            updated_by=created_by,
        )

        db.add(bank_account)
        db.flush()

        return bank_account

    def get(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
    ) -> Optional[BankAccount]:
        """Get a bank account by ID."""
        org_id = coerce_uuid(organization_id)
        account = db.get(BankAccount, bank_account_id)
        if not account or account.organization_id != org_id:
            return None
        return account

    def get_by_account_number(
        self,
        db: Session,
        organization_id: UUID,
        account_number: str,
        bank_code: Optional[str] = None,
    ) -> Optional[BankAccount]:
        """Get a bank account by account number."""
        query = select(BankAccount).where(
            and_(
                BankAccount.organization_id == organization_id,
                BankAccount.account_number == account_number,
            )
        )
        if bank_code:
            query = query.where(BankAccount.bank_code == bank_code)

        return db.execute(query).scalar_one_or_none()

    def list(
        self,
        db: Session,
        organization_id: UUID,
        status: Optional[BankAccountStatus] = None,
        currency_code: Optional[str] = None,
        account_type: Optional[BankAccountType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[BankAccount]:
        """List bank accounts with optional filters."""
        query = select(BankAccount).where(
            BankAccount.organization_id == organization_id
        )

        if status:
            query = query.where(BankAccount.status == status)
        if currency_code:
            query = query.where(BankAccount.currency_code == currency_code)
        if account_type:
            query = query.where(BankAccount.account_type == account_type)

        query = query.order_by(BankAccount.bank_name, BankAccount.account_name)
        query = query.offset(offset).limit(limit)

        return list(db.execute(query).scalars().all())

    def count(
        self,
        db: Session,
        organization_id: UUID,
        status: Optional[BankAccountStatus] = None,
        currency_code: Optional[str] = None,
        account_type: Optional[BankAccountType] = None,
    ) -> int:
        """Count bank accounts matching filters (for pagination)."""
        from sqlalchemy import func as sqla_func

        query = select(sqla_func.count(BankAccount.bank_account_id)).where(
            BankAccount.organization_id == organization_id
        )

        if status:
            query = query.where(BankAccount.status == status)
        if currency_code:
            query = query.where(BankAccount.currency_code == currency_code)
        if account_type:
            query = query.where(BankAccount.account_type == account_type)

        return db.execute(query).scalar() or 0

    def update(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        input: BankAccountInput,
        updated_by: Optional[UUID] = None,
    ) -> BankAccount:
        """Update a bank account."""
        org_id = coerce_uuid(organization_id)
        bank_account = db.get(BankAccount, bank_account_id)
        if not bank_account or bank_account.organization_id != org_id:
            raise HTTPException(status_code=404, detail=f"Bank account {bank_account_id} not found")

        # Validate GL account
        gl_account = db.get(Account, input.gl_account_id)
        if not gl_account or gl_account.organization_id != org_id:
            raise HTTPException(status_code=404, detail=f"GL account {input.gl_account_id} not found")

        # Update fields
        bank_account.bank_name = input.bank_name
        bank_account.bank_code = input.bank_code
        bank_account.branch_code = input.branch_code
        bank_account.branch_name = input.branch_name
        bank_account.account_number = input.account_number
        bank_account.account_name = input.account_name
        bank_account.account_type = input.account_type
        bank_account.iban = input.iban
        bank_account.currency_code = input.currency_code
        bank_account.gl_account_id = input.gl_account_id
        bank_account.contact_name = input.contact_name
        bank_account.contact_phone = input.contact_phone
        bank_account.contact_email = input.contact_email
        bank_account.notes = input.notes
        bank_account.allow_overdraft = input.allow_overdraft
        bank_account.overdraft_limit = input.overdraft_limit
        bank_account.updated_by = updated_by

        # Handle primary flag
        if input.is_primary and not bank_account.is_primary:
            db.execute(
                BankAccount.__table__.update()
                .where(
                    and_(
                        BankAccount.organization_id == org_id,
                        BankAccount.currency_code == input.currency_code,
                        BankAccount.is_primary == True,
                        BankAccount.bank_account_id != bank_account_id,
                    )
                )
                .values(is_primary=False)
            )
            bank_account.is_primary = True
        elif not input.is_primary:
            bank_account.is_primary = False

        db.flush()
        return bank_account

    def update_status(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        status: BankAccountStatus,
        updated_by: Optional[UUID] = None,
    ) -> BankAccount:
        """Update bank account status."""
        org_id = coerce_uuid(organization_id)
        bank_account = db.get(BankAccount, bank_account_id)
        if not bank_account or bank_account.organization_id != org_id:
            raise HTTPException(status_code=404, detail=f"Bank account {bank_account_id} not found")

        bank_account.status = status
        bank_account.updated_by = updated_by
        db.flush()

        return bank_account

    def update_reconciled_balance(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        reconciled_date: datetime,
        reconciled_balance: Decimal,
    ) -> BankAccount:
        """Update last reconciled date and balance."""
        org_id = coerce_uuid(organization_id)
        bank_account = db.get(BankAccount, bank_account_id)
        if not bank_account or bank_account.organization_id != org_id:
            raise HTTPException(status_code=404, detail=f"Bank account {bank_account_id} not found")

        bank_account.last_reconciled_date = reconciled_date
        bank_account.last_reconciled_balance = reconciled_balance
        db.flush()

        return bank_account

    def deactivate(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        updated_by: Optional[UUID] = None,
    ) -> BankAccount:
        """
        Deactivate a bank account (soft delete).

        Sets the account status to 'closed'. The account cannot be closed
        if it has a non-zero balance.

        Args:
            db: Database session
            organization_id: Organization scope
            bank_account_id: Bank account to deactivate
            updated_by: User performing the action

        Returns:
            Updated BankAccount

        Raises:
            ValueError: If account not found or has non-zero balance
        """
        org_id = coerce_uuid(organization_id)
        bank_account = db.get(BankAccount, bank_account_id)
        if not bank_account or bank_account.organization_id != org_id:
            raise HTTPException(status_code=404, detail=f"Bank account {bank_account_id} not found")

        if bank_account.status == BankAccountStatus.closed:
            raise HTTPException(status_code=400, detail="Bank account is already closed")

        # Check GL balance before closing
        gl_balance = self.get_gl_balance(db, org_id, bank_account_id)
        if gl_balance != Decimal("0"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot close bank account with non-zero balance: {gl_balance}",
            )

        bank_account.status = BankAccountStatus.closed
        bank_account.updated_by = updated_by
        db.flush()

        return bank_account

    def get_gl_balance(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        as_of_date: Optional[datetime] = None,
    ) -> Decimal:
        """Get current GL balance for the bank account."""
        org_id = coerce_uuid(organization_id)
        bank_account = db.get(BankAccount, bank_account_id)
        if not bank_account or bank_account.organization_id != org_id:
            raise HTTPException(status_code=404, detail=f"Bank account {bank_account_id} not found")

        # Query GL balance from journal entry lines
        from app.models.ifrs.gl.journal_entry_line import JournalEntryLine
        from app.models.ifrs.gl.journal_entry import JournalEntry, JournalEntryStatus

        query = select(
            JournalEntryLine.debit_amount,
            JournalEntryLine.credit_amount,
        ).join(
            JournalEntry,
            JournalEntryLine.entry_id == JournalEntry.entry_id,
        ).where(
            and_(
                JournalEntryLine.account_id == bank_account.gl_account_id,
                JournalEntry.status == JournalEntryStatus.posted,
            )
        )

        if as_of_date:
            query = query.where(JournalEntry.entry_date <= as_of_date)

        results = db.execute(query).all()

        total_debit = sum(r.debit_amount or Decimal("0") for r in results)
        total_credit = sum(r.credit_amount or Decimal("0") for r in results)

        # For asset accounts (bank), balance = debit - credit
        return total_debit - total_credit


# Singleton instance
bank_account_service = BankAccountService()
