"""ReconciliationCoreService component."""

from __future__ import annotations

from datetime import timedelta

from app.services.finance.banking.reconciliation_parts.base import (
    AMOUNT_MISMATCH_ABSOLUTE_TOLERANCE,
    AMOUNT_MISMATCH_RELATIVE_THRESHOLD,
    AuditAction,
    BankAccount,
    BankReconciliation,
    BankReconciliationLine,
    BankStatement,
    BankStatementLine,
    BankStatementLineMatch,
    Decimal,
    HTTPException,
    JournalEntry,
    JournalEntryLine,
    JournalStatus,
    ReconciliationInput,
    ReconciliationMatchType,
    ReconciliationStatus,
    Session,
    UUID,
    and_,
    date,
    datetime,
    fire_audit_event,
    func,
    select,
)


class ReconciliationCoreService:
    """Bank reconciliation methods for core."""

    def _validate_amount_match(
        self,
        statement_amount: Decimal,
        gl_amount: Decimal,
        *,
        force_match: bool = False,
    ) -> None:
        """Block obvious mismatches unless explicitly overridden."""
        abs_statement = abs(statement_amount)
        abs_gl = abs(gl_amount)
        max_amount = max(abs_statement, abs_gl)
        diff = abs(abs_statement - abs_gl)

        # Absolute tolerance handles harmless rounding deltas.
        if diff <= AMOUNT_MISMATCH_ABSOLUTE_TOLERANCE:
            return

        # If one side is zero and the other is not, always require override.
        mismatch_ratio = Decimal("1") if max_amount == 0 else (diff / max_amount)
        if mismatch_ratio > AMOUNT_MISMATCH_RELATIVE_THRESHOLD and not force_match:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Amount mismatch requires review: statement amount "
                    f"{statement_amount:,.2f} vs GL amount {gl_amount:,.2f}. "
                    "Confirm and retry with force_match=true to proceed."
                ),
            )

    def _get_for_org(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
    ) -> BankReconciliation:
        reconciliation = db.get(BankReconciliation, reconciliation_id)
        if not reconciliation:
            raise HTTPException(
                status_code=404, detail=f"Reconciliation {reconciliation_id} not found"
            )
        if reconciliation.organization_id != organization_id:
            raise HTTPException(
                status_code=404,
                detail=f"Reconciliation {reconciliation_id} not found",
            )
        return reconciliation

    def create_reconciliation(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        input: ReconciliationInput,
        prepared_by: UUID | None = None,
    ) -> BankReconciliation:
        """Create a new reconciliation session."""
        # Validate bank account
        bank_account = db.get(BankAccount, bank_account_id)
        if not bank_account:
            raise HTTPException(
                status_code=404, detail=f"Bank account {bank_account_id} not found"
            )
        if bank_account.organization_id != organization_id:
            raise HTTPException(
                status_code=403,
                detail="Bank account does not belong to this organization",
            )

        # Check for existing reconciliation at this date
        existing = db.execute(
            select(BankReconciliation).where(
                and_(
                    BankReconciliation.organization_id == organization_id,
                    BankReconciliation.bank_account_id == bank_account_id,
                    BankReconciliation.reconciliation_date == input.reconciliation_date,
                )
            )
        ).scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Reconciliation already exists for {input.reconciliation_date}",
            )

        # GL opening = cumulative balance BEFORE period_start (i.e. close of prior day)
        gl_opening = self._get_gl_balance(
            db,
            bank_account.gl_account_id,
            input.period_start - timedelta(days=1),
            organization_id=organization_id,
        )
        # GL closing = cumulative balance AS OF period_end
        gl_closing = self._get_gl_balance(
            db,
            bank_account.gl_account_id,
            input.period_end,
            organization_id=organization_id,
        )

        # Get prior outstanding items
        prior_recon = self._get_prior_reconciliation(
            db, bank_account_id, input.reconciliation_date, organization_id
        )
        prior_deposits = Decimal("0")
        prior_payments = Decimal("0")

        if prior_recon:
            prior_deposits = prior_recon.outstanding_deposits
            prior_payments = prior_recon.outstanding_payments

        reconciliation = BankReconciliation(
            organization_id=organization_id,
            bank_account_id=bank_account_id,
            reconciliation_date=input.reconciliation_date,
            period_start=input.period_start,
            period_end=input.period_end,
            statement_opening_balance=input.statement_opening_balance,
            statement_closing_balance=input.statement_closing_balance,
            gl_opening_balance=gl_opening,
            gl_closing_balance=gl_closing,
            currency_code=bank_account.currency_code,
            status=ReconciliationStatus.draft,
            prior_outstanding_deposits=prior_deposits,
            prior_outstanding_payments=prior_payments,
            notes=input.notes,
            prepared_by=prepared_by,
            prepared_at=datetime.utcnow(),
        )

        db.add(reconciliation)
        db.flush()

        # Import any pre-existing matches in this period (e.g. from a prior
        # CLEAN_SWEEP auto-match run) into bank_reconciliation_lines so the
        # workspace shows them as matched rather than orphaned. Without this,
        # statement lines with is_matched=true silently disappear from the
        # workspace and the matched-count shows zero even though the
        # underlying bank_statement_line_matches rows exist.
        imported = self._import_existing_matches(
            db,
            reconciliation,
            bank_account,
            organization_id,
            prepared_by,
        )

        # Calculate initial difference
        reconciliation.calculate_difference()
        db.flush()

        fire_audit_event(
            db=db,
            organization_id=organization_id,
            table_schema="banking",
            table_name="reconciliation",
            record_id=str(reconciliation.reconciliation_id),
            action=AuditAction.INSERT,
            new_values={
                "bank_account_id": str(bank_account_id),
                "status": "draft",
                "imported_matches": imported,
            },
        )

        db.flush()
        return reconciliation

    def _import_existing_matches(
        self,
        db: Session,
        reconciliation: BankReconciliation,
        bank_account: BankAccount,
        organization_id: UUID,
        created_by: UUID | None,
    ) -> int:
        """Copy pre-existing bank_statement_line_matches into this rec.

        When CLEAN_SWEEP or earlier ad-hoc runs matched statement lines to GL
        entries outside of any reconciliation record, the matches live only in
        bank_statement_line_matches. A new period rec needs corresponding
        bank_reconciliation_lines rows or its workspace shows 0 matched.

        Returns the number of reconciliation lines created.
        """
        # Find every statement line in this rec's period that is matched to a GL
        # journal line, joining through bank_statement_line_matches.
        rows = db.execute(
            select(
                BankStatementLine.line_id.label("statement_line_id"),
                BankStatementLine.transaction_date,
                BankStatementLine.transaction_type,
                BankStatementLine.amount,
                BankStatementLine.description,
                BankStatementLine.reference,
                BankStatementLineMatch.journal_line_id,
                BankStatementLineMatch.is_primary,
                BankStatementLineMatch.match_type,
                JournalEntryLine.debit_amount,
                JournalEntryLine.credit_amount,
            )
            .join(
                BankStatement,
                BankStatement.statement_id == BankStatementLine.statement_id,
            )
            .join(
                BankStatementLineMatch,
                BankStatementLineMatch.statement_line_id == BankStatementLine.line_id,
            )
            .join(
                JournalEntryLine,
                JournalEntryLine.line_id == BankStatementLineMatch.journal_line_id,
            )
            .where(
                BankStatement.organization_id == organization_id,
                BankStatement.bank_account_id == reconciliation.bank_account_id,
                BankStatementLine.transaction_date >= reconciliation.period_start,
                BankStatementLine.transaction_date <= reconciliation.period_end,
            )
        ).all()

        created = 0
        matched_total = Decimal("0")
        grouped: dict[UUID, dict] = {}
        for row in rows:
            stmt_amt = row.amount or Decimal("0")
            if getattr(row.transaction_type, "value", row.transaction_type) != "credit":
                stmt_amt = -stmt_amt
            gl_amt = (row.debit_amount or Decimal("0")) - (
                row.credit_amount or Decimal("0")
            )
            group = grouped.setdefault(
                row.statement_line_id,
                {
                    "statement_amount": stmt_amt,
                    "gl_amount": Decimal("0"),
                    "journal_line_id": row.journal_line_id,
                    "match_count": 0,
                    "transaction_date": row.transaction_date,
                    "description": row.description,
                    "reference": row.reference,
                },
            )
            group["gl_amount"] += gl_amt
            group["match_count"] += 1
            if row.is_primary:
                group["journal_line_id"] = row.journal_line_id

        for statement_line_id, group in grouped.items():
            stmt_amt = group["statement_amount"]
            gl_amt = group["gl_amount"]
            matched_total += abs(stmt_amt)
            line = BankReconciliationLine(
                reconciliation_id=reconciliation.reconciliation_id,
                match_type=(
                    ReconciliationMatchType.split
                    if group["match_count"] > 1
                    else ReconciliationMatchType.auto_exact
                ),
                statement_line_id=statement_line_id,
                journal_line_id=group["journal_line_id"],
                transaction_date=group["transaction_date"],
                description=group["description"],
                reference=group["reference"],
                statement_amount=stmt_amt,
                gl_amount=gl_amt,
                difference=stmt_amt - gl_amt,
                is_adjustment=False,
                is_outstanding=False,
                is_cleared=True,
                cleared_at=datetime.utcnow(),
                created_by=created_by,
            )
            db.add(line)
            created += 1

        # Persist the total so the workspace template's
        # `{{ reconciliation.total_matched }}` shows the right figure.
        reconciliation.total_matched = matched_total

        if created:
            db.flush()
        return created

    def get(
        self, db: Session, organization_id: UUID, reconciliation_id: UUID
    ) -> BankReconciliation:
        """Get a reconciliation by ID."""
        return self._get_for_org(db, organization_id, reconciliation_id)

    def get_with_lines(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
    ) -> BankReconciliation | None:
        """Get reconciliation with all lines loaded."""
        recon = self._get_for_org(db, organization_id, reconciliation_id)
        _ = recon.lines
        return recon

    def list(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID | None = None,
        status: ReconciliationStatus | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BankReconciliation]:
        """List reconciliations with optional filters."""
        query = select(BankReconciliation).where(
            BankReconciliation.organization_id == organization_id
        )

        if bank_account_id:
            query = query.where(BankReconciliation.bank_account_id == bank_account_id)
        if status:
            query = query.where(BankReconciliation.status == status)
        if start_date:
            query = query.where(BankReconciliation.reconciliation_date >= start_date)
        if end_date:
            query = query.where(BankReconciliation.reconciliation_date <= end_date)

        query = query.order_by(BankReconciliation.reconciliation_date.desc())
        query = query.offset(offset).limit(limit)

        return list(db.execute(query).scalars().all())

    def count(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID | None = None,
        status: ReconciliationStatus | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> int:
        """Count reconciliations matching filters (for pagination)."""
        query = select(func.count(BankReconciliation.reconciliation_id)).where(
            BankReconciliation.organization_id == organization_id
        )

        if bank_account_id:
            query = query.where(BankReconciliation.bank_account_id == bank_account_id)
        if status:
            query = query.where(BankReconciliation.status == status)
        if start_date:
            query = query.where(BankReconciliation.reconciliation_date >= start_date)
        if end_date:
            query = query.where(BankReconciliation.reconciliation_date <= end_date)

        return db.execute(query).scalar() or 0

    def add_adjustment(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        transaction_date: date,
        amount: Decimal,
        description: str,
        adjustment_type: str,
        adjustment_account_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> BankReconciliationLine:
        """Add a reconciling adjustment."""
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        recon_line = BankReconciliationLine(
            reconciliation_id=reconciliation_id,
            match_type=ReconciliationMatchType.adjustment,
            transaction_date=transaction_date,
            description=description,
            statement_amount=amount,
            gl_amount=Decimal("0"),
            difference=amount,
            is_adjustment=True,
            adjustment_type=adjustment_type,
            adjustment_account_id=adjustment_account_id,
            created_by=created_by,
        )

        db.add(recon_line)

        # Update totals
        reconciliation.total_adjustments += amount
        reconciliation.calculate_difference()

        db.flush()
        return recon_line

    def add_outstanding_item(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        transaction_date: date,
        amount: Decimal,
        description: str,
        outstanding_type: str,  # "deposit" or "payment"
        reference: str | None = None,
        journal_line_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> BankReconciliationLine:
        """Add an outstanding item (deposit in transit or outstanding check)."""
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        recon_line = BankReconciliationLine(
            reconciliation_id=reconciliation_id,
            match_type=ReconciliationMatchType.manual,
            journal_line_id=journal_line_id,
            transaction_date=transaction_date,
            description=description,
            reference=reference,
            gl_amount=amount if outstanding_type == "deposit" else -amount,
            is_outstanding=True,
            outstanding_type=outstanding_type,
            created_by=created_by,
        )

        db.add(recon_line)

        # Update outstanding totals
        if outstanding_type == "deposit":
            reconciliation.outstanding_deposits += amount
        else:
            reconciliation.outstanding_payments += amount

        reconciliation.calculate_difference()

        db.flush()
        return recon_line

    def _get_gl_balance(
        self,
        db: Session,
        gl_account_id: UUID,
        as_of_date: date,
        organization_id: UUID | None = None,
    ) -> Decimal:
        """Get GL account balance as of a date."""
        conditions = [
            JournalEntryLine.account_id == gl_account_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date <= as_of_date,
        ]
        if organization_id is not None:
            conditions.append(JournalEntry.organization_id == organization_id)
        query = (
            select(
                func.coalesce(func.sum(JournalEntryLine.debit_amount), 0).label(
                    "debits"
                ),
                func.coalesce(func.sum(JournalEntryLine.credit_amount), 0).label(
                    "credits"
                ),
            )
            .join(JournalEntry)
            .where(and_(*conditions))
        )

        result = db.execute(query).one()
        return Decimal(str(result.debits)) - Decimal(str(result.credits))

    def _get_prior_reconciliation(
        self,
        db: Session,
        bank_account_id: UUID,
        before_date: date,
        organization_id: UUID,
    ) -> BankReconciliation | None:
        """Get most recent approved reconciliation before a date."""
        conditions = [
            BankReconciliation.organization_id == organization_id,
            BankReconciliation.bank_account_id == bank_account_id,
            BankReconciliation.status == ReconciliationStatus.approved,
            BankReconciliation.reconciliation_date < before_date,
        ]
        query = (
            select(BankReconciliation)
            .where(and_(*conditions))
            .order_by(BankReconciliation.reconciliation_date.desc())
            .limit(1)
        )

        return db.execute(query).scalar_one_or_none()
