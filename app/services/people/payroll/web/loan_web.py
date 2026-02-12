"""
Loan Web Service - Web layer for employee loan management.

Provides HTML response methods for loan-related pages.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee
from app.models.people.payroll.employee_loan import EmployeeLoan, LoanStatus
from app.models.people.payroll.loan_type import InterestMethod, LoanCategory, LoanType
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.people.payroll.loan_service import LoanApplicationInput, LoanService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class LoanWebService:
    """Web service for loan management pages."""

    # =========================================================================
    # Loan Types
    # =========================================================================

    def list_loan_types_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
    ) -> HTMLResponse:
        """Render loan types list page."""
        org_id = coerce_uuid(auth.organization_id)

        stmt = (
            select(LoanType)
            .where(LoanType.organization_id == org_id)
            .order_by(LoanType.type_name)
        )

        if search:
            search_term = f"%{search}%"
            stmt = stmt.where(
                (LoanType.type_name.ilike(search_term))
                | (LoanType.type_code.ilike(search_term))
            )

        loan_types = list(db.scalars(stmt).all())

        context = base_context(request, auth, "Loan Types", "payroll", db=db)
        context.update(
            {
                "loan_types": loan_types,
                "search": search or "",
                "categories": [c.value for c in LoanCategory],
            }
        )

        return templates.TemplateResponse(
            request, "people/payroll/loan_types.html", context
        )

    def loan_type_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        loan_type_id: str | None = None,
    ) -> HTMLResponse:
        """Render loan type create/edit form."""
        org_id = coerce_uuid(auth.organization_id)

        loan_type = None
        if loan_type_id:
            lt_id = coerce_uuid(loan_type_id)
            loan_type = db.get(LoanType, lt_id)
            if not loan_type or loan_type.organization_id != org_id:
                raise HTTPException(status_code=404, detail="Loan type not found")

        context = base_context(
            request,
            auth,
            "Edit Loan Type" if loan_type else "New Loan Type",
            "payroll",
            db=db,
        )
        context.update(
            {
                "loan_type": loan_type,
                "categories": [
                    (c.value, c.value.replace("_", " ").title()) for c in LoanCategory
                ],
                "interest_methods": [
                    (m.value, m.value.replace("_", " ").title()) for m in InterestMethod
                ],
            }
        )

        return templates.TemplateResponse(
            request, "people/payroll/loan_type_form.html", context
        )

    async def save_loan_type_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        loan_type_id: str | None = None,
    ) -> RedirectResponse:
        """Save loan type (create or update)."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)

        form = await request.form()

        # Parse form data
        type_code = str(form.get("type_code", "")).strip().upper()
        type_name = str(form.get("type_name", "")).strip()
        category = str(form.get("category", LoanCategory.PERSONAL_LOAN.value))
        description = str(form.get("description", "")).strip() or None

        try:
            min_amount = Decimal(str(form.get("min_amount", "0")))
            max_amount_str = str(form.get("max_amount", "")).strip()
            max_amount = Decimal(max_amount_str) if max_amount_str else None
            min_tenure = int(str(form.get("min_tenure_months", 1)))
            max_tenure = int(str(form.get("max_tenure_months", 12)))
            min_service = int(str(form.get("min_service_months", 0)))
            interest_rate = Decimal(str(form.get("default_interest_rate", "0")))
        except (InvalidOperation, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid number: {e}")

        interest_method = str(form.get("interest_method", InterestMethod.NONE.value))
        requires_approval = form.get("requires_approval") == "on"
        is_active = form.get("is_active") == "on"

        if loan_type_id:
            # Update existing
            lt_id = coerce_uuid(loan_type_id)
            loan_type = db.get(LoanType, lt_id)
            if not loan_type or loan_type.organization_id != org_id:
                raise HTTPException(status_code=404, detail="Loan type not found")
        else:
            # Create new
            loan_type = LoanType(
                organization_id=org_id,
                created_by_id=person_id,
            )
            db.add(loan_type)

        # Update fields
        loan_type.type_code = type_code
        loan_type.type_name = type_name
        loan_type.category = LoanCategory(category)
        loan_type.description = description
        loan_type.min_amount = min_amount
        loan_type.max_amount = max_amount
        loan_type.min_tenure_months = min_tenure
        loan_type.max_tenure_months = max_tenure
        loan_type.min_service_months = min_service
        loan_type.interest_method = InterestMethod(interest_method)
        loan_type.default_interest_rate = interest_rate
        loan_type.requires_approval = requires_approval
        loan_type.is_active = is_active

        db.commit()

        return RedirectResponse(
            url="/people/payroll/loans/types?success=Record+saved+successfully",
            status_code=303,
        )

    # =========================================================================
    # Loans
    # =========================================================================

    def list_loans_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        status: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render loans list page."""
        org_id = coerce_uuid(auth.organization_id)
        per_page = 20

        stmt = (
            select(EmployeeLoan)
            .options(
                joinedload(EmployeeLoan.employee),
                joinedload(EmployeeLoan.loan_type),
            )
            .where(EmployeeLoan.organization_id == org_id)
            .order_by(EmployeeLoan.created_at.desc())
        )

        if search:
            search_term = f"%{search}%"
            stmt = stmt.join(EmployeeLoan.employee).join(Employee.person)
            stmt = stmt.where(
                (EmployeeLoan.loan_number.ilike(search_term))
                | (Person.display_name.ilike(search_term))
                | (Person.first_name.ilike(search_term))
                | (Person.last_name.ilike(search_term))
            )

        if status:
            try:
                status_val = LoanStatus(status)
                stmt = stmt.where(EmployeeLoan.status == status_val)
            except ValueError:
                pass

        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.scalar(count_stmt) or 0

        # Paginate
        offset = (page - 1) * per_page
        loans = list(db.scalars(stmt.offset(offset).limit(per_page)).unique().all())

        total_pages = (total + per_page - 1) // per_page if total else 1

        context = base_context(request, auth, "Employee Loans", "payroll", db=db)
        context.update(
            {
                "loans": loans,
                "search": search or "",
                "status": status or "",
                "statuses": [
                    (s.value, s.value.replace("_", " ").title()) for s in LoanStatus
                ],
                "page": page,
                "total_pages": total_pages,
                "total_count": total,
                "total": total,
                "limit": per_page,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            }
        )

        return templates.TemplateResponse(request, "people/payroll/loans.html", context)

    def loan_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        loan_id: str,
    ) -> HTMLResponse:
        """Render loan detail page."""
        org_id = coerce_uuid(auth.organization_id)
        l_id = coerce_uuid(loan_id)

        loan = db.scalar(
            select(EmployeeLoan)
            .options(
                joinedload(EmployeeLoan.employee),
                joinedload(EmployeeLoan.loan_type),
                joinedload(EmployeeLoan.repayments),
            )
            .where(
                EmployeeLoan.loan_id == l_id,
                EmployeeLoan.organization_id == org_id,
            )
        )

        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found")

        # Calculate progress
        progress_pct = 0.0
        if loan.total_repayable > 0:
            paid = loan.principal_paid + loan.interest_paid
            progress_pct = float(paid / loan.total_repayable * 100)

        context = base_context(
            request, auth, f"Loan {loan.loan_number}", "payroll", db=db
        )
        context.update(
            {
                "loan": loan,
                "progress_pct": progress_pct,
                "can_approve": loan.status == LoanStatus.PENDING,
                "can_disburse": loan.status == LoanStatus.APPROVED,
                "can_cancel": loan.status in [LoanStatus.DRAFT, LoanStatus.PENDING],
            }
        )

        return templates.TemplateResponse(
            request, "people/payroll/loan_detail.html", context
        )

    def loan_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str | None = None,
    ) -> HTMLResponse:
        """Render new loan application form."""
        org_id = coerce_uuid(auth.organization_id)

        # Get active loan types
        loan_types = list(
            db.scalars(
                select(LoanType)
                .where(LoanType.organization_id == org_id, LoanType.is_active == True)
                .order_by(LoanType.type_name)
            ).all()
        )

        # Get active employees
        employees = list(
            db.scalars(
                select(Employee)
                .join(Person, Employee.person_id == Person.id)
                .where(Employee.organization_id == org_id, Employee.status == "ACTIVE")
                .order_by(Person.display_name, Person.last_name, Person.first_name)
            ).all()
        )

        selected_employee = None
        if employee_id:
            emp_id = coerce_uuid(employee_id)
            selected_employee = db.get(Employee, emp_id)

        context = base_context(request, auth, "New Loan Application", "payroll", db=db)
        context.update(
            {
                "loan_types": loan_types,
                "employees": employees,
                "selected_employee": selected_employee,
            }
        )

        return templates.TemplateResponse(
            request, "people/payroll/loan_form.html", context
        )

    async def create_loan_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Create new loan application."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)

        form = await request.form()

        employee_id = coerce_uuid(str(form.get("employee_id", "")))
        loan_type_id = coerce_uuid(str(form.get("loan_type_id", "")))

        try:
            principal_amount = Decimal(str(form.get("principal_amount", "0")))
            tenure_months = int(str(form.get("tenure_months", 1)))
        except (InvalidOperation, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid number: {e}")

        purpose = str(form.get("purpose", "")).strip() or None

        # Parse first repayment date if provided
        first_repayment_str = str(form.get("first_repayment_date", "")).strip()
        first_repayment_date = None
        if first_repayment_str:
            try:
                first_repayment_date = date.fromisoformat(first_repayment_str)
            except ValueError:
                pass

        loan_service = LoanService(db)
        loan_input = LoanApplicationInput(
            employee_id=employee_id,
            loan_type_id=loan_type_id,
            principal_amount=principal_amount,
            tenure_months=tenure_months,
            purpose=purpose,
            first_repayment_date=first_repayment_date,
        )

        loan = loan_service.create_loan(org_id, loan_input, person_id)
        db.commit()

        return RedirectResponse(
            url=f"/people/payroll/loans/{loan.loan_id}?saved=1", status_code=303
        )

    def approve_loan_response(
        self,
        auth: WebAuthContext,
        db: Session,
        loan_id: str,
    ) -> RedirectResponse:
        """Approve a pending loan."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        l_id = coerce_uuid(loan_id)

        loan_service = LoanService(db)
        loan_service.approve_loan(org_id, l_id, person_id)
        db.commit()

        return RedirectResponse(
            url=f"/people/payroll/loans/{loan_id}?saved=1", status_code=303
        )

    async def reject_loan_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        loan_id: str,
    ) -> RedirectResponse:
        """Reject a pending loan."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        l_id = coerce_uuid(loan_id)

        form = await request.form()
        reason = str(form.get("rejection_reason", "")).strip() or "Rejected"

        loan_service = LoanService(db)
        loan_service.reject_loan(org_id, l_id, person_id, reason)
        db.commit()

        return RedirectResponse(
            url=f"/people/payroll/loans/{loan_id}?saved=1", status_code=303
        )

    async def disburse_loan_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        loan_id: str,
    ) -> RedirectResponse:
        """Mark loan as disbursed."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        l_id = coerce_uuid(loan_id)

        form = await request.form()
        reference = str(form.get("disbursement_reference", "")).strip() or None

        loan_service = LoanService(db)
        loan_service.disburse_loan(org_id, l_id, person_id, reference)
        db.commit()

        return RedirectResponse(
            url=f"/people/payroll/loans/{loan_id}?saved=1", status_code=303
        )


loan_web_service = LoanWebService()
