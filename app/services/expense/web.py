"""
Expense claims and category web view service.

Provides view-focused data and operations for expense claim-related web routes.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from datetime import date as date_type
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
)
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.domain_settings import SettingDomain
from app.models.expense.expense_claim import (
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.models.people.hr.employee import Employee
from app.services.common import PaginationParams, coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.expense.expense_service import (
    ApproverAuthorityError,
    ExpenseClaimStatusError,
    ExpenseService,
    ExpenseServiceError,
)
from app.services.file_upload import get_expense_receipt_upload, resolve_safe_path
from app.services.finance.platform.authorization import AuthorizationService
from app.services.pm.comment import comment_service
from app.services.recent_activity import get_recent_activity_for_record
from app.services.settings_spec import resolve_value
from app.services.storage import get_storage
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class ExpenseClaimsWebService:
    """Web service methods for expense claims, categories, and reports."""

    # Characters unsafe in Content-Disposition filenames
    _UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f\x7f"\\]')

    @staticmethod
    def _form_str(form: Any, key: str) -> str:
        value = form.get(key)
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _resolve_claim_receipt_path(receipt_url: str) -> Path:
        upload_base = get_expense_receipt_upload().base_path
        raw = receipt_url.strip()
        candidate = Path(raw)

        if candidate.is_absolute():
            resolved = candidate.resolve(strict=True)
            if resolved != upload_base and upload_base not in resolved.parents:
                raise ValueError("Receipt path is outside configured upload directory")
            return resolved

        return resolve_safe_path(upload_base, raw).resolve(strict=True)

    @staticmethod
    def _parse_receipt_urls(receipt_url: str | None) -> list[str]:
        if not receipt_url:
            return []
        raw = receipt_url.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                decoded = json.loads(raw)
            except Exception:
                return [raw]
            if isinstance(decoded, list):
                return [str(entry).strip() for entry in decoded if str(entry).strip()]
        return [raw]

    @staticmethod
    def claims_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        view: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        employee_id = coerce_uuid(auth.employee_id)
        filter_view = "submitted_to_me" if view == "submitted_to_me" else "all"
        status_value = None
        if status:
            try:
                status_value = ExpenseClaimStatus(status)
            except ValueError:
                status_value = None

        start = date_type.fromisoformat(start_date) if start_date else None
        end = date_type.fromisoformat(end_date) if end_date else None

        stmt = (
            select(ExpenseClaim)
            .options(joinedload(ExpenseClaim.employee))
            .where(ExpenseClaim.organization_id == org_id)
        )
        if filter_view == "submitted_to_me":
            if employee_id:
                stmt = stmt.where(
                    or_(
                        ExpenseClaim.requested_approver_id == employee_id,
                        and_(
                            ExpenseClaim.requested_approver_id.is_(None),
                            ExpenseClaim.approver_id == employee_id,
                        ),
                    )
                )
            else:
                stmt = stmt.where(false())
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
                stmt.order_by(ExpenseClaim.claim_date.desc())
                .offset(offset)
                .limit(limit)
            )
            .unique()
            .all()
        )

        status_rows = db.execute(
            select(ExpenseClaim.status, func.count())
            .where(ExpenseClaim.organization_id == org_id)
            .group_by(ExpenseClaim.status)
        ).all()
        status_counts: dict[ExpenseClaimStatus | None, int] = {
            row[0]: row[1] for row in status_rows
        }
        counts = {s.value if s else "UNKNOWN": c for s, c in status_counts.items()}
        can_delete_claim = auth.is_admin
        if not can_delete_claim and auth.person_id:
            can_delete_claim = AuthorizationService.check_permission(
                db, auth.person_id, "expense:claims:delete", org_id
            )

        context = base_context(request, auth, "Expense Claims", "claims")
        active_filters = build_active_filters(
            params={
                "status": status,
                "view": view,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
        )
        context.update(
            {
                "claims": claims,
                "search": search or "",
                "statuses": [s.value for s in ExpenseClaimStatus],
                "status_counts": counts,
                "filter_status": status or "",
                "filter_view": filter_view,
                "filter_start_date": start_date or "",
                "filter_end_date": end_date or "",
                "total": total or 0,
                "offset": offset,
                "limit": limit,
                "can_delete_claim": can_delete_claim,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(request, "expense/claims_list.html", context)

    @staticmethod
    def claim_detail_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        claim_id: str,
    ) -> HTMLResponse | RedirectResponse:
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
                .where(ExpenseClaim.organization_id == org_id)
                .where(ExpenseClaim.claim_id == claim_uuid)
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
        paystack_enabled = resolve_value(db, SettingDomain.payments, "paystack_enabled")
        transfers_enabled = resolve_value(
            db, SettingDomain.payments, "paystack_transfers_enabled"
        )

        # Check for existing active payment intent to prevent duplicate payments
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

        can_paystack = (
            (auth.is_admin or can_approve)
            and bool(paystack_enabled)
            and bool(transfers_enabled)
            and claim.status == ExpenseClaimStatus.APPROVED
            and not has_active_payment
        )

        # Load active categories for the review-mode dropdown
        categories: list = []
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

        context = base_context(request, auth, f"Claim {claim.claim_number}", "claims")
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
                    db,
                    org_id,
                    record=claim,
                    limit=10,
                ),
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
        return templates.TemplateResponse(request, "expense/claim_detail.html", context)

    @staticmethod
    def add_claim_comment_response(
        claim_id: str,
        content: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Add a comment on an expense claim."""
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
                f"/expense/claims/{claim_id}?error=Comment+is+too+long",
                status_code=303,
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
        db.commit()
        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=comment_added",
            status_code=303,
        )

    @staticmethod
    def claim_item_detail_response(
        request: Request,
        claim_id: str,
        item_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
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

        context = base_context(
            request,
            auth,
            f"Expense Item {item.sequence or 0}",
            "claims",
        )
        context.update(
            {
                "claim": claim,
                "item": item,
                "requested_approver": requested_approver,
                "error": request.query_params.get("error"),
            }
        )
        return templates.TemplateResponse(
            request, "expense/claim_item_detail.html", context
        )

    @staticmethod
    def claim_receipt_response(
        claim_id: str,
        item_id: str,
        auth: WebAuthContext,
        db: Session,
        index: int = 0,
    ) -> FileResponse | RedirectResponse:
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
                f"/expense/claims/{claim_id}?error=Receipt+not+found",
                status_code=303,
            )
        receipt_urls = ExpenseClaimsWebService._parse_receipt_urls(item.receipt_url)
        if not receipt_urls or index < 0 or index >= len(receipt_urls):
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=Receipt+not+found",
                status_code=303,
            )
        receipt_url = receipt_urls[index]

        parsed = urlparse(receipt_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return RedirectResponse(receipt_url, status_code=302)

        # S3-backed receipts are stored as object keys like:
        #   expense_receipts/{org_id}/{filename}
        # Stream via app so the user doesn't need direct MinIO access.
        org_prefix = f"expense_receipts/{org_id}/"
        if receipt_url.startswith("expense_receipts/"):
            # Defensive: allow case differences in UUID string formatting, but do NOT
            # relax the org scoping requirement.
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
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=Receipt+not+found",
                    status_code=303,
                )

            chunks, content_type, content_length = storage.stream(receipt_url)
            filename = Path(receipt_url).name
            safe_name = ExpenseClaimsWebService._UNSAFE_FILENAME_RE.sub("_", filename)

            headers: dict[str, str] = {}
            if content_length is not None:
                headers["Content-Length"] = str(content_length)
            # Render in browser where possible (PDF/images)
            headers["Content-Disposition"] = f'inline; filename="{safe_name}"'

            return StreamingResponse(
                chunks,
                media_type=content_type or "application/octet-stream",
                headers=headers,
            )

        try:
            receipt_path = ExpenseClaimsWebService._resolve_claim_receipt_path(
                receipt_url
            )
        except FileNotFoundError:
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=Receipt+not+found",
                status_code=303,
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

        media_type = mimetypes.guess_type(receipt_path.name)[0]
        return FileResponse(
            path=str(receipt_path),
            media_type=media_type or "application/octet-stream",
            filename=receipt_path.name,
            content_disposition_type="inline",
        )

    @staticmethod
    def submit_claim_response(
        claim_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
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
        svc = ExpenseService(db)

        try:
            result = svc.submit_claim(org_id, claim_uuid)
            if not result.success:
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=submit_in_progress",
                    status_code=303,
                )
            db.commit()
        except ExpenseClaimStatusError:
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except ExpenseServiceError as exc:
            message = quote(str(exc))
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error={message}", status_code=303
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Expense claim submit failed",
                extra={"claim_id": claim_id},
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=submit_failed", status_code=303
            )

        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=submitted", status_code=303
        )

    @staticmethod
    def approve_claim_response(
        claim_id: str,
        auth: WebAuthContext,
        db: Session,
        form_data: dict[str, str] | None = None,
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
        approver = db.scalars(
            select(Employee)
            .where(Employee.organization_id == org_id)
            .where(Employee.person_id == auth.person_id)
        ).first()
        approver_id = approver.employee_id if approver else None

        # Parse corrections from form data
        corrections: list[dict] | None = None
        approval_notes: str | None = None
        if form_data:
            approval_notes = (form_data.get("approval_notes") or "").strip() or None
            item_ids = (
                form_data.getlist("item_id") if hasattr(form_data, "getlist") else []
            )
            if item_ids:
                corrections = []
                for iid in item_ids:
                    iid_str = str(iid).strip()
                    if not iid_str:
                        continue
                    corr: dict = {"item_id": iid_str}
                    raw_amt = (
                        form_data.get(f"approved_amount_{iid_str}") or ""
                    ).strip()
                    corr["approved_amount"] = (
                        Decimal(raw_amt) if raw_amt else Decimal("0")
                    )
                    cat = (form_data.get(f"category_id_{iid_str}") or "").strip()
                    if cat:
                        corr["category_id"] = cat
                    desc = (form_data.get(f"description_{iid_str}") or "").strip()
                    if desc:
                        corr["description"] = desc
                    corrections.append(corr)

        svc = ExpenseService(db)
        try:
            claim = svc.approve_claim(
                org_id,
                claim_uuid,
                approver_id=approver_id,
                corrections=corrections,
                notes=approval_notes,
            )
            if claim.status != ExpenseClaimStatus.APPROVED:
                db.rollback()
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=approve_in_progress",
                    status_code=303,
                )
            db.commit()
        except ApproverAuthorityError as exc:
            db.rollback()
            message = quote(str(exc))
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error={message}",
                status_code=303,
            )
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except Exception:
            db.rollback()
            logging.getLogger(__name__).exception(
                "Expense claim approval failed",
                extra={"claim_id": claim_id},
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=approve_failed", status_code=303
            )

        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=approved", status_code=303
        )

    @staticmethod
    def reject_claim_response(
        claim_id: str,
        reason: str | None,
        auth: WebAuthContext,
        db: Session,
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
        approver = db.scalars(
            select(Employee)
            .where(Employee.organization_id == org_id)
            .where(Employee.person_id == auth.person_id)
        ).first()
        approver_id = approver.employee_id if approver else None

        svc = ExpenseService(db)
        rejection_reason = (reason or "").strip() or "Rejected"
        try:
            claim = svc.reject_claim(
                org_id,
                claim_uuid,
                approver_id=approver_id,
                reason=rejection_reason,
            )
            if claim.status != ExpenseClaimStatus.REJECTED:
                db.rollback()
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=reject_in_progress",
                    status_code=303,
                )
            db.commit()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except Exception:
            db.rollback()
            logging.getLogger(__name__).exception(
                "Expense claim rejection failed",
                extra={"claim_id": claim_id},
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=reject_failed", status_code=303
            )

        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=rejected", status_code=303
        )

    @staticmethod
    def cancel_claim_response(
        claim_id: str,
        reason: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Cancel an expense claim (DRAFT or SUBMITTED only)."""
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)

        svc = ExpenseService(db)
        try:
            claim = svc.cancel_claim(
                org_id,
                claim_uuid,
                reason=(reason or "").strip() or None,
            )
            if claim.status != ExpenseClaimStatus.CANCELLED:
                db.rollback()
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=cancel_in_progress",
                    status_code=303,
                )
            db.commit()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Expense claim cancellation failed",
                extra={"claim_id": claim_id},
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=cancel_failed", status_code=303
            )

        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=cancelled", status_code=303
        )

    @staticmethod
    def resubmit_claim_response(
        claim_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Resubmit a rejected expense claim (resets to DRAFT)."""
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)

        svc = ExpenseService(db)
        try:
            claim = svc.resubmit_claim(org_id, claim_uuid)
            if claim.status != ExpenseClaimStatus.DRAFT:
                db.rollback()
                return RedirectResponse(
                    f"/expense/claims/{claim_id}?error=resubmit_failed",
                    status_code=303,
                )
            db.commit()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Expense claim resubmission failed",
                extra={"claim_id": claim_id},
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=resubmit_failed", status_code=303
            )

        return RedirectResponse(
            f"/expense/claims/{claim_id}?action=resubmitted", status_code=303
        )

    @staticmethod
    def delete_claim_response(
        claim_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Delete a draft expense claim."""
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        svc = ExpenseService(db)

        try:
            svc.delete_claim(org_id, claim_uuid)
            db.commit()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=invalid_status", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Expense claim delete failed",
                extra={"claim_id": claim_id},
            )
            return RedirectResponse(
                f"/expense/claims/{claim_id}?error=delete_failed", status_code=303
            )

        return RedirectResponse("/expense/claims/list?saved=1", status_code=303)

    @staticmethod
    def categories_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        is_active: str | None,
        page: int,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        is_active_value: bool | None = None
        if isinstance(is_active, str):
            lowered = is_active.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                is_active_value = True
            elif lowered in {"false", "0", "no", "off"}:
                is_active_value = False

        pagination = PaginationParams.from_page(page, 20)
        result = svc.list_categories(
            org_id,
            search=search,
            is_active=is_active_value,
            pagination=pagination,
        )

        context = base_context(request, auth, "Expense Categories", "categories")
        context.update(
            {
                "categories": result.items,
                "search": search or "",
                "is_active": is_active_value,
                "page": page,
                "total_pages": result.total_pages,
                "total": result.total,
                "limit": pagination.limit,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(request, "expense/categories.html", context)

    @staticmethod
    def new_category_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        org_id = coerce_uuid(auth.organization_id)

        expense_accounts = db.scalars(
            select(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
        ).all()

        context = base_context(request, auth, "New Expense Category", "categories")
        context.update(
            {
                "category": None,
                "expense_accounts": expense_accounts,
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "expense/category_form.html", context
        )

    @staticmethod
    async def create_category_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        category_code = ExpenseClaimsWebService._form_str(form, "category_code")
        category_name = ExpenseClaimsWebService._form_str(form, "category_name")
        description = ExpenseClaimsWebService._form_str(form, "description")
        expense_account_id = ExpenseClaimsWebService._form_str(
            form, "expense_account_id"
        )
        max_amount = ExpenseClaimsWebService._form_str(form, "max_amount_per_claim")
        requires_receipt = ExpenseClaimsWebService._form_str(
            form, "requires_receipt"
        ) in {"1", "true", "on", "yes"}
        is_active = ExpenseClaimsWebService._form_str(form, "is_active") in {
            "1",
            "true",
            "on",
            "yes",
        }

        errors = {}
        if not category_code:
            errors["category_code"] = "Required"
        if not category_name:
            errors["category_name"] = "Required"

        max_amount_value = None
        if max_amount:
            try:
                max_amount_value = Decimal(max_amount)
            except Exception:
                errors["max_amount_per_claim"] = "Invalid amount"

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        if errors:
            expense_accounts = db.scalars(
                select(Account)
                .join(
                    AccountCategory,
                    Account.category_id == AccountCategory.category_id,
                )
                .where(
                    Account.organization_id == org_id,
                    AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                    Account.is_active.is_(True),
                    AccountCategory.is_active.is_(True),
                )
                .order_by(Account.account_code)
            ).all()
            context = base_context(request, auth, "New Expense Category", "categories")
            context.update(
                {
                    "category": {
                        "category_code": category_code,
                        "category_name": category_name,
                        "description": description,
                        "expense_account_id": expense_account_id,
                        "max_amount_per_claim": max_amount,
                        "requires_receipt": requires_receipt,
                        "is_active": is_active,
                    },
                    "expense_accounts": expense_accounts,
                    "errors": errors,
                }
            )
            return templates.TemplateResponse(
                request, "expense/category_form.html", context
            )

        try:
            svc.create_category(
                org_id,
                category_code=category_code,
                category_name=category_name,
                description=description or None,
                expense_account_id=coerce_uuid(expense_account_id)
                if expense_account_id
                else None,
                max_amount_per_claim=max_amount_value,
                requires_receipt=requires_receipt if requires_receipt else False,
                is_active=is_active if is_active else False,
            )
            db.commit()
        except ExpenseServiceError as exc:
            db.rollback()
            expense_accounts = db.scalars(
                select(Account)
                .join(
                    AccountCategory,
                    Account.category_id == AccountCategory.category_id,
                )
                .where(
                    Account.organization_id == org_id,
                    AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                    Account.is_active.is_(True),
                    AccountCategory.is_active.is_(True),
                )
                .order_by(Account.account_code)
            ).all()
            context = base_context(request, auth, "New Expense Category", "categories")
            context.update(
                {
                    "category": {
                        "category_code": category_code,
                        "category_name": category_name,
                        "description": description,
                        "expense_account_id": expense_account_id,
                        "max_amount_per_claim": max_amount,
                        "requires_receipt": requires_receipt,
                        "is_active": is_active,
                    },
                    "expense_accounts": expense_accounts,
                    "errors": {"_": str(exc)},
                }
            )
            return templates.TemplateResponse(
                request, "expense/category_form.html", context
            )

        return RedirectResponse(
            url="/expense/categories?success=Record+saved+successfully", status_code=303
        )

    @staticmethod
    def edit_category_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        category_id: str,
    ) -> HTMLResponse:
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        category = svc.get_category(org_id, coerce_uuid(category_id))

        expense_accounts = db.scalars(
            select(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
        ).all()

        context = base_context(request, auth, "Edit Expense Category", "categories")
        context.update(
            {
                "category": category,
                "expense_accounts": expense_accounts,
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "expense/category_form.html", context
        )

    @staticmethod
    async def update_category_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        category_id: str,
    ) -> HTMLResponse | RedirectResponse:
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        category_code = ExpenseClaimsWebService._form_str(form, "category_code")
        category_name = ExpenseClaimsWebService._form_str(form, "category_name")
        description = ExpenseClaimsWebService._form_str(form, "description")
        expense_account_id = ExpenseClaimsWebService._form_str(
            form, "expense_account_id"
        )
        max_amount = ExpenseClaimsWebService._form_str(form, "max_amount_per_claim")
        requires_receipt = ExpenseClaimsWebService._form_str(
            form, "requires_receipt"
        ) in {"1", "true", "on", "yes"}
        is_active = ExpenseClaimsWebService._form_str(form, "is_active") in {
            "1",
            "true",
            "on",
            "yes",
        }

        errors = {}
        if not category_code:
            errors["category_code"] = "Required"
        if not category_name:
            errors["category_name"] = "Required"

        max_amount_value = None
        if max_amount:
            try:
                max_amount_value = Decimal(max_amount)
            except Exception:
                errors["max_amount_per_claim"] = "Invalid amount"

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        if errors:
            expense_accounts = db.scalars(
                select(Account)
                .join(
                    AccountCategory,
                    Account.category_id == AccountCategory.category_id,
                )
                .where(
                    Account.organization_id == org_id,
                    AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                    Account.is_active.is_(True),
                    AccountCategory.is_active.is_(True),
                )
                .order_by(Account.account_code)
            ).all()
            context = base_context(request, auth, "Edit Expense Category", "categories")
            context.update(
                {
                    "category": {
                        "category_id": category_id,
                        "category_code": category_code,
                        "category_name": category_name,
                        "description": description,
                        "expense_account_id": expense_account_id,
                        "max_amount_per_claim": max_amount,
                        "requires_receipt": requires_receipt,
                        "is_active": is_active,
                    },
                    "expense_accounts": expense_accounts,
                    "errors": errors,
                }
            )
            return templates.TemplateResponse(
                request, "expense/category_form.html", context
            )

        svc.update_category(
            org_id,
            coerce_uuid(category_id),
            category_code=category_code,
            category_name=category_name,
            description=description or None,
            expense_account_id=coerce_uuid(expense_account_id)
            if expense_account_id
            else None,
            max_amount_per_claim=max_amount_value,
            requires_receipt=requires_receipt,
            is_active=is_active,
        )
        db.commit()

        return RedirectResponse(
            url="/expense/categories?success=Record+saved+successfully", status_code=303
        )

    @staticmethod
    def delete_category_response(
        category_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        svc.update_category(org_id, coerce_uuid(category_id), is_active=False)
        db.commit()
        return RedirectResponse(
            url="/expense/categories?success=Record+deleted+successfully",
            status_code=303,
        )

    @staticmethod
    def expense_summary_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: str | None,
        end_date: str | None,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        parsed_start = date_type.fromisoformat(start_date) if start_date else None
        parsed_end = date_type.fromisoformat(end_date) if end_date else None

        report_data = svc.get_expense_summary_report(
            org_id,
            start_date=parsed_start,
            end_date=parsed_end,
        )

        context = base_context(request, auth, "Expense Summary Report", "expense")
        context.update(
            {
                "report": report_data,
                "start_date": start_date or report_data["start_date"].isoformat(),
                "end_date": end_date or report_data["end_date"].isoformat(),
            }
        )
        return templates.TemplateResponse(
            request, "expense/reports/summary.html", context
        )

    @staticmethod
    def expense_by_category_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: str | None,
        end_date: str | None,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        parsed_start = date_type.fromisoformat(start_date) if start_date else None
        parsed_end = date_type.fromisoformat(end_date) if end_date else None

        report_data = svc.get_expense_by_category_report(
            org_id,
            start_date=parsed_start,
            end_date=parsed_end,
        )

        context = base_context(request, auth, "Expense by Category Report", "expense")
        context.update(
            {
                "report": report_data,
                "start_date": start_date or report_data["start_date"].isoformat(),
                "end_date": end_date or report_data["end_date"].isoformat(),
            }
        )
        return templates.TemplateResponse(
            request, "expense/reports/by_category.html", context
        )

    @staticmethod
    def expense_by_employee_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: str | None,
        end_date: str | None,
        department_id: str | None,
    ) -> HTMLResponse:
        from app.services.people.hr import DepartmentFilters, OrganizationService

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        org_svc = OrganizationService(db, org_id)

        parsed_start = date_type.fromisoformat(start_date) if start_date else None
        parsed_end = date_type.fromisoformat(end_date) if end_date else None
        parsed_dept = coerce_uuid(department_id) if department_id else None

        report_data = svc.get_expense_by_employee_report(
            org_id,
            start_date=parsed_start,
            end_date=parsed_end,
            department_id=parsed_dept,
        )

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        context = base_context(request, auth, "Expense by Employee Report", "expense")
        context.update(
            {
                "report": report_data,
                "departments": departments,
                "start_date": start_date or report_data["start_date"].isoformat(),
                "end_date": end_date or report_data["end_date"].isoformat(),
                "department_id": department_id,
            }
        )
        return templates.TemplateResponse(
            request, "expense/reports/by_employee.html", context
        )

    @staticmethod
    def expense_trends_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        months: int,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        report_data = svc.get_expense_trends_report(org_id, months=months)

        context = base_context(request, auth, "Expense Trends Report", "expense")
        context.update(
            {
                "report": report_data,
                "months": months,
            }
        )
        return templates.TemplateResponse(
            request, "expense/reports/trends.html", context
        )

    @staticmethod
    def cash_advances_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None,
        page: int,
    ) -> HTMLResponse:
        from app.models.expense.cash_advance import CashAdvanceStatus

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        pagination = PaginationParams.from_page(page, 20)
        status_filter = None
        if status:
            try:
                status_filter = CashAdvanceStatus(status)
            except ValueError:
                pass

        result = svc.list_advances(
            org_id,
            status=status_filter,
            pagination=pagination,
        )

        context = base_context(request, auth, "Cash Advances", "advances")
        active_filters = build_active_filters(params={"status": status})
        context.update(
            {
                "advances": result.items,
                "status": status,
                "statuses": [s.value for s in CashAdvanceStatus],
                "page": page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request, "expense/advances/list.html", context
        )

    @staticmethod
    def cash_advance_detail_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        advance_id: str,
    ) -> HTMLResponse:
        from app.models.expense.cash_advance import CashAdvanceStatus
        from app.models.finance.banking.bank_account import (
            BankAccount,
            BankAccountStatus,
        )

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        try:
            advance = svc.get_advance(org_id, coerce_uuid(advance_id))
        except Exception:
            context = base_context(request, auth, "Cash Advance", "advances")
            context["advance"] = None
            context["error"] = "Advance not found"
            return templates.TemplateResponse(
                request, "expense/advances/detail.html", context
            )

        bank_accounts = list(
            db.scalars(
                select(BankAccount)
                .where(
                    BankAccount.organization_id == org_id,
                    BankAccount.status == BankAccountStatus.active,
                )
                .order_by(BankAccount.account_name)
            ).all()
        )

        linked_claims = []
        if advance.status in [
            CashAdvanceStatus.DISBURSED,
            CashAdvanceStatus.PARTIALLY_SETTLED,
        ]:
            claims = svc.list_claims(
                org_id,
                employee_id=advance.employee_id,
                pagination=PaginationParams(offset=0, limit=20),
            )
            linked_claims = [
                c for c in claims.items if c.cash_advance_id == advance.advance_id
            ]

        context = base_context(
            request, auth, f"Advance {advance.advance_number}", "advances"
        )
        context.update(
            {
                "advance": advance,
                "bank_accounts": bank_accounts,
                "linked_claims": linked_claims,
                "can_disburse": advance.status == CashAdvanceStatus.APPROVED,
                "can_settle": advance.status
                in [CashAdvanceStatus.DISBURSED, CashAdvanceStatus.PARTIALLY_SETTLED],
            }
        )
        return templates.TemplateResponse(
            request, "expense/advances/detail.html", context
        )

    @staticmethod
    def disburse_cash_advance_response(
        advance_id: str,
        bank_account_id: str,
        payment_mode: str,
        payment_reference: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        from app.tasks.expense import post_cash_advance_disbursement

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        try:
            advance = svc.disburse_advance(
                org_id,
                coerce_uuid(advance_id),
                payment_reference=payment_reference,
            )
            db.commit()

            post_cash_advance_disbursement.delay(
                organization_id=str(org_id),
                advance_id=str(advance.advance_id),
                user_id=str(auth.user_id),
                bank_account_id=bank_account_id,
            )
        except ExpenseServiceError:
            db.rollback()

        return RedirectResponse(f"/expense/advances/{advance_id}", status_code=303)

    @staticmethod
    def settle_cash_advance_response(
        advance_id: str,
        claim_id: str,
        settlement_amount: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        from app.tasks.expense import settle_cash_advance_with_claim

        org_id = coerce_uuid(auth.organization_id)
        try:
            settle_cash_advance_with_claim.delay(
                organization_id=str(org_id),
                advance_id=advance_id,
                claim_id=claim_id,
                user_id=str(auth.user_id),
                settlement_amount=settlement_amount,
            )
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(f"/expense/advances/{advance_id}", status_code=303)


expense_claims_web_service = ExpenseClaimsWebService()
