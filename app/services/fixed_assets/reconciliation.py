"""Fixed asset depreciation to GL reconciliation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TypedDict
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.audit.approval_request import (
    ApprovalRequest,
    ApprovalRequestStatus,
)
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.models.fixed_assets.gl_reconciliation import (
    FixedAssetGLReconciliationException,
    FixedAssetGLReconciliationRun,
)
from app.models.fixed_assets.asset import Asset
from app.models.fixed_assets.asset_category import AssetCategory
from app.models.finance.gl.journal_entry import (
    JournalEntry,
    JournalStatus,
    JournalType,
)
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.fixed_assets.depreciation_run import (
    DepreciationRun,
    DepreciationRunStatus,
)
from app.models.fixed_assets.depreciation_schedule import DepreciationSchedule
from app.services.common import coerce_uuid
from app.services.finance.gl.period_guard import PeriodGuardService
from app.services.finance.platform.approval_workflow import ApprovalWorkflowService
from app.services.finance.platform.org_context import org_context_service
from app.services.finance.platform.sequence import SequenceService
from app.services.fixed_assets.web import FixedAssetWebService


DEFAULT_RECONCILIATION_TOLERANCE = Decimal("0.01")
FA_GL_RECONCILIATION_DOCUMENT_TYPE = "FA_GL_RECONCILIATION"


class DraftCorrectionLine(TypedDict):
    account_id: UUID
    description: str
    debit: Decimal
    credit: Decimal


SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


@dataclass(frozen=True)
class DepreciationGLReconciliationLine:
    """One expected-vs-actual GL comparison for a depreciation run."""

    account_id: UUID
    side: str
    expected_amount: Decimal
    gl_amount: Decimal
    variance: Decimal
    status: str


@dataclass(frozen=True)
class DepreciationGLReconciliationResult:
    """Summary of depreciation run reconciliation against GL lines."""

    run_id: UUID
    organization_id: UUID
    status: str
    is_reconciled: bool
    matched_count: int
    variance_count: int
    missing_gl_count: int
    extra_gl_count: int
    expected_total: Decimal
    gl_total: Decimal
    net_variance: Decimal
    lines: list[DepreciationGLReconciliationLine]

    def as_dict(self) -> dict[str, object]:
        """Return a Celery/result friendly representation."""
        return {
            "run_id": str(self.run_id),
            "organization_id": str(self.organization_id),
            "status": self.status,
            "is_reconciled": self.is_reconciled,
            "matched_count": self.matched_count,
            "variance_count": self.variance_count,
            "missing_gl_count": self.missing_gl_count,
            "extra_gl_count": self.extra_gl_count,
            "expected_total": str(self.expected_total),
            "gl_total": str(self.gl_total),
            "net_variance": str(self.net_variance),
            "lines": [
                {
                    "account_id": str(line.account_id),
                    "side": line.side,
                    "expected_amount": str(line.expected_amount),
                    "gl_amount": str(line.gl_amount),
                    "variance": str(line.variance),
                    "status": line.status,
                }
                for line in self.lines
            ],
        }


class FixedAssetDepreciationReconciliationService:
    """Compare a depreciation run's calculated schedules to posted GL evidence."""

    EXPENSE_SIDE = "DEBIT_EXPENSE"
    ACCUMULATED_DEPRECIATION_SIDE = "CREDIT_ACCUMULATED_DEPRECIATION"

    @staticmethod
    def reconcile_run(
        db: Session,
        organization_id: UUID,
        run_id: UUID,
        *,
        tolerance: Decimal = DEFAULT_RECONCILIATION_TOLERANCE,
    ) -> DepreciationGLReconciliationResult:
        """Auto-match safe depreciation lines and flag real differences.

        This method does not post correction journals or force balances to agree.
        It only compares the run schedules with the posted FA depreciation journal.
        """
        org_id = coerce_uuid(organization_id)
        r_id = coerce_uuid(run_id)

        run = db.get(DepreciationRun, r_id)
        if not run or run.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Depreciation run not found")

        if run.status not in {
            DepreciationRunStatus.POSTED,
            DepreciationRunStatus.REVERSED,
        }:
            raise HTTPException(
                status_code=400,
                detail="Depreciation run must be posted before GL reconciliation",
            )

        expected = FixedAssetDepreciationReconciliationService._expected_amounts(
            db, r_id
        )
        actual = FixedAssetDepreciationReconciliationService._posted_gl_amounts(
            db, org_id, run
        )

        lines: list[DepreciationGLReconciliationLine] = []
        for key in sorted(expected.keys() | actual.keys(), key=lambda item: str(item)):
            account_id, side = key
            expected_amount = expected.get(key, Decimal("0"))
            gl_amount = actual.get(key, Decimal("0"))
            variance = expected_amount - gl_amount
            status = FixedAssetDepreciationReconciliationService._line_status(
                expected_amount,
                gl_amount,
                variance,
                tolerance,
            )
            lines.append(
                DepreciationGLReconciliationLine(
                    account_id=account_id,
                    side=side,
                    expected_amount=expected_amount,
                    gl_amount=gl_amount,
                    variance=variance,
                    status=status,
                )
            )

        matched_count = sum(1 for line in lines if line.status == "MATCHED")
        variance_count = sum(1 for line in lines if line.status == "VARIANCE")
        missing_gl_count = sum(1 for line in lines if line.status == "MISSING_GL")
        extra_gl_count = sum(1 for line in lines if line.status == "EXTRA_GL")
        expected_total = sum((line.expected_amount for line in lines), Decimal("0"))
        gl_total = sum((line.gl_amount for line in lines), Decimal("0"))
        net_variance = expected_total - gl_total
        is_reconciled = bool(lines) and all(line.status == "MATCHED" for line in lines)

        return DepreciationGLReconciliationResult(
            run_id=r_id,
            organization_id=org_id,
            status="reconciled" if is_reconciled else "review_required",
            is_reconciled=is_reconciled,
            matched_count=matched_count,
            variance_count=variance_count,
            missing_gl_count=missing_gl_count,
            extra_gl_count=extra_gl_count,
            expected_total=expected_total,
            gl_total=gl_total,
            net_variance=net_variance,
            lines=lines,
        )

    @staticmethod
    def _expected_amounts(
        db: Session,
        run_id: UUID,
    ) -> dict[tuple[UUID, str], Decimal]:
        expected: dict[tuple[UUID, str], Decimal] = {}
        schedules = db.scalars(
            select(DepreciationSchedule).where(DepreciationSchedule.run_id == run_id)
        ).all()
        for schedule in schedules:
            amount = Decimal(str(schedule.depreciation_amount or 0))
            if amount <= 0:
                continue
            expense_key = (
                schedule.expense_account_id,
                FixedAssetDepreciationReconciliationService.EXPENSE_SIDE,
            )
            accum_key = (
                schedule.accumulated_depreciation_account_id,
                FixedAssetDepreciationReconciliationService.ACCUMULATED_DEPRECIATION_SIDE,
            )
            expected[expense_key] = expected.get(expense_key, Decimal("0")) + amount
            expected[accum_key] = expected.get(accum_key, Decimal("0")) + amount
        return expected

    @staticmethod
    def _posted_gl_amounts(
        db: Session,
        organization_id: UUID,
        run: DepreciationRun,
    ) -> dict[tuple[UUID, str], Decimal]:
        actual: dict[tuple[UUID, str], Decimal] = {}
        source_filter = and_(
            JournalEntry.source_module == "FA",
            JournalEntry.source_document_type == "DEPRECIATION_RUN",
            JournalEntry.source_document_id == run.run_id,
        )
        if run.journal_entry_id:
            source_filter = or_(
                JournalEntry.journal_entry_id == run.journal_entry_id,
                source_filter,
            )

        rows = db.execute(
            select(
                JournalEntryLine.account_id,
                JournalEntryLine.debit_amount_functional,
                JournalEntryLine.credit_amount_functional,
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.status == JournalStatus.POSTED,
                source_filter,
            )
        ).all()

        for row in rows:
            debit_amount = Decimal(str(row.debit_amount_functional or 0))
            credit_amount = Decimal(str(row.credit_amount_functional or 0))
            if debit_amount > 0:
                key = (
                    row.account_id,
                    FixedAssetDepreciationReconciliationService.EXPENSE_SIDE,
                )
                actual[key] = actual.get(key, Decimal("0")) + debit_amount
            if credit_amount > 0:
                key = (
                    row.account_id,
                    FixedAssetDepreciationReconciliationService.ACCUMULATED_DEPRECIATION_SIDE,
                )
                actual[key] = actual.get(key, Decimal("0")) + credit_amount
        return actual

    @staticmethod
    def _line_status(
        expected_amount: Decimal,
        gl_amount: Decimal,
        variance: Decimal,
        tolerance: Decimal,
    ) -> str:
        if expected_amount == 0 and gl_amount > 0:
            return "EXTRA_GL"
        if expected_amount > 0 and gl_amount == 0:
            return "MISSING_GL"
        if variance.copy_abs() <= tolerance:
            return "MATCHED"
        return "VARIANCE"


