"""
FinancialStatementService - Financial statement line management.

Manages financial statement line items and generates statements per IAS 1.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.rpt.financial_statement_line import (
    FinancialStatementLine,
    StatementType,
)
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class StatementLineInput:
    """Input for creating a financial statement line."""

    statement_type: StatementType
    line_code: str
    line_name: str
    sequence_number: int
    calculation_type: str = "SUM_ACCOUNTS"
    parent_line_id: UUID | None = None
    description: str | None = None
    indent_level: int = 0
    is_header: bool = False
    is_total: bool = False
    is_subtotal: bool = False
    is_separator: bool = False
    calculation_formula: str | None = None
    account_codes: list | None = None
    account_categories: list | None = None
    exclude_account_codes: list | None = None
    normal_balance: str = "DEBIT"
    display_sign: str = "NATURAL"
    xbrl_element: str | None = None
    formatting: dict | None = None


@dataclass
class StatementLineResult:
    """Result for a statement line with calculated amounts."""

    line_id: UUID
    line_code: str
    line_name: str
    sequence_number: int
    indent_level: int
    is_header: bool
    is_total: bool
    is_subtotal: bool
    current_period: Decimal
    prior_period: Decimal | None
    variance: Decimal | None
    variance_percent: Decimal | None


@dataclass
class FinancialStatementResult:
    """Complete financial statement."""

    statement_type: StatementType
    fiscal_period_id: UUID
    organization_name: str
    currency_code: str
    lines: list[StatementLineResult]
    generated_at: str


class FinancialStatementService(ListResponseMixin):
    """
    Service for financial statement management.

    Handles:
    - Statement line item definitions
    - Statement generation from GL balances
    - Line calculations and aggregations
    - Multi-period comparisons
    """

    @staticmethod
    def create_line(
        db: Session,
        organization_id: UUID,
        input: StatementLineInput,
    ) -> FinancialStatementLine:
        """
        Create a financial statement line.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Line input data

        Returns:
            Created FinancialStatementLine
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate line code
        existing = db.scalar(
            select(FinancialStatementLine).where(
                FinancialStatementLine.organization_id == org_id,
                FinancialStatementLine.statement_type == input.statement_type,
                FinancialStatementLine.line_code == input.line_code,
            )
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Line code {input.line_code} already exists for this statement type",
            )

        # Validate parent if specified
        if input.parent_line_id:
            parent = db.get(FinancialStatementLine, input.parent_line_id)
            if not parent or parent.organization_id != org_id:
                raise HTTPException(status_code=404, detail="Parent line not found")
            if parent.statement_type != input.statement_type:
                raise HTTPException(
                    status_code=400,
                    detail="Parent line must be same statement type",
                )

        line = FinancialStatementLine(
            organization_id=org_id,
            statement_type=input.statement_type,
            line_code=input.line_code,
            line_name=input.line_name,
            description=input.description,
            parent_line_id=input.parent_line_id,
            sequence_number=input.sequence_number,
            indent_level=input.indent_level,
            is_header=input.is_header,
            is_total=input.is_total,
            is_subtotal=input.is_subtotal,
            is_separator=input.is_separator,
            calculation_type=input.calculation_type,
            calculation_formula=input.calculation_formula,
            account_codes=input.account_codes,
            account_categories=input.account_categories,
            exclude_account_codes=input.exclude_account_codes,
            normal_balance=input.normal_balance,
            display_sign=input.display_sign,
            xbrl_element=input.xbrl_element,
            formatting=input.formatting,
        )

        db.add(line)
        db.commit()
        db.refresh(line)

        return line

    @staticmethod
    def update_line(
        db: Session,
        organization_id: UUID,
        line_id: UUID,
        line_name: str | None = None,
        description: str | None = None,
        sequence_number: int | None = None,
        indent_level: int | None = None,
        formatting: dict | None = None,
    ) -> FinancialStatementLine:
        """
        Update a financial statement line.

        Args:
            db: Database session
            organization_id: Organization scope
            line_id: Line to update
            line_name: New name
            description: New description
            sequence_number: New sequence
            indent_level: New indent
            formatting: New formatting

        Returns:
            Updated FinancialStatementLine
        """
        org_id = coerce_uuid(organization_id)
        ln_id = coerce_uuid(line_id)

        line = db.get(FinancialStatementLine, ln_id)
        if not line or line.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Statement line not found")

        if line_name is not None:
            line.line_name = line_name
        if description is not None:
            line.description = description
        if sequence_number is not None:
            line.sequence_number = sequence_number
        if indent_level is not None:
            line.indent_level = indent_level
        if formatting is not None:
            line.formatting = formatting

        db.commit()
        db.refresh(line)

        return line

    @staticmethod
    def update_calculation(
        db: Session,
        organization_id: UUID,
        line_id: UUID,
        calculation_type: str,
        calculation_formula: str | None = None,
        account_codes: list | None = None,
        account_categories: list | None = None,
        exclude_account_codes: list | None = None,
    ) -> FinancialStatementLine:
        """
        Update line calculation configuration.

        Args:
            db: Database session
            organization_id: Organization scope
            line_id: Line to update
            calculation_type: Type of calculation
            calculation_formula: Formula if applicable
            account_codes: Account codes to include
            account_categories: Account categories to include
            exclude_account_codes: Account codes to exclude

        Returns:
            Updated FinancialStatementLine
        """
        org_id = coerce_uuid(organization_id)
        ln_id = coerce_uuid(line_id)

        line = db.get(FinancialStatementLine, ln_id)
        if not line or line.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Statement line not found")

        line.calculation_type = calculation_type
        line.calculation_formula = calculation_formula
        line.account_codes = account_codes
        line.account_categories = account_categories
        line.exclude_account_codes = exclude_account_codes

        db.commit()
        db.refresh(line)

        return line

    @staticmethod
    def reorder_lines(
        db: Session,
        organization_id: UUID,
        statement_type: StatementType,
        line_sequences: list[tuple[UUID, int]],
    ) -> builtins.list[FinancialStatementLine]:
        """
        Reorder statement lines.

        Args:
            db: Database session
            organization_id: Organization scope
            statement_type: Statement type
            line_sequences: List of (line_id, new_sequence) tuples

        Returns:
            Updated lines
        """
        org_id = coerce_uuid(organization_id)

        updated_lines = []
        for line_id, new_sequence in line_sequences:
            line = db.get(FinancialStatementLine, coerce_uuid(line_id))
            if (
                line
                and line.organization_id == org_id
                and line.statement_type == statement_type
            ):
                line.sequence_number = new_sequence
                updated_lines.append(line)

        db.commit()

        for line in updated_lines:
            db.refresh(line)

        return updated_lines

    @staticmethod
    def get_statement_structure(
        db: Session,
        organization_id: str,
        statement_type: StatementType,
    ) -> list[FinancialStatementLine]:
        """
        Get statement structure (all lines in order).

        Args:
            db: Database session
            organization_id: Organization scope
            statement_type: Type of statement

        Returns:
            Ordered list of statement lines
        """
        return list(
            db.scalars(
                select(FinancialStatementLine)
                .where(
                    FinancialStatementLine.organization_id
                    == coerce_uuid(organization_id),
                    FinancialStatementLine.statement_type == statement_type,
                    FinancialStatementLine.is_active == True,
                )
                .order_by(FinancialStatementLine.sequence_number)
            )
        )

    @staticmethod
    def calculate_line_amount(
        line: FinancialStatementLine,
        account_balances: dict[str, Decimal],
    ) -> Decimal:
        """
        Calculate line amount from account balances.

        Args:
            line: Statement line definition
            account_balances: Dict of account_code -> balance

        Returns:
            Calculated amount
        """
        if line.is_header or line.is_separator:
            return Decimal("0")

        total = Decimal("0")

        if line.calculation_type == "SUM_ACCOUNTS":
            # Sum balances for specified accounts
            if line.account_codes:
                for code in line.account_codes:
                    if code in account_balances:
                        total += account_balances[code]

            # Exclude specified accounts
            if line.exclude_account_codes:
                for code in line.exclude_account_codes:
                    if code in account_balances:
                        total -= account_balances[code]

        elif line.calculation_type == "FORMULA":
            # Would evaluate calculation_formula
            # For now, return 0 - actual implementation would use a formula parser
            pass

        # Apply sign convention
        if line.display_sign == "REVERSE":
            total = -total
        elif line.display_sign == "ABSOLUTE":
            total = abs(total)

        return total

    @staticmethod
    def deactivate(
        db: Session,
        organization_id: UUID,
        line_id: UUID,
    ) -> FinancialStatementLine:
        """Deactivate a statement line."""
        org_id = coerce_uuid(organization_id)
        ln_id = coerce_uuid(line_id)

        line = db.get(FinancialStatementLine, ln_id)
        if not line or line.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Statement line not found")

        line.is_active = False
        db.commit()
        db.refresh(line)

        return line

    @staticmethod
    def copy_statement_structure(
        db: Session,
        source_organization_id: UUID,
        target_organization_id: UUID,
        statement_type: StatementType,
    ) -> builtins.list[FinancialStatementLine]:
        """
        Copy statement structure from one org to another.

        Args:
            db: Database session
            source_organization_id: Source org
            target_organization_id: Target org
            statement_type: Statement type to copy

        Returns:
            Created lines
        """
        src_org_id = coerce_uuid(source_organization_id)
        tgt_org_id = coerce_uuid(target_organization_id)

        # Get source lines
        source_lines = list(
            db.scalars(
                select(FinancialStatementLine)
                .where(
                    FinancialStatementLine.organization_id == src_org_id,
                    FinancialStatementLine.statement_type == statement_type,
                    FinancialStatementLine.is_active == True,
                )
                .order_by(FinancialStatementLine.sequence_number)
            )
        )

        # Map old IDs to new IDs for parent references
        id_map: dict[UUID, UUID] = {}
        created_lines = []

        for source in source_lines:
            new_line = FinancialStatementLine(
                organization_id=tgt_org_id,
                statement_type=source.statement_type,
                line_code=source.line_code,
                line_name=source.line_name,
                description=source.description,
                sequence_number=source.sequence_number,
                indent_level=source.indent_level,
                is_header=source.is_header,
                is_total=source.is_total,
                is_subtotal=source.is_subtotal,
                is_separator=source.is_separator,
                calculation_type=source.calculation_type,
                calculation_formula=source.calculation_formula,
                account_codes=source.account_codes,
                account_categories=source.account_categories,
                exclude_account_codes=source.exclude_account_codes,
                normal_balance=source.normal_balance,
                display_sign=source.display_sign,
                xbrl_element=source.xbrl_element,
                formatting=source.formatting,
            )

            db.add(new_line)
            db.flush()

            id_map[source.line_id] = new_line.line_id
            created_lines.append(new_line)

        # Update parent references
        for i, source in enumerate(source_lines):
            if source.parent_line_id and source.parent_line_id in id_map:
                created_lines[i].parent_line_id = id_map[source.parent_line_id]

        db.commit()

        for line in created_lines:
            db.refresh(line)

        return created_lines

    @staticmethod
    def get(
        db: Session,
        line_id: str,
        organization_id: UUID | None = None,
    ) -> FinancialStatementLine:
        """Get a statement line by ID."""
        line = db.get(FinancialStatementLine, coerce_uuid(line_id))
        if not line:
            raise HTTPException(status_code=404, detail="Statement line not found")
        if organization_id is not None and line.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Statement line not found")
        return line

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        statement_type: StatementType | None = None,
        parent_line_id: str | None = None,
        is_active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FinancialStatementLine]:
        """List statement lines with optional filters."""
        stmt = select(FinancialStatementLine)

        if organization_id:
            stmt = stmt.where(
                FinancialStatementLine.organization_id == coerce_uuid(organization_id)
            )

        if statement_type:
            stmt = stmt.where(FinancialStatementLine.statement_type == statement_type)

        if parent_line_id:
            stmt = stmt.where(
                FinancialStatementLine.parent_line_id == coerce_uuid(parent_line_id)
            )

        if is_active is not None:
            stmt = stmt.where(FinancialStatementLine.is_active == is_active)

        stmt = (
            stmt.order_by(FinancialStatementLine.sequence_number)
            .limit(limit)
            .offset(offset)
        )
        return list(db.scalars(stmt))


# Module-level singleton instance
financial_statement_service = FinancialStatementService()
