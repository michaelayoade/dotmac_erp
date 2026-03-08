"""Expense claim web responses."""

from __future__ import annotations

import logging
from importlib import import_module
from decimal import Decimal
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import and_, exists, false, func, or_, select
from sqlalchemy.orm import joinedload

from app.models.domain_settings import SettingDomain
from app.models.expense import (
    ExpenseClaim,
    ExpenseClaimApprovalStep,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.models.people.hr.employee import Employee
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.expense.expense_service import (
    ApproverAuthorityError,
    ExpenseClaimStatusError,
    ExpenseService,
    ExpenseServiceError,
)
from app.services.expense.limit_service import ExpenseLimitServiceError
from app.services.finance.platform.authorization import AuthorizationService
from app.services.pm.comment import comment_service
from app.services.recent_activity import get_recent_activity_for_record
from app.services.settings_spec import resolve_value
from app.services.storage import get_storage
logger = logging.getLogger(__name__)


def _web_facade():
    return import_module("app.services.expense.web")


class ExpenseClaimsWebMixin:
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
        offset: int = 0,
        limit: int = 25,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        auth_employee_id = coerce_uuid(auth.employee_id)
        filter_employee_id = coerce_uuid(employee_id) if employee_id else None
        filter_view = "submitted_to_me" if view == "submitted_to_me" else "all"
        status_value = None
        if status:
            try:
                status_value = ExpenseClaimStatus(status)
            except ValueError:
                status_value = None

        from datetime import date as date_type

        start = date_type.fromisoformat(start_date) if start_date else None
        end = date_type.fromisoformat(end_date) if end_date else None

        stmt = (
            select(ExpenseClaim)
            .options(joinedload(ExpenseClaim.employee).joinedload(Employee.person))
            .where(ExpenseClaim.organization_id == org_id)
        )
        if filter_view == "submitted_to_me":
            if auth_employee_id:
                latest_round = (
                    select(func.max(ExpenseClaimApprovalStep.submission_round))
                    .where(ExpenseClaimApprovalStep.claim_id == ExpenseClaim.claim_id)
                    .correlate(ExpenseClaim)
                    .scalar_subquery()
                )
                current_step = (
                    select(func.min(ExpenseClaimApprovalStep.step_number))
                    .where(
                        ExpenseClaimApprovalStep.claim_id == ExpenseClaim.claim_id,
                        ExpenseClaimApprovalStep.submission_round == latest_round,
                        ExpenseClaimApprovalStep.decision.is_(None),
                    )
                    .correlate(ExpenseClaim)
                    .scalar_subquery()
                )
                step_assigned = exists(
                    select(1).where(
                        ExpenseClaimApprovalStep.claim_id == ExpenseClaim.claim_id,
                        ExpenseClaimApprovalStep.submission_round == latest_round,
                        ExpenseClaimApprovalStep.approver_id == auth_employee_id,
                        ExpenseClaimApprovalStep.decision.is_(None),
                        or_(
                            ExpenseClaimApprovalStep.requires_all_approvals.is_(True),
                            ExpenseClaimApprovalStep.step_number == current_step,
                        ),
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
                        and_(
                            ExpenseClaim.status.in_(
                                [
                                    ExpenseClaimStatus.SUBMITTED,
                                    ExpenseClaimStatus.PENDING_APPROVAL,
                                ]
                            ),
                            step_assigned,
                        ),
                        and_(~has_steps, legacy_assignment),
                    )
                )
            else:
                stmt = stmt.where(false())
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
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))

        claims = list(
            db.scalars(
                stmt.order_by(ExpenseClaim.claim_date.desc()).offset(offset).limit(limit)
            )
            .unique()
            .all()
        )

        status_rows = db.execute(
            select(ExpenseClaim.status, func.count())
            .where(ExpenseClaim.organization_id == org_id)
            .group_by(ExpenseClaim.status)
        ).all()
        counts = {(status.value if status else "UNKNOWN"): count for status, count in status_rows}
        can_delete_claim = auth.is_admin
        if not can_delete_claim and auth.person_id:
            can_delete_claim = AuthorizationService.check_permission(
                db, auth.person_id, "expense:claims:delete", org_id
            )

        context = _web_facade().base_context(request, auth, "Expense Claims", "claims")
        context.update(
            {
                "claims": claims,
                "search": search or "",
                "statuses": [status.value for status in ExpenseClaimStatus],
                "status_counts": counts,
                "filter_status": status or "",
                "filter_view": filter_view,
                "filter_start_date": start_date or "",
                "filter_end_date": end_date or "",
                "filter_employee_id": employee_id or "",
                "total": total or 0,
                "offset": offset,
                "limit": limit,
                "can_delete_claim": can_delete_claim,
                "active_filters": build_active_filters(
                    params={
                        "status": status,
                        "view": view,
                        "start_date": start_date,
                        "end_date": end_date,
                        "employee_id": employee_id,
                    },
                    labels={
                        "start_date": "From",
                        "end_date": "To",
                        "employee_id": "Employee",
                    },
                ),
            }
        )
        return _web_facade().templates.TemplateResponse(request, "expense/claims_list.html", context)

    @staticmethod
    def claim_detail_response(request: Request, auth, db, claim_id: str):
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        claim = (
            db.scalars(
                select(ExpenseClaim)
                .options(
                    joinedload(ExpenseClaim.items).joinedload(ExpenseClaimItem.category),
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
        can_submit = (auth.is_admin or can_approve) and claim.status == ExpenseClaimStatus.DRAFT
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

        paystack_enabled = resolve_value(db, SettingDomain.payments, "paystack_enabled")
        transfers_enabled = resolve_value(db, SettingDomain.payments, "paystack_transfers_enabled")
        has_active_payment = False
        if claim.status == ExpenseClaimStatus.APPROVED:
            from app.models.finance.payments.payment_intent import PaymentIntent, PaymentIntentStatus

            active_statuses = [PaymentIntentStatus.PENDING, PaymentIntentStatus.PROCESSING]
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

        can_paystack = (
            (auth.is_admin or can_approve)
            and bool(paystack_enabled)
            and bool(transfers_enabled)
            and claim.status == ExpenseClaimStatus.APPROVED
            and not has_active_payment
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

        context = _web_facade().base_context(request, auth, f"Claim {claim.claim_number}", "claims")
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
                "recent_activity": get_recent_activity_for_record(db, org_id, record=claim, limit=10),
                "categories": categories,
                "can_submit": can_submit,
                "can_act": can_act,
                "can_approve": can_approve,
                "can_reject": can_reject,
                "can_delete": can_delete,
                "can_paystack": can_paystack,
                "has_active_payment": has_active_payment,
                "action": request.query_params.get("action"),
                "error": request.query_params.get("error"),
            }
        )
        return _web_facade().templates.TemplateResponse(request, "expense/claim_detail.html", context)

    @staticmethod
    def add_claim_comment_response(claim_id: str, content: str, auth, db) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        comment_text = (content or "").strip()
        if not comment_text:
            return RedirectResponse(f"/expense/claims/{claim_id}?error=Comment+cannot+be+empty", status_code=303)
        if len(comment_text) > 5000:
            return RedirectResponse(f"/expense/claims/{claim_id}?error=Comment+is+too+long", status_code=303)

        claim = db.scalar(
            select(ExpenseClaim).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.claim_id == claim_uuid,
            )
        )
        if not claim:
            return RedirectResponse("/expense/claims/list?error=not_found", status_code=302)

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
        return RedirectResponse(f"/expense/claims/{claim_id}?action=comment_added", status_code=303)

    @staticmethod
    def claim_item_detail_response(request: Request, claim_id: str, item_id: str, auth, db):
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        item_uuid = coerce_uuid(item_id)

        item = (
            db.scalars(
                select(ExpenseClaimItem)
                .options(
                    joinedload(ExpenseClaimItem.category),
                    joinedload(ExpenseClaimItem.claim).joinedload(ExpenseClaim.employee),
                    joinedload(ExpenseClaimItem.claim).joinedload(ExpenseClaim.approver),
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

        context = _web_facade().base_context(request, auth, f"Expense Item {item.sequence or 0}", "claims")
        context.update(
            {
                "claim": claim,
                "item": item,
                "requested_approver": requested_approver,
                "error": request.query_params.get("error"),
            }
        )
        return _web_facade().templates.TemplateResponse(request, "expense/claim_item_detail.html", context)

    @classmethod
    def claim_receipt_response(cls, claim_id: str, item_id: str, auth, db, index: int = 0):
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
            return RedirectResponse(f"/expense/claims/{claim_id}?error=Receipt+not+found", status_code=303)

        receipt_urls = cls._parse_receipt_urls(item.receipt_url)
        if not receipt_urls or index < 0 or index >= len(receipt_urls):
            return RedirectResponse(f"/expense/claims/{claim_id}?error=Receipt+not+found", status_code=303)

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

            storage = get_storage()
            if not storage.exists(receipt_url):
                return RedirectResponse(f"/expense/claims/{claim_id}?error=Receipt+not+found", status_code=303)

            chunks, content_type, content_length = storage.stream(receipt_url)
            filename = cls._UNSAFE_FILENAME_RE.sub("_", receipt_url.split("/")[-1])
            headers: dict[str, str] = {"Content-Disposition": f'inline; filename="{filename}"'}
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
            return RedirectResponse(f"/expense/claims/{claim_id}?error=Receipt+not+found", status_code=303)
        except Exception:
            logger.warning(
                "Invalid receipt path for claim item",
                extra={"claim_id": claim_id, "item_id": item_id, "organization_id": str(org_id)},
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
            return RedirectResponse("/expense/claims/list?error=permission", status_code=302)

        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        svc = ExpenseService(db)
        try:
            result = svc.submit_claim(org_id, claim_uuid)
            if not result.success:
                return RedirectResponse(f"/expense/claims/{claim_id}?error=submit_in_progress", status_code=303)
            db.flush()
        except ExpenseClaimStatusError:
            return RedirectResponse(f"/expense/claims/{claim_id}?error=invalid_status", status_code=303)
        except ExpenseServiceError as exc:
            return RedirectResponse(f"/expense/claims/{claim_id}?error={quote(str(exc))}", status_code=303)
        except Exception:
            logging.getLogger(__name__).exception("Expense claim submit failed", extra={"claim_id": claim_id})
            return RedirectResponse(f"/expense/claims/{claim_id}?error=submit_failed", status_code=303)
        return RedirectResponse(f"/expense/claims/{claim_id}?action=submitted", status_code=303)

    @classmethod
    def approve_claim_response(cls, claim_id: str, auth, db, form_data: dict[str, str] | None = None) -> RedirectResponse:
        if not auth.has_any_permission(
            [
                "expense:claims:approve:tier1",
                "expense:claims:approve:tier2",
                "expense:claims:approve:tier3",
            ]
        ):
            return RedirectResponse("/expense/claims/list?error=permission", status_code=302)

        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        approver = db.scalars(
            select(Employee)
            .where(Employee.organization_id == org_id)
            .where(Employee.person_id == auth.person_id)
        ).first()
        approver_id = approver.employee_id if approver else None

        corrections = None
        approval_notes = None
        if form_data:
            approval_notes = (form_data.get("approval_notes") or "").strip() or None
            item_ids = form_data.getlist("item_id") if hasattr(form_data, "getlist") else []
            if item_ids:
                corrections = []
                for raw_item_id in item_ids:
                    item_id = str(raw_item_id).strip()
                    if not item_id:
                        continue
                    correction = {"item_id": item_id}
                    raw_amount = (form_data.get(f"approved_amount_{item_id}") or "").strip()
                    correction["approved_amount"] = Decimal(raw_amount) if raw_amount else Decimal("0")
                    category_id = (form_data.get(f"category_id_{item_id}") or "").strip()
                    if category_id:
                        correction["category_id"] = category_id
                    description = (form_data.get(f"description_{item_id}") or "").strip()
                    if description:
                        correction["description"] = description
                    corrections.append(correction)

        route_to_ap = bool(resolve_value(db, SettingDomain.expense, "expense_route_to_ap"))
        svc = ExpenseService(db)
        try:
            claim = svc.approve_claim(
                org_id,
                claim_uuid,
                approver_id=approver_id,
                corrections=corrections,
                notes=approval_notes,
                create_supplier_invoice=route_to_ap,
            )
            if claim.status not in {ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PENDING_APPROVAL}:
                db.rollback()
                return RedirectResponse(f"/expense/claims/{claim_id}?error=approve_in_progress", status_code=303)
            db.flush()
        except ApproverAuthorityError as exc:
            db.rollback()
            return RedirectResponse(f"/expense/claims/{claim_id}?error={quote(str(exc))}", status_code=303)
        except ExpenseLimitServiceError as exc:
            db.rollback()
            return RedirectResponse(f"/expense/claims/{claim_id}?error={quote(str(exc))}", status_code=303)
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(f"/expense/claims/{claim_id}?error=invalid_status", status_code=303)
        except ExpenseServiceError as exc:
            db.rollback()
            return RedirectResponse(f"/expense/claims/{claim_id}?error={quote(str(exc))}", status_code=303)
        except Exception:
            db.rollback()
            logging.getLogger(__name__).exception("Expense claim approval failed", extra={"claim_id": claim_id})
            return RedirectResponse(f"/expense/claims/{claim_id}?error=approve_failed", status_code=303)

        action = "approved" if claim.status == ExpenseClaimStatus.APPROVED else "approval_recorded"
        return RedirectResponse(f"/expense/claims/{claim_id}?action={action}", status_code=303)

    @staticmethod
    def reject_claim_response(claim_id: str, reason: str | None, auth, db) -> RedirectResponse:
        if not auth.has_any_permission(
            [
                "expense:claims:approve:tier1",
                "expense:claims:approve:tier2",
                "expense:claims:approve:tier3",
            ]
        ):
            return RedirectResponse("/expense/claims/list?error=permission", status_code=302)

        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
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
            )
            if claim.status != ExpenseClaimStatus.REJECTED:
                db.rollback()
                return RedirectResponse(f"/expense/claims/{claim_id}?error=reject_in_progress", status_code=303)
            db.flush()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(f"/expense/claims/{claim_id}?error=invalid_status", status_code=303)
        except Exception:
            db.rollback()
            logging.getLogger(__name__).exception("Expense claim rejection failed", extra={"claim_id": claim_id})
            return RedirectResponse(f"/expense/claims/{claim_id}?error=reject_failed", status_code=303)

        return RedirectResponse(f"/expense/claims/{claim_id}?action=rejected", status_code=303)

    @staticmethod
    def cancel_claim_response(claim_id: str, reason: str | None, auth, db) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        try:
            claim = svc.cancel_claim(org_id, coerce_uuid(claim_id), reason=(reason or "").strip() or None)
            if claim.status != ExpenseClaimStatus.CANCELLED:
                db.rollback()
                return RedirectResponse(f"/expense/claims/{claim_id}?error=cancel_in_progress", status_code=303)
            db.flush()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(f"/expense/claims/{claim_id}?error=invalid_status", status_code=303)
        except Exception:
            db.rollback()
            logger.exception("Expense claim cancellation failed", extra={"claim_id": claim_id})
            return RedirectResponse(f"/expense/claims/{claim_id}?error=cancel_failed", status_code=303)
        return RedirectResponse(f"/expense/claims/{claim_id}?action=cancelled", status_code=303)

    @staticmethod
    def resubmit_claim_response(claim_id: str, auth, db) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        try:
            claim = svc.resubmit_claim(org_id, coerce_uuid(claim_id))
            if claim.status != ExpenseClaimStatus.DRAFT:
                db.rollback()
                return RedirectResponse(f"/expense/claims/{claim_id}?error=resubmit_failed", status_code=303)
            db.flush()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(f"/expense/claims/{claim_id}?error=invalid_status", status_code=303)
        except Exception:
            db.rollback()
            logger.exception("Expense claim resubmission failed", extra={"claim_id": claim_id})
            return RedirectResponse(f"/expense/claims/{claim_id}?error=resubmit_failed", status_code=303)
        return RedirectResponse(f"/expense/claims/{claim_id}?action=resubmitted", status_code=303)

    @staticmethod
    def delete_claim_response(claim_id: str, auth, db) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        try:
            svc.delete_claim(org_id, coerce_uuid(claim_id))
            db.flush()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(f"/expense/claims/{claim_id}?error=invalid_status", status_code=303)
        except Exception:
            db.rollback()
            logger.exception("Expense claim delete failed", extra={"claim_id": claim_id})
            return RedirectResponse(f"/expense/claims/{claim_id}?error=delete_failed", status_code=303)
        return RedirectResponse("/expense/claims/list?saved=1", status_code=303)