fa_depreciation_reconciliation_service = FixedAssetDepreciationReconciliationService()


class FixedAssetGLReconciliationPackageService:
    """Persist FA GL reconciliation evidence and route variances for approval."""

    STATUS_BALANCED = "BALANCED"
    STATUS_PENDING_APPROVAL = "PENDING_APPROVAL"
    STATUS_APPROVAL_NOT_CONFIGURED = "APPROVAL_NOT_CONFIGURED"
    STATUS_DRAFT_CREATED = "DRAFT_CREATED"

    @staticmethod
    def _actionable_total_variance(totals: dict[str, object]) -> Decimal:
        """Return independent GL variances that require correction.

        NBV variance is derived from cost less accumulated depreciation, so
        counting it with the underlying variances overstates the approval amount.
        """
        return (
            Decimal(str(totals["cost_variance"])).copy_abs()
            + Decimal(str(totals["accumulated_depreciation_variance"])).copy_abs()
        )

    @staticmethod
    def _actionable_row_variance(row: dict[str, object]) -> Decimal:
        return (
            Decimal(str(row["cost_variance"])).copy_abs()
            + Decimal(str(row["accumulated_depreciation_variance"])).copy_abs()
        )

    @staticmethod
    def create_package(
        db: Session,
        organization_id: UUID,
        *,
        as_of: date | None = None,
        requested_by_user_id: UUID | None = None,
        submit_for_approval: bool = True,
    ) -> FixedAssetGLReconciliationRun:
        """Create a reconciliation package without posting any GL correction."""
        org_id = coerce_uuid(organization_id)
        actor_id = coerce_uuid(requested_by_user_id or SYSTEM_USER_ID)
        context = FixedAssetWebService.gl_reconciliation_context(
            db,
            str(org_id),
            as_of=as_of,
        )
        totals = context["totals"]
        report_date = date.fromisoformat(str(context["as_of"]))
        total_variance_abs = (
            FixedAssetGLReconciliationPackageService._actionable_total_variance(totals)
        )
        is_balanced = bool(context["is_balanced"])

        run = FixedAssetGLReconciliationRun(
            run_id=uuid4(),
            organization_id=org_id,
            as_of_date=report_date,
            status=(
                FixedAssetGLReconciliationPackageService.STATUS_BALANCED
                if is_balanced
                else FixedAssetGLReconciliationPackageService.STATUS_APPROVAL_NOT_CONFIGURED
            ),
            category_count=int(totals["category_count"]),
            asset_count=int(totals["asset_count"]),
            total_variance_abs=total_variance_abs,
            nbv_variance=Decimal(str(totals["nbv_variance"])),
            cost_variance=Decimal(str(totals["cost_variance"])),
            accumulated_depreciation_variance=Decimal(
                str(totals["accumulated_depreciation_variance"])
            ),
            summary_payload=FixedAssetGLReconciliationPackageService._summary_payload(
                context
            ),
            created_by_user_id=actor_id,
        )
        db.add(run)
        db.flush()

        if not is_balanced:
            for row in context["rows"]:
                if bool(row["is_balanced"]):
                    continue
                exception = FixedAssetGLReconciliationException(
                    exception_id=uuid4(),
                    run_id=run.run_id,
                    organization_id=org_id,
                    status="OPEN",
                    exception_type="GL_MAPPING_VARIANCE",
                    asset_account_id=FixedAssetGLReconciliationPackageService._account_id(
                        row.get("asset_account")
                    ),
                    accumulated_depreciation_account_id=(
                        FixedAssetGLReconciliationPackageService._account_id(
                            row.get("accumulated_depreciation_account")
                        )
                    ),
                    category_codes=str(row.get("category_codes") or ""),
                    variance_amount=(
                        FixedAssetGLReconciliationPackageService._actionable_row_variance(
                            row
                        )
                    ),
                    evidence_payload=(
                        FixedAssetGLReconciliationPackageService._row_payload(row)
                    ),
                )
                db.add(exception)

        if submit_for_approval and not is_balanced:
            workflow_id = ApprovalWorkflowService.check_workflow_required(
                db,
                org_id,
                FA_GL_RECONCILIATION_DOCUMENT_TYPE,
                document_amount=total_variance_abs,
                currency_code=None,
            )
            if workflow_id:
                request_id = ApprovalWorkflowService.submit_for_approval(
                    db,
                    org_id,
                    workflow_id,
                    FA_GL_RECONCILIATION_DOCUMENT_TYPE,
                    run.run_id,
                    f"FA GL reconciliation {report_date.isoformat()}",
                    total_variance_abs,
                    None,
                    actor_id,
                    correlation_id=f"FA_GL_RECON:{run.run_id}",
                )
                run.approval_request_id = request_id
                run.status = (
                    FixedAssetGLReconciliationPackageService.STATUS_PENDING_APPROVAL
                )

        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    def create_draft_correction_journal(
        db: Session,
        organization_id: UUID,
        run_id: UUID,
        *,
        created_by_user_id: UUID | None = None,
    ) -> JournalEntry:
        """Create a draft GL correction journal after package approval.

        The journal is intentionally left in DRAFT for finance review. It is not
        submitted, approved, or posted by this method.
        """
        org_id = coerce_uuid(organization_id)
        actor_id = coerce_uuid(created_by_user_id or SYSTEM_USER_ID)
        r_id = coerce_uuid(run_id)

        run = db.get(FixedAssetGLReconciliationRun, r_id)
        if not run or run.organization_id != org_id:
            raise HTTPException(
                status_code=404,
                detail="Fixed asset GL reconciliation package not found",
            )

        if run.proposed_journal_entry_id:
            existing = db.get(JournalEntry, run.proposed_journal_entry_id)
            if existing and existing.organization_id == org_id:
                return existing

        if not run.approval_request_id:
            raise HTTPException(
                status_code=400,
                detail="Reconciliation package must be submitted for approval first",
            )

        approval = db.get(ApprovalRequest, run.approval_request_id)
        if (
            not approval
            or approval.organization_id != org_id
            or approval.document_id != run.run_id
            or approval.document_type != FA_GL_RECONCILIATION_DOCUMENT_TYPE
        ):
            raise HTTPException(
                status_code=400,
                detail="Approval request does not match reconciliation package",
            )
        if approval.status != ApprovalRequestStatus.APPROVED:
            raise HTTPException(
                status_code=400,
                detail="Reconciliation package must be approved before drafting a correction journal",
            )

        if (
            Decimal(str(run.cost_variance or 0)).copy_abs()
            > DEFAULT_RECONCILIATION_TOLERANCE
        ):
            raise HTTPException(
                status_code=400,
                detail="Draft correction currently supports accumulated depreciation variance only",
            )

        exceptions = list(
            db.scalars(
                select(FixedAssetGLReconciliationException)
                .where(
                    FixedAssetGLReconciliationException.run_id == run.run_id,
                    FixedAssetGLReconciliationException.organization_id == org_id,
                    FixedAssetGLReconciliationException.status == "OPEN",
                )
                .order_by(FixedAssetGLReconciliationException.created_at.asc())
            ).all()
        )
        if not exceptions:
            raise HTTPException(
                status_code=400,
                detail="No open reconciliation exceptions found",
            )

        posting_date = run.as_of_date
        period = PeriodGuardService.get_period_for_date(db, org_id, posting_date)
        if not period:
            raise HTTPException(
                status_code=400,
                detail=f"No fiscal period found for posting date {posting_date}",
            )

        journal_number = SequenceService.get_next_number(
            db,
            org_id,
            SequenceType.JOURNAL,
            period.fiscal_year_id,
        )
        currency_code = org_context_service.get_functional_currency(db, org_id)
        lines = FixedAssetGLReconciliationPackageService._draft_correction_lines(
            db,
            org_id,
            exceptions,
        )
        total_debit = sum((line["debit"] for line in lines), Decimal("0"))
        total_credit = sum((line["credit"] for line in lines), Decimal("0"))
        if (total_debit - total_credit).copy_abs() > Decimal("0.000001"):
            raise HTTPException(
                status_code=400,
                detail="Draft correction journal is not balanced",
            )

        journal = JournalEntry(
            organization_id=org_id,
            journal_number=journal_number,
            journal_type=JournalType.ADJUSTMENT,
            entry_date=date.today(),
            posting_date=posting_date,
            fiscal_period_id=period.fiscal_period_id,
            description=f"Draft FA GL reconciliation correction {posting_date.isoformat()}",
            reference=f"FA-GL-RECON:{run.run_id}",
            currency_code=currency_code,
            exchange_rate=Decimal("1.0"),
            total_debit=total_debit,
            total_credit=total_credit,
            total_debit_functional=total_debit,
            total_credit_functional=total_credit,
            status=JournalStatus.DRAFT,
            source_module="FA",
            source_document_type=FA_GL_RECONCILIATION_DOCUMENT_TYPE,
            source_document_id=run.run_id,
            created_by_user_id=actor_id,
            approval_request_id=run.approval_request_id,
            correlation_id=f"FA_GL_RECON_DRAFT:{run.run_id}",
        )
        db.add(journal)
        db.flush()

        for line_number, line in enumerate(lines, start=1):
            db.add(
                JournalEntryLine(
                    journal_entry_id=journal.journal_entry_id,
                    line_number=line_number,
                    account_id=line["account_id"],
                    description=line["description"],
                    debit_amount=line["debit"],
                    credit_amount=line["credit"],
                    debit_amount_functional=line["debit"],
                    credit_amount_functional=line["credit"],
                    currency_code=currency_code,
                    exchange_rate=Decimal("1.0"),
                    reconciliation_id=str(run.run_id),
                )
            )

        run.proposed_journal_entry_id = journal.journal_entry_id
        run.status = FixedAssetGLReconciliationPackageService.STATUS_DRAFT_CREATED
        db.commit()
        db.refresh(journal)
        return journal

    @staticmethod
    def _draft_correction_lines(
        db: Session,
        organization_id: UUID,
        exceptions: list[FixedAssetGLReconciliationException],
    ) -> list[DraftCorrectionLine]:
        lines: list[DraftCorrectionLine] = []
        for exception in exceptions:
            accumulated_account_id = exception.accumulated_depreciation_account_id
            if not accumulated_account_id:
                raise HTTPException(
                    status_code=400,
                    detail="Exception is missing an accumulated depreciation account",
                )
            payload = exception.evidence_payload or {}
            ad_variance = Decimal(
                str(payload.get("accumulated_depreciation_variance") or "0")
            )
            if ad_variance.copy_abs() <= DEFAULT_RECONCILIATION_TOLERANCE:
                continue

            amount = ad_variance.copy_abs()
            category_allocations = (
                FixedAssetGLReconciliationPackageService._expense_allocations(
                    db,
                    organization_id,
                    exception,
                    amount,
                )
            )
            if ad_variance < 0:
                lines.append(
                    {
                        "account_id": accumulated_account_id,
                        "description": "Reduce over-posted accumulated depreciation",
                        "debit": amount,
                        "credit": Decimal("0"),
                    }
                )
                for account_id, allocated_amount in category_allocations:
                    lines.append(
                        {
                            "account_id": account_id,
                            "description": "Reverse over-posted depreciation expense",
                            "debit": Decimal("0"),
                            "credit": allocated_amount,
                        }
                    )
            else:
                for account_id, allocated_amount in category_allocations:
                    lines.append(
                        {
                            "account_id": account_id,
                            "description": "Record missing depreciation expense",
                            "debit": allocated_amount,
                            "credit": Decimal("0"),
                        }
                    )
                lines.append(
                    {
                        "account_id": accumulated_account_id,
                        "description": "Record missing accumulated depreciation",
                        "debit": Decimal("0"),
                        "credit": amount,
                    }
                )
        if not lines:
            raise HTTPException(
                status_code=400,
                detail="No accumulated depreciation variance requires correction",
            )
        return lines

    @staticmethod
    def _expense_allocations(
        db: Session,
        organization_id: UUID,
        exception: FixedAssetGLReconciliationException,
        correction_amount: Decimal,
    ) -> list[tuple[UUID, Decimal]]:
        category_codes = [
            code.strip()
            for code in (exception.category_codes or "").split(",")
            if code.strip()
        ]
        query = select(
            AssetCategory.depreciation_expense_account_id,
            AssetCategory.category_code,
        ).where(
            AssetCategory.organization_id == organization_id,
            AssetCategory.asset_account_id == exception.asset_account_id,
            AssetCategory.accumulated_depreciation_account_id
            == exception.accumulated_depreciation_account_id,
            AssetCategory.is_active.is_(True),
        )
        if category_codes:
            query = query.where(AssetCategory.category_code.in_(category_codes))
        categories = db.execute(query).all()
        if not categories:
            raise HTTPException(
                status_code=400,
                detail="No asset categories found for reconciliation exception",
            )

        weights: list[tuple[UUID, Decimal]] = []
        for category in categories:
            category_weight = db.scalar(
                select(func.coalesce(func.sum(Asset.functional_currency_cost), 0))
                .join(
                    AssetCategory,
                    AssetCategory.category_id == Asset.category_id,
                )
                .where(
                    Asset.organization_id == organization_id,
                    AssetCategory.organization_id == organization_id,
                    AssetCategory.category_code == category.category_code,
                )
            )
            weights.append(
                (
                    category.depreciation_expense_account_id,
                    Decimal(str(category_weight or 0)),
                )
            )

        total_weight = sum((weight for _, weight in weights), Decimal("0"))
        if total_weight <= 0:
            equal_weight = Decimal("1")
            weights = [(account_id, equal_weight) for account_id, _ in weights]
            total_weight = equal_weight * len(weights)

        by_account: dict[UUID, Decimal] = {}
        allocated_total = Decimal("0")
        quant = Decimal("0.000001")
        for index, (account_id, weight) in enumerate(weights):
            if index == len(weights) - 1:
                allocated = correction_amount - allocated_total
            else:
                allocated = (correction_amount * weight / total_weight).quantize(quant)
                allocated_total += allocated
            by_account[account_id] = (
                by_account.get(account_id, Decimal("0")) + allocated
            )

        return [
            (account_id, amount)
            for account_id, amount in by_account.items()
            if amount.copy_abs() > DEFAULT_RECONCILIATION_TOLERANCE
        ]

    @staticmethod
    def _account_id(account: object) -> UUID | None:
        if not isinstance(account, dict):
            return None
        raw = account.get("account_id")
        return coerce_uuid(raw) if raw else None

    @staticmethod
    def _summary_payload(context: dict) -> dict[str, object]:
        totals = context["totals"]
        return {
            "as_of": context["as_of"],
            "out_of_balance_count": context["out_of_balance_count"],
            "is_balanced": context["is_balanced"],
            "totals": {key: str(value) for key, value in totals.items()},
        }

    @staticmethod
    def _row_payload(row: dict[str, object]) -> dict[str, object]:
        keys = (
            "category_code",
            "category_name",
            "category_codes",
            "asset_count",
            "register_cost",
            "gl_cost",
            "cost_variance",
            "register_accumulated_depreciation",
            "gl_accumulated_depreciation",
            "accumulated_depreciation_variance",
            "register_nbv",
            "gl_nbv",
            "nbv_variance",
        )
        return {key: str(row.get(key, "")) for key in keys}


fa_gl_reconciliation_package_service = FixedAssetGLReconciliationPackageService()
