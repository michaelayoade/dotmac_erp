"""Extracts ERP's legacy general ledger as read-only composition evidence.

ADR-0003 selects a governed-opening clean installation and forbids replaying the
legacy transaction history into it. This module therefore inventories the
legacy chart, calendar, dimensions and period effects for forensic review only.
It deliberately exports no loader.

The clean bootstrap is a separate future adapter over reviewed private input and
the module's published commands. Keeping it separate prevents a useful legacy
reader from quietly becoming a table-copy migration.

## Masters are rows; transactions are a work list

The chart of accounts, the fiscal calendar and the dimension registry are
bounded and are extracted row by row.  Journals and posted lines are not: a
single organization's ledger runs to millions of lines, and a "plan" holding all
of them would be a copy of the database with none of its guarantees.

So the transactional half is extracted as a WORK LIST — one item per fiscal
period, carrying its counts and ERP's digest for that period. The work list
describes the legacy record without copying its lines or granting them admission
to the clean system.

## Three shape changes this must perform, not copy

1. **Dimensions.**  ERP carries four FIXED dimension columns on its lines
   (business unit, cost centre, project, segment).  The module carries a generic
   dimension registry.  So the extraction synthesises four dimension definitions
   and reads their values from ERP's own masters — real codes and names, not
   stringified ids.
2. **Period status.**  ERP keeps status as a column on `gl.fiscal_period`; the
   module keeps an event stream (`period_events`).  A single current status
   cannot reconstruct a history, so the extraction states the CURRENT status and
   the loader opens each period and replays it forward to that status.  What is
   lost — who closed a period in 2024 and when — is lost in ERP too, except for
   the `soft_closed_at`/`hard_closed_at` stamps this carries across.
3. **Account classification, and WHERE it lives.**  ERP puts the IFRS class on
   the CATEGORY (`gl.account_category.ifrs_category`) and the posting nature on
   the ACCOUNT (`gl.account.account_type`: control, posting, statistical).  The
   module splits them the same way — `account_categories.account_class` and
   `accounts.kind` — but names them differently, so both mappings live in one
   table each below (`IFRS_CATEGORY_TO_ACCOUNT_CLASS`, `ACCOUNT_TYPE_TO_KIND`)
   with an explicit failure for anything unmapped.  Reading ERP's
   `account_type` as if it were the module's `account_class` would put "POSTING"
   where "ASSET" belongs and misclassify the entire trial balance — the exact
   error a single shared mapping table makes visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from collections.abc import Mapping
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.core_org.business_unit import BusinessUnit
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.project import Project
from app.models.finance.core_org.reporting_segment import ReportingSegment
from app.models.finance.gl.account import Account, AccountType
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.fiscal_year import FiscalYear
from app.models.finance.gl.journal_entry import JournalEntry
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from app.services.finance.gl.accounting_shadow import (
    ErpLedgerDigestService,
    PeriodLedgerDigest,
    PeriodScope,
)


class BackfillNotPossible(RuntimeError):
    """ERP holds something this extraction cannot faithfully represent."""


#: The four dimensions ERP carries as fixed columns, in the order the module's
#: registry will hold them.  The code is the module-side dimension code; the
#: attribute is the ERP column that references it.
@dataclass(frozen=True)
class DimensionBinding:
    code: str
    name: str
    line_column: str


DIMENSION_BINDINGS: Final[tuple[DimensionBinding, ...]] = (
    DimensionBinding("BUSINESS_UNIT", "Business unit", "business_unit_id"),
    DimensionBinding("COST_CENTER", "Cost centre", "cost_center_id"),
    DimensionBinding("PROJECT", "Project", "project_id"),
    DimensionBinding("SEGMENT", "Reporting segment", "segment_id"),
)

#: ERP category IFRS class -> module `account_categories.account_class`.
#:
#: Plural to singular, and nothing else: the two vocabularies genuinely agree on
#: the six classes.  A category absent from this map is not defaulted — an
#: account group whose class the module would guess is an account group whose
#: trial balance the module would misreport.
IFRS_CATEGORY_TO_ACCOUNT_CLASS: Final[Mapping[IFRSCategory, str]] = {
    IFRSCategory.ASSETS: "ASSET",
    IFRSCategory.LIABILITIES: "LIABILITY",
    IFRSCategory.EQUITY: "EQUITY",
    IFRSCategory.REVENUE: "REVENUE",
    IFRSCategory.EXPENSES: "EXPENSE",
    IFRSCategory.OTHER_COMPREHENSIVE_INCOME: "OTHER_COMPREHENSIVE_INCOME",
}

#: ERP `gl.account.account_type` -> module `accounts.kind`.
#:
#: An identity mapping today, written out rather than assumed: it is the
#: statement that ERP's "account type" is the module's KIND and not its CLASS,
#: which is the confusion this whole extraction has to avoid.
ACCOUNT_TYPE_TO_KIND: Final[Mapping[AccountType, str]] = {
    AccountType.CONTROL: "CONTROL",
    AccountType.POSTING: "POSTING",
    AccountType.STATISTICAL: "STATISTICAL",
}


@dataclass(frozen=True)
class CategoryRow:
    code: str
    name: str
    account_class: str
    parent_code: str | None
    hierarchy_level: int
    display_order: int
    is_active: bool


@dataclass(frozen=True)
class AccountRow:
    code: str
    name: str
    category_code: str
    kind: str
    normal_balance: str
    currency_code: str | None
    is_active: bool
    is_posting_allowed: bool


@dataclass(frozen=True)
class FiscalYearRow:
    code: str
    name: str
    start_date: date
    end_date: date
    is_closed: bool


@dataclass(frozen=True)
class FiscalPeriodRow:
    year_code: str
    period_number: int
    name: str
    start_date: date
    end_date: date
    status: str
    soft_closed_at: datetime | None
    hard_closed_at: datetime | None


@dataclass(frozen=True)
class DimensionValueRow:
    dimension_code: str
    value_code: str
    value_name: str
    is_active: bool


@dataclass(frozen=True)
class MasterBackfill:
    """Everything the module needs before a single journal can be loaded."""

    organization_id: UUID
    categories: tuple[CategoryRow, ...]
    accounts: tuple[AccountRow, ...]
    fiscal_years: tuple[FiscalYearRow, ...]
    fiscal_periods: tuple[FiscalPeriodRow, ...]
    dimensions: tuple[DimensionBinding, ...]
    dimension_values: tuple[DimensionValueRow, ...]

    def counts(self) -> dict[str, int]:
        return {
            "categories": len(self.categories),
            "accounts": len(self.accounts),
            "fiscal_years": len(self.fiscal_years),
            "fiscal_periods": len(self.fiscal_periods),
            "dimensions": len(self.dimensions),
            "dimension_values": len(self.dimension_values),
        }


@dataclass(frozen=True)
class PeriodWorkItem:
    """One historical period inventory item with exact digest evidence."""

    scope: PeriodScope
    fiscal_period_id: UUID
    journal_count: int
    posted_line_count: int
    erp_digest: PeriodLedgerDigest


class AccountingBackfillExtractor:
    """Reads ERP.  Writes nothing, anywhere — including to ERP.

    Safe to run against a live database while ERP remains the authority. The
    extraction has to be repeatable as forensic evidence, and an extraction
    with a side effect is not.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._digests = ErpLedgerDigestService(db)

    # -- masters ---------------------------------------------------------

    def extract_masters(self, organization_id: UUID) -> MasterBackfill:
        categories = self._categories(organization_id)
        return MasterBackfill(
            organization_id=organization_id,
            categories=categories,
            accounts=self._accounts(organization_id),
            fiscal_years=self._fiscal_years(organization_id),
            fiscal_periods=self._fiscal_periods(organization_id),
            dimensions=DIMENSION_BINDINGS,
            dimension_values=self._dimension_values(organization_id),
        )

    def _categories(self, organization_id: UUID) -> tuple[CategoryRow, ...]:
        rows = list(
            self.db.scalars(
                select(AccountCategory)
                .where(AccountCategory.organization_id == organization_id)
                .order_by(
                    AccountCategory.hierarchy_level,
                    AccountCategory.display_order,
                    AccountCategory.category_code,
                )
            ).all()
        )
        codes = {row.category_id: row.category_code for row in rows}
        extracted: list[CategoryRow] = []
        for row in rows:
            parent_code: str | None = None
            if row.parent_category_id is not None:
                parent_code = codes.get(row.parent_category_id)
                if parent_code is None:
                    raise BackfillNotPossible(
                        f"category {row.category_code} has parent "
                        f"{row.parent_category_id} outside this organization; "
                        "the hierarchy cannot be reproduced without it"
                    )
            try:
                account_class = IFRS_CATEGORY_TO_ACCOUNT_CLASS[row.ifrs_category]
            except KeyError:
                raise BackfillNotPossible(
                    f"category {row.category_code} has IFRS category "
                    f"{row.ifrs_category!r}, which has no mapped module account "
                    "class; add the mapping deliberately rather than defaulting it"
                ) from None
            extracted.append(
                CategoryRow(
                    code=row.category_code,
                    name=row.category_name,
                    account_class=account_class,
                    parent_code=parent_code,
                    hierarchy_level=row.hierarchy_level,
                    display_order=row.display_order,
                    is_active=row.is_active,
                )
            )
        return tuple(extracted)

    def _accounts(self, organization_id: UUID) -> tuple[AccountRow, ...]:
        stmt = (
            select(Account, AccountCategory.category_code)
            .join(AccountCategory, AccountCategory.category_id == Account.category_id)
            .where(Account.organization_id == organization_id)
            .order_by(Account.account_code)
        )
        extracted: list[AccountRow] = []
        for account, category_code in self.db.execute(stmt):
            try:
                kind = ACCOUNT_TYPE_TO_KIND[account.account_type]
            except KeyError:
                raise BackfillNotPossible(
                    f"account {account.account_code} has ERP type "
                    f"{account.account_type!r}, which has no mapped module kind; "
                    "add the mapping deliberately rather than defaulting it"
                ) from None
            extracted.append(
                AccountRow(
                    code=account.account_code,
                    name=account.account_name,
                    category_code=category_code,
                    kind=kind,
                    normal_balance=account.normal_balance.value,
                    currency_code=account.default_currency_code,
                    is_active=account.is_active,
                    is_posting_allowed=account.is_posting_allowed,
                )
            )
        return tuple(extracted)

    def _fiscal_years(self, organization_id: UUID) -> tuple[FiscalYearRow, ...]:
        rows = self.db.scalars(
            select(FiscalYear)
            .where(FiscalYear.organization_id == organization_id)
            .order_by(FiscalYear.start_date)
        ).all()
        return tuple(
            FiscalYearRow(
                code=row.year_code,
                name=row.year_name,
                start_date=row.start_date,
                end_date=row.end_date,
                is_closed=row.is_closed,
            )
            for row in rows
        )

    def _fiscal_periods(self, organization_id: UUID) -> tuple[FiscalPeriodRow, ...]:
        stmt = (
            select(FiscalPeriod, FiscalYear.year_code)
            .join(FiscalYear, FiscalYear.fiscal_year_id == FiscalPeriod.fiscal_year_id)
            .where(FiscalPeriod.organization_id == organization_id)
            .order_by(FiscalYear.start_date, FiscalPeriod.period_number)
        )
        return tuple(
            FiscalPeriodRow(
                year_code=year_code,
                period_number=period.period_number,
                name=period.period_name,
                start_date=period.start_date,
                end_date=period.end_date,
                status=period.status.value,
                soft_closed_at=period.soft_closed_at,
                hard_closed_at=period.hard_closed_at,
            )
            for period, year_code in self.db.execute(stmt)
        )

    def _dimension_values(self, organization_id: UUID) -> tuple[DimensionValueRow, ...]:
        """Read the four ERP dimension masters into the generic registry shape.

        Read from the MASTERS, not from distinct ids observed on ledger lines.
        Deriving the registry from usage would silently drop every dimension
        value that exists but has not been posted to yet, and would give the
        module a registry that shrinks whenever history is trimmed.
        """
        values: list[DimensionValueRow] = []
        for unit in self.db.scalars(
            select(BusinessUnit)
            .where(BusinessUnit.organization_id == organization_id)
            .order_by(BusinessUnit.unit_code)
        ).all():
            values.append(
                DimensionValueRow(
                    "BUSINESS_UNIT", unit.unit_code, unit.unit_name, unit.is_active
                )
            )
        for centre in self.db.scalars(
            select(CostCenter)
            .where(CostCenter.organization_id == organization_id)
            .order_by(CostCenter.cost_center_code)
        ).all():
            values.append(
                DimensionValueRow(
                    "COST_CENTER",
                    centre.cost_center_code,
                    centre.cost_center_name,
                    centre.is_active,
                )
            )
        for project in self.db.scalars(
            select(Project)
            .where(Project.organization_id == organization_id)
            .order_by(Project.project_code)
        ).all():
            # `core_org.project` carries a lifecycle status rather than an
            # is_active flag, and which statuses count as active is a Projects
            # decision, not this extraction's. Every project is registered as an
            # active dimension value; deactivation is a later Projects slice.
            values.append(
                DimensionValueRow(
                    "PROJECT", project.project_code, project.project_name, True
                )
            )
        for segment in self.db.scalars(
            select(ReportingSegment)
            .where(ReportingSegment.organization_id == organization_id)
            .order_by(ReportingSegment.segment_code)
        ).all():
            values.append(
                DimensionValueRow(
                    "SEGMENT",
                    segment.segment_code,
                    segment.segment_name,
                    segment.is_active,
                )
            )
        return tuple(values)

    # -- transactional work list -----------------------------------------

    def period_work_list(self, organization_id: UUID) -> tuple[PeriodWorkItem, ...]:
        """One item per fiscal period, oldest first, each carrying its evidence.

        Ordered by period start so the loader replays history forward: a period
        cannot be closed before the one before it, and the module enforces that.
        """
        stmt = (
            select(
                FiscalPeriod.fiscal_period_id,
                FiscalPeriod.period_number,
                FiscalYear.year_code,
            )
            .join(FiscalYear, FiscalYear.fiscal_year_id == FiscalPeriod.fiscal_year_id)
            .where(FiscalPeriod.organization_id == organization_id)
            .order_by(FiscalYear.start_date, FiscalPeriod.period_number)
        )
        items: list[PeriodWorkItem] = []
        for period_id, period_number, year_code in self.db.execute(stmt):
            scope = PeriodScope(
                organization_id=organization_id,
                fiscal_year_code=year_code,
                period_number=period_number,
            )
            items.append(
                PeriodWorkItem(
                    scope=scope,
                    fiscal_period_id=period_id,
                    journal_count=self._journal_count(organization_id, period_id),
                    posted_line_count=self._posted_line_count(
                        organization_id, period_id
                    ),
                    erp_digest=self._digests.build_digest(organization_id, period_id),
                )
            )
        return tuple(items)

    def _journal_count(self, organization_id: UUID, fiscal_period_id: UUID) -> int:
        from sqlalchemy import func

        return int(
            self.db.scalar(
                select(func.count(JournalEntry.journal_entry_id)).where(
                    JournalEntry.organization_id == organization_id,
                    JournalEntry.fiscal_period_id == fiscal_period_id,
                )
            )
            or 0
        )

    def _posted_line_count(self, organization_id: UUID, fiscal_period_id: UUID) -> int:
        from sqlalchemy import func

        return int(
            self.db.scalar(
                select(func.count(PostedLedgerLine.ledger_line_id)).where(
                    PostedLedgerLine.organization_id == organization_id,
                    PostedLedgerLine.fiscal_period_id == fiscal_period_id,
                )
            )
            or 0
        )


__all__ = [
    "ACCOUNT_TYPE_TO_KIND",
    "IFRS_CATEGORY_TO_ACCOUNT_CLASS",
    "AccountRow",
    "AccountingBackfillExtractor",
    "BackfillNotPossible",
    "CategoryRow",
    "DIMENSION_BINDINGS",
    "DimensionBinding",
    "DimensionValueRow",
    "FiscalPeriodRow",
    "FiscalYearRow",
    "MasterBackfill",
    "PeriodWorkItem",
]
