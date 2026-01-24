"""
Payroll service - structures, assignments, and payroll entries.

Builds payroll runs and generates salary slips using SalarySlipService.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.payroll.payroll_entry import PayrollEntry, PayrollEntryStatus
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.models.people.payroll.salary_component import SalaryComponent
from app.models.people.payroll.salary_structure import (
    PayrollFrequency,
    SalaryStructure,
    SalaryStructureDeduction,
    SalaryStructureEarning,
)
from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus
from app.services.common import PaginatedResult, PaginationParams
from app.services.people.integrations.payroll_gl_adapter import PayrollGLAdapter
from app.services.people.payroll.salary_slip_service import (
    SalarySlipInput,
    salary_slip_service,
)

__all__ = ["PayrollService", "PayrollServiceError"]


class PayrollServiceError(Exception):
    """Base error for payroll service."""

    pass


class PayrollEntryNotFoundError(PayrollServiceError):
    """Payroll entry not found."""

    def __init__(self, entry_id: UUID):
        self.entry_id = entry_id
        super().__init__(f"Payroll entry {entry_id} not found")


class SalaryStructureNotFoundError(PayrollServiceError):
    """Salary structure not found."""

    def __init__(self, structure_id: UUID):
        self.structure_id = structure_id
        super().__init__(f"Salary structure {structure_id} not found")


class SalaryAssignmentNotFoundError(PayrollServiceError):
    """Salary structure assignment not found."""

    def __init__(self, assignment_id: UUID):
        self.assignment_id = assignment_id
        super().__init__(f"Salary assignment {assignment_id} not found")


class PayrollService:
    """Service for payroll structures, assignments, and payroll entries."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # =========================================================================
    # Salary Structures
    # =========================================================================

    def list_salary_structures(
        self,
        org_id: UUID,
        *,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[SalaryStructure]:
        query = select(SalaryStructure).where(SalaryStructure.organization_id == org_id)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    SalaryStructure.structure_code.ilike(search_term),
                    SalaryStructure.structure_name.ilike(search_term),
                )
            )

        if is_active is not None:
            query = query.where(SalaryStructure.is_active == is_active)

        query = query.order_by(SalaryStructure.structure_name.asc())

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())
        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_salary_structure(self, org_id: UUID, structure_id: UUID) -> SalaryStructure:
        structure = self.db.scalar(
            select(SalaryStructure).where(
                SalaryStructure.organization_id == org_id,
                SalaryStructure.structure_id == structure_id,
            )
        )
        if not structure:
            raise SalaryStructureNotFoundError(structure_id)
        return structure

    def create_salary_structure(
        self,
        org_id: UUID,
        *,
        structure_code: str,
        structure_name: str,
        description: Optional[str] = None,
        payroll_frequency: PayrollFrequency = PayrollFrequency.MONTHLY,
        currency_code: str = "NGN",
        earnings: Optional[list[dict]] = None,
        deductions: Optional[list[dict]] = None,
    ) -> SalaryStructure:
        structure = SalaryStructure(
            organization_id=org_id,
            structure_code=structure_code,
            structure_name=structure_name,
            description=description,
            payroll_frequency=payroll_frequency,
            currency_code=currency_code,
        )
        self.db.add(structure)
        self.db.flush()

        self._replace_structure_lines(structure, earnings, deductions)
        self.db.flush()
        return structure

    def update_salary_structure(
        self,
        org_id: UUID,
        structure_id: UUID,
        *,
        earnings: Optional[list[dict]] = None,
        deductions: Optional[list[dict]] = None,
        **kwargs,
    ) -> SalaryStructure:
        structure = self.get_salary_structure(org_id, structure_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(structure, key):
                setattr(structure, key, value)

        if earnings is not None or deductions is not None:
            self._replace_structure_lines(structure, earnings, deductions)

        self.db.flush()
        return structure

    def delete_salary_structure(self, org_id: UUID, structure_id: UUID) -> None:
        structure = self.get_salary_structure(org_id, structure_id)
        structure.is_active = False
        self.db.flush()

    def _replace_structure_lines(
        self,
        structure: SalaryStructure,
        earnings: Optional[list[dict]],
        deductions: Optional[list[dict]],
    ) -> None:
        if earnings is not None:
            structure.earnings.clear()
            for line in earnings:
                component = self.db.get(SalaryComponent, line["component_id"])
                if not component:
                    raise PayrollServiceError("Salary component not found")
                structure.earnings.append(
                    SalaryStructureEarning(
                        component_id=line["component_id"],
                        amount=line.get("amount", Decimal("0")),
                        amount_based_on_formula=line.get("amount_based_on_formula", False),
                        formula=line.get("formula"),
                        condition=line.get("condition"),
                        display_order=line.get("display_order", 0),
                    )
                )

        if deductions is not None:
            structure.deductions.clear()
            for line in deductions:
                component = self.db.get(SalaryComponent, line["component_id"])
                if not component:
                    raise PayrollServiceError("Salary component not found")
                structure.deductions.append(
                    SalaryStructureDeduction(
                        component_id=line["component_id"],
                        amount=line.get("amount", Decimal("0")),
                        amount_based_on_formula=line.get("amount_based_on_formula", False),
                        formula=line.get("formula"),
                        condition=line.get("condition"),
                        display_order=line.get("display_order", 0),
                    )
                )

    # =========================================================================
    # Salary Structure Assignments
    # =========================================================================

    def list_assignments(
        self,
        org_id: UUID,
        *,
        employee_id: Optional[UUID] = None,
        structure_id: Optional[UUID] = None,
        active_on: Optional[date] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[SalaryStructureAssignment]:
        query = select(SalaryStructureAssignment).where(
            SalaryStructureAssignment.organization_id == org_id
        )

        if employee_id:
            query = query.where(SalaryStructureAssignment.employee_id == employee_id)

        if structure_id:
            query = query.where(SalaryStructureAssignment.structure_id == structure_id)

        if active_on:
            query = query.where(
                SalaryStructureAssignment.from_date <= active_on,
                or_(
                    SalaryStructureAssignment.to_date.is_(None),
                    SalaryStructureAssignment.to_date >= active_on,
                ),
            )

        query = query.order_by(SalaryStructureAssignment.from_date.desc())

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())
        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_assignment(self, org_id: UUID, assignment_id: UUID) -> SalaryStructureAssignment:
        assignment = self.db.scalar(
            select(SalaryStructureAssignment).where(
                SalaryStructureAssignment.organization_id == org_id,
                SalaryStructureAssignment.assignment_id == assignment_id,
            )
        )
        if not assignment:
            raise SalaryAssignmentNotFoundError(assignment_id)
        return assignment

    def create_assignment(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        structure_id: UUID,
        from_date: date,
        to_date: Optional[date] = None,
        base: Decimal = Decimal("0"),
        variable: Decimal = Decimal("0"),
        income_tax_slab: Optional[str] = None,
    ) -> SalaryStructureAssignment:
        assignment = SalaryStructureAssignment(
            organization_id=org_id,
            employee_id=employee_id,
            structure_id=structure_id,
            from_date=from_date,
            to_date=to_date,
            base=base,
            variable=variable,
            income_tax_slab=income_tax_slab,
        )
        self.db.add(assignment)
        self.db.flush()
        return assignment

    def update_assignment(
        self,
        org_id: UUID,
        assignment_id: UUID,
        **kwargs,
    ) -> SalaryStructureAssignment:
        assignment = self.get_assignment(org_id, assignment_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(assignment, key):
                setattr(assignment, key, value)

        self.db.flush()
        return assignment

    def delete_assignment(self, org_id: UUID, assignment_id: UUID) -> None:
        assignment = self.get_assignment(org_id, assignment_id)
        self.db.delete(assignment)
        self.db.flush()

    # =========================================================================
    # Payroll Entries
    # =========================================================================

    def list_payroll_entries(
        self,
        org_id: UUID,
        *,
        status: Optional[PayrollEntryStatus] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        payroll_frequency: Optional[PayrollFrequency] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[PayrollEntry]:
        query = select(PayrollEntry).where(PayrollEntry.organization_id == org_id)

        if status:
            query = query.where(PayrollEntry.status == status)

        if from_date:
            query = query.where(PayrollEntry.start_date >= from_date)

        if to_date:
            query = query.where(PayrollEntry.end_date <= to_date)

        if payroll_frequency:
            query = query.where(PayrollEntry.payroll_frequency == payroll_frequency)

        query = query.order_by(PayrollEntry.start_date.desc())

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())
        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_payroll_entry(self, org_id: UUID, entry_id: UUID) -> PayrollEntry:
        entry = self.db.scalar(
            select(PayrollEntry).where(
                PayrollEntry.organization_id == org_id,
                PayrollEntry.entry_id == entry_id,
            )
        )
        if not entry:
            raise PayrollEntryNotFoundError(entry_id)
        return entry

    def create_payroll_entry(
        self,
        org_id: UUID,
        *,
        posting_date: date,
        start_date: date,
        end_date: date,
        payroll_frequency: PayrollFrequency = PayrollFrequency.MONTHLY,
        currency_code: str = "NGN",
        department_id: Optional[UUID] = None,
        designation_id: Optional[UUID] = None,
        notes: Optional[str] = None,
    ) -> PayrollEntry:
        count = (
            self.db.scalar(
                select(func.count(PayrollEntry.entry_id)).where(
                    PayrollEntry.organization_id == org_id
                )
            )
            or 0
        )
        entry_number = f"PAY-{posting_date.year}-{count + 1:04d}"

        entry = PayrollEntry(
            organization_id=org_id,
            entry_number=entry_number,
            posting_date=posting_date,
            start_date=start_date,
            end_date=end_date,
            payroll_frequency=payroll_frequency,
            currency_code=currency_code,
            department_id=department_id,
            designation_id=designation_id,
            notes=notes,
            status=PayrollEntryStatus.DRAFT,
        )
        self.db.add(entry)
        self.db.flush()
        return entry

    def update_payroll_entry(
        self,
        org_id: UUID,
        entry_id: UUID,
        **kwargs,
    ) -> PayrollEntry:
        entry = self.get_payroll_entry(org_id, entry_id)
        if entry.salary_slips_created:
            raise PayrollServiceError("Cannot update payroll entry after slips are created")

        for key, value in kwargs.items():
            if value is not None and hasattr(entry, key):
                setattr(entry, key, value)

        self.db.flush()
        return entry

    def delete_payroll_entry(self, org_id: UUID, entry_id: UUID) -> None:
        entry = self.get_payroll_entry(org_id, entry_id)
        if entry.salary_slips_created:
            raise PayrollServiceError("Cannot delete payroll entry after slips are created")
        self.db.delete(entry)
        self.db.flush()

    def generate_salary_slips(
        self,
        org_id: UUID,
        entry_id: UUID,
        *,
        created_by_id: UUID,
    ) -> dict:
        entry = self.get_payroll_entry(org_id, entry_id)
        if entry.salary_slips_created:
            raise PayrollServiceError("Salary slips already created for this entry")

        assignments = self._get_entry_assignments(org_id, entry)
        created = 0
        skipped = 0
        errors: list[dict] = []

        for assignment in assignments:
            try:
                slip = salary_slip_service.create_salary_slip(
                    db=self.db,
                    organization_id=org_id,
                    input=SalarySlipInput(
                        employee_id=assignment.employee_id,
                        start_date=entry.start_date,
                        end_date=entry.end_date,
                        posting_date=entry.posting_date,
                    ),
                    created_by_user_id=created_by_id,
                )
                slip.payroll_entry_id = entry.entry_id
                created += 1
            except Exception as exc:
                skipped += 1
                errors.append(
                    {"employee_id": str(assignment.employee_id), "reason": str(exc)}
                )

        self._update_entry_totals(entry)
        entry.salary_slips_created = True
        entry.status = PayrollEntryStatus.SLIPS_CREATED
        self.db.flush()
        return {"created_count": created, "skipped_count": skipped, "errors": errors}

    def regenerate_salary_slips(
        self,
        org_id: UUID,
        entry_id: UUID,
        *,
        created_by_id: UUID,
    ) -> dict:
        entry = self.get_payroll_entry(org_id, entry_id)
        slips = list(entry.salary_slips or [])
        if any(slip.status != SalarySlipStatus.DRAFT for slip in slips):
            raise PayrollServiceError("Only draft slips can be regenerated")

        for slip in slips:
            self.db.delete(slip)

        entry.salary_slips_created = False
        entry.status = PayrollEntryStatus.DRAFT
        self.db.flush()

        return self.generate_salary_slips(org_id, entry_id, created_by_id=created_by_id)

    def payout_payroll_entry(
        self,
        org_id: UUID,
        entry_id: UUID,
        *,
        paid_by_id: UUID,
        slip_ids: Optional[list[UUID]] = None,
        payment_reference: Optional[str] = None,
    ) -> dict:
        entry = self.get_payroll_entry(org_id, entry_id)
        slips = list(entry.salary_slips or [])
        if slip_ids:
            slips = [s for s in slips if s.slip_id in slip_ids]

        updated = 0
        errors: list[dict] = []

        for slip in slips:
            if slip.status != SalarySlipStatus.APPROVED:
                errors.append(
                    {"slip_id": str(slip.slip_id), "reason": "Slip not approved"}
                )
                continue
            slip.status = SalarySlipStatus.PAID
            slip.paid_at = func.now()
            slip.paid_by_id = paid_by_id
            slip.payment_reference = payment_reference
            updated += 1

        self.db.flush()
        return {"updated": updated, "requested": len(slips), "errors": errors}

    def handoff_payroll_to_books(
        self,
        org_id: UUID,
        entry_id: UUID,
        *,
        posting_date: date,
        user_id: UUID,
    ) -> dict:
        result = PayrollGLAdapter.post_payroll_run(
            self.db,
            org_id=org_id,
            payroll_entry_id=entry_id,
            posting_date=posting_date,
            user_id=user_id,
            consolidated=False,
        )
        return {"success": result.success, "error": result.error_message}

    def _get_entry_assignments(
        self, org_id: UUID, entry: PayrollEntry
    ) -> list[SalaryStructureAssignment]:
        query = (
            self.db.query(SalaryStructureAssignment)
            .join(Employee, SalaryStructureAssignment.employee_id == Employee.employee_id)
            .filter(SalaryStructureAssignment.organization_id == org_id)
            .filter(SalaryStructureAssignment.from_date <= entry.start_date)
            .filter(
                or_(
                    SalaryStructureAssignment.to_date.is_(None),
                    SalaryStructureAssignment.to_date >= entry.start_date,
                )
            )
            .filter(Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]))
        )
        if entry.department_id:
            query = query.filter(Employee.department_id == entry.department_id)
        if entry.designation_id:
            query = query.filter(Employee.designation_id == entry.designation_id)
        return list(query.all())

    def _update_entry_totals(self, entry: PayrollEntry) -> None:
        slips = list(
            self.db.scalars(
                select(SalarySlip).where(
                    SalarySlip.payroll_entry_id == entry.entry_id
                )
            ).all()
        )
        entry.employee_count = len(slips)
        entry.total_gross_pay = sum((s.gross_pay or Decimal("0")) for s in slips)
        entry.total_deductions = sum((s.total_deduction or Decimal("0")) for s in slips)
        entry.total_net_pay = sum((s.net_pay or Decimal("0")) for s in slips)

    # =========================================================================
    # Reports
    # =========================================================================

    def get_payroll_summary_report(
        self,
        org_id: UUID,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """
        Get payroll summary report by period.

        Returns total gross, deductions, net pay, and breakdown by status.
        """
        from app.models.people.hr import Department

        today = date.today()
        if not start_date:
            start_date = today.replace(month=1, day=1)  # Year to date
        if not end_date:
            end_date = today

        # Aggregate by status
        status_query = (
            self.db.query(
                PayrollEntry.status,
                func.count(PayrollEntry.entry_id).label("run_count"),
                func.sum(PayrollEntry.employee_count).label("employee_count"),
                func.sum(PayrollEntry.total_gross_pay).label("total_gross"),
                func.sum(PayrollEntry.total_deductions).label("total_deductions"),
                func.sum(PayrollEntry.total_net_pay).label("total_net"),
            )
            .filter(
                PayrollEntry.organization_id == org_id,
                PayrollEntry.start_date >= start_date,
                PayrollEntry.end_date <= end_date,
            )
            .group_by(PayrollEntry.status)
        )

        status_results = status_query.all()
        status_breakdown = []
        total_runs = 0
        total_employees = 0
        total_gross = Decimal("0")
        total_deductions = Decimal("0")
        total_net = Decimal("0")

        for row in status_results:
            run_count = row.run_count or 0
            emp_count = row.employee_count or 0
            gross = row.total_gross or Decimal("0")
            deductions = row.total_deductions or Decimal("0")
            net = row.total_net or Decimal("0")

            status_breakdown.append({
                "status": row.status.value if row.status else "Unknown",
                "run_count": run_count,
                "employee_count": emp_count,
                "total_gross": gross,
                "total_deductions": deductions,
                "total_net": net,
            })

            total_runs += run_count
            total_employees += emp_count
            total_gross += gross
            total_deductions += deductions
            total_net += net

        return {
            "start_date": start_date,
            "end_date": end_date,
            "total_runs": total_runs,
            "total_employees": total_employees,
            "total_gross": total_gross,
            "total_deductions": total_deductions,
            "total_net": total_net,
            "status_breakdown": status_breakdown,
        }

    def get_payroll_by_department_report(
        self,
        org_id: UUID,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """
        Get payroll breakdown by department.

        Returns payroll costs by department with employee counts.
        """
        from app.models.people.hr import Department, Employee

        today = date.today()
        if not start_date:
            start_date = today.replace(day=1)  # Current month
        if not end_date:
            end_date = today

        # Get salary slips grouped by department
        results = (
            self.db.query(
                Department.department_id,
                Department.department_name.label("department_name"),
                func.count(SalarySlip.slip_id).label("slip_count"),
                func.sum(SalarySlip.gross_pay).label("total_gross"),
                func.sum(SalarySlip.total_deduction).label("total_deductions"),
                func.sum(SalarySlip.net_pay).label("total_net"),
            )
            .select_from(SalarySlip)
            .join(Employee, SalarySlip.employee_id == Employee.employee_id)
            .outerjoin(Department, Employee.department_id == Department.department_id)
            .filter(
                SalarySlip.organization_id == org_id,
                SalarySlip.start_date >= start_date,
                SalarySlip.end_date <= end_date,
            )
            .group_by(Department.department_id, Department.department_name)
            .order_by(func.sum(SalarySlip.net_pay).desc())
            .all()
        )

        departments = []
        total_gross = Decimal("0")
        total_deductions = Decimal("0")
        total_net = Decimal("0")

        for row in results:
            gross = row.total_gross or Decimal("0")
            deductions = row.total_deductions or Decimal("0")
            net = row.total_net or Decimal("0")

            departments.append({
                "department_id": str(row.department_id) if row.department_id else None,
                "department_name": row.department_name or "No Department",
                "slip_count": row.slip_count or 0,
                "total_gross": gross,
                "total_deductions": deductions,
                "total_net": net,
            })

            total_gross += gross
            total_deductions += deductions
            total_net += net

        # Calculate percentages
        for dept in departments:
            dept["percentage"] = round(float(dept["total_net"]) / float(total_net) * 100, 1) if total_net > 0 else 0

        return {
            "start_date": start_date,
            "end_date": end_date,
            "departments": departments,
            "total_departments": len(departments),
            "total_gross": total_gross,
            "total_deductions": total_deductions,
            "total_net": total_net,
        }

    def get_payroll_tax_summary_report(
        self,
        org_id: UUID,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """
        Get payroll tax deduction summary.

        Returns breakdown of statutory deductions (tax, pension, etc.).
        """
        from app.models.people.payroll.salary_slip import SalarySlipDeduction

        today = date.today()
        if not start_date:
            start_date = today.replace(day=1)
        if not end_date:
            end_date = today

        # Get deductions grouped by component
        results = (
            self.db.query(
                SalaryComponent.component_id,
                SalaryComponent.component_name,
                SalaryComponent.component_code,
                SalaryComponent.is_statutory,
                func.count(SalarySlipDeduction.line_id).label("deduction_count"),
                func.sum(SalarySlipDeduction.amount).label("total_amount"),
            )
            .select_from(SalarySlipDeduction)
            .join(SalarySlip, SalarySlipDeduction.slip_id == SalarySlip.slip_id)
            .join(SalaryComponent, SalarySlipDeduction.component_id == SalaryComponent.component_id)
            .filter(
                SalarySlip.organization_id == org_id,
                SalarySlip.start_date >= start_date,
                SalarySlip.end_date <= end_date,
            )
            .group_by(
                SalaryComponent.component_id,
                SalaryComponent.component_name,
                SalaryComponent.component_code,
                SalaryComponent.is_statutory,
            )
            .order_by(func.sum(SalarySlipDeduction.amount).desc())
            .all()
        )

        deductions = []
        statutory_total = Decimal("0")
        non_statutory_total = Decimal("0")

        for row in results:
            amount = row.total_amount or Decimal("0")
            deductions.append({
                "component_id": str(row.component_id),
                "component_name": row.component_name,
                "component_code": row.component_code,
                "is_statutory": row.is_statutory,
                "deduction_count": row.deduction_count or 0,
                "total_amount": amount,
            })

            if row.is_statutory:
                statutory_total += amount
            else:
                non_statutory_total += amount

        total_deductions = statutory_total + non_statutory_total

        # Calculate percentages
        for d in deductions:
            d["percentage"] = round(float(d["total_amount"]) / float(total_deductions) * 100, 1) if total_deductions > 0 else 0

        return {
            "start_date": start_date,
            "end_date": end_date,
            "deductions": deductions,
            "statutory_total": statutory_total,
            "non_statutory_total": non_statutory_total,
            "total_deductions": total_deductions,
        }

    def get_payroll_trends_report(
        self,
        org_id: UUID,
        *,
        months: int = 12,
    ) -> dict:
        """
        Get payroll trends over time.

        Returns monthly breakdown of payroll costs.
        """
        from dateutil.relativedelta import relativedelta

        today = date.today()
        end_date = today.replace(day=1)
        start_date = end_date - relativedelta(months=months - 1)

        # Query monthly aggregates
        results = (
            self.db.query(
                func.date_trunc("month", SalarySlip.start_date).label("month"),
                func.count(SalarySlip.slip_id).label("slip_count"),
                func.sum(SalarySlip.gross_pay).label("total_gross"),
                func.sum(SalarySlip.total_deduction).label("total_deductions"),
                func.sum(SalarySlip.net_pay).label("total_net"),
            )
            .filter(
                SalarySlip.organization_id == org_id,
                SalarySlip.start_date >= start_date,
                SalarySlip.start_date <= today,
            )
            .group_by(func.date_trunc("month", SalarySlip.start_date))
            .order_by(func.date_trunc("month", SalarySlip.start_date))
            .all()
        )

        # Build results dict by month
        monthly_data = {}
        for row in results:
            month_key = row.month.strftime("%Y-%m")
            monthly_data[month_key] = {
                "month": month_key,
                "month_label": row.month.strftime("%b %Y"),
                "slip_count": row.slip_count or 0,
                "total_gross": row.total_gross or Decimal("0"),
                "total_deductions": row.total_deductions or Decimal("0"),
                "total_net": row.total_net or Decimal("0"),
            }

        # Fill in missing months with zeros
        months_list = []
        current = start_date
        total_gross = Decimal("0")
        total_net = Decimal("0")

        while current <= today:
            month_key = current.strftime("%Y-%m")
            if month_key in monthly_data:
                months_list.append(monthly_data[month_key])
                total_gross += monthly_data[month_key]["total_gross"]
                total_net += monthly_data[month_key]["total_net"]
            else:
                months_list.append({
                    "month": month_key,
                    "month_label": current.strftime("%b %Y"),
                    "slip_count": 0,
                    "total_gross": Decimal("0"),
                    "total_deductions": Decimal("0"),
                    "total_net": Decimal("0"),
                })
            current = current + relativedelta(months=1)

        num_months = len(months_list)
        average_monthly = total_net / num_months if num_months > 0 else Decimal("0")

        return {
            "months": months_list,
            "total_months": num_months,
            "total_gross": total_gross,
            "total_net": total_net,
            "average_monthly": average_monthly,
        }
