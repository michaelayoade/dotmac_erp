"""Expense claim web responses."""

from __future__ import annotations

import csv
import io
import logging
from datetime import date as date_type
from decimal import Decimal
from importlib import import_module
from typing import Any, cast
from urllib.parse import quote, urlencode

from fastapi import Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    Response,
    RedirectResponse,
    StreamingResponse,
)
from sqlalchemy import and_, exists, false, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.models.domain_settings import SettingDomain
from app.models.expense import (
    ExpenseClaim,
    ExpenseClaimApprovalStep,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.models.people.hr.employee import Employee
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.expense.expense_service import (
    ApproverAuthorityError,
    ExpenseClaimStatusError,
    ExpenseService,
    ExpenseServiceError,
)
from app.services.expense.limit_service import (
    ApproverWeeklyBudgetExhaustedError,
    ExpenseLimitServiceError,
)
from app.services.expense.web_common import ExpenseWebCommonMixin
from app.services.finance.platform.authorization import AuthorizationService
from app.services.pm.comment import comment_service
from app.services.recent_activity import get_recent_activity_for_record
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)


def _web_facade():
    return import_module("app.services.expense.web")


class ExpenseClaimsWebMixin(ExpenseWebCommonMixin):
    @staticmethod
    def _parse_claim_filter_date(value: str | None) -> date_type | None:
        if not value:
            return None
        try:
            return date_type.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _claim_status_filter(status: str | None) -> ExpenseClaimStatus | None:
        if not status:
            return None
        try:
            return ExpenseClaimStatus(status)
        except ValueError:
            return None

    @staticmethod
    def _filtered_claims_stmt(
        auth,
        org_id,
        view: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        search: str | None = None,
        employee_id: str | None = None,
        approver_id: str | None = None,
    ):
        auth_employee_id = coerce_uuid(auth.employee_id)
        filter_employee_id = coerce_uuid(employee_id) if employee_id else None
        filter_approver_id = coerce_uuid(approver_id) if approver_id else None
        filter_view = "submitted_to_me" if view == "submitted_to_me" else "all"
        status_value = ExpenseClaimsWebMixin._claim_status_filter(status)
        start = ExpenseClaimsWebMixin._parse_claim_filter_date(start_date)
        end = ExpenseClaimsWebMixin._parse_claim_filter_date(end_date)

        stmt = select(ExpenseClaim).where(ExpenseClaim.organization_id == org_id)
        if filter_view == "submitted_to_me":
            if auth_employee_id:
                latest_round = (
                    select(func.max(ExpenseClaimApprovalStep.submission_round))
                    .where(ExpenseClaimApprovalStep.claim_id == ExpenseClaim.claim_id)
                    .correlate(ExpenseClaim)
                    .scalar_subquery()
                )
                assigned_in_latest_round = exists(
                    select(1).where(
                        ExpenseClaimApprovalStep.claim_id == ExpenseClaim.claim_id,
                        ExpenseClaimApprovalStep.submission_round == latest_round,
                        ExpenseClaimApprovalStep.approver_id == auth_employee_id,
                    )
                )
                has_steps = exists(
                    select(1).where(
                        ExpenseClaimApprovalStep.claim_id == ExpenseClaim.claim_id
                    )
                )
                legacy_assignment = or_(
                    ExpenseClaim.requested_approver_id == auth_employee_id,
                    and_(
                        ExpenseClaim.requested_approver_id.is_(None),
                        ExpenseClaim.approver_id == auth_employee_id,
                    ),
                )
                stmt = stmt.where(
                    or_(
                        assigned_in_latest_round,
                        and_(~has_steps, legacy_assignment),
                    )
                )
            else:
                stmt = stmt.where(false())
        if filter_approver_id:
            latest_round = (
                select(func.max(ExpenseClaimApprovalStep.submission_round))
                .where(ExpenseClaimApprovalStep.claim_id == ExpenseClaim.claim_id)
                .correlate(ExpenseClaim)
                .scalar_subquery()
            )
            assigned_in_latest_round = exists(
                select(1).where(
                    ExpenseClaimApprovalStep.claim_id == ExpenseClaim.claim_id,
                    ExpenseClaimApprovalStep.submission_round == latest_round,
                    ExpenseClaimApprovalStep.approver_id == filter_approver_id,
                )
            )
            has_steps = exists(
                select(1).where(
                    ExpenseClaimApprovalStep.claim_id == ExpenseClaim.claim_id
                )
            )
            legacy_assignment = or_(
                ExpenseClaim.requested_approver_id == filter_approver_id,
                ExpenseClaim.approver_id == filter_approver_id,
            )
            stmt = stmt.where(
                or_(assigned_in_latest_round, and_(~has_steps, legacy_assignment))
            )
        if filter_employee_id:
            stmt = stmt.where(ExpenseClaim.employee_id == filter_employee_id)
        if status_value:
            stmt = stmt.where(ExpenseClaim.status == status_value)
        if start:
            stmt = stmt.where(ExpenseClaim.claim_date >= start)
        if end:
            stmt = stmt.where(ExpenseClaim.claim_date <= end)
        if search:
            term = f"%{search}%"
            stmt = stmt.where(
                or_(
                    ExpenseClaim.claim_number.ilike(term),
                    ExpenseClaim.purpose.ilike(term),
                )
            )
        return stmt, filter_view, filter_employee_id, filter_approver_id

    @staticmethod
    def _claim_query_options():
        return (
            joinedload(ExpenseClaim.employee).joinedload(Employee.person),
            selectinload(ExpenseClaim.approval_steps),
            joinedload(ExpenseClaim.requested_approver).joinedload(Employee.person),
            joinedload(ExpenseClaim.approver).joinedload(Employee.person),
        )

    @staticmethod
    def claim_employee_typeahead(
        db,
        organization_id: str,
        query: str,
        limit: int = 20,
    ) -> dict[str, list[dict[str, str]]]:
        """Search employees that have expense claims for claims list filters."""
        org_id = coerce_uuid(organization_id)
        search_term = f"%{query.strip()}%"
        stmt = (
            select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .options(joinedload(Employee.person))
            .where(
                Employee.organization_id == org_id,
                exists(
                    select(1).where(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.employee_id == Employee.employee_id,
                    )
                ),
            )
            .where(
                or_(
                    Person.first_name.ilike(search_term),
                    Person.last_name.ilike(search_term),
                    Person.email.ilike(search_term),
                    Employee.employee_code.ilike(search_term),
                )
            )
            .order_by(Person.first_name.asc(), Person.last_name.asc())
            .limit(limit)
        )
        employees = list(db.scalars(stmt).unique().all())
        items: list[dict[str, str]] = []
        for employee in employees:
            label = employee.full_name or employee.employee_code or "Unknown Employee"
            if employee.employee_code and employee.full_name:
                label = f"{employee.full_name} ({employee.employee_code})"
            items.append(
                {
                    "ref": str(employee.employee_id),
                    "label": label,
                    "name": employee.full_name or "",
                    "employee_code": employee.employee_code or "",
                }
            )
        return {"items": items}

    @staticmethod
    def _current_approver_name(claim: ExpenseClaim) -> str:
        """Extract the current pending approver name for display in the list.

        Checks persisted approval steps first (latest round, first pending),
        then falls back to the requested_approver relationship.
        """
        if claim.approval_steps:
            max_round = max(s.submission_round for s in claim.approval_steps)
            current_round_steps = sorted(
                (s for s in claim.approval_steps if s.submission_round == max_round),
                key=lambda s: s.step_number,
            )
            # Find first pending step
            for step in current_round_steps:
                if not step.decision:
                    return step.approver_name
            # All decided — show last decider
            if current_round_steps:
                return current_round_steps[-1].approver_name
        # Legacy fallback
        if claim.requested_approver:
            return claim.requested_approver.full_name or ""
        if claim.approver:
            return claim.approver.full_name or ""
        return ""

    @staticmethod
    def claims_list_response(
        request: Request,
        auth,
        db,
        view: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        search: str | None = None,
        employee_id: str | None = None,
        approver_id: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        stmt, filter_view, filter_employee_id, filter_approver_id = (
            ExpenseClaimsWebMixin._filtered_claims_stmt(
                auth=auth,
                org_id=org_id,
                view=view,
                status=status,
                start_date=start_date,
                end_date=end_date,
                search=search,
                employee_id=employee_id,
                approver_id=approver_id,
            )
        )
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))

        claims = list(
            db.scalars(
                stmt.options(*ExpenseClaimsWebMixin._claim_query_options())
                .order_by(
                    ExpenseClaim.claim_date.desc(), ExpenseClaim.claim_number.desc()
                )
                .offset(offset)
                .limit(limit)
            )
            .unique()
            .all()
        )
        claim_employees = list(
            db.scalars(
                select(Employee)
                .join(Person, Person.id == Employee.person_id)
                .options(joinedload(Employee.person))
                .where(
                    Employee.organization_id == org_id,
                    exists(
                        select(1).where(
                            ExpenseClaim.organization_id == org_id,
                            ExpenseClaim.employee_id == Employee.employee_id,
                        )
                    ),
                )
                .order_by(Person.first_name.asc(), Person.last_name.asc())
            )
            .unique()
            .all()
        )
        claim_approvers = list(
            db.scalars(
                select(Employee)
                .join(Person, Person.id == Employee.person_id)
                .options(joinedload(Employee.person))
                .where(
                    Employee.organization_id == org_id,
                    or_(
                        exists(
                            select(1).where(
                                ExpenseClaimApprovalStep.organization_id == org_id,
                                ExpenseClaimApprovalStep.approver_id
                                == Employee.employee_id,
                            )
                        ),
                        exists(
                            select(1).where(
                                ExpenseClaim.organization_id == org_id,
                                or_(
                                    ExpenseClaim.requested_approver_id
                                    == Employee.employee_id,
                                    ExpenseClaim.approver_id == Employee.employee_id,
                                ),
                            )
                        ),
                    ),
                )
                .order_by(Person.first_name.asc(), Person.last_name.asc())
            )
            .unique()
            .all()
        )
        selected_employee = None
        selected_approver = None
        employee_options: dict[str, str] = {}
        approver_options: dict[str, str] = {}
        if filter_employee_id:
            selected_employee = db.scalars(
                select(Employee)
                .join(Person, Person.id == Employee.person_id)
                .options(joinedload(Employee.person))
                .where(
                    Employee.organization_id == org_id,
                    Employee.employee_id == filter_employee_id,
                )
            ).first()
            if selected_employee:
                employee_options[str(selected_employee.employee_id)] = (
                    selected_employee.full_name
                    or selected_employee.employee_code
                    or str(selected_employee.employee_id)
                )
        if filter_approver_id:
            selected_approver = db.scalars(
                select(Employee)
                .join(Person, Person.id == Employee.person_id)
                .options(joinedload(Employee.person))
                .where(
                    Employee.organization_id == org_id,
                    Employee.employee_id == filter_approver_id,
                )
            ).first()
            if selected_approver:
                approver_options[str(selected_approver.employee_id)] = (
                    selected_approver.full_name
                    or selected_approver.employee_code
                    or str(selected_approver.employee_id)
                )

        status_rows = db.execute(
            select(ExpenseClaim.status, func.count())
            .where(ExpenseClaim.organization_id == org_id)
            .group_by(ExpenseClaim.status)
        ).all()
        counts = {
            (status.value if status else "UNKNOWN"): count
            for status, count in status_rows
        }
        can_delete_claim = auth.is_admin
        if not can_delete_claim and auth.person_id:
            can_delete_claim = AuthorizationService.check_permission(
                db, auth.person_id, "expense:claims:delete", org_id
            )

        # Build approver name lookup for each claim
        claim_approver_names: dict[str, str] = {}
        for c in claims:
            name = ExpenseClaimsWebMixin._current_approver_name(c)
            if name:
                claim_approver_names[str(c.claim_id)] = name

        export_params = {
            key: value
            for key, value in {
                "view": filter_view if filter_view == "submitted_to_me" else None,
                "status": status,
                "start_date": start_date,
                "end_date": end_date,
                "search": search,
                "employee_id": employee_id,
                "approver_id": approver_id,
            }.items()
            if value
        }
        export_url = "/expense/claims/export"
        if export_params:
            export_url = f"{export_url}?{urlencode(export_params)}"

        context = _web_facade().base_context(request, auth, "Expense Claims", "claims")
        context.update(
            {
                "claims": claims,
                "claim_approver_names": claim_approver_names,
                "search": search or "",
                "statuses": [status.value for status in ExpenseClaimStatus],
                "status_counts": counts,
                "filter_status": status or "",
                "filter_view": filter_view,
                "filter_start_date": start_date or "",
                "filter_end_date": end_date or "",
                "filter_employee_id": employee_id or "",
                "filter_approver_id": approver_id or "",
                "claim_employees": claim_employees,
                "claim_approvers": claim_approvers,
                "selected_employee": selected_employee,
                "selected_approver": selected_approver,
                "export_url": export_url,
                "total": total or 0,
                "offset": offset,
                "limit": limit,
                "page": (offset // limit) + 1 if limit > 0 else 1,
                "total_pages": ((total or 0) + limit - 1) // limit if limit > 0 else 1,
                "can_delete_claim": can_delete_claim,
                "active_filters": build_active_filters(
                    params={
                        "status": status,
                        "view": view,
                        "start_date": start_date,
                        "end_date": end_date,
                        "employee_id": employee_id,
                        "approver_id": approver_id,
                    },
                    labels={
                        "start_date": "From",
                        "end_date": "To",
                        "employee_id": "Employee",
                        "approver_id": "Approver",
                    },
                    options={
                        "employee_id": employee_options,
                        "approver_id": approver_options,
                    },
                ),
            }
        )
        return cast(
            HTMLResponse,
            _web_facade().templates.TemplateResponse(
                request, "expense/claims_list.html", context
            ),
        )

    @staticmethod
    def _csv_text(value: object) -> str:
        text = "" if value is None else str(value)
        if text.startswith(("=", "+", "-", "@", "\t", "\r")):
            return f"'{text}"
        return text

    @staticmethod
    def _csv_date(value: date_type | None) -> str:
        return value.isoformat() if value else ""

    @staticmethod
    def _csv_decimal(value: Decimal | None) -> str:
        return f"{value:.2f}" if value is not None else ""

    @staticmethod
    def claims_export_response(
        auth,
        db,
        view: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        search: str | None = None,
        employee_id: str | None = None,
        approver_id: str | None = None,
    ) -> Response:
        org_id = coerce_uuid(auth.organization_id)
        stmt, _filter_view, _filter_employee_id, _filter_approver_id = (
            ExpenseClaimsWebMixin._filtered_claims_stmt(
                auth=auth,
                org_id=org_id,
                view=view,
                status=status,
                start_date=start_date,
                end_date=end_date,
                search=search,
                employee_id=employee_id,
                approver_id=approver_id,
            )
        )
        claims = list(
            db.scalars(
                stmt.options(*ExpenseClaimsWebMixin._claim_query_options()).order_by(
                    ExpenseClaim.claim_date.desc(), ExpenseClaim.claim_number.desc()
                )
            )
            .unique()
            .all()
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Claim Number",
                "Claim Date",
                "Expense Period Start",
                "Expense Period End",
                "Purpose",
                "Raised By",
                "Employee Code",
                "Approver",
                "Status",
                "Currency",
                "Claimed Amount",
                "Approved Amount",
                "Advance Adjusted",
                "Net Payable",
                "Approved On",
                "Paid On",
                "Payment Reference",
                "Recipient Name",
                "Recipient Bank",
                "Notes",
            ]
        )
        for claim in claims:
            employee = claim.employee
            writer.writerow(
                [
                    ExpenseClaimsWebMixin._csv_text(claim.claim_number),
                    ExpenseClaimsWebMixin._csv_date(claim.claim_date),
                    ExpenseClaimsWebMixin._csv_date(claim.expense_period_start),
                    ExpenseClaimsWebMixin._csv_date(claim.expense_period_end),
                    ExpenseClaimsWebMixin._csv_text(claim.purpose),
                    ExpenseClaimsWebMixin._csv_text(
                        employee.full_name if employee else ""
                    ),
                    ExpenseClaimsWebMixin._csv_text(
                        employee.employee_code if employee else ""
                    ),
                    ExpenseClaimsWebMixin._csv_text(
                        ExpenseClaimsWebMixin._current_approver_name(claim)
                    ),
                    claim.status.value if claim.status else "",
                    ExpenseClaimsWebMixin._csv_text(claim.currency_code),
                    ExpenseClaimsWebMixin._csv_decimal(claim.total_claimed_amount),
                    ExpenseClaimsWebMixin._csv_decimal(claim.total_approved_amount),
                    ExpenseClaimsWebMixin._csv_decimal(claim.advance_adjusted),
                    ExpenseClaimsWebMixin._csv_decimal(claim.net_payable_amount),
                    ExpenseClaimsWebMixin._csv_date(claim.approved_on),
                    ExpenseClaimsWebMixin._csv_date(claim.paid_on),
                    ExpenseClaimsWebMixin._csv_text(claim.payment_reference),
                    ExpenseClaimsWebMixin._csv_text(claim.recipient_name),
                    ExpenseClaimsWebMixin._csv_text(claim.recipient_bank_name),
                    ExpenseClaimsWebMixin._csv_text(claim.notes),
                ]
            )

        start = ExpenseClaimsWebMixin._parse_claim_filter_date(start_date)
        end = ExpenseClaimsWebMixin._parse_claim_filter_date(end_date)
        filename_parts = ["expense_claims"]
        if start:
            filename_parts.append(f"from_{start.isoformat()}")
        if end:
            filename_parts.append(f"to_{end.isoformat()}")
        filename = "_".join(filename_parts) + ".csv"
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @staticmethod
    def claim_detail_response(request: Request, auth, db, claim_id: str):
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        claim = (
            db.scalars(
                select(ExpenseClaim)
                .options(
                    joinedload(ExpenseClaim.items).joinedload(
                        ExpenseClaimItem.category
                    ),
                    joinedload(ExpenseClaim.employee),
                )
                .where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.claim_id == claim_uuid,
                )
            )
            .unique()
            .first()
        )
        if not claim:
            return RedirectResponse("/expense/claims/list", status_code=302)

        approve_perms = [
            "expense:claims:approve:tier1",
            "expense:claims:approve:tier2",
            "expense:claims:approve:tier3",
        ]
        can_approve = auth.is_admin
        if not can_approve and auth.person_id:
            can_approve = AuthorizationService.check_any_permission(
                db, auth.person_id, approve_perms, org_id
            )
        can_submit = (
            auth.is_admin or can_approve
        ) and claim.status == ExpenseClaimStatus.DRAFT
        can_reject = auth.is_admin
        if not can_reject and auth.person_id:
            can_reject = AuthorizationService.check_permission(
                db, auth.person_id, "expense:claims:reject", org_id
            )
        can_delete = auth.is_admin
        if not can_delete and auth.person_id:
            can_delete = AuthorizationService.check_permission(
                db, auth.person_id, "expense:claims:delete", org_id
            )
        can_delete = can_delete and claim.status == ExpenseClaimStatus.DRAFT
        can_act = (can_approve or can_reject) and claim.status in {
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        }
        can_reimburse_expense = auth.is_admin or auth.has_permission(
            "expense:claims:reimburse"
        )
        if not can_reimburse_expense and auth.person_id:
            can_reimburse_expense = AuthorizationService.check_permission(
                db, auth.person_id, "expense:claims:reimburse", org_id
            )

        has_active_payment = False
        if claim.status == ExpenseClaimStatus.APPROVED:
            from app.models.finance.payments.payment_intent import (
                PaymentIntent,
                PaymentIntentStatus,
            )

            active_statuses = [
                PaymentIntentStatus.PENDING,
                PaymentIntentStatus.PROCESSING,
            ]
            has_active_payment = (
                db.scalars(
                    select(PaymentIntent).where(
                        PaymentIntent.organization_id == org_id,
                        PaymentIntent.source_type == "EXPENSE_CLAIM",
                        PaymentIntent.source_id == claim_uuid,
                        PaymentIntent.status.in_(active_statuses),
                    )
                ).first()
                is not None
            )

        categories = []
        if can_act and can_approve:
            from app.models.expense.expense_claim import ExpenseCategory

            categories = list(
                db.scalars(
                    select(ExpenseCategory)
                    .where(
                        ExpenseCategory.organization_id == org_id,
                        ExpenseCategory.is_active.is_(True),
                    )
                    .order_by(ExpenseCategory.category_name)
                ).all()
            )

        context = _web_facade().base_context(
            request, auth, f"Claim {claim.claim_number}", "claims"
        )
        context.update(
            {
                "claim": claim,
                "comments": comment_service.list_comments(
                    db,
                    organization_id=org_id,
                    entity_type="EXPENSE_CLAIM",
                    entity_id=claim_uuid,
                    include_internal=auth.is_admin,
                ),
                "recent_activity": get_recent_activity_for_record(
                    db, org_id, record=claim, limit=10
                ),
                "categories": categories,
                "can_submit": can_submit,
                "can_act": can_act,
                "can_approve": can_approve,
                "can_reject": can_reject,
                "can_delete": can_delete,
                "can_reimburse_expense": can_reimburse_expense
                and claim.status == ExpenseClaimStatus.APPROVED,
                "has_active_payment": has_active_payment,
                "action": request.query_params.get("action"),
                "error": request.query_params.get("error_message")
                or request.query_params.get("error"),
            }
        )
        return _web_facade().templates.TemplateResponse(
            request, "expense/claim_detail.html", context
        )

    @staticmethod
    def add_claim_comment_response(
        claim_id: str, content: str, auth, db
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        comment_text = (content or "").strip()
        if not comment_text:
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=Comment+cannot+be+empty",
                status_code=303,
            )
        if len(comment_text) > 5000:
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=Comment+is+too+long", status_code=303
            )

        claim = db.scalar(
            select(ExpenseClaim).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.claim_id == claim_uuid,
            )
        )
        if not claim:
            return RedirectResponse(
                "/expense/claims/list?error=not_found", status_code=302
            )

        author_id_raw = auth.person_id or auth.user_id
        if not author_id_raw:
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=Unable+to+identify+comment+author",
                status_code=303,
            )

        comment_service.add_comment(
            db,
            organization_id=org_id,
            entity_type="EXPENSE_CLAIM",
            entity_id=claim_uuid,
            author_id=coerce_uuid(author_id_raw),
            content=comment_text,
            is_internal=False,
        )
        db.flush()
        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=comment_added", status_code=303
        )

    @staticmethod
    def claim_item_detail_response(
        request: Request, claim_id: str, item_id: str, auth, db
    ):
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        item_uuid = coerce_uuid(item_id)

        item = (
            db.scalars(
                select(ExpenseClaimItem)
                .options(
                    joinedload(ExpenseClaimItem.category),
                    joinedload(ExpenseClaimItem.claim).joinedload(
                        ExpenseClaim.employee
                    ),
                    joinedload(ExpenseClaimItem.claim).joinedload(
                        ExpenseClaim.approver
                    ),
                    joinedload(ExpenseClaimItem.claim).joinedload(ExpenseClaim.project),
                    joinedload(ExpenseClaimItem.claim).joinedload(ExpenseClaim.ticket),
                    joinedload(ExpenseClaimItem.claim).joinedload(ExpenseClaim.task),
                )
                .join(ExpenseClaim, ExpenseClaim.claim_id == ExpenseClaimItem.claim_id)
                .where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.claim_id == claim_uuid,
                    ExpenseClaimItem.item_id == item_uuid,
                )
            )
            .unique()
            .first()
        )
        if not item:
            return RedirectResponse(f"/expense/claims/{claim_id}", status_code=302)

        requested_approver = None
        claim = item.claim
        if claim and claim.requested_approver_id:
            requested_approver = db.scalars(
                select(Employee).where(
                    Employee.organization_id == org_id,
                    Employee.employee_id == claim.requested_approver_id,
                )
            ).first()

        context = _web_facade().base_context(
            request, auth, f"Expense Item {item.sequence or 0}", "claims"
        )
        context.update(
            {
                "claim": claim,
                "item": item,
                "requested_approver": requested_approver,
                "error": request.query_params.get("error"),
            }
        )
        return _web_facade().templates.TemplateResponse(
            request, "expense/claim_item_detail.html", context
        )

    @classmethod
    def claim_receipt_response(
        cls, claim_id: str, item_id: str, auth, db, index: int = 0
    ):
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        item_uuid = coerce_uuid(item_id)

        item = db.scalar(
            select(ExpenseClaimItem)
            .join(ExpenseClaim, ExpenseClaim.claim_id == ExpenseClaimItem.claim_id)
            .where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.claim_id == claim_uuid,
                ExpenseClaimItem.item_id == item_uuid,
            )
        )
        if not item or not item.receipt_url:
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=Receipt+not+found", status_code=303
            )

        receipt_urls = cls._parse_receipt_urls(item.receipt_url)
        if not receipt_urls or index < 0 or index >= len(receipt_urls):
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=Receipt+not+found", status_code=303
            )

        receipt_url = receipt_urls[index]
        if cls._is_remote_receipt(receipt_url):
            return RedirectResponse(receipt_url, status_code=302)

        org_prefix = f"expense_receipts/{org_id}/"
        if receipt_url.startswith("expense_receipts/"):
            if not receipt_url.lower().startswith(org_prefix.lower()):
                logger.warning(
                    "Receipt key org mismatch",
                    extra={
                        "claim_id": claim_id,
                        "item_id": item_id,
                        "organization_id": str(org_id),
                        "receipt_url": receipt_url,
                    },
                )
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=Receipt+file+is+unavailable",
                    status_code=303,
                )

            storage = _web_facade().get_storage()
            if not storage.exists(receipt_url):
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=Receipt+not+found",
                    status_code=303,
                )

            chunks, content_type, content_length = storage.stream(receipt_url)
            filename = cls._UNSAFE_FILENAME_RE.sub("_", receipt_url.split("/")[-1])
            headers: dict[str, str] = {
                "Content-Disposition": f'inline; filename="{filename}"'
            }
            if content_length is not None:
                headers["Content-Length"] = str(content_length)
            return StreamingResponse(
                chunks,
                media_type=content_type or "application/octet-stream",
                headers=headers,
            )

        try:
            receipt_path = cls._resolve_claim_receipt_path(receipt_url)
        except FileNotFoundError:
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=Receipt+not+found", status_code=303
            )
        except Exception:
            logger.warning(
                "Invalid receipt path for claim item",
                extra={
                    "claim_id": claim_id,
                    "item_id": item_id,
                    "organization_id": str(org_id),
                },
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=Receipt+file+is+unavailable",
                status_code=303,
            )

        return FileResponse(
            path=str(receipt_path),
            media_type=cls._guess_media_type(receipt_path.name),
            filename=receipt_path.name,
            content_disposition_type="inline",
        )

    @staticmethod
    def submit_claim_response(claim_id: str, auth, db) -> RedirectResponse:
        if not (
            auth.is_admin
            or auth.has_any_permission(
                [
                    "expense:claims:approve:tier1",
                    "expense:claims:approve:tier2",
                    "expense:claims:approve:tier3",
                ]
            )
        ):
            return RedirectResponse(
                "/expense/claims/list?error=permission", status_code=302
            )

        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        actor_id = coerce_uuid(auth.person_id) if auth.person_id else None
        svc = ExpenseService(db)
        try:
            result = svc.submit_claim(org_id, claim_uuid, actor_id=actor_id)
            if not result.success:
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=submit_in_progress",
                    status_code=303,
                )
            db.flush()
        except ExpenseClaimStatusError:
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except ExpenseServiceError as exc:
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error={quote(str(exc))}", status_code=303
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Expense claim submit failed", extra={"claim_id": claim_id}
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=submit_failed", status_code=303
            )
        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=submitted", status_code=303
        )

    @classmethod
    def approve_claim_response(
        cls, claim_id: str, auth, db, form_data: dict[str, str] | None = None
    ) -> RedirectResponse:
        if not auth.has_any_permission(
            [
                "expense:claims:approve:tier1",
                "expense:claims:approve:tier2",
                "expense:claims:approve:tier3",
            ]
        ):
            return RedirectResponse(
                "/expense/claims/list?error=permission", status_code=302
            )

        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        actor_id = coerce_uuid(auth.person_id) if auth.person_id else None
        approver = db.scalars(
            select(Employee)
            .where(Employee.organization_id == org_id)
            .where(Employee.person_id == auth.person_id)
        ).first()
        approver_id = approver.employee_id if approver else None

        corrections: list[dict[str, Any]] | None = None
        approval_notes = None
        if form_data:
            approval_notes = (form_data.get("approval_notes") or "").strip() or None
            item_ids = (
                form_data.getlist("item_id") if hasattr(form_data, "getlist") else []
            )
            if item_ids:
                corrections = []
                for raw_item_id in item_ids:
                    item_id = str(raw_item_id).strip()
                    if not item_id:
                        continue
                    correction: dict[str, Any] = {"item_id": item_id}
                    raw_amount = (
                        form_data.get(f"approved_amount_{item_id}") or ""
                    ).strip()
                    correction["approved_amount"] = (
                        Decimal(raw_amount) if raw_amount else Decimal("0")
                    )
                    category_id = (
                        form_data.get(f"category_id_{item_id}") or ""
                    ).strip()
                    if category_id:
                        correction["category_id"] = category_id
                    description = (
                        form_data.get(f"description_{item_id}") or ""
                    ).strip()
                    if description:
                        correction["description"] = description
                    corrections.append(correction)

        route_to_ap = bool(
            resolve_value(db, SettingDomain.expense, "expense_route_to_ap")
        )
        svc = ExpenseService(db)

        def _claim_error_redirect(message: str) -> RedirectResponse:
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error_message={quote(message)}",
                status_code=303,
            )

        try:
            claim = svc.approve_claim(
                org_id,
                claim_uuid,
                approver_id=approver_id,
                corrections=corrections,
                notes=approval_notes,
                create_supplier_invoice=route_to_ap,
                actor_id=actor_id,
            )
            if claim.status not in {
                ExpenseClaimStatus.APPROVED,
                ExpenseClaimStatus.PENDING_APPROVAL,
            }:
                db.rollback()
                return _claim_error_redirect(
                    "Approval is already in progress for this claim."
                )
            db.flush()
        except ApproverAuthorityError as exc:
            db.rollback()
            return _claim_error_redirect(str(exc))
        except ApproverWeeklyBudgetExhaustedError as exc:
            db.rollback()
            return _claim_error_redirect(str(exc))
        except ExpenseLimitServiceError as exc:
            db.rollback()
            return _claim_error_redirect(str(exc))
        except ExpenseClaimStatusError:
            db.rollback()
            return _claim_error_redirect(
                "This claim can no longer be approved in its current status."
            )
        except ValueError as exc:
            db.rollback()
            message = str(exc).strip() or "Approval could not be completed."
            if message == "Approver is not assigned to the current approval step":
                message = (
                    "You cannot approve this claim yet because it is assigned "
                    "to a different approval step."
                )
            elif message == "Claim has no pending approval steps":
                message = "This claim has no pending approval step."
            return _claim_error_redirect(message)
        except ExpenseServiceError as exc:
            db.rollback()
            return _claim_error_redirect(str(exc))
        except Exception:
            db.rollback()
            logging.getLogger(__name__).exception(
                "Expense claim approval failed", extra={"claim_id": claim_id}
            )
            return _claim_error_redirect(
                "Approval failed. Please refresh the claim and try again."
            )

        action = (
            "approved"
            if claim.status == ExpenseClaimStatus.APPROVED
            else "approval_recorded"
        )
        return RedirectResponse(
            f"/expense/claims/{claim_id}?action={action}", status_code=303
        )

    @staticmethod
    def reject_claim_response(
        claim_id: str, reason: str | None, auth, db
    ) -> RedirectResponse:
        if not auth.has_any_permission(
            [
                "expense:claims:approve:tier1",
                "expense:claims:approve:tier2",
                "expense:claims:approve:tier3",
            ]
        ):
            return RedirectResponse(
                "/expense/claims/list?error=permission", status_code=302
            )

        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        actor_id = coerce_uuid(auth.person_id) if auth.person_id else None
        approver = db.scalars(
            select(Employee)
            .where(Employee.organization_id == org_id)
            .where(Employee.person_id == auth.person_id)
        ).first()
        approver_id = approver.employee_id if approver else None
        svc = ExpenseService(db)

        try:
            claim = svc.reject_claim(
                org_id,
                claim_uuid,
                approver_id=approver_id,
                reason=(reason or "").strip() or "Rejected",
                actor_id=actor_id,
            )
            if claim.status != ExpenseClaimStatus.REJECTED:
                db.rollback()
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=reject_in_progress",
                    status_code=303,
                )
            db.flush()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except ValueError as exc:
            db.rollback()
            message = str(exc).strip() or "Rejection could not be completed."
            if message == "Approver is not assigned to the current approval step":
                message = (
                    "You cannot reject this claim yet because it is assigned "
                    "to a different approval step."
                )
            elif message == "Claim has no pending approval steps":
                message = "This claim has no pending approval step."
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error_message={quote(message)}",
                status_code=303,
            )
        except Exception:
            db.rollback()
            logging.getLogger(__name__).exception(
                "Expense claim rejection failed", extra={"claim_id": claim_id}
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=reject_failed", status_code=303
            )

        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=rejected", status_code=303
        )

    @staticmethod
    def cancel_claim_response(
        claim_id: str, reason: str | None, auth, db
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        actor_id = coerce_uuid(auth.person_id) if auth.person_id else None
        svc = ExpenseService(db)
        try:
            claim = svc.cancel_claim(
                org_id,
                coerce_uuid(claim_id),
                reason=(reason or "").strip() or None,
                actor_id=actor_id,
            )
            if claim.status != ExpenseClaimStatus.CANCELLED:
                db.rollback()
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=cancel_in_progress",
                    status_code=303,
                )
            db.flush()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Expense claim cancellation failed", extra={"claim_id": claim_id}
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=cancel_failed", status_code=303
            )
        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=cancelled", status_code=303
        )

    @staticmethod
    def resubmit_claim_response(claim_id: str, auth, db) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        actor_id = coerce_uuid(auth.person_id) if auth.person_id else None
        svc = ExpenseService(db)
        try:
            claim = svc.resubmit_claim(org_id, coerce_uuid(claim_id), actor_id=actor_id)
            if claim.status != ExpenseClaimStatus.DRAFT:
                db.rollback()
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=resubmit_failed", status_code=303
                )
            db.flush()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Expense claim resubmission failed", extra={"claim_id": claim_id}
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=resubmit_failed", status_code=303
            )
        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=resubmitted", status_code=303
        )

    @staticmethod
    def delete_claim_response(claim_id: str, auth, db) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        try:
            svc.delete_claim(org_id, coerce_uuid(claim_id))
            db.flush()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Expense claim delete failed", extra={"claim_id": claim_id}
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=delete_failed", status_code=303
            )
        return RedirectResponse("/expense/claims/list?saved=1", status_code=303)
