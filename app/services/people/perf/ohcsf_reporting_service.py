"""
OHCSF Reporting Service - Post-Appraisal Reports.

Implements the 11 mandatory post-appraisal reports from OHCSF PMS Guidelines
Section 5.11, plus a compliance dashboard covering contracts, monthly reviews,
appraisals, appeals, and PIPs.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.services.people.perf.performance_policy import GOVERNMENT_PMS_POLICY

logger = logging.getLogger(__name__)

# Canonical rating label order (highest → lowest)
_RATING_LABELS = [
    band.label
    for band in sorted(
        GOVERNMENT_PMS_POLICY.rating_scale,
        key=lambda band: band.rating,
        reverse=True,
    )
]


def _pct(count: int, total: int) -> Decimal:
    """Return percentage rounded to 2dp; zero-safe."""
    if total == 0:
        return Decimal("0.00")
    return (Decimal(count) / Decimal(total) * 100).quantize(Decimal("0.01"))


class OHCSFReportingService:
    """
    Service for generating OHCSF PMS post-appraisal reports.

    All report methods take ``org_id`` and ``cycle_id`` to ensure multi-tenant
    isolation.  Model imports are performed inside each method to prevent
    circular-import issues.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Report 1 — Rating Summary (org-wide count by rating label)
    # ------------------------------------------------------------------

    def rating_summary(self, org_id: UUID, cycle_id: UUID) -> dict:
        """
        Count completed appraisals grouped by rating_label.

        Returns::

            {
                "ratings": [{"label": str, "count": int, "percentage": Decimal}],
                "total": int,
            }
        """
        from app.models.people.perf.appraisal import Appraisal, AppraisalStatus

        stmt = (
            select(Appraisal.rating_label, func.count().label("cnt"))
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
            )
            .group_by(Appraisal.rating_label)
        )

        rows = self.db.execute(stmt).all()
        counts: dict[str, int] = {label: 0 for label in _RATING_LABELS}
        for label, cnt in rows:
            if label:
                # Normalise capitalisation so "OUTSTANDING" → "Outstanding"
                key = label.strip().title()
                if key in counts:
                    counts[key] = cnt
                else:
                    counts[key] = cnt

        total = sum(counts.values())

        ratings = [
            {
                "label": lbl,
                "count": counts.get(lbl, 0),
                "percentage": _pct(counts.get(lbl, 0), total),
            }
            for lbl in _RATING_LABELS
        ]

        logger.info("rating_summary: org=%s cycle=%s total=%d", org_id, cycle_id, total)
        return {"ratings": ratings, "total": total}

    # ------------------------------------------------------------------
    # Report 2 — Rating by Department
    # ------------------------------------------------------------------

    def rating_by_department(self, org_id: UUID, cycle_id: UUID) -> dict:
        """
        Average score and count grouped by department.

        Returns::

            {
                "departments": [
                    {
                        "department_name": str,
                        "count": int,
                        "avg_score": Decimal,
                        "avg_rating": Decimal,
                    }
                ],
                "total": int,
            }
        """
        from app.models.people.hr.department import Department
        from app.models.people.hr.employee import Employee
        from app.models.people.perf.appraisal import Appraisal, AppraisalStatus

        stmt = (
            select(
                Department.department_name,
                func.count(Appraisal.appraisal_id).label("cnt"),
                func.avg(Appraisal.final_score).label("avg_score"),
                func.avg(Appraisal.final_rating).label("avg_rating"),
            )
            .join(Employee, Employee.employee_id == Appraisal.employee_id)
            .join(Department, Department.department_id == Employee.department_id)
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
            )
            .group_by(Department.department_id, Department.department_name)
            .order_by(func.avg(Appraisal.final_score).desc().nulls_last())
        )

        rows = self.db.execute(stmt).all()
        departments = [
            {
                "department_name": dept_name,
                "count": cnt,
                "avg_score": (
                    Decimal(str(avg_score)).quantize(Decimal("0.01"))
                    if avg_score is not None
                    else Decimal("0.00")
                ),
                "avg_rating": (
                    Decimal(str(avg_rating)).quantize(Decimal("0.01"))
                    if avg_rating is not None
                    else Decimal("0.00")
                ),
            }
            for dept_name, cnt, avg_score, avg_rating in rows
        ]
        total = sum(d["count"] for d in departments)
        return {"departments": departments, "total": total}

    # ------------------------------------------------------------------
    # Report 3 — Rating by Grade Level
    # ------------------------------------------------------------------

    def rating_by_grade_level(self, org_id: UUID, cycle_id: UUID) -> dict:
        """
        Average score and count grouped by employee grade.

        Returns::

            {
                "grades": [{"grade_name": str, "count": int, "avg_score": Decimal}],
                "total": int,
            }
        """
        from app.models.people.hr.employee import Employee
        from app.models.people.hr.employee_grade import EmployeeGrade
        from app.models.people.perf.appraisal import Appraisal, AppraisalStatus

        stmt = (
            select(
                EmployeeGrade.grade_name,
                func.count(Appraisal.appraisal_id).label("cnt"),
                func.avg(Appraisal.final_score).label("avg_score"),
            )
            .join(Employee, Employee.employee_id == Appraisal.employee_id)
            .join(EmployeeGrade, EmployeeGrade.grade_id == Employee.grade_id)
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
            )
            .group_by(EmployeeGrade.grade_id, EmployeeGrade.grade_name)
            .order_by(func.avg(Appraisal.final_score).desc().nulls_last())
        )

        rows = self.db.execute(stmt).all()
        grades = [
            {
                "grade_name": grade_name,
                "count": cnt,
                "avg_score": (
                    Decimal(str(avg_score)).quantize(Decimal("0.01"))
                    if avg_score is not None
                    else Decimal("0.00")
                ),
            }
            for grade_name, cnt, avg_score in rows
        ]
        total = sum(g["count"] for g in grades)
        return {"grades": grades, "total": total}

    # ------------------------------------------------------------------
    # Report 4 — Distribution Org-Wide
    # ------------------------------------------------------------------

    def distribution_org_wide(self, org_id: UUID, cycle_id: UUID) -> dict:
        """
        Percentage distribution of ratings across the entire organisation.

        Returns::

            {
                "distribution": [{"label": str, "count": int, "percentage": Decimal}],
                "total": int,
            }
        """
        # Reuse rating_summary — the structure is identical
        summary = self.rating_summary(org_id, cycle_id)
        return {
            "distribution": summary["ratings"],
            "total": summary["total"],
        }

    # ------------------------------------------------------------------
    # Report 5 — Distribution by Department
    # ------------------------------------------------------------------

    def distribution_by_department(self, org_id: UUID, cycle_id: UUID) -> dict:
        """
        Rating label distribution per department.

        Returns::

            {
                "departments": [
                    {
                        "department_name": str,
                        "distribution": [{"label": str, "count": int, "percentage": Decimal}],
                        "total": int,
                    }
                ],
                "total": int,
            }
        """
        from app.models.people.hr.department import Department
        from app.models.people.hr.employee import Employee
        from app.models.people.perf.appraisal import Appraisal, AppraisalStatus

        stmt = (
            select(
                Department.department_name,
                Appraisal.rating_label,
                func.count(Appraisal.appraisal_id).label("cnt"),
            )
            .join(Employee, Employee.employee_id == Appraisal.employee_id)
            .join(Department, Department.department_id == Employee.department_id)
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
            )
            .group_by(
                Department.department_id,
                Department.department_name,
                Appraisal.rating_label,
            )
            .order_by(Department.department_name)
        )

        rows = self.db.execute(stmt).all()

        # Group into {dept_name: {label: count}}
        dept_data: dict[str, dict[str, int]] = {}
        for dept_name, label, cnt in rows:
            if dept_name not in dept_data:
                dept_data[dept_name] = {lbl: 0 for lbl in _RATING_LABELS}
            if label:
                key = label.strip().title()
                dept_data[dept_name][key] = dept_data[dept_name].get(key, 0) + cnt

        departments = []
        grand_total = 0
        for dept_name, label_counts in dept_data.items():
            dept_total = sum(label_counts.values())
            grand_total += dept_total
            departments.append(
                {
                    "department_name": dept_name,
                    "distribution": [
                        {
                            "label": lbl,
                            "count": label_counts.get(lbl, 0),
                            "percentage": _pct(label_counts.get(lbl, 0), dept_total),
                        }
                        for lbl in _RATING_LABELS
                    ],
                    "total": dept_total,
                }
            )

        return {"departments": departments, "total": grand_total}

    # ------------------------------------------------------------------
    # Report 6 — Distribution by Grade
    # ------------------------------------------------------------------

    def distribution_by_grade(self, org_id: UUID, cycle_id: UUID) -> dict:
        """
        Rating label distribution per employee grade level.

        Returns::

            {
                "grades": [
                    {
                        "grade_name": str,
                        "distribution": [{"label": str, "count": int, "percentage": Decimal}],
                        "total": int,
                    }
                ],
                "total": int,
            }
        """
        from app.models.people.hr.employee import Employee
        from app.models.people.hr.employee_grade import EmployeeGrade
        from app.models.people.perf.appraisal import Appraisal, AppraisalStatus

        stmt = (
            select(
                EmployeeGrade.grade_name,
                Appraisal.rating_label,
                func.count(Appraisal.appraisal_id).label("cnt"),
            )
            .join(Employee, Employee.employee_id == Appraisal.employee_id)
            .join(EmployeeGrade, EmployeeGrade.grade_id == Employee.grade_id)
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
            )
            .group_by(
                EmployeeGrade.grade_id,
                EmployeeGrade.grade_name,
                Appraisal.rating_label,
            )
            .order_by(EmployeeGrade.grade_name)
        )

        rows = self.db.execute(stmt).all()

        grade_data: dict[str, dict[str, int]] = {}
        for grade_name, label, cnt in rows:
            if grade_name not in grade_data:
                grade_data[grade_name] = {lbl: 0 for lbl in _RATING_LABELS}
            if label:
                key = label.strip().title()
                grade_data[grade_name][key] = grade_data[grade_name].get(key, 0) + cnt

        grades = []
        grand_total = 0
        for grade_name, label_counts in grade_data.items():
            grade_total = sum(label_counts.values())
            grand_total += grade_total
            grades.append(
                {
                    "grade_name": grade_name,
                    "distribution": [
                        {
                            "label": lbl,
                            "count": label_counts.get(lbl, 0),
                            "percentage": _pct(label_counts.get(lbl, 0), grade_total),
                        }
                        for lbl in _RATING_LABELS
                    ],
                    "total": grade_total,
                }
            )

        return {"grades": grades, "total": grand_total}

    # ------------------------------------------------------------------
    # Report 7 — Top Performers
    # ------------------------------------------------------------------

    def top_performers(
        self, org_id: UUID, cycle_id: UUID, *, n: int = 10
    ) -> list[dict]:
        """
        Top N employees by final_score.

        Returns a list of::

            {
                "employee_name": str,
                "department": str,
                "score": Decimal,
                "rating_label": str,
            }
        """
        from app.models.people.hr.department import Department
        from app.models.people.hr.employee import Employee
        from app.models.people.perf.appraisal import Appraisal, AppraisalStatus
        from app.models.person import Person

        stmt = (
            select(
                Person.display_name,
                Department.department_name,
                Appraisal.final_score,
                Appraisal.rating_label,
            )
            .join(Employee, Employee.employee_id == Appraisal.employee_id)
            .join(Person, Person.id == Employee.person_id)
            .outerjoin(Department, Department.department_id == Employee.department_id)
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
                Appraisal.final_score.is_not(None),
            )
            .order_by(Appraisal.final_score.desc())
            .limit(n)
        )

        # Use `.all()` (not `.tuples()`) so unit tests can stub `execute().all()`
        # with plain tuples, while SQLAlchemy will still yield unpackable Row objects.
        rows = self.db.execute(stmt).all()
        return self._format_performer_rows(rows)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Report 8 — Bottom Performers
    # ------------------------------------------------------------------

    def bottom_performers(
        self, org_id: UUID, cycle_id: UUID, *, n: int = 10
    ) -> list[dict]:
        """
        Bottom N employees by final_score.

        Returns a list with the same shape as :meth:`top_performers`.
        """
        from app.models.people.hr.department import Department
        from app.models.people.hr.employee import Employee
        from app.models.people.perf.appraisal import Appraisal, AppraisalStatus
        from app.models.person import Person

        stmt = (
            select(
                Person.display_name,
                Department.department_name,
                Appraisal.final_score,
                Appraisal.rating_label,
            )
            .join(Employee, Employee.employee_id == Appraisal.employee_id)
            .join(Person, Person.id == Employee.person_id)
            .outerjoin(Department, Department.department_id == Employee.department_id)
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
                Appraisal.final_score.is_not(None),
            )
            .order_by(Appraisal.final_score.asc())
            .limit(n)
        )

        rows = self.db.execute(stmt).all()
        return self._format_performer_rows(rows)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Report 9 — Development Needs Overview
    # ------------------------------------------------------------------

    def development_needs_overview(self, org_id: UUID, cycle_id: UUID) -> dict:
        """
        Aggregate development_needs text from all completed appraisals.

        Returns::

            {
                "total_with_needs": int,
                "total_appraisals": int,
                "needs_list": [{"need": str, "count": int}],
            }
        """
        from app.models.people.perf.appraisal import Appraisal, AppraisalStatus

        stmt = select(Appraisal.development_needs).where(
            Appraisal.organization_id == org_id,
            Appraisal.cycle_id == cycle_id,
            Appraisal.status == AppraisalStatus.COMPLETED,
            Appraisal.development_needs.is_not(None),
        )

        total_stmt = select(func.count(Appraisal.appraisal_id)).where(
            Appraisal.organization_id == org_id,
            Appraisal.cycle_id == cycle_id,
            Appraisal.status == AppraisalStatus.COMPLETED,
        )

        needs_rows = self.db.scalars(stmt).all()
        total_appraisals: int = self.db.scalar(total_stmt) or 0

        # Tokenise: split on common separators, strip and count
        counter: Counter[str] = Counter()
        for text in needs_rows:
            if text:
                for line in text.replace(";", "\n").replace(",", "\n").splitlines():
                    need = line.strip()
                    if len(need) > 3:
                        counter[need] += 1

        needs_list = [
            {"need": need, "count": cnt} for need, cnt in counter.most_common()
        ]

        return {
            "total_with_needs": len(needs_rows),
            "total_appraisals": total_appraisals,
            "needs_list": needs_list,
        }

    # ------------------------------------------------------------------
    # Report 10 — Development Needs by Department
    # ------------------------------------------------------------------

    def development_needs_by_department(
        self,
        org_id: UUID,
        cycle_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> dict:
        """
        Development needs aggregated per department.

        Pass ``department_id`` to filter to a single department.

        Returns::

            {
                "departments": [
                    {
                        "department_name": str,
                        "total_with_needs": int,
                        "needs_list": [{"need": str, "count": int}],
                    }
                ],
                "total": int,
            }
        """
        from app.models.people.hr.department import Department
        from app.models.people.hr.employee import Employee
        from app.models.people.perf.appraisal import Appraisal, AppraisalStatus

        filters = [
            Appraisal.organization_id == org_id,
            Appraisal.cycle_id == cycle_id,
            Appraisal.status == AppraisalStatus.COMPLETED,
            Appraisal.development_needs.is_not(None),
        ]
        if department_id is not None:
            filters.append(Employee.department_id == department_id)

        stmt = (
            select(
                Department.department_name,
                Appraisal.development_needs,
            )
            .join(Employee, Employee.employee_id == Appraisal.employee_id)
            .outerjoin(Department, Department.department_id == Employee.department_id)
            .where(and_(*filters))
            .order_by(Department.department_name)
        )

        rows = self.db.execute(stmt).all()

        dept_needs: dict[str, list[str]] = {}
        for dept_name, needs_text in rows:
            key = dept_name or "Unassigned"
            if key not in dept_needs:
                dept_needs[key] = []
            if needs_text:
                dept_needs[key].append(needs_text)

        departments = []
        grand_total = 0
        for dept_name, needs_texts in dept_needs.items():
            counter: Counter[str] = Counter()
            for text in needs_texts:
                for line in text.replace(";", "\n").replace(",", "\n").splitlines():
                    need = line.strip()
                    if len(need) > 3:
                        counter[need] += 1
            total_with_needs = len(needs_texts)
            grand_total += total_with_needs
            departments.append(
                {
                    "department_name": dept_name,
                    "total_with_needs": total_with_needs,
                    "needs_list": [
                        {"need": need, "count": cnt}
                        for need, cnt in counter.most_common()
                    ],
                }
            )

        return {"departments": departments, "total": grand_total}

    # ------------------------------------------------------------------
    # Report 11 — Cycle Compliance Dashboard
    # ------------------------------------------------------------------

    def cycle_compliance_dashboard(self, org_id: UUID, cycle_id: UUID) -> dict:
        """
        High-level compliance metrics for a single appraisal cycle.

        Returns::

            {
                "contracts": {
                    "total": int, "signed": int, "unsigned": int, "compliance_pct": Decimal
                },
                "monthly_reviews": {
                    "completed": int, "expected": int, "completion_pct": Decimal
                },
                "appraisals": {
                    "total": int, "completed": int, "pending": int, "completion_pct": Decimal
                },
                "appeals": {
                    "total": int, "pending": int, "resolved": int
                },
                "pips": {"active": int},
            }
        """
        from app.models.people.perf.appraisal import Appraisal, AppraisalStatus
        from app.models.people.perf.appraisal_appeal import AppraisalAppeal
        from app.models.people.perf.monthly_review import MonthlyReview
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pip import PerformanceImprovementPlan
        from app.models.people.perf.pms_enums import (
            AppealStatus,
            MonthlyReviewStatus,
            PIPStatus,
        )

        # --- Contracts ---
        total_contracts: int = (
            self.db.scalar(
                select(func.count(PerformanceContract.contract_id)).where(
                    PerformanceContract.organization_id == org_id,
                    PerformanceContract.cycle_id == cycle_id,
                )
            )
            or 0
        )
        # "Signed" = both employee and supervisor have signed
        signed_contracts: int = (
            self.db.scalar(
                select(func.count(PerformanceContract.contract_id)).where(
                    PerformanceContract.organization_id == org_id,
                    PerformanceContract.cycle_id == cycle_id,
                    PerformanceContract.employee_signed_date.is_not(None),
                    PerformanceContract.supervisor_signed_date.is_not(None),
                )
            )
            or 0
        )
        unsigned_contracts = total_contracts - signed_contracts

        # --- Monthly Reviews ---
        completed_reviews: int = (
            self.db.scalar(
                select(func.count(MonthlyReview.review_id)).where(
                    MonthlyReview.organization_id == org_id,
                    MonthlyReview.contract_id.in_(
                        select(PerformanceContract.contract_id).where(
                            PerformanceContract.organization_id == org_id,
                            PerformanceContract.cycle_id == cycle_id,
                        )
                    ),
                    MonthlyReview.status.in_(
                        [
                            MonthlyReviewStatus.ACKNOWLEDGED,
                            MonthlyReviewStatus.SUBMITTED,
                        ]
                    ),
                )
            )
            or 0
        )
        total_reviews: int = (
            self.db.scalar(
                select(func.count(MonthlyReview.review_id)).where(
                    MonthlyReview.organization_id == org_id,
                    MonthlyReview.contract_id.in_(
                        select(PerformanceContract.contract_id).where(
                            PerformanceContract.organization_id == org_id,
                            PerformanceContract.cycle_id == cycle_id,
                        )
                    ),
                )
            )
            or 0
        )

        # --- Appraisals ---
        total_appraisals: int = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.cycle_id == cycle_id,
                )
            )
            or 0
        )
        completed_appraisals: int = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.cycle_id == cycle_id,
                    Appraisal.status == AppraisalStatus.COMPLETED,
                )
            )
            or 0
        )
        pending_appraisals = total_appraisals - completed_appraisals

        # --- Appeals ---
        # Appeals are linked to appraisals which are linked to cycles via the
        # appraisal_id → Appraisal.cycle_id path.
        appraisal_ids_subq = (
            select(Appraisal.appraisal_id)
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
            )
            .scalar_subquery()
        )
        total_appeals: int = (
            self.db.scalar(
                select(func.count(AppraisalAppeal.appeal_id)).where(
                    AppraisalAppeal.organization_id == org_id,
                    AppraisalAppeal.appraisal_id.in_(appraisal_ids_subq),
                )
            )
            or 0
        )
        pending_appeal_statuses = [
            AppealStatus.FILED,
            AppealStatus.UNDER_MEDIATION,
            AppealStatus.REFERRED_TO_COMMITTEE,
        ]
        resolved_appeal_statuses = [
            AppealStatus.RESOLVED,
            AppealStatus.DISMISSED,
        ]
        pending_appeals: int = (
            self.db.scalar(
                select(func.count(AppraisalAppeal.appeal_id)).where(
                    AppraisalAppeal.organization_id == org_id,
                    AppraisalAppeal.appraisal_id.in_(appraisal_ids_subq),
                    AppraisalAppeal.status.in_(pending_appeal_statuses),
                )
            )
            or 0
        )
        resolved_appeals: int = (
            self.db.scalar(
                select(func.count(AppraisalAppeal.appeal_id)).where(
                    AppraisalAppeal.organization_id == org_id,
                    AppraisalAppeal.appraisal_id.in_(appraisal_ids_subq),
                    AppraisalAppeal.status.in_(resolved_appeal_statuses),
                )
            )
            or 0
        )

        # --- PIPs ---
        # PIPs linked to appraisals in this cycle via appraisal_id
        active_pips: int = (
            self.db.scalar(
                select(func.count(PerformanceImprovementPlan.pip_id)).where(
                    PerformanceImprovementPlan.organization_id == org_id,
                    PerformanceImprovementPlan.appraisal_id.in_(appraisal_ids_subq),
                    PerformanceImprovementPlan.status.in_(
                        [PIPStatus.ACTIVE, PIPStatus.UNDER_REVIEW, PIPStatus.EXTENDED]
                    ),
                )
            )
            or 0
        )

        logger.info(
            "cycle_compliance_dashboard: org=%s cycle=%s contracts=%d appraisals=%d",
            org_id,
            cycle_id,
            total_contracts,
            total_appraisals,
        )

        return {
            "contracts": {
                "total": total_contracts,
                "signed": signed_contracts,
                "unsigned": unsigned_contracts,
                "compliance_pct": _pct(signed_contracts, total_contracts),
            },
            "monthly_reviews": {
                "completed": completed_reviews,
                "expected": total_reviews,
                "completion_pct": _pct(completed_reviews, total_reviews),
            },
            "appraisals": {
                "total": total_appraisals,
                "completed": completed_appraisals,
                "pending": pending_appraisals,
                "completion_pct": _pct(completed_appraisals, total_appraisals),
            },
            "appeals": {
                "total": total_appeals,
                "pending": pending_appeals,
                "resolved": resolved_appeals,
            },
            "pips": {
                "active": active_pips,
            },
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_performer_rows(
        rows: Sequence[tuple[str | None, str | None, Decimal | None, str | None]],
    ) -> list[dict]:
        """
        Convert (display_name, department_name, final_score, rating_label) rows
        into the standard performer dict shape.
        """
        result = []
        for display_name, dept_name, final_score, rating_label in rows:
            result.append(
                {
                    "employee_name": display_name or "",
                    "department": dept_name or "",
                    "score": (
                        Decimal(str(final_score)).quantize(Decimal("0.01"))
                        if final_score is not None
                        else Decimal("0.00")
                    ),
                    "rating_label": rating_label or "",
                }
            )
        return result
