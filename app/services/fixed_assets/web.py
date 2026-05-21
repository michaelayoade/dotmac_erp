"""
Fixed assets web view service.

Provides view-focused data for FA web routes.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from urllib.parse import quote
from typing import Any, TypedDict
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased
from starlette.datastructures import UploadFile

from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    SequenceType,
)
from app.models.finance.core_org.location import Location
from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_category import AssetCategory, DepreciationMethod
from app.models.fixed_assets.maintenance_request import (
    MaintenanceRequest,
    MaintenanceRequestStatus,
)
from app.models.fixed_assets.depreciation_run import DepreciationRun
from app.models.fixed_assets.depreciation_schedule import DepreciationSchedule
from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
from app.models.people.assets.audit import (
    AssetAuditDiscrepancy,
    AssetAuditLine,
    AssetAuditLineStatus,
    AssetAuditPlan,
    AssetAuditPlanStatus,
)
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service
from app.services.fixed_assets.asset import (
    AssetCategoryInput,
    AssetInput,
    asset_category_service,
    asset_service,
)
from app.services.fixed_assets.depreciation import DepreciationService
from app.services.people.assets.audit_service import AssetAuditService
from app.services.formatters import format_currency as _format_currency
from app.services.formatters import format_date as _format_date
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class GLReconciliationTotals(TypedDict):
    """Numeric totals for the fixed asset GL reconciliation page."""

    category_count: int
    asset_count: int
    register_cost: Decimal
    gl_cost: Decimal
    cost_variance: Decimal
    register_accumulated_depreciation: Decimal
    gl_accumulated_depreciation: Decimal
    accumulated_depreciation_variance: Decimal
    register_nbv: Decimal
    gl_nbv: Decimal
    nbv_variance: Decimal


EDITABLE_ASSET_STATUSES = (
    AssetStatus.NOT_IN_USE,
    AssetStatus.IN_USE,
    AssetStatus.IN_STORE,
    AssetStatus.FAULTY,
    AssetStatus.UNDER_REPAIR,
    AssetStatus.FULLY_DEPRECIATED,
)


def _safe_form_text(value: object) -> str:
    if value is None or isinstance(value, UploadFile):
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _parse_status(value: str | None) -> AssetStatus | None:
    if not value:
        return None
    try:
        return AssetStatus(value)
    except ValueError:
        try:
            return AssetStatus(value.upper())
        except ValueError:
            return None


def _try_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


class FixedAssetWebService:
    """View service for fixed assets web routes."""

    @staticmethod
    def _assignment_options(
        db: Session,
        organization_id: str,
        selected_employee_id: UUID | None = None,
    ) -> dict[str, object]:
        """Load department + employee options for asset assignment fields."""
        from app.models.people.hr.department import Department
        from app.models.people.hr.employee import Employee, EmployeeStatus
        from app.models.person import Person

        org_id = coerce_uuid(organization_id)
        selected_employee_uuid = (
            coerce_uuid(selected_employee_id) if selected_employee_id else None
        )

        departments = db.scalars(
            select(Department)
            .where(
                Department.organization_id == org_id,
                Department.is_active.is_(True),
            )
            .order_by(Department.department_name)
        ).all()

        employee_query = (
            select(
                Employee.employee_id,
                Employee.employee_code,
                Employee.department_id,
                Person.name_expr().label("full_name"),
            )
            .join(Person, Person.id == Employee.person_id)
            .where(Employee.organization_id == org_id)
            .order_by(Person.first_name, Person.last_name)
        )
        if selected_employee_uuid is not None:
            employee_query = employee_query.where(
                or_(
                    Employee.status == EmployeeStatus.ACTIVE,
                    Employee.employee_id == selected_employee_uuid,
                )
            )
        else:
            employee_query = employee_query.where(
                Employee.status == EmployeeStatus.ACTIVE
            )

        employee_rows = db.execute(employee_query).all()
        employees = [
            {
                "employee_id": str(row.employee_id),
                "employee_code": row.employee_code,
                "department_id": (
                    str(row.department_id) if row.department_id is not None else ""
                ),
                "full_name": row.full_name,
            }
            for row in employee_rows
        ]

        selected_department_id = ""
        if selected_employee_uuid is not None:
            selected_employee = next(
                (
                    employee
                    for employee in employees
                    if employee["employee_id"] == str(selected_employee_uuid)
                ),
                None,
            )
            if selected_employee:
                selected_department_id = str(selected_employee["department_id"] or "")

        return {
            "departments_list": [
                {
                    "department_id": str(department.department_id),
                    "department_code": department.department_code,
                    "department_name": department.department_name,
                }
                for department in departments
            ],
            "department_employees": employees,
            "selected_department_id": selected_department_id,
        }

    @staticmethod
    def _validate_assignment_selection(
        db: Session,
        organization_id: str,
        department_id: str | None,
        custodian_employee_id: str | None,
        *,
        allowed_employee_id: UUID | None = None,
    ) -> tuple[UUID | None, UUID | None]:
        """Validate department/employee pairing and return normalized UUIDs."""
        from app.models.people.hr.department import Department
        from app.models.people.hr.employee import Employee, EmployeeStatus

        org_id = coerce_uuid(organization_id)
        resolved_department_id = (
            coerce_uuid(department_id)
            if department_id and department_id.strip()
            else None
        )
        resolved_employee_id = (
            coerce_uuid(custodian_employee_id)
            if custodian_employee_id and custodian_employee_id.strip()
            else None
        )

        if resolved_department_id is not None:
            department = db.get(Department, resolved_department_id)
            if (
                not department
                or department.organization_id != org_id
                or not department.is_active
            ):
                raise HTTPException(
                    status_code=400, detail="Selected department is invalid"
                )

        if resolved_employee_id is None:
            return resolved_department_id, None

        employee_query = select(Employee).where(
            Employee.organization_id == org_id,
            Employee.employee_id == resolved_employee_id,
        )
        if allowed_employee_id is not None:
            employee_query = employee_query.where(
                or_(
                    Employee.status == EmployeeStatus.ACTIVE,
                    Employee.employee_id == coerce_uuid(allowed_employee_id),
                )
            )
        else:
            employee_query = employee_query.where(
                Employee.status == EmployeeStatus.ACTIVE
            )

        employee = db.scalar(employee_query)
        if employee is None:
            raise HTTPException(status_code=400, detail="Selected employee is invalid")

        if (
            resolved_department_id is not None
            and employee.department_id != resolved_department_id
        ):
            raise HTTPException(
                status_code=400,
                detail="Selected employee does not belong to the chosen department",
            )

        return resolved_department_id, resolved_employee_id

    @staticmethod
    def gl_reconciliation_context(
        db: Session,
        organization_id: str,
        as_of: date | None = None,
    ) -> dict:
        """Build fixed asset register to GL reconciliation context."""
        org_id = coerce_uuid(organization_id)
        report_date = as_of or date.today()
        tolerance = Decimal("0.01")

        category_rows = db.execute(
            select(
                AssetCategory.asset_account_id,
                AssetCategory.accumulated_depreciation_account_id,
                func.count(func.distinct(AssetCategory.category_id)).label(
                    "category_count"
                ),
                func.string_agg(
                    func.distinct(AssetCategory.category_code),
                    ", ",
                ).label("category_codes"),
                func.string_agg(
                    func.distinct(AssetCategory.category_name),
                    ", ",
                ).label("category_names"),
                func.count(Asset.asset_id).label("asset_count"),
                func.coalesce(func.sum(Asset.functional_currency_cost), 0).label(
                    "register_cost"
                ),
                func.coalesce(func.sum(Asset.accumulated_depreciation), 0).label(
                    "register_accumulated_depreciation"
                ),
                func.coalesce(func.sum(Asset.net_book_value), 0).label("register_nbv"),
            )
            .outerjoin(
                Asset,
                and_(
                    Asset.category_id == AssetCategory.category_id,
                    Asset.organization_id == org_id,
                    Asset.acquisition_date <= report_date,
                    (
                        Asset.disposal_date.is_(None)
                        | (Asset.disposal_date > report_date)
                    ),
                ),
            )
            .where(AssetCategory.organization_id == org_id)
            .where(AssetCategory.is_active.is_(True))
            .group_by(
                AssetCategory.asset_account_id,
                AssetCategory.accumulated_depreciation_account_id,
            )
            .order_by(
                AssetCategory.asset_account_id.asc(),
                AssetCategory.accumulated_depreciation_account_id.asc(),
            )
        ).all()

        account_ids: set[UUID] = set()
        for row in category_rows:
            if row.asset_account_id:
                account_ids.add(row.asset_account_id)
            if row.accumulated_depreciation_account_id:
                account_ids.add(row.accumulated_depreciation_account_id)

        accounts_by_id: dict[UUID, Account] = {}
        gl_balances: dict[UUID, Decimal] = {}
        if account_ids:
            accounts_by_id = {
                account.account_id: account
                for account in db.scalars(
                    select(Account).where(
                        Account.organization_id == org_id,
                        Account.account_id.in_(account_ids),
                    )
                ).all()
            }

            gl_rows = db.execute(
                select(
                    JournalEntryLine.account_id,
                    func.coalesce(
                        func.sum(
                            JournalEntryLine.debit_amount_functional
                            - JournalEntryLine.credit_amount_functional
                        ),
                        0,
                    ).label("balance"),
                )
                .join(
                    JournalEntry,
                    JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
                )
                .where(
                    JournalEntry.organization_id == org_id,
                    JournalEntry.status == JournalStatus.POSTED,
                    JournalEntry.posting_date <= report_date,
                    JournalEntryLine.account_id.in_(account_ids),
                )
                .group_by(JournalEntryLine.account_id)
            ).all()
            gl_balances = {
                row.account_id: Decimal(str(row.balance or 0)) for row in gl_rows
            }

        rows: list[dict[str, object]] = []
        totals: GLReconciliationTotals = {
            "category_count": 0,
            "asset_count": 0,
            "register_cost": Decimal("0"),
            "gl_cost": Decimal("0"),
            "cost_variance": Decimal("0"),
            "register_accumulated_depreciation": Decimal("0"),
            "gl_accumulated_depreciation": Decimal("0"),
            "accumulated_depreciation_variance": Decimal("0"),
            "register_nbv": Decimal("0"),
            "gl_nbv": Decimal("0"),
            "nbv_variance": Decimal("0"),
        }

        for row in category_rows:
            register_cost = Decimal(str(row.register_cost or 0))
            register_accumulated_depreciation = Decimal(
                str(row.register_accumulated_depreciation or 0)
            )
            register_nbv = Decimal(str(row.register_nbv or 0))

            asset_account = accounts_by_id.get(row.asset_account_id)
            accumulated_account = accounts_by_id.get(
                row.accumulated_depreciation_account_id
            )
            gl_cost = gl_balances.get(row.asset_account_id, Decimal("0"))
            gl_accumulated_depreciation = -gl_balances.get(
                row.accumulated_depreciation_account_id, Decimal("0")
            )
            gl_nbv = gl_cost - gl_accumulated_depreciation

            cost_variance = register_cost - gl_cost
            accumulated_depreciation_variance = (
                register_accumulated_depreciation - gl_accumulated_depreciation
            )
            nbv_variance = register_nbv - gl_nbv

            if int(row.asset_count or 0) > 0:
                totals["category_count"] += 1
            totals["asset_count"] += int(row.asset_count or 0)
            totals["register_cost"] += register_cost
            totals["register_accumulated_depreciation"] += (
                register_accumulated_depreciation
            )
            totals["register_nbv"] += register_nbv

            rows.append(
                {
                    "category_id": (
                        f"{row.asset_account_id}:{row.accumulated_depreciation_account_id}"
                    ),
                    "category_code": (
                        row.category_codes
                        if int(row.category_count or 0) == 1
                        else "Multiple categories"
                    ),
                    "category_name": (
                        f"{int(row.category_count or 0)} categories"
                        if int(row.category_count or 0) != 1
                        else row.category_names
                    ),
                    "category_codes": row.category_codes,
                    "category_names": row.category_names,
                    "category_count": int(row.category_count or 0),
                    "asset_count": int(row.asset_count or 0),
                    "asset_account": (
                        {
                            "account_id": str(asset_account.account_id),
                            "account_code": asset_account.account_code,
                            "account_name": asset_account.account_name,
                        }
                        if asset_account
                        else None
                    ),
                    "accumulated_depreciation_account": (
                        {
                            "account_id": str(accumulated_account.account_id),
                            "account_code": accumulated_account.account_code,
                            "account_name": accumulated_account.account_name,
                        }
                        if accumulated_account
                        else None
                    ),
                    "register_cost": register_cost,
                    "gl_cost": gl_cost,
                    "cost_variance": cost_variance,
                    "register_accumulated_depreciation": register_accumulated_depreciation,
                    "gl_accumulated_depreciation": gl_accumulated_depreciation,
                    "accumulated_depreciation_variance": accumulated_depreciation_variance,
                    "register_nbv": register_nbv,
                    "gl_nbv": gl_nbv,
                    "nbv_variance": nbv_variance,
                    "is_balanced": (
                        cost_variance.copy_abs() <= tolerance
                        and accumulated_depreciation_variance.copy_abs() <= tolerance
                        and nbv_variance.copy_abs() <= tolerance
                    ),
                }
            )

        mapped_asset_account_ids = {
            row.asset_account_id for row in category_rows if row.asset_account_id
        }
        mapped_accumulated_depreciation_account_ids = {
            row.accumulated_depreciation_account_id
            for row in category_rows
            if row.accumulated_depreciation_account_id
        }

        totals["gl_cost"] = sum(
            (
                gl_balances.get(account_id, Decimal("0"))
                for account_id in mapped_asset_account_ids
            ),
            Decimal("0"),
        )
        totals["gl_accumulated_depreciation"] = sum(
            (
                -gl_balances.get(account_id, Decimal("0"))
                for account_id in mapped_accumulated_depreciation_account_ids
            ),
            Decimal("0"),
        )
        totals["gl_nbv"] = totals["gl_cost"] - totals["gl_accumulated_depreciation"]
        totals["cost_variance"] = totals["register_cost"] - totals["gl_cost"]
        totals["accumulated_depreciation_variance"] = (
            totals["register_accumulated_depreciation"]
            - totals["gl_accumulated_depreciation"]
        )
        totals["nbv_variance"] = totals["register_nbv"] - totals["gl_nbv"]

        total_variance_abs = (
            totals["cost_variance"].copy_abs()
            + totals["accumulated_depreciation_variance"].copy_abs()
            + totals["nbv_variance"].copy_abs()
        )

        currency_context = get_currency_context(db, str(org_id))
        currency_code = currency_context.get("presentation_currency_code") or (
            currency_context.get("default_currency_code") or ""
        )
        currency_prefix = next(
            (
                currency.get("symbol") or ""
                for currency in currency_context.get("currencies", [])
                if currency.get("code") == currency_code
            ),
            "",
        )

        return {
            "as_of": report_date.isoformat(),
            "as_of_label": _format_date(report_date),
            "rows": rows,
            "totals": totals,
            "out_of_balance_count": sum(1 for row in rows if not row["is_balanced"]),
            "is_balanced": total_variance_abs <= tolerance,
            "currency_prefix": currency_prefix,
        }

    @staticmethod
    def export_gl_reconciliation_csv_response(
        db: Session,
        organization_id: str,
        as_of: date | None = None,
    ) -> Response:
        """Export fixed asset GL reconciliation rows as CSV."""
        context = FixedAssetWebService.gl_reconciliation_context(
            db,
            organization_id,
            as_of=as_of,
        )

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "As Of",
                "Category Mapping",
                "Category Names",
                "Assets",
                "Control Account",
                "Accumulated Depreciation Account",
                "Register Cost",
                "GL Cost",
                "Cost Variance",
                "Register Accumulated Depreciation",
                "GL Accumulated Depreciation",
                "Accumulated Depreciation Variance",
                "Register NBV",
                "GL NBV",
                "NBV Variance",
                "Status",
            ]
        )
        for row in context["rows"]:
            asset_account = row.get("asset_account") or {}
            accumulated_account = row.get("accumulated_depreciation_account") or {}
            writer.writerow(
                [
                    context["as_of"],
                    row["category_code"],
                    row["category_names"],
                    row["asset_count"],
                    FixedAssetWebService._account_export_label(asset_account),
                    FixedAssetWebService._account_export_label(accumulated_account),
                    row["register_cost"],
                    row["gl_cost"],
                    row["cost_variance"],
                    row["register_accumulated_depreciation"],
                    row["gl_accumulated_depreciation"],
                    row["accumulated_depreciation_variance"],
                    row["register_nbv"],
                    row["gl_nbv"],
                    row["nbv_variance"],
                    "Balanced" if row["is_balanced"] else "Review",
                ]
            )

        totals = context["totals"]
        writer.writerow(
            [
                context["as_of"],
                "Total",
                "",
                totals["asset_count"],
                "",
                "",
                totals["register_cost"],
                totals["gl_cost"],
                totals["cost_variance"],
                totals["register_accumulated_depreciation"],
                totals["gl_accumulated_depreciation"],
                totals["accumulated_depreciation_variance"],
                totals["register_nbv"],
                totals["gl_nbv"],
                totals["nbv_variance"],
                "Balanced" if context["is_balanced"] else "Review",
            ]
        )

        filename_stem = FixedAssetWebService._gl_reconciliation_filename(context)
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.csv"'
            },
        )

    @staticmethod
    def export_gl_reconciliation_pdf_response(
        db: Session,
        organization_id: str,
        as_of: date | None = None,
    ) -> Response:
        """Export fixed asset GL reconciliation rows as PDF."""
        from app.services.finance.rpt.pdf import ReportPDFService

        context = FixedAssetWebService.gl_reconciliation_context(
            db,
            organization_id,
            as_of=as_of,
        )
        context["row_count"] = len(context["rows"])
        filename_stem = FixedAssetWebService._gl_reconciliation_filename(context)
        pdf_bytes = ReportPDFService(db).render(
            "asset_gl_reconciliation",
            organization_id,
            context,
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.pdf"'
            },
        )

    @staticmethod
    def _account_export_label(account: object) -> str:
        """Format an account dict for export output."""
        if not isinstance(account, dict):
            return ""
        account_code = account.get("account_code") or ""
        account_name = account.get("account_name") or ""
        if account_code and account_name:
            return f"{account_code} - {account_name}"
        return str(account_code or account_name)

    @staticmethod
    def _gl_reconciliation_filename(context: dict) -> str:
        """Build a stable fixed asset GL reconciliation export filename stem."""
        as_of = str(context.get("as_of") or "").replace("-", "")
        return (
            f"asset_gl_reconciliation_{as_of}" if as_of else "asset_gl_reconciliation"
        )

    @staticmethod
    def _build_depreciation_posting_preview(
        db: Session,
        organization_id: UUID,
        run: DepreciationRun,
        fiscal_period: FiscalPeriod | None,
        schedules: list[dict[str, object]],
        current_user_id: UUID | None = None,
    ) -> dict[str, object] | None:
        if not schedules:
            return None

        schedule_rows = list(
            db.scalars(
                select(DepreciationSchedule).where(
                    DepreciationSchedule.run_id == run.run_id
                )
            ).all()
        )
        if not schedule_rows:
            return None

        expense_by_account: dict[UUID, Decimal] = {}
        accum_by_account: dict[UUID, Decimal] = {}

        for schedule in schedule_rows:
            if schedule.depreciation_amount <= 0:
                continue
            expense_by_account[schedule.expense_account_id] = (
                expense_by_account.get(schedule.expense_account_id, Decimal("0"))
                + schedule.depreciation_amount
            )
            accum_by_account[schedule.accumulated_depreciation_account_id] = (
                accum_by_account.get(
                    schedule.accumulated_depreciation_account_id, Decimal("0")
                )
                + schedule.depreciation_amount
            )

        account_ids = list({*expense_by_account.keys(), *accum_by_account.keys()})
        if not account_ids:
            return None

        accounts = {
            account.account_id: account
            for account in db.scalars(
                select(Account).where(
                    Account.organization_id == organization_id,
                    Account.account_id.in_(account_ids),
                )
            ).all()
        }

        currency_code = org_context_service.get_functional_currency(db, organization_id)

        def _sorted_items(
            balances: dict[UUID, Decimal],
        ) -> list[tuple[UUID, Decimal]]:
            return sorted(
                balances.items(),
                key=lambda item: (
                    accounts[item[0]].account_code if item[0] in accounts else "ZZZ",
                    str(item[0]),
                ),
            )

        lines: list[dict[str, str]] = []
        total_debits = Decimal("0")
        total_credits = Decimal("0")

        for account_id, amount in _sorted_items(expense_by_account):
            account = accounts.get(account_id)
            total_debits += amount
            lines.append(
                {
                    "entry_type": "Debit",
                    "account_code": account.account_code if account else "Unknown",
                    "account_name": account.account_name
                    if account
                    else str(account_id),
                    "description": f"Depreciation expense - Run #{run.run_number}",
                    "amount": _format_currency(amount, currency_code),
                }
            )

        for account_id, amount in _sorted_items(accum_by_account):
            account = accounts.get(account_id)
            total_credits += amount
            lines.append(
                {
                    "entry_type": "Credit",
                    "account_code": account.account_code if account else "Unknown",
                    "account_name": account.account_name
                    if account
                    else str(account_id),
                    "description": f"Accumulated depreciation - Run #{run.run_number}",
                    "amount": _format_currency(amount, currency_code),
                }
            )

        suggested_posting_date = date.today()
        if fiscal_period:
            suggested_posting_date = min(fiscal_period.end_date, date.today())

        can_post = run.status == DepreciationRunStatus.CALCULATED and total_debits > 0
        cannot_post_reason = None
        if run.status == DepreciationRunStatus.CALCULATED and total_debits <= 0:
            cannot_post_reason = "There are no depreciation amounts to post."
        elif run.status == DepreciationRunStatus.CALCULATED and current_user_id:
            if run.created_by_user_id == current_user_id:
                can_post = False
                cannot_post_reason = (
                    "Segregation of duties: ask another authorized user to post "
                    "this depreciation run."
                )

        return {
            "currency_code": currency_code,
            "line_count": len(lines),
            "lines": lines,
            "total_debits": _format_currency(total_debits, currency_code),
            "total_credits": _format_currency(total_credits, currency_code),
            "posting_date_default": suggested_posting_date.isoformat(),
            "can_post": can_post,
            "cannot_post_reason": cannot_post_reason,
        }

    @staticmethod
    def _sequence_preview(sequence: NumberingSequence | None) -> str | None:
        if not sequence:
            return None
        next_number = sequence.current_number + 1
        number_str = str(next_number).zfill(sequence.min_digits)
        return f"{sequence.prefix or ''}{number_str}{sequence.suffix or ''}"

    @staticmethod
    def _get_accounts(
        db: Session,
        organization_id: UUID,
    ) -> list[Account]:
        return list(
            db.scalars(
                select(Account)
                .where(
                    Account.organization_id == organization_id,
                    Account.is_active.is_(True),
                )
                .order_by(Account.account_code)
            )
        )

    @staticmethod
    def dashboard_context(
        db: Session,
        organization_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        today = date.today()
        context: dict = {
            "kpi": {
                "by_state": [],
                "maintenance_due": 0,
                "depreciation_due": 0,
                "location_mismatch_count": 0,
                "discrepancy_count": 0,
            }
        }

        try:
            asset_rows = list(
                db.execute(
                    select(Asset.status, func.count(Asset.asset_id))
                    .where(Asset.organization_id == org_id)
                    .group_by(Asset.status)
                )
            )
            state_counts = {
                str(status.value): int(count)
                for status, count in asset_rows
                if status is not None
            }
            context["kpi"]["by_state"] = [
                {
                    "status": status.value,
                    "label": status.value.replace("_", " ").title(),
                    "count": state_counts[status.value],
                }
                for status in AssetStatus
                if state_counts.get(status.value, 0) > 0
            ]
        except SQLAlchemyError as exc:
            logger.warning("Unable to load asset state KPIs: %s", exc)
            context["kpi"]["by_state"] = []

        maintenance_open_statuses = [
            MaintenanceRequestStatus.OPEN,
            MaintenanceRequestStatus.ASSIGNED,
            MaintenanceRequestStatus.IN_PROGRESS,
            MaintenanceRequestStatus.WAITING_FOR_PARTS,
        ]
        try:
            context["kpi"]["maintenance_due"] = (
                db.scalar(
                    select(func.count(MaintenanceRequest.maintenance_request_id)).where(
                        and_(
                            MaintenanceRequest.organization_id == org_id,
                            MaintenanceRequest.status.in_(maintenance_open_statuses),
                            MaintenanceRequest.due_date.is_not(None),
                            MaintenanceRequest.due_date <= today,
                        )
                    )
                )
                or 0
            )
        except SQLAlchemyError as exc:
            logger.warning("Unable to load maintenance due KPI: %s", exc)
            context["kpi"]["maintenance_due"] = 0

        try:
            eligible_depreciation_assets = (
                db.scalar(
                    select(func.count(Asset.asset_id)).where(
                        and_(
                            Asset.organization_id == org_id,
                            Asset.status == AssetStatus.IN_USE,
                            Asset.net_book_value > Asset.residual_value,
                            Asset.remaining_life_months > 0,
                            Asset.depreciation_start_date.is_not(None),
                            Asset.depreciation_start_date <= today,
                        )
                    )
                )
                or 0
            )

            current_period_id = db.scalar(
                select(FiscalPeriod.fiscal_period_id)
                .where(
                    and_(
                        FiscalPeriod.organization_id == org_id,
                        FiscalPeriod.status.in_(
                            [PeriodStatus.OPEN, PeriodStatus.REOPENED]
                        ),
                    )
                )
                .order_by(FiscalPeriod.end_date.desc())
                .limit(1)
            )

            has_posted_run = False
            if current_period_id:
                has_posted_run = bool(
                    db.scalar(
                        select(func.count(DepreciationRun.run_id)).where(
                            and_(
                                DepreciationRun.organization_id == org_id,
                                DepreciationRun.fiscal_period_id == current_period_id,
                                DepreciationRun.status == DepreciationRunStatus.POSTED,
                            )
                        )
                    )
                    or 0
                )

            context["kpi"]["depreciation_due"] = (
                0 if has_posted_run else eligible_depreciation_assets
            )
        except SQLAlchemyError as exc:
            logger.warning("Unable to load depreciation due KPI: %s", exc)
            context["kpi"]["depreciation_due"] = 0

        try:
            context["kpi"]["location_mismatch_count"] = (
                db.scalar(
                    select(func.count(AssetAuditDiscrepancy.discrepancy_id)).where(
                        and_(
                            AssetAuditDiscrepancy.organization_id == org_id,
                            AssetAuditDiscrepancy.status == "OPEN",
                            AssetAuditDiscrepancy.discrepancy_type.in_(
                                ["LOCATION", "MULTI"]
                            ),
                        )
                    )
                )
                or 0
            )
        except SQLAlchemyError as exc:
            logger.warning("Unable to load location mismatch KPI: %s", exc)
            context["kpi"]["location_mismatch_count"] = 0

        try:
            context["kpi"]["discrepancy_count"] = (
                db.scalar(
                    select(func.count(AssetAuditDiscrepancy.discrepancy_id)).where(
                        and_(
                            AssetAuditDiscrepancy.organization_id == org_id,
                            AssetAuditDiscrepancy.status == "OPEN",
                        )
                    )
                )
                or 0
            )
        except SQLAlchemyError as exc:
            logger.warning("Unable to load discrepancy KPI: %s", exc)
            context["kpi"]["discrepancy_count"] = 0

        return context

    @staticmethod
    def reporting_context(
        db: Session,
        organization_id: str,
        section: str = "overview",
        discrepancy_status: str = "OPEN",
    ) -> dict:
        """Build context for fixed-asset KPI reporting pages."""
        org_id = coerce_uuid(organization_id)
        today = date.today()
        requested = (section or "overview").strip().lower()

        valid_sections = {
            "overview",
            "state",
            "maintenance_due",
            "depreciation_due",
            "location_mismatches",
            "discrepancies",
        }
        if requested not in valid_sections:
            requested = "overview"

        normalized_discrepancy_status = (discrepancy_status or "OPEN").strip().upper()
        row_limit = 25

        data: dict = {
            "section": requested,
            "discrepancy_status": normalized_discrepancy_status,
            "kpi": {
                "by_state": [],
                "maintenance_due": 0,
                "depreciation_due": 0,
                "location_mismatch_count": 0,
                "discrepancy_count": 0,
            },
            "maintenance_due_items": [],
            "depreciation_due_items": [],
            "location_mismatch_items": [],
            "discrepancy_items": [],
        }

        dashboard_data = FixedAssetWebService.dashboard_context(
            db,
            organization_id,
        )
        data["kpi"].update(dashboard_data["kpi"])

        maintenance_open_statuses = [
            MaintenanceRequestStatus.OPEN,
            MaintenanceRequestStatus.ASSIGNED,
            MaintenanceRequestStatus.IN_PROGRESS,
            MaintenanceRequestStatus.WAITING_FOR_PARTS,
        ]
        try:
            data["kpi"]["maintenance_due"] = (
                db.scalar(
                    select(func.count(MaintenanceRequest.maintenance_request_id)).where(
                        and_(
                            MaintenanceRequest.organization_id == org_id,
                            MaintenanceRequest.status.in_(maintenance_open_statuses),
                            MaintenanceRequest.due_date.is_not(None),
                            MaintenanceRequest.due_date <= today,
                        )
                    )
                )
                or 0
            )

            maintenance_rows = db.execute(
                select(
                    MaintenanceRequest.maintenance_request_id,
                    Asset.asset_id,
                    Asset.asset_number,
                    Asset.asset_name,
                    MaintenanceRequest.title,
                    MaintenanceRequest.due_date,
                    MaintenanceRequest.status,
                )
                .join(Asset, Asset.asset_id == MaintenanceRequest.asset_id)
                .where(
                    and_(
                        MaintenanceRequest.organization_id == org_id,
                        MaintenanceRequest.status.in_(maintenance_open_statuses),
                        MaintenanceRequest.due_date.is_not(None),
                        MaintenanceRequest.due_date <= today,
                    )
                )
                .order_by(
                    MaintenanceRequest.due_date.asc(), MaintenanceRequest.title.asc()
                )
                .limit(row_limit)
            ).all()

            data["maintenance_due_items"] = [
                {
                    "asset_id": str(row.asset_id),
                    "maintenance_request_id": str(row.maintenance_request_id),
                    "asset_number": row.asset_number,
                    "asset_name": row.asset_name,
                    "title": row.title,
                    "due_date": _format_date(row.due_date),
                    "status": str(row.status),
                }
                for row in maintenance_rows
            ]
        except SQLAlchemyError as exc:
            logger.warning("Unable to load maintenance due report rows: %s", exc)
            data["maintenance_due_items"] = []
            data["kpi"]["maintenance_due"] = 0

        try:
            eligible_query = select(
                Asset.asset_id,
                Asset.asset_number,
                Asset.asset_name,
                Asset.net_book_value,
                Asset.residual_value,
                Asset.remaining_life_months,
                Asset.status,
            ).where(
                and_(
                    Asset.organization_id == org_id,
                    Asset.status == AssetStatus.IN_USE,
                    Asset.net_book_value > Asset.residual_value,
                    Asset.remaining_life_months > 0,
                    Asset.depreciation_start_date.is_not(None),
                    Asset.depreciation_start_date <= today,
                )
            )

            current_period_id = db.scalar(
                select(FiscalPeriod.fiscal_period_id)
                .where(
                    and_(
                        FiscalPeriod.organization_id == org_id,
                        FiscalPeriod.status.in_(
                            [PeriodStatus.OPEN, PeriodStatus.REOPENED]
                        ),
                    )
                )
                .order_by(FiscalPeriod.end_date.desc())
                .limit(1)
            )

            if current_period_id:
                posted_asset_ids = db.execute(
                    select(DepreciationSchedule.asset_id)
                    .join(
                        DepreciationRun,
                        DepreciationRun.run_id == DepreciationSchedule.run_id,
                    )
                    .where(
                        and_(
                            DepreciationRun.organization_id == org_id,
                            DepreciationRun.fiscal_period_id == current_period_id,
                            DepreciationRun.status == DepreciationRunStatus.POSTED,
                        )
                    )
                ).all()
                if posted_asset_ids:
                    eligible_query = eligible_query.where(
                        Asset.asset_id.not_in([row[0] for row in posted_asset_ids])
                    )

            data["kpi"]["depreciation_due"] = (
                db.scalar(select(func.count()).select_from(eligible_query.subquery()))
                or 0
            )

            depreciation_rows = db.execute(
                eligible_query.order_by(Asset.asset_name.asc()).limit(row_limit)
            ).all()

            data["depreciation_due_items"] = [
                {
                    "asset_id": str(row.asset_id),
                    "asset_number": row.asset_number,
                    "asset_name": row.asset_name,
                    "net_book_value": _format_currency(row.net_book_value),
                    "residual_value": _format_currency(row.residual_value),
                    "remaining_life_months": int(row.remaining_life_months or 0),
                    "status": str(row.status),
                }
                for row in depreciation_rows
            ]
        except SQLAlchemyError as exc:
            logger.warning("Unable to load depreciation due report rows: %s", exc)
            data["depreciation_due_items"] = []
            data["kpi"]["depreciation_due"] = 0

        mismatch_types = ["LOCATION", "MULTI", "OWNERSHIP"]
        try:
            data["kpi"]["location_mismatch_count"] = (
                db.scalar(
                    select(func.count(AssetAuditDiscrepancy.discrepancy_id)).where(
                        and_(
                            AssetAuditDiscrepancy.organization_id == org_id,
                            AssetAuditDiscrepancy.status
                            == normalized_discrepancy_status,
                            AssetAuditDiscrepancy.discrepancy_type.in_(mismatch_types),
                        )
                    )
                )
                or 0
            )
            mismatch_rows = db.execute(
                select(
                    Asset.asset_id,
                    Asset.asset_number,
                    Asset.asset_name,
                    AssetAuditDiscrepancy.discrepancy_id,
                    AssetAuditDiscrepancy.discrepancy_type,
                    AssetAuditDiscrepancy.status,
                    AssetAuditDiscrepancy.detected_at,
                    AssetAuditDiscrepancy.notes,
                )
                .join(Asset, Asset.asset_id == AssetAuditDiscrepancy.asset_id)
                .where(
                    and_(
                        AssetAuditDiscrepancy.organization_id == org_id,
                        AssetAuditDiscrepancy.status == normalized_discrepancy_status,
                        AssetAuditDiscrepancy.discrepancy_type.in_(mismatch_types),
                    )
                )
                .order_by(AssetAuditDiscrepancy.detected_at.desc())
                .limit(row_limit)
            ).all()

            data["location_mismatch_items"] = [
                {
                    "asset_id": str(row.asset_id),
                    "asset_number": row.asset_number,
                    "asset_name": row.asset_name,
                    "discrepancy_id": str(row.discrepancy_id),
                    "discrepancy_type": row.discrepancy_type,
                    "status": row.status,
                    "detected_at": _format_date(row.detected_at),
                    "notes": row.notes,
                }
                for row in mismatch_rows
            ]
        except SQLAlchemyError as exc:
            logger.warning("Unable to load location mismatch report rows: %s", exc)
            data["location_mismatch_items"] = []
            data["kpi"]["location_mismatch_count"] = 0

        try:
            data["kpi"]["discrepancy_count"] = (
                db.scalar(
                    select(func.count(AssetAuditDiscrepancy.discrepancy_id)).where(
                        and_(
                            AssetAuditDiscrepancy.organization_id == org_id,
                            AssetAuditDiscrepancy.status
                            == normalized_discrepancy_status,
                        )
                    )
                )
                or 0
            )
            discrepancy_rows = db.execute(
                select(
                    Asset.asset_id,
                    Asset.asset_number,
                    Asset.asset_name,
                    AssetAuditDiscrepancy.discrepancy_id,
                    AssetAuditDiscrepancy.discrepancy_type,
                    AssetAuditDiscrepancy.status,
                    AssetAuditDiscrepancy.detected_at,
                    AssetAuditDiscrepancy.notes,
                )
                .join(Asset, Asset.asset_id == AssetAuditDiscrepancy.asset_id)
                .where(
                    and_(
                        AssetAuditDiscrepancy.organization_id == org_id,
                        AssetAuditDiscrepancy.status == normalized_discrepancy_status,
                    )
                )
                .order_by(AssetAuditDiscrepancy.detected_at.desc())
                .limit(row_limit)
            ).all()

            data["discrepancy_items"] = [
                {
                    "asset_id": str(row.asset_id),
                    "asset_number": row.asset_number,
                    "asset_name": row.asset_name,
                    "discrepancy_id": str(row.discrepancy_id),
                    "discrepancy_type": row.discrepancy_type,
                    "status": row.status,
                    "detected_at": _format_date(row.detected_at),
                    "notes": row.notes,
                }
                for row in discrepancy_rows
            ]
        except SQLAlchemyError as exc:
            logger.warning("Unable to load discrepancy report rows: %s", exc)
            data["discrepancy_items"] = []
            data["kpi"]["discrepancy_count"] = 0

        # Provide state distribution for overview and state-section cards.
        data["kpi"]["by_state"] = dashboard_data.get("kpi", {}).get("by_state", [])
        return data

    @staticmethod
    def asset_count_sheet_context(
        db: Session,
        organization_id: str,
        audit_plan_id: str | None = None,
        location: str | None = None,
        category: str | None = None,
    ) -> dict:
        """Build a count sheet comparing asset register quantities to physical checks."""
        org_id = coerce_uuid(organization_id)
        selected_plan_id = _try_uuid(audit_plan_id)
        selected_location_id = _try_uuid(location)
        selected_category_id = _try_uuid(category)

        audit_plans = db.scalars(
            select(AssetAuditPlan)
            .where(AssetAuditPlan.organization_id == org_id)
            .order_by(
                AssetAuditPlan.planned_date.desc(), AssetAuditPlan.created_at.desc()
            )
        ).all()
        if selected_plan_id is None and audit_plans:
            selected_plan_id = audit_plans[0].audit_plan_id

        selected_plan = None
        if selected_plan_id is not None:
            selected_plan = db.get(AssetAuditPlan, selected_plan_id)
            if not selected_plan or selected_plan.organization_id != org_id:
                raise HTTPException(
                    status_code=404, detail="Asset count plan not found"
                )

        categories = db.scalars(
            select(AssetCategory)
            .where(
                AssetCategory.organization_id == org_id,
                AssetCategory.is_active.is_(True),
            )
            .order_by(AssetCategory.category_code)
        ).all()
        locations = db.scalars(
            select(Location)
            .where(
                Location.organization_id == org_id,
                Location.is_active.is_(True),
            )
            .order_by(Location.location_name)
        ).all()

        line_rows: list[Any] = []
        if selected_plan_id is not None:
            line_query = (
                select(
                    Asset.asset_id,
                    Asset.asset_number,
                    Asset.asset_name,
                    Asset.serial_number,
                    Asset.status.label("system_status"),
                    AssetCategory.category_id,
                    AssetCategory.category_code,
                    AssetCategory.category_name,
                    Location.location_id,
                    Location.location_code,
                    Location.location_name,
                    AssetAuditLine.status.label("line_status"),
                    AssetAuditLine.is_found,
                    AssetAuditLine.physical_check_at,
                    AssetAuditLine.discrepancy_notes,
                )
                .join(Asset, Asset.asset_id == AssetAuditLine.asset_id)
                .join(AssetCategory, Asset.category_id == AssetCategory.category_id)
                .outerjoin(Location, Asset.location_id == Location.location_id)
                .where(
                    AssetAuditLine.organization_id == org_id,
                    AssetAuditLine.audit_plan_id == selected_plan_id,
                )
            )
            if selected_location_id is not None:
                line_query = line_query.where(Asset.location_id == selected_location_id)
            if selected_category_id is not None:
                line_query = line_query.where(Asset.category_id == selected_category_id)
            line_rows = list(
                db.execute(
                    line_query.order_by(
                        Location.location_name.asc().nulls_last(),
                        AssetCategory.category_code.asc(),
                        Asset.asset_number.asc(),
                    )
                ).all()
            )

        detail_rows: list[dict[str, Any]] = []
        group_map: dict[tuple[str, str], dict[str, Any]] = {}
        totals = {
            "system_qty": 0,
            "physical_qty": 0,
            "variance_qty": 0,
            "variance_count": 0,
            "unchecked_qty": 0,
        }

        for row in line_rows:
            system_qty = 1
            physical_qty = (
                1
                if row.is_found is True
                or row.line_status
                in {AssetAuditLineStatus.FOUND, AssetAuditLineStatus.DISCREPANCY}
                else 0
            )
            is_unchecked = row.line_status == AssetAuditLineStatus.PENDING
            variance_qty = physical_qty - system_qty
            has_variance = variance_qty != 0

            location_label = row.location_name or "Unassigned"
            category_label = (
                f"{row.category_code} - {row.category_name}"
                if row.category_code
                else row.category_name
            )
            group_key = (location_label, category_label)
            if group_key not in group_map:
                group_map[group_key] = {
                    "location_name": location_label,
                    "category_name": category_label,
                    "system_qty": 0,
                    "physical_qty": 0,
                    "variance_qty": 0,
                    "variance_count": 0,
                    "unchecked_qty": 0,
                }

            group = group_map[group_key]
            group["system_qty"] = int(group["system_qty"]) + system_qty
            group["physical_qty"] = int(group["physical_qty"]) + physical_qty
            group["variance_qty"] = int(group["variance_qty"]) + variance_qty
            group["variance_count"] = int(group["variance_count"]) + int(has_variance)
            group["unchecked_qty"] = int(group["unchecked_qty"]) + int(is_unchecked)

            totals["system_qty"] += system_qty
            totals["physical_qty"] += physical_qty
            totals["variance_qty"] += variance_qty
            totals["variance_count"] += int(has_variance)
            totals["unchecked_qty"] += int(is_unchecked)

            detail_rows.append(
                {
                    "asset_id": str(row.asset_id),
                    "asset_number": row.asset_number,
                    "asset_name": row.asset_name,
                    "serial_number": row.serial_number or "",
                    "location_name": location_label,
                    "category_name": category_label,
                    "system_qty": system_qty,
                    "physical_qty": physical_qty,
                    "variance_qty": variance_qty,
                    "line_status": row.line_status.value
                    if hasattr(row.line_status, "value")
                    else str(row.line_status),
                    "system_status": row.system_status.value
                    if hasattr(row.system_status, "value")
                    else str(row.system_status),
                    "physical_check_at": _format_date(row.physical_check_at),
                    "discrepancy_notes": row.discrepancy_notes or "",
                    "has_variance": has_variance,
                    "is_unchecked": is_unchecked,
                }
            )

        return {
            "audit_plans": [
                {
                    "audit_plan_id": str(plan.audit_plan_id),
                    "plan_number": plan.plan_number,
                    "title": plan.title,
                    "planned_date": _format_date(plan.planned_date),
                    "status": plan.status.value
                    if hasattr(plan.status, "value")
                    else str(plan.status),
                }
                for plan in audit_plans
            ],
            "selected_plan": selected_plan,
            "audit_plan_id": str(selected_plan_id) if selected_plan_id else "",
            "location": str(selected_location_id) if selected_location_id else "",
            "category": str(selected_category_id) if selected_category_id else "",
            "locations": locations,
            "categories": categories,
            "summary_rows": sorted(
                group_map.values(),
                key=lambda item: (
                    str(item["location_name"]),
                    str(item["category_name"]),
                ),
            ),
            "count_sheet_rows": detail_rows,
            "count_sheet_totals": totals,
            "has_count_plan": selected_plan is not None,
        }

    @staticmethod
    def export_asset_count_sheet_csv_response(
        db: Session,
        organization_id: str,
        audit_plan_id: str | None = None,
        location: str | None = None,
        category: str | None = None,
    ) -> Response:
        """Export asset count sheet rows as CSV."""
        context = FixedAssetWebService.asset_count_sheet_context(
            db,
            organization_id,
            audit_plan_id=audit_plan_id,
            location=location,
            category=category,
        )

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "Audit Plan",
                "Asset Number",
                "Asset Name",
                "Serial Number",
                "Location",
                "Category",
                "System Status",
                "Line Status",
                "System Qty",
                "Physical Qty",
                "Variance Qty",
                "Physical Check Date",
                "Notes",
            ]
        )
        plan_label = FixedAssetWebService._asset_count_sheet_plan_label(context)
        for row in context["count_sheet_rows"]:
            writer.writerow(
                [
                    plan_label,
                    row["asset_number"],
                    row["asset_name"],
                    row["serial_number"],
                    row["location_name"],
                    row["category_name"],
                    row["system_status"],
                    row["line_status"],
                    row["system_qty"],
                    row["physical_qty"],
                    row["variance_qty"],
                    row["physical_check_at"],
                    row["discrepancy_notes"],
                ]
            )

        totals = context["count_sheet_totals"]
        writer.writerow(
            [
                plan_label,
                "Total",
                "",
                "",
                "",
                "",
                "",
                "",
                totals["system_qty"],
                totals["physical_qty"],
                totals["variance_qty"],
                "",
                "",
            ]
        )

        filename_stem = FixedAssetWebService._asset_count_sheet_filename(context)
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.csv"'
            },
        )

    @staticmethod
    def export_asset_count_sheet_pdf_response(
        db: Session,
        organization_id: str,
        audit_plan_id: str | None = None,
        location: str | None = None,
        category: str | None = None,
    ) -> Response:
        """Export asset count sheet rows as PDF."""
        from app.services.finance.rpt.pdf import ReportPDFService

        context = FixedAssetWebService.asset_count_sheet_context(
            db,
            organization_id,
            audit_plan_id=audit_plan_id,
            location=location,
            category=category,
        )
        context["row_count"] = len(context["count_sheet_rows"])
        context["plan_label"] = FixedAssetWebService._asset_count_sheet_plan_label(
            context
        )
        filename_stem = FixedAssetWebService._asset_count_sheet_filename(context)
        pdf_bytes = ReportPDFService(db).render(
            "asset_count_sheets",
            organization_id,
            context,
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.pdf"'
            },
        )

    @staticmethod
    def _asset_count_sheet_plan_label(context: dict) -> str:
        """Return a readable plan label for exports."""
        selected_plan = context.get("selected_plan")
        if selected_plan is None:
            return "No audit plan"
        return (
            f"{getattr(selected_plan, 'plan_number', '')} - "
            f"{getattr(selected_plan, 'title', '')}"
        ).strip(" -")

    @staticmethod
    def _asset_count_sheet_filename(context: dict) -> str:
        """Build a stable asset count sheet export filename stem."""
        selected_plan = context.get("selected_plan")
        plan_number = getattr(selected_plan, "plan_number", None)
        if not plan_number:
            return "asset_count_sheets"
        safe_plan = "".join(
            char.lower() if char.isalnum() else "_" for char in str(plan_number).strip()
        ).strip("_")
        return f"asset_count_sheets_{safe_plan}" if safe_plan else "asset_count_sheets"

    @staticmethod
    def count_plans_context(
        db: Session,
        organization_id: str,
        status: str | None = None,
    ) -> dict:
        """Build context for fixed asset physical count plans."""
        org_id = coerce_uuid(organization_id)
        status_value = None
        if status:
            try:
                status_value = AssetAuditPlanStatus(status.upper())
            except ValueError:
                status_value = None

        query = (
            select(AssetAuditPlan, Location)
            .outerjoin(
                Location, AssetAuditPlan.scope_location_id == Location.location_id
            )
            .where(AssetAuditPlan.organization_id == org_id)
        )
        if status_value:
            query = query.where(AssetAuditPlan.status == status_value)
        rows = db.execute(
            query.order_by(
                AssetAuditPlan.planned_date.desc(),
                AssetAuditPlan.created_at.desc(),
            )
        ).all()

        plans = []
        for plan, location_row in rows:
            plans.append(
                {
                    "audit_plan_id": str(plan.audit_plan_id),
                    "plan_number": plan.plan_number,
                    "title": plan.title,
                    "planned_date": _format_date(plan.planned_date),
                    "scope": location_row.location_name
                    if location_row
                    else "All locations",
                    "status": plan.status.value
                    if hasattr(plan.status, "value")
                    else str(plan.status),
                    "total_assets": int(plan.total_assets or 0),
                    "found_count": int(plan.found_count or 0),
                    "missing_count": int(plan.missing_count or 0),
                    "discrepancy_count": int(plan.discrepancy_count or 0),
                    "started_at": _format_date(plan.started_at),
                    "completed_at": _format_date(plan.completed_at),
                }
            )

        return {
            "plans": plans,
            "status": status or "",
            "status_options": [
                {"value": option.value, "label": option.value.replace("_", " ").title()}
                for option in AssetAuditPlanStatus
            ],
        }

    @staticmethod
    def count_plan_form_context(db: Session, organization_id: str) -> dict:
        """Build context for the new asset count plan form."""
        org_id = coerce_uuid(organization_id)
        locations = db.scalars(
            select(Location)
            .where(
                Location.organization_id == org_id,
                Location.is_active.is_(True),
            )
            .order_by(Location.location_name)
        ).all()
        return {
            "locations": locations,
            "today": _format_date(date.today()),
        }

    @staticmethod
    def count_plan_detail_context(
        db: Session,
        organization_id: str,
        audit_plan_id: str,
        line_status: str | None = None,
    ) -> dict:
        """Build context for performing a fixed asset physical count plan."""
        org_id = coerce_uuid(organization_id)
        plan_id = coerce_uuid(audit_plan_id)
        plan = db.get(AssetAuditPlan, plan_id)
        if not plan or plan.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset count plan not found")

        selected_status = None
        if line_status:
            try:
                selected_status = AssetAuditLineStatus(line_status.upper())
            except ValueError:
                selected_status = None

        expected_location = aliased(Location)
        observed_location = aliased(Location)
        line_query = (
            select(AssetAuditLine, Asset, expected_location, observed_location)
            .join(Asset, Asset.asset_id == AssetAuditLine.asset_id)
            .outerjoin(
                expected_location,
                expected_location.location_id == AssetAuditLine.expected_location_id,
            )
            .outerjoin(
                observed_location,
                observed_location.location_id == AssetAuditLine.observed_location_id,
            )
            .where(
                AssetAuditLine.organization_id == org_id,
                AssetAuditLine.audit_plan_id == plan_id,
            )
        )
        if selected_status:
            line_query = line_query.where(AssetAuditLine.status == selected_status)

        rows = db.execute(
            line_query.order_by(Asset.asset_number.asc(), Asset.asset_name.asc())
        ).all()
        counts_by_status: dict[Any, int] = {
            row[0]: row[1]
            for row in db.execute(
                select(AssetAuditLine.status, func.count(AssetAuditLine.audit_line_id))
                .where(
                    AssetAuditLine.organization_id == org_id,
                    AssetAuditLine.audit_plan_id == plan_id,
                )
                .group_by(AssetAuditLine.status)
            ).all()
        }
        locations = db.scalars(
            select(Location)
            .where(
                Location.organization_id == org_id,
                Location.is_active.is_(True),
            )
            .order_by(Location.location_name)
        ).all()

        count_totals = {
            "total": int(plan.total_assets or 0),
            "found": int(counts_by_status.get(AssetAuditLineStatus.FOUND, 0) or 0),
            "missing": int(counts_by_status.get(AssetAuditLineStatus.MISSING, 0) or 0),
            "discrepancy": int(
                counts_by_status.get(AssetAuditLineStatus.DISCREPANCY, 0) or 0
            ),
            "pending": int(counts_by_status.get(AssetAuditLineStatus.PENDING, 0) or 0),
            "resolved": int(
                counts_by_status.get(AssetAuditLineStatus.RESOLVED, 0) or 0
            ),
        }

        lines = []
        for line, asset, expected_loc, observed_loc in rows:
            lines.append(
                {
                    "audit_line_id": str(line.audit_line_id),
                    "asset_id": str(asset.asset_id),
                    "asset_number": asset.asset_number,
                    "asset_name": asset.asset_name,
                    "serial_number": asset.serial_number or "",
                    "barcode": asset.barcode or "",
                    "expected_location_id": str(line.expected_location_id or ""),
                    "expected_location": expected_loc.location_name
                    if expected_loc
                    else "Unassigned",
                    "observed_location_id": str(line.observed_location_id or ""),
                    "observed_location": observed_loc.location_name
                    if observed_loc
                    else "",
                    "expected_status": line.expected_status or "",
                    "observed_status": line.observed_status or "",
                    "status": line.status.value
                    if hasattr(line.status, "value")
                    else str(line.status),
                    "is_found": line.is_found,
                    "physical_check_at": _format_date(line.physical_check_at),
                    "discrepancy_notes": line.discrepancy_notes or "",
                }
            )

        return {
            "plan": {
                "audit_plan_id": str(plan.audit_plan_id),
                "plan_number": plan.plan_number,
                "title": plan.title,
                "planned_date": _format_date(plan.planned_date),
                "scope_location_id": str(plan.scope_location_id or ""),
                "status": plan.status.value
                if hasattr(plan.status, "value")
                else str(plan.status),
                "total_assets": int(plan.total_assets or 0),
                "started_at": _format_date(plan.started_at),
                "completed_at": _format_date(plan.completed_at),
            },
            "lines": lines,
            "line_status": line_status or "",
            "count_totals": count_totals,
            "locations": locations,
            "asset_statuses": [
                {"value": option.value, "label": option.value.replace("_", " ").title()}
                for option in EDITABLE_ASSET_STATUSES
            ],
            "line_status_options": [
                {"value": option.value, "label": option.value.replace("_", " ").title()}
                for option in AssetAuditLineStatus
            ],
        }

    @staticmethod
    def create_count_plan_response(
        db: Session,
        organization_id: str,
        user_id: UUID | None,
        title: str,
        planned_date: str,
        scope_location_id: str | None = None,
    ) -> RedirectResponse:
        """Create a fixed asset count plan and redirect to its detail page."""
        org_id = coerce_uuid(organization_id)
        parsed_date = date.fromisoformat(planned_date)
        location_id = _try_uuid(scope_location_id)
        plan = AssetAuditService(db).create_plan(
            org_id,
            title=title.strip(),
            planned_date=parsed_date,
            scope_location_id=location_id,
            created_by_user_id=user_id,
        )
        db.commit()
        return RedirectResponse(
            f"/fixed-assets/count-plans/{plan.audit_plan_id}?success=Count+plan+created",
            status_code=303,
        )

    @staticmethod
    def start_count_plan_response(
        db: Session,
        organization_id: str,
        audit_plan_id: str,
    ) -> RedirectResponse:
        """Start a fixed asset count plan."""
        plan_id = coerce_uuid(audit_plan_id)
        AssetAuditService(db).start_plan(coerce_uuid(organization_id), plan_id)
        db.commit()
        return RedirectResponse(
            f"/fixed-assets/count-plans/{plan_id}?success=Count+plan+started",
            status_code=303,
        )

    @staticmethod
    def complete_count_plan_response(
        db: Session,
        organization_id: str,
        audit_plan_id: str,
    ) -> RedirectResponse:
        """Complete a fixed asset count plan."""
        plan_id = coerce_uuid(audit_plan_id)
        AssetAuditService(db).complete_plan(coerce_uuid(organization_id), plan_id)
        db.commit()
        return RedirectResponse(
            f"/fixed-assets/count-plans/{plan_id}?success=Count+plan+completed",
            status_code=303,
        )

    @staticmethod
    def check_count_plan_line_response(
        db: Session,
        organization_id: str,
        user_id: UUID | None,
        audit_plan_id: str,
        audit_line_id: str,
        action: str,
        observed_location_id: str | None = None,
        observed_status: str | None = None,
        discrepancy_notes: str | None = None,
    ) -> RedirectResponse:
        """Record one physical count line result."""
        org_id = coerce_uuid(organization_id)
        plan_id = coerce_uuid(audit_plan_id)
        line_id = coerce_uuid(audit_line_id)
        line = db.get(AssetAuditLine, line_id)
        if not line or line.organization_id != org_id or line.audit_plan_id != plan_id:
            raise HTTPException(status_code=404, detail="Asset count line not found")

        normalized_action = (action or "").strip().lower()
        if normalized_action == "found":
            is_found = True
            resolved_location_id = line.expected_location_id
            resolved_status = line.expected_status
            notes = discrepancy_notes
        elif normalized_action == "missing":
            is_found = False
            resolved_location_id = None
            resolved_status = None
            notes = discrepancy_notes or "Asset not found during physical count"
        elif normalized_action == "discrepancy":
            is_found = True
            resolved_location_id = _try_uuid(observed_location_id)
            resolved_status = observed_status or line.expected_status
            notes = discrepancy_notes
        else:
            raise HTTPException(status_code=400, detail="Invalid count action")

        AssetAuditService(db).record_check(
            org_id,
            line_id,
            is_found=is_found,
            observed_location_id=resolved_location_id,
            observed_status=resolved_status,
            discrepancy_notes=notes,
            checked_by_user_id=user_id,
        )
        db.commit()
        return RedirectResponse(
            f"/fixed-assets/count-plans/{plan_id}?success=Count+line+updated",
            status_code=303,
        )

    @staticmethod
    def mark_count_plan_pending_found_response(
        db: Session,
        organization_id: str,
        user_id: UUID | None,
        audit_plan_id: str,
    ) -> RedirectResponse:
        """Mark every pending count plan line as found."""
        org_id = coerce_uuid(organization_id)
        plan_id = coerce_uuid(audit_plan_id)
        plan = db.get(AssetAuditPlan, plan_id)
        if not plan or plan.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset count plan not found")
        if plan.status != AssetAuditPlanStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=400,
                detail="Only in-progress count plans can be updated",
            )

        audit_service = AssetAuditService(db)
        pending_lines = db.scalars(
            select(AssetAuditLine).where(
                AssetAuditLine.organization_id == org_id,
                AssetAuditLine.audit_plan_id == plan_id,
                AssetAuditLine.status == AssetAuditLineStatus.PENDING,
            )
        ).all()
        for line in pending_lines:
            audit_service.record_check(
                org_id,
                line.audit_line_id,
                is_found=True,
                observed_location_id=line.expected_location_id,
                observed_status=line.expected_status,
                discrepancy_notes=None,
                checked_by_user_id=user_id,
            )

        db.commit()
        return RedirectResponse(
            f"/fixed-assets/count-plans/{plan_id}?success=Marked+{len(pending_lines)}+pending+assets+as+found",
            status_code=303,
        )

    @staticmethod
    def asset_form_context(
        db: Session,
        organization_id: str,
        selected_employee_id: UUID | None = None,
    ) -> dict:
        """Build context for new asset form."""
        from app.models.finance.ap.supplier import Supplier

        org_id = coerce_uuid(organization_id)

        categories = db.scalars(
            select(AssetCategory)
            .where(
                AssetCategory.organization_id == org_id,
                AssetCategory.is_active.is_(True),
                AssetCategory.parent_category_id.is_(None),
            )
            .order_by(AssetCategory.category_code)
        ).all()

        sequence = db.scalar(
            select(NumberingSequence).where(
                NumberingSequence.organization_id == org_id,
                NumberingSequence.sequence_type == SequenceType.ASSET,
            )
        )

        # Get suppliers list for FA → AP source tracking
        suppliers = db.scalars(
            select(Supplier)
            .where(
                Supplier.organization_id == org_id,
                Supplier.is_active.is_(True),
            )
            .order_by(Supplier.legal_name)
        ).all()
        suppliers_list = [
            {
                "supplier_id": str(s.supplier_id),
                "supplier_name": s.trading_name or s.legal_name,
                "supplier_code": s.supplier_code,
            }
            for s in suppliers
        ]

        locations = db.scalars(
            select(Location)
            .where(
                Location.organization_id == org_id,
                Location.is_active.is_(True),
            )
            .order_by(Location.location_name)
        ).all()
        locations_list = [
            {
                "location_id": str(loc.location_id),
                "location_code": loc.location_code,
                "location_name": loc.location_name,
            }
            for loc in locations
        ]
        assignment_options = FixedAssetWebService._assignment_options(
            db,
            organization_id,
            selected_employee_id=selected_employee_id,
        )

        depreciation_schedule_rows = db.execute(
            select(
                DepreciationSchedule.schedule_id,
                DepreciationRun.run_number,
                FiscalPeriod.period_name,
                FiscalPeriod.start_date,
                FiscalPeriod.end_date,
            )
            .join(
                DepreciationRun, DepreciationSchedule.run_id == DepreciationRun.run_id
            )
            .join(
                FiscalPeriod,
                DepreciationRun.fiscal_period_id == FiscalPeriod.fiscal_period_id,
            )
            .where(DepreciationRun.organization_id == org_id)
            .order_by(
                DepreciationRun.created_at.desc(),
                DepreciationSchedule.created_at.desc(),
            )
        ).all()
        depreciation_schedules = []
        for (
            schedule_id,
            run_number,
            period_name,
            start_date,
            end_date,
        ) in depreciation_schedule_rows:
            depreciation_schedules.append(
                {
                    "schedule_id": str(schedule_id),
                    "label": f"Run {run_number} - {period_name} ({_format_date(start_date)} - {_format_date(end_date)})",
                    "run_number": run_number,
                }
            )

        context = {
            "categories": categories,
            "suppliers_list": suppliers_list,
            "locations_list": locations_list,
            "departments_list": assignment_options["departments_list"],
            "department_employees": assignment_options["department_employees"],
            "depreciation_schedules": depreciation_schedules,
            "asset_statuses": [
                {"value": status.value, "label": status.value.replace("_", " ").title()}
                for status in EDITABLE_ASSET_STATUSES
            ],
            "today": _format_date(date.today()),
            "asset_number_preview": FixedAssetWebService._sequence_preview(sequence),
            "selected_department_id": assignment_options["selected_department_id"],
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def list_assets_context(
        db: Session,
        organization_id: str,
        search: str | None,
        category: str | None,
        status: str | None,
        location: str | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        offset = (page - 1) * limit
        org_id = coerce_uuid(organization_id)
        from app.services.fixed_assets.asset_query import build_asset_query

        query = build_asset_query(
            db=db,
            organization_id=organization_id,
            search=search,
            category=category,
            status=status,
            location=location,
        )
        filtered_assets = query.subquery()

        summary_row = db.execute(
            select(
                func.count(filtered_assets.c.asset_id).label("total_assets"),
                func.coalesce(func.sum(filtered_assets.c.acquisition_cost), 0).label(
                    "total_cost"
                ),
                func.coalesce(func.sum(filtered_assets.c.net_book_value), 0).label(
                    "total_nbv"
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (filtered_assets.c.status == AssetStatus.IN_USE, 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("active_count"),
            )
        ).one()

        total_count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.execute(
            query.add_columns(AssetCategory, Location)
            .order_by(Asset.asset_number)
            .limit(limit)
            .offset(offset)
        ).all()

        assets_view = []
        for row in rows:
            asset = row[0]
            category_row = row[1] if len(row) > 1 else None
            location_row = row[2] if len(row) > 2 else None
            assets_view.append(
                {
                    "asset_id": asset.asset_id,
                    "asset_number": asset.asset_number,
                    "asset_name": asset.asset_name,
                    "description": getattr(asset, "description", None),
                    "serial_number": asset.serial_number,
                    "location_name": (
                        location_row.location_name if location_row else None
                    ),
                    "category_name": (
                        category_row.category_name if category_row else None
                    ),
                    "category_code": (
                        category_row.category_code if category_row else None
                    ),
                    "acquisition_date": _format_date(asset.acquisition_date),
                    "acquisition_cost": _format_currency(
                        asset.acquisition_cost, asset.currency_code
                    ),
                    "net_book_value": _format_currency(
                        asset.net_book_value, asset.currency_code
                    ),
                    "status": asset.status.value,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        categories = db.scalars(
            select(AssetCategory)
            .where(
                AssetCategory.organization_id == org_id,
                AssetCategory.is_active.is_(True),
                AssetCategory.parent_category_id.is_(None),
            )
            .order_by(AssetCategory.category_code)
        ).all()
        locations = db.scalars(
            select(Location)
            .where(
                Location.organization_id == org_id,
                Location.is_active.is_(True),
            )
            .order_by(Location.location_name)
        ).all()

        active_filters = build_active_filters(
            params={
                "search": search,
                "category": category,
                "status": status,
                "location": location,
            },
            labels={
                "search": "Search",
                "category": "Category",
                "status": "Status",
                "location": "Location",
            },
            options={
                "category": {
                    str(cat.category_id): cat.category_name for cat in categories
                },
                "location": {
                    str(loc.location_id): loc.location_name for loc in locations
                },
            },
        )

        return {
            "assets": assets_view,
            "categories": categories,
            "locations": locations,
            "search": search,
            "category": category,
            "status": status,
            "location": location,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "active_count": int(summary_row.active_count or 0),
            "total_cost": _format_currency(summary_row.total_cost),
            "total_nbv": _format_currency(summary_row.total_nbv),
            "active_filters": active_filters,
        }

    def asset_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Asset", "fixed_assets")
        context.update(self.asset_form_context(db, str(auth.organization_id)))
        context["form_asset_number"] = ""
        context["form_asset_name"] = ""
        context["form_category_id"] = ""
        context["form_serial_number"] = ""
        context["form_location_id"] = ""
        context["form_department_id"] = ""
        context["form_custodian_employee_id"] = ""
        context["form_status"] = AssetStatus.NOT_IN_USE.value
        return templates.TemplateResponse(
            request, "fixed_assets/asset_form.html", context
        )

    def create_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        asset_number: str | None,
        asset_name: str,
        serial_number: str | None,
        location_id: str | None,
        department_id: str | None,
        custodian_employee_id: str | None,
        category_id: str,
        acquisition_date: str | None,
        acquisition_cost: str | None,
        currency_code: str | None,
        status: str | None,
        description: str | None,
        depreciation_schedule_id: str | None,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if not acquisition_date or not acquisition_date.strip():
                raise ValueError("Acquisition date is required")
            if not acquisition_cost or not acquisition_cost.strip():
                raise ValueError("Acquisition cost is required")
            if not currency_code or not currency_code.strip():
                raise ValueError("Currency is required")
            parsed_acquisition_date = (
                datetime.strptime(acquisition_date, "%Y-%m-%d").date()
                if acquisition_date and acquisition_date.strip()
                else date.today()
            )
            parsed_acquisition_cost = (
                Decimal(acquisition_cost.strip())
                if acquisition_cost and acquisition_cost.strip()
                else Decimal("0")
            )
            resolved_currency = currency_code.strip()
            resolved_department_id, resolved_custodian_employee_id = (
                self._validate_assignment_selection(
                    db,
                    str(org_id),
                    department_id,
                    custodian_employee_id,
                )
            )

            input_data = AssetInput(
                asset_number=(asset_number.strip() if asset_number else None),
                asset_name=asset_name,
                serial_number=(serial_number.strip() if serial_number else None),
                location_id=(UUID(location_id) if location_id else None),
                category_id=UUID(category_id),
                custodian_user_id=resolved_custodian_employee_id,
                acquisition_date=parsed_acquisition_date,
                acquisition_cost=parsed_acquisition_cost,
                currency_code=resolved_currency,
                status=(
                    AssetStatus(status.strip())
                    if status and status.strip()
                    else AssetStatus.NOT_IN_USE
                ),
                description=description,
                depreciation_schedule_id=(
                    UUID(depreciation_schedule_id) if depreciation_schedule_id else None
                ),
            )

            asset_service.create_asset(
                db,
                org_id,
                input_data,
                created_by_user_id=user_id,
            )
            db.commit()
            return RedirectResponse(
                url="/fixed-assets/assets?success=Record+created+successfully",
                status_code=303,
            )

        except Exception as e:
            db.rollback()
            context = base_context(request, auth, "New Asset", "fixed_assets")
            selected_employee_id = (
                _try_uuid(custodian_employee_id) if custodian_employee_id else None
            )
            context.update(
                self.asset_form_context(
                    db,
                    str(auth.organization_id),
                    selected_employee_id=selected_employee_id,
                )
            )
            context["error"] = str(e)
            context["form_asset_number"] = asset_number or ""
            context["form_asset_name"] = asset_name
            context["form_category_id"] = category_id
            context["form_serial_number"] = serial_number or ""
            context["form_location_id"] = location_id or ""
            context["form_department_id"] = department_id or (
                str(resolved_department_id)
                if "resolved_department_id" in locals() and resolved_department_id
                else ""
            )
            context["form_custodian_employee_id"] = custodian_employee_id or ""
            context["form_acquisition_date"] = acquisition_date or ""
            context["form_acquisition_cost"] = acquisition_cost or ""
            context["form_currency_code"] = currency_code or ""
            context["form_status"] = status or AssetStatus.NOT_IN_USE.value
            context["selected_depreciation_schedule_id"] = depreciation_schedule_id
            return templates.TemplateResponse(
                request, "fixed_assets/asset_form.html", context
            )

    @staticmethod
    def list_categories_context(
        db: Session,
        organization_id: str,
        is_active: bool | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit
        query = select(AssetCategory).where(
            AssetCategory.organization_id == org_id,
            AssetCategory.parent_category_id.is_(None),
        )
        if is_active is not None:
            query = query.where(
                AssetCategory.is_active.is_(True)
                if is_active
                else AssetCategory.is_active.is_(False)
            )

        total_count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.execute(
            query.order_by(AssetCategory.category_code).limit(limit).offset(offset)
        ).all()

        categories_view = []
        for row in rows:
            category = row[0]
            categories_view.append(
                {
                    "category_id": category.category_id,
                    "category_code": category.category_code,
                    "category_name": category.category_name,
                    "depreciation_method": category.depreciation_method.value,
                    "useful_life_months": category.useful_life_months,
                    "residual_value_percent": category.residual_value_percent,
                    "capitalization_threshold": category.capitalization_threshold,
                    "is_active": category.is_active,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        active_filters = build_active_filters(
            params={
                "is_active": str(is_active).lower() if is_active is not None else ""
            },
            labels={"is_active": "Status"},
            options={"is_active": {"true": "Active", "false": "Inactive"}},
        )

        return {
            "categories": categories_view,
            "is_active": is_active,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "active_filters": active_filters,
        }

    @staticmethod
    def category_form_context(
        db: Session,
        organization_id: str,
        category_id: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        categories = db.scalars(
            select(AssetCategory)
            .where(
                AssetCategory.organization_id == org_id,
                AssetCategory.parent_category_id.is_(None),
            )
            .order_by(AssetCategory.category_code)
        ).all()
        accounts = FixedAssetWebService._get_accounts(db, org_id)

        category = None
        if category_id:
            category = db.get(AssetCategory, coerce_uuid(category_id))
            if category and category.parent_category_id:
                category = None

        return {
            "category": category,
            "categories": categories,
            "accounts": accounts,
            "depreciation_methods": list(DepreciationMethod),
        }

    def list_categories_response(
        self,
        request: Request,
        auth: WebAuthContext,
        is_active: bool | None,
        page: int,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Asset Categories", "fixed_assets")
        context.update(
            self.list_categories_context(
                db,
                str(auth.organization_id),
                is_active=is_active,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "fixed_assets/categories.html", context
        )

    def new_category_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: str | None = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Asset Category", "fixed_assets")
        context.update(self.category_form_context(db, str(auth.organization_id)))
        context["error"] = error
        return templates.TemplateResponse(
            request, "fixed_assets/category_form.html", context
        )

    async def create_category_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form = await request.form()
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            category_input = AssetCategoryInput(
                category_code=_safe_form_text(form.get("category_code")).strip(),
                category_name=_safe_form_text(form.get("category_name")).strip(),
                asset_account_id=coerce_uuid(
                    _safe_form_text(form.get("asset_account_id"))
                ),
                accumulated_depreciation_account_id=coerce_uuid(
                    _safe_form_text(form.get("accumulated_depreciation_account_id"))
                ),
                depreciation_expense_account_id=coerce_uuid(
                    _safe_form_text(form.get("depreciation_expense_account_id"))
                ),
                gain_loss_disposal_account_id=coerce_uuid(
                    _safe_form_text(form.get("gain_loss_disposal_account_id"))
                ),
                useful_life_months=int(
                    _safe_form_text(form.get("useful_life_months")) or 0
                ),
                depreciation_method=DepreciationMethod(
                    _safe_form_text(form.get("depreciation_method"))
                    or DepreciationMethod.STRAIGHT_LINE.value
                ),
                residual_value_percent=Decimal(
                    _safe_form_text(form.get("residual_value_percent")) or "0"
                ),
                capitalization_threshold=Decimal(
                    _safe_form_text(form.get("capitalization_threshold")) or "0"
                ),
                revaluation_model_allowed=bool(
                    _safe_form_text(form.get("revaluation_model_allowed"))
                ),
                revaluation_surplus_account_id=coerce_uuid(
                    _safe_form_text(form.get("revaluation_surplus_account_id"))
                )
                if _safe_form_text(form.get("revaluation_surplus_account_id"))
                else None,
                impairment_loss_account_id=coerce_uuid(
                    _safe_form_text(form.get("impairment_loss_account_id"))
                )
                if _safe_form_text(form.get("impairment_loss_account_id"))
                else None,
                parent_category_id=None,
                description=_safe_form_text(form.get("description")) or None,
            )

            asset_category_service.create_category(db, org_id, category_input)
            return RedirectResponse(
                url="/fixed-assets/categories?success=Record+saved+successfully",
                status_code=303,
            )
        except Exception as e:
            return self.new_category_form_response(request, auth, db, error=str(e))

    def edit_category_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        category_id: str,
        db: Session,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        category = asset_category_service.get(db, category_id, auth.organization_id)
        if not category or category.organization_id != auth.organization_id:
            return RedirectResponse(url="/fixed-assets/categories", status_code=302)
        if category.parent_category_id:
            return RedirectResponse(url="/fixed-assets/categories", status_code=302)

        context = base_context(request, auth, "Edit Asset Category", "fixed_assets")
        context.update(
            self.category_form_context(
                db, str(auth.organization_id), category_id=category_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(
            request, "fixed_assets/category_form.html", context
        )

    async def update_category_response(
        self,
        request: Request,
        auth: WebAuthContext,
        category_id: str,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form = await request.form()
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            category_input = AssetCategoryInput(
                category_code=_safe_form_text(form.get("category_code")).strip(),
                category_name=_safe_form_text(form.get("category_name")).strip(),
                asset_account_id=coerce_uuid(
                    _safe_form_text(form.get("asset_account_id"))
                ),
                accumulated_depreciation_account_id=coerce_uuid(
                    _safe_form_text(form.get("accumulated_depreciation_account_id"))
                ),
                depreciation_expense_account_id=coerce_uuid(
                    _safe_form_text(form.get("depreciation_expense_account_id"))
                ),
                gain_loss_disposal_account_id=coerce_uuid(
                    _safe_form_text(form.get("gain_loss_disposal_account_id"))
                ),
                useful_life_months=int(
                    _safe_form_text(form.get("useful_life_months")) or 0
                ),
                depreciation_method=DepreciationMethod(
                    _safe_form_text(form.get("depreciation_method"))
                    or DepreciationMethod.STRAIGHT_LINE.value
                ),
                residual_value_percent=Decimal(
                    _safe_form_text(form.get("residual_value_percent")) or "0"
                ),
                capitalization_threshold=Decimal(
                    _safe_form_text(form.get("capitalization_threshold")) or "0"
                ),
                revaluation_model_allowed=bool(
                    _safe_form_text(form.get("revaluation_model_allowed"))
                ),
                revaluation_surplus_account_id=coerce_uuid(
                    _safe_form_text(form.get("revaluation_surplus_account_id"))
                )
                if _safe_form_text(form.get("revaluation_surplus_account_id"))
                else None,
                impairment_loss_account_id=coerce_uuid(
                    _safe_form_text(form.get("impairment_loss_account_id"))
                )
                if _safe_form_text(form.get("impairment_loss_account_id"))
                else None,
                parent_category_id=None,
                description=_safe_form_text(form.get("description")) or None,
            )

            is_active = form.get("is_active") == "on"
            asset_category_service.update_category(
                db, org_id, category_id, category_input, is_active=is_active
            )
            return RedirectResponse(
                url="/fixed-assets/categories?success=Record+saved+successfully",
                status_code=303,
            )
        except Exception as e:
            return self.edit_category_form_response(
                request, auth, category_id, db, error=str(e)
            )

    def toggle_category_response(
        self,
        auth: WebAuthContext,
        category_id: str,
        db: Session,
    ) -> RedirectResponse:
        try:
            org_id = auth.organization_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            asset_category_service.toggle_category(db, org_id, category_id)
        except Exception:
            logger.exception("Ignored exception")

        return RedirectResponse(
            url="/fixed-assets/categories?success=Record+saved+successfully",
            status_code=303,
        )

    @staticmethod
    def depreciation_context(
        db: Session,
        organization_id: str,
        asset_id: str | None,
        period: str | None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        period_id = _try_uuid(period)

        query = select(DepreciationRun, FiscalPeriod).join(
            FiscalPeriod,
            DepreciationRun.fiscal_period_id == FiscalPeriod.fiscal_period_id,
        )
        query = query.where(DepreciationRun.organization_id == org_id)

        if period_id:
            query = query.where(DepreciationRun.fiscal_period_id == period_id)

        total_count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.execute(
            query.order_by(DepreciationRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()

        runs_view = []
        for run, fiscal_period in rows:
            runs_view.append(
                {
                    "run_id": run.run_id,
                    "run_number": run.run_number,
                    "run_description": run.run_description,
                    "detail_url": f"/fixed-assets/depreciation/runs/{run.run_id}",
                    "period_name": fiscal_period.period_name,
                    "period_start": _format_date(fiscal_period.start_date),
                    "period_end": _format_date(fiscal_period.end_date),
                    "status": run.status.value,
                    "assets_processed": run.assets_processed,
                    "total_depreciation": _format_currency(run.total_depreciation),
                    "created_at": _format_date(
                        run.created_at.date() if run.created_at else None
                    ),
                }
            )

        fiscal_period_rows = db.execute(
            select(
                FiscalPeriod.fiscal_period_id,
                FiscalPeriod.period_name,
                FiscalPeriod.start_date,
                FiscalPeriod.end_date,
            )
            .where(FiscalPeriod.organization_id == org_id)
            .order_by(FiscalPeriod.start_date.desc())
        ).all()
        fiscal_periods = []
        for row in fiscal_period_rows:
            if len(row) == 4:
                (
                    fiscal_period_id,
                    period_name,
                    start_date,
                    end_date,
                ) = row
            elif len(row) == 2 and hasattr(row[1], "period_name"):
                period_obj = row[1]
                fiscal_period_id = getattr(period_obj, "fiscal_period_id", None)
                period_name = getattr(period_obj, "period_name", "")
                start_date = getattr(period_obj, "start_date", None)
                end_date = getattr(period_obj, "end_date", None)
            else:
                continue

            fiscal_periods.append(
                {
                    "fiscal_period_id": str(fiscal_period_id),
                    "label": (
                        f"{period_name} ({_format_date(start_date)} - {_format_date(end_date)})"
                    ),
                    "period_name": period_name,
                }
            )

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "depreciation_runs": runs_view,
            "asset_id": asset_id,
            "fiscal_periods": fiscal_periods,
            "period": period,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def depreciation_run_detail_context(
        db: Session,
        organization_id: str,
        run_id: str,
        current_user_id: str | UUID | None = None,
    ) -> dict:
        """Build view context for a single depreciation run."""
        org_id = coerce_uuid(organization_id)
        run = DepreciationService.get(db, run_id, organization_id=org_id)
        fiscal_period = db.get(FiscalPeriod, run.fiscal_period_id)

        schedule_rows = db.execute(
            select(DepreciationSchedule, Asset, AssetCategory)
            .join(Asset, Asset.asset_id == DepreciationSchedule.asset_id)
            .outerjoin(AssetCategory, AssetCategory.category_id == Asset.category_id)
            .where(
                DepreciationSchedule.run_id == run.run_id,
                Asset.organization_id == org_id,
            )
            .order_by(Asset.asset_number.asc(), Asset.asset_name.asc())
        ).all()

        schedules = []
        for schedule, asset, category in schedule_rows:
            schedules.append(
                {
                    "schedule_id": str(schedule.schedule_id),
                    "asset_id": str(asset.asset_id),
                    "asset_number": asset.asset_number,
                    "asset_name": asset.asset_name,
                    "asset_url": f"/fixed-assets/assets/{asset.asset_id}",
                    "category_name": category.category_name if category else "",
                    "depreciation_amount": _format_currency(
                        schedule.depreciation_amount, asset.currency_code
                    ),
                    "opening_nbv": _format_currency(
                        schedule.net_book_value_opening, asset.currency_code
                    ),
                    "closing_nbv": _format_currency(
                        schedule.net_book_value_closing, asset.currency_code
                    ),
                    "opening_accumulated_depreciation": _format_currency(
                        schedule.accumulated_depreciation_opening, asset.currency_code
                    ),
                    "closing_accumulated_depreciation": _format_currency(
                        schedule.accumulated_depreciation_closing, asset.currency_code
                    ),
                    "remaining_life_opening": int(
                        schedule.remaining_life_months_opening
                    ),
                    "remaining_life_closing": int(
                        schedule.remaining_life_months_closing
                    ),
                }
            )

        posting_preview = FixedAssetWebService._build_depreciation_posting_preview(
            db,
            org_id,
            run,
            fiscal_period,
            schedules,
            coerce_uuid(current_user_id) if current_user_id else None,
        )

        return {
            "run": {
                "run_id": str(run.run_id),
                "run_number": run.run_number,
                "run_description": run.run_description,
                "status": run.status.value,
                "assets_processed": run.assets_processed,
                "total_depreciation": _format_currency(run.total_depreciation),
                "journal_entry_id": (
                    str(run.journal_entry_id) if run.journal_entry_id else None
                ),
                "created_at": _format_date(
                    run.created_at.date() if run.created_at else None
                ),
                "calculation_started_at": _format_date(
                    run.calculation_started_at.date()
                    if run.calculation_started_at
                    else None
                ),
                "calculation_completed_at": _format_date(
                    run.calculation_completed_at.date()
                    if run.calculation_completed_at
                    else None
                ),
                "posted_at": _format_date(
                    run.posted_at.date() if run.posted_at else None
                ),
            },
            "period": {
                "period_name": fiscal_period.period_name if fiscal_period else "",
                "period_start": (
                    _format_date(fiscal_period.start_date) if fiscal_period else ""
                ),
                "period_end": (
                    _format_date(fiscal_period.end_date) if fiscal_period else ""
                ),
            },
            "posting_preview": posting_preview,
            "schedules": schedules,
        }

    def depreciation_run_form_context(
        self,
        db: Session,
        organization_id: str,
        period: str | None = None,
    ) -> dict:
        """Context for the dedicated depreciation run creation page."""
        org_id = coerce_uuid(organization_id)
        recommended_period = DepreciationService.get_next_automation_period(db, org_id)

        fiscal_period_rows = db.execute(
            select(
                FiscalPeriod.fiscal_period_id,
                FiscalPeriod.period_name,
                FiscalPeriod.start_date,
                FiscalPeriod.end_date,
                FiscalPeriod.status,
            )
            .where(FiscalPeriod.organization_id == org_id)
            .order_by(FiscalPeriod.start_date.desc())
        ).all()
        fallback_period_id = None
        fiscal_periods = []
        for row_id, period_name, start_date, end_date, status in fiscal_period_rows:
            row_id_str = str(row_id)
            is_posting_eligible = status in PeriodStatus.accepts_postings()
            if fallback_period_id is None and is_posting_eligible:
                fallback_period_id = row_id_str

            is_recommended = (
                recommended_period is not None
                and row_id == recommended_period.fiscal_period_id
            )
            fiscal_periods.append(
                {
                    "fiscal_period_id": row_id_str,
                    "label": (
                        f"{period_name} ({_format_date(start_date)} - "
                        f"{_format_date(end_date)})"
                    ),
                    "period_name": period_name,
                    "is_recommended": is_recommended,
                    "status": status.value if status else None,
                }
            )

        selected_period = period or (
            str(recommended_period.fiscal_period_id)
            if recommended_period is not None
            else fallback_period_id
        )

        return {
            "fiscal_periods": fiscal_periods,
            "period": selected_period,
            "recommended_period_id": (
                str(recommended_period.fiscal_period_id)
                if recommended_period is not None
                else None
            ),
        }

    def asset_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Return asset detail page."""
        org_id = coerce_uuid(auth.organization_id)
        a_id = coerce_uuid(asset_id)

        asset = db.get(Asset, a_id)
        if not asset or asset.organization_id != org_id:
            return RedirectResponse(
                url="/fixed-assets/assets?success=Record+saved+successfully",
                status_code=303,
            )

        category = (
            db.get(AssetCategory, asset.category_id) if asset.category_id else None
        )

        context = base_context(request, auth, "Asset Details", "fixed_assets")
        context.update(
            {
                "asset": {
                    "asset_id": asset.asset_id,
                    "asset_code": asset.asset_number,
                    "asset_name": asset.asset_name,
                    "serial_number": asset.serial_number,
                    "manufacturer": getattr(asset, "manufacturer", None),
                    "model": getattr(asset, "model", None),
                    "description": getattr(asset, "description", None),
                    "category_name": category.category_name if category else None,
                    "status": asset.status.value if asset.status else "IN_USE",
                    "acquisition_date": _format_date(asset.acquisition_date),
                    "acquisition_cost": _format_currency(
                        asset.acquisition_cost, asset.currency_code
                    ),
                    "revalued_amount": _format_currency(
                        asset.revalued_amount, asset.currency_code
                    ),
                    "accumulated_depreciation": _format_currency(
                        asset.accumulated_depreciation, asset.currency_code
                    ),
                    "impairment_loss": _format_currency(
                        asset.impairment_loss, asset.currency_code
                    ),
                    "net_book_value": _format_currency(
                        asset.net_book_value, asset.currency_code
                    ),
                    "currency_code": asset.currency_code,
                    "useful_life_months": asset.useful_life_months,
                    "residual_value": _format_currency(
                        asset.residual_value, asset.currency_code
                    ),
                },
            }
        )
        return templates.TemplateResponse(
            request, "fixed_assets/asset_detail.html", context
        )

    def asset_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Return asset edit form page."""
        org_id = coerce_uuid(auth.organization_id)
        a_id = coerce_uuid(asset_id)

        asset = db.get(Asset, a_id)
        if not asset or asset.organization_id != org_id:
            return RedirectResponse(
                url="/fixed-assets/assets?success=Record+updated+successfully",
                status_code=303,
            )

        if asset.status == AssetStatus.RETIRED:
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?error=Retired+assets+cannot+be+edited",
                status_code=303,
            )

        context = base_context(request, auth, "Edit Asset", "fixed_assets")
        context.update(
            self.asset_form_context(
                db,
                str(auth.organization_id),
                selected_employee_id=asset.custodian_employee_id,
            )
        )
        context["asset"] = asset
        context["is_pre_use_asset"] = asset.status in {
            AssetStatus.NOT_IN_USE,
            AssetStatus.IN_STORE,
        }
        context["form_asset_number"] = asset.asset_number
        context["form_asset_name"] = asset.asset_name
        context["form_category_id"] = (
            str(asset.category_id) if asset.category_id else ""
        )
        context["form_serial_number"] = asset.serial_number or ""
        context["form_location_id"] = (
            str(asset.location_id) if asset.location_id else ""
        )
        context["form_department_id"] = context.get("selected_department_id", "") or ""
        context["form_custodian_employee_id"] = (
            str(asset.custodian_employee_id) if asset.custodian_employee_id else ""
        )
        context["form_description"] = asset.description or ""
        context["form_acquisition_date"] = (
            asset.acquisition_date.isoformat() if asset.acquisition_date else ""
        )
        context["form_acquisition_cost"] = (
            str(asset.acquisition_cost) if asset.acquisition_cost is not None else ""
        )
        context["form_currency_code"] = asset.currency_code or ""
        context["form_status"] = asset.status.value if asset.status else ""
        context["selected_depreciation_schedule_id"] = (
            str(asset.current_depreciation_schedule_id)
            if asset.current_depreciation_schedule_id
            else None
        )
        return templates.TemplateResponse(
            request, "fixed_assets/asset_form.html", context
        )

    async def update_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> RedirectResponse:
        """Handle asset update."""
        try:
            form_data = await request.form()
            logger.info(
                "Asset edit handler entered: asset_id=%s form_keys=%s content_type=%s content_length=%s",
                asset_id,
                sorted(str(key) for key in form_data),
                request.headers.get("content-type"),
                request.headers.get("content-length"),
            )
            org_id = coerce_uuid(auth.organization_id)
            ast_id = coerce_uuid(asset_id)
            asset = db.get(Asset, ast_id)
            if not asset or asset.organization_id != org_id:
                raise HTTPException(status_code=404, detail="Asset not found")

            is_pre_use_asset = asset.status in {
                AssetStatus.NOT_IN_USE,
                AssetStatus.IN_STORE,
            }
            updates: dict[str, object] = {}

            serial_number = _safe_form_text(form_data.get("serial_number")).strip()
            location_id = _safe_form_text(form_data.get("location_id")).strip()
            department_id = _safe_form_text(form_data.get("department_id")).strip()
            custodian_employee_id = _safe_form_text(
                form_data.get("custodian_employee_id")
            ).strip()
            description = _safe_form_text(form_data.get("description")).strip()
            status = _safe_form_text(form_data.get("status")).strip()
            depreciation_schedule_id = _safe_form_text(
                form_data.get("depreciation_schedule_id")
            ).strip()
            _, resolved_custodian_employee_id = self._validate_assignment_selection(
                db,
                str(org_id),
                department_id,
                custodian_employee_id,
                allowed_employee_id=asset.custodian_employee_id,
            )
            updates["serial_number"] = serial_number or None
            updates["location_id"] = UUID(location_id) if location_id else None
            updates["custodian_employee_id"] = resolved_custodian_employee_id
            updates["description"] = description or None
            if status:
                updates["status"] = AssetStatus(status)
            updates["current_depreciation_schedule_id"] = (
                UUID(depreciation_schedule_id) if depreciation_schedule_id else None
            )

            if is_pre_use_asset:
                asset_number = _safe_form_text(form_data.get("asset_number")).strip()
                asset_name = _safe_form_text(form_data.get("asset_name")).strip()
                category_id = _safe_form_text(form_data.get("category_id")).strip()
                acquisition_date = _safe_form_text(
                    form_data.get("acquisition_date")
                ).strip()
                acquisition_cost = _safe_form_text(
                    form_data.get("acquisition_cost")
                ).strip()
                currency_code = _safe_form_text(form_data.get("currency_code")).strip()

                if asset_number:
                    updates["asset_number"] = asset_number
                if asset_name:
                    updates["asset_name"] = asset_name
                if category_id:
                    updates["category_id"] = UUID(category_id)
                if acquisition_date:
                    updates["acquisition_date"] = datetime.strptime(
                        acquisition_date, "%Y-%m-%d"
                    ).date()
                if acquisition_cost:
                    updates["acquisition_cost"] = Decimal(acquisition_cost)
                if currency_code:
                    updates["currency_code"] = currency_code

            asset_service.update_asset(
                db,
                org_id,
                ast_id,
                updates,
            )
            db.commit()
            logger.info(
                "Asset edit committed: asset_id=%s updated_fields=%s status=%s",
                asset_id,
                sorted(updates.keys()),
                asset.status.value if asset.status else None,
            )

            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?success=Asset+updated",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.warning(
                "Asset edit failed: asset_id=%s error=%s",
                asset_id,
                str(e),
            )
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?error={str(e)}",
                status_code=303,
            )

    async def dispose_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> RedirectResponse:
        """Handle asset disposal."""
        try:
            form_data = await request.form()
            org_id = auth.organization_id
            user_id = auth.user_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            disposal_date = _safe_form_text(form_data.get("disposal_date"))
            proceeds = _safe_form_text(form_data.get("proceeds")) or "0"
            costs_of_disposal = (
                _safe_form_text(form_data.get("costs_of_disposal")) or "0"
            )
            disposal_type = _safe_form_text(form_data.get("disposal_type")) or "SALE"
            fiscal_period_id = _safe_form_text(form_data.get("fiscal_period_id"))
            reason = _safe_form_text(form_data.get("reason")) or None

            from app.services.fixed_assets.disposal import (
                DisposalInput,
                DisposalType,
                asset_disposal_service,
            )

            input_data = DisposalInput(
                asset_id=coerce_uuid(asset_id),
                fiscal_period_id=coerce_uuid(fiscal_period_id),
                disposal_date=datetime.strptime(disposal_date, "%Y-%m-%d").date()
                if disposal_date
                else date.today(),
                disposal_type=DisposalType(disposal_type),
                disposal_proceeds=Decimal(proceeds) if proceeds else Decimal("0"),
                costs_of_disposal=Decimal(costs_of_disposal)
                if costs_of_disposal
                else Decimal("0"),
                disposal_reason=reason,
            )

            asset_disposal_service.create_disposal(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?success=Asset+disposed+successfully",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?error={str(e)}",
                status_code=303,
            )

    async def revalue_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> RedirectResponse:
        """Handle asset revaluation."""
        try:
            form_data = await request.form()
            org_id = auth.organization_id
            user_id = auth.user_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            revaluation_date = _safe_form_text(form_data.get("revaluation_date"))
            new_value = _safe_form_text(form_data.get("new_value")) or "0"
            valuation_method = (
                _safe_form_text(form_data.get("valuation_method")) or "MARKET"
            )
            fiscal_period_id = _safe_form_text(form_data.get("fiscal_period_id"))
            reason = _safe_form_text(form_data.get("reason")) or None

            from app.services.fixed_assets.revaluation import (
                RevaluationInput,
                asset_revaluation_service,
            )

            input_data = RevaluationInput(
                asset_id=coerce_uuid(asset_id),
                fiscal_period_id=coerce_uuid(fiscal_period_id),
                revaluation_date=datetime.strptime(revaluation_date, "%Y-%m-%d").date()
                if revaluation_date
                else date.today(),
                fair_value=Decimal(new_value) if new_value else Decimal("0"),
                valuation_method=valuation_method,
                valuation_basis=reason,
            )

            asset_revaluation_service.create_revaluation(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?success=Asset+revalued+successfully",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?error={str(e)}",
                status_code=303,
            )

    async def impair_asset_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        asset_id: str,
    ) -> RedirectResponse:
        """Handle asset impairment."""
        try:
            form_data = await request.form()
            form_data.get("impairment_date")
            form_data.get("impairment_amount", "0")
            form_data.get("reason", "")

            # Impairment is handled through the asset service or a dedicated impairment service
            # For now, redirect with placeholder
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?success=Impairment+recorded",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/fixed-assets/assets/{asset_id}?error={str(e)}",
                status_code=303,
            )

    async def run_depreciation_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Run depreciation for a period."""
        try:
            form_data = await request.form()
            fiscal_period_id = _safe_form_text(form_data.get("fiscal_period_id"))
            posting_date_text = _safe_form_text(form_data.get("posting_date"))
            org_id = auth.organization_id
            user_id = auth.user_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if not fiscal_period_id:
                raise ValueError("Fiscal period is required")
            if posting_date_text:
                datetime.strptime(posting_date_text, "%Y-%m-%d").date()

            from app.services.fixed_assets.depreciation import DepreciationService

            run = DepreciationService.create_depreciation_run(
                db,
                org_id,
                coerce_uuid(fiscal_period_id),
                user_id,
            )
            DepreciationService.calculate_run(db, org_id, run.run_id)

            return RedirectResponse(
                url=(
                    f"/fixed-assets/depreciation/runs/{run.run_id}"
                    "?success=Depreciation+run+calculated.+Awaiting+posting"
                ),
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/fixed-assets/depreciation?error={str(e)}",
                status_code=303,
            )

    async def post_depreciation_run_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        run_id: str,
    ) -> RedirectResponse:
        """Post a calculated depreciation run to the general ledger."""
        try:
            form_data = await request.form()
            posting_date_text = _safe_form_text(form_data.get("posting_date"))
            org_id = auth.organization_id
            user_id = auth.user_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")

            posting_date = None
            if posting_date_text:
                posting_date = datetime.strptime(posting_date_text, "%Y-%m-%d").date()

            DepreciationService.post_run(
                db,
                org_id,
                coerce_uuid(run_id),
                user_id,
                posting_date=posting_date,
            )
            return RedirectResponse(
                url=(
                    f"/fixed-assets/depreciation/runs/{run_id}"
                    "?success=Depreciation+posted+successfully"
                ),
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/fixed-assets/depreciation/runs/{run_id}?error={quote(str(e))}",
                status_code=303,
            )


fa_web_service = FixedAssetWebService()
