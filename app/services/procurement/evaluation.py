"""
Bid Evaluation Service.

Business logic for bid evaluation and scoring.
"""

import logging
from datetime import datetime
from decimal import Decimal
from datetime import timezone
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.procurement.bid_evaluation import BidEvaluation
from app.models.procurement.bid_evaluation_score import BidEvaluationScore
from app.models.procurement.enums import EvaluationStatus
from app.schemas.procurement.evaluation import EvaluationCreate, EvaluationScoreCreate
from app.services.common import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


class BidEvaluationService:
    """Service for bid evaluation management."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(
        self,
        organization_id: UUID,
        evaluation_id: UUID,
    ) -> Optional[BidEvaluation]:
        """Get an evaluation by ID."""
        stmt = select(BidEvaluation).where(
            BidEvaluation.organization_id == organization_id,
            BidEvaluation.evaluation_id == evaluation_id,
        )
        return self.db.scalar(stmt)

    def list_evaluations(
        self,
        organization_id: UUID,
        *,
        rfq_id: Optional[UUID] = None,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 25,
    ) -> Tuple[List[BidEvaluation], int]:
        """List evaluations with filters."""
        base = select(BidEvaluation).where(
            BidEvaluation.organization_id == organization_id,
        )
        if rfq_id:
            base = base.where(BidEvaluation.rfq_id == rfq_id)
        if status:
            base = base.where(BidEvaluation.status == EvaluationStatus(status))

        total = self.db.scalar(select(func.count()).select_from(base.subquery()))
        items = list(
            self.db.scalars(
                base.order_by(BidEvaluation.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return items, total or 0

    def create(
        self,
        organization_id: UUID,
        data: EvaluationCreate,
    ) -> BidEvaluation:
        """Create a new bid evaluation."""
        evaluation = BidEvaluation(
            organization_id=organization_id,
            rfq_id=data.rfq_id,
            evaluation_date=data.evaluation_date,
            evaluated_by_user_id=data.evaluated_by_user_id,
            evaluation_report=data.evaluation_report,
        )
        self.db.add(evaluation)
        self.db.flush()

        for score_data in data.scores:
            weighted = score_data.weight * score_data.score / Decimal("100")
            score = BidEvaluationScore(
                evaluation_id=evaluation.evaluation_id,
                response_id=score_data.response_id,
                criterion_name=score_data.criterion_name,
                weight=score_data.weight,
                score=score_data.score,
                weighted_score=weighted,
                comments=score_data.comments,
            )
            self.db.add(score)

        self.db.flush()
        logger.info("Created bid evaluation for RFQ %s", data.rfq_id)
        return evaluation

    def score_bid(
        self,
        organization_id: UUID,
        evaluation_id: UUID,
        score_data: EvaluationScoreCreate,
    ) -> BidEvaluationScore:
        """Add or update a score for a bid."""
        evaluation = self.get_by_id(organization_id, evaluation_id)
        if not evaluation:
            raise NotFoundError("Evaluation not found")

        weighted = score_data.weight * score_data.score / Decimal("100")
        score = BidEvaluationScore(
            evaluation_id=evaluation_id,
            response_id=score_data.response_id,
            criterion_name=score_data.criterion_name,
            weight=score_data.weight,
            score=score_data.score,
            weighted_score=weighted,
            comments=score_data.comments,
        )
        self.db.add(score)
        self.db.flush()
        return score

    def recommend(
        self,
        organization_id: UUID,
        evaluation_id: UUID,
        supplier_id: UUID,
        response_id: UUID,
    ) -> BidEvaluation:
        """Set recommended vendor for an evaluation."""
        evaluation = self.get_by_id(organization_id, evaluation_id)
        if not evaluation:
            raise NotFoundError("Evaluation not found")

        evaluation.recommended_supplier_id = supplier_id
        evaluation.recommended_response_id = response_id
        evaluation.status = EvaluationStatus.COMPLETED
        self.db.flush()
        logger.info(
            "Recommended vendor %s for evaluation %s", supplier_id, evaluation_id
        )
        return evaluation

    def approve(
        self,
        organization_id: UUID,
        evaluation_id: UUID,
        approved_by_user_id: UUID,
    ) -> BidEvaluation:
        """Approve a bid evaluation."""
        evaluation = self.get_by_id(organization_id, evaluation_id)
        if not evaluation:
            raise NotFoundError("Evaluation not found")
        if evaluation.status != EvaluationStatus.COMPLETED:
            raise ValidationError("Only completed evaluations can be approved")

        evaluation.status = EvaluationStatus.APPROVED
        evaluation.approved_by_user_id = approved_by_user_id
        evaluation.approved_at = datetime.now(timezone.utc)
        self.db.flush()
        logger.info("Approved evaluation %s", evaluation_id)
        return evaluation
