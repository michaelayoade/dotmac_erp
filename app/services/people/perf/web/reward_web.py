"""
PMS Rewards and Recognition Web Service.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.perf.appraisal_cycle import AppraisalCycle
from app.models.people.perf.pms_enums import OutcomeActionStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.perf.reward_service import PMSRewardService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import parse_uuid

logger = logging.getLogger(__name__)


class RewardWebService:
    """Web service for PMS rewards/recognition pages."""

    @staticmethod
    def _text(value: object | None) -> str:
        if isinstance(value, str):
            return value.strip()
        return ""

    def rewards_hub_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        status: str | None = None,
        cycle_id: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render rewards nomination and approval page."""
        org_id = coerce_uuid(auth.organization_id)
        cycle_uuid = parse_uuid(cycle_id)
        reward_status = None
        if status:
            try:
                reward_status = OutcomeActionStatus(status)
            except ValueError:
                reward_status = None

        svc = PMSRewardService(db)
        candidate_rows = svc.list_reward_candidates_with_eligibility(
            org_id,
            cycle_id=cycle_uuid,
            limit=200,
        )
        actions = svc.list_reward_actions(
            org_id,
            status=reward_status,
            cycle_id=cycle_uuid,
            pagination=PaginationParams.from_page(page, per_page=20),
        )
        cycles = list(
            db.scalars(
                select(AppraisalCycle)
                .where(AppraisalCycle.organization_id == org_id)
                .order_by(AppraisalCycle.start_date.desc())
            ).all()
        )

        context = base_context(request, auth, "Rewards and Recognition", "pms-rewards", db=db)
        context.update(
            {
                "candidate_rows": candidate_rows,
                "actions": actions.items,
                "status": status,
                "statuses": [s.value for s in OutcomeActionStatus],
                "cycle_id": cycle_id,
                "cycles": cycles,
                "page": actions.page,
                "total_pages": actions.total_pages,
                "total": actions.total,
                "has_prev": actions.has_prev,
                "has_next": actions.has_next,
                "saved": request.query_params.get("saved"),
                "error": request.query_params.get("error"),
            }
        )
        return templates.TemplateResponse(request, "people/perf/pms/rewards.html", context)

    async def nominate_reward_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Nominate an appraisal for reward."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSRewardService(db)

        try:
            appraisal_id = parse_uuid(self._text(form_data.get("appraisal_id")))
            reward_type = self._text(form_data.get("reward_type"))
            if appraisal_id is None:
                raise ValueError("Appraisal is required")
            if not reward_type:
                raise ValueError("Reward type is required")

            svc.nominate_reward(
                org_id,
                appraisal_id=appraisal_id,
                reward_type=reward_type,
                nomination_notes=self._text(form_data.get("nomination_notes")) or None,
                nominated_by_person_id=coerce_uuid(auth.person_id)
                if auth.person_id
                else None,
            )
            db.commit()
            return RedirectResponse("/people/perf/pms/rewards?saved=1", status_code=303)
        except Exception as exc:
            db.rollback()
            logger.exception("Failed reward nomination")
            return RedirectResponse(
                f"/people/perf/pms/rewards?error={str(exc)}",
                status_code=303,
            )

    async def approve_reward_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        action_id: str,
    ) -> RedirectResponse:
        """Approve pending reward action."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSRewardService(db)
        try:
            action_uuid = coerce_uuid(action_id)
            svc.approve_reward(
                org_id,
                action_uuid,
                approved_by_employee_id=auth.employee_id,
                approved_by_person_id=coerce_uuid(auth.person_id)
                if auth.person_id
                else None,
                approval_notes=self._text(form_data.get("approval_notes")) or None,
            )
            db.commit()
            return RedirectResponse("/people/perf/pms/rewards?saved=1", status_code=303)
        except Exception as exc:
            db.rollback()
            logger.exception("Failed reward approval")
            return RedirectResponse(
                f"/people/perf/pms/rewards?error={str(exc)}",
                status_code=303,
            )

    async def cancel_reward_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        action_id: str,
    ) -> RedirectResponse:
        """Cancel pending reward action."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSRewardService(db)
        try:
            action_uuid = coerce_uuid(action_id)
            svc.cancel_reward(
                org_id,
                action_uuid,
                cancelled_by_person_id=coerce_uuid(auth.person_id)
                if auth.person_id
                else None,
                cancellation_notes=self._text(form_data.get("cancellation_notes"))
                or None,
            )
            db.commit()
            return RedirectResponse("/people/perf/pms/rewards?saved=1", status_code=303)
        except Exception as exc:
            db.rollback()
            logger.exception("Failed reward cancellation")
            return RedirectResponse(
                f"/people/perf/pms/rewards?error={str(exc)}",
                status_code=303,
            )


reward_web_service = RewardWebService()
