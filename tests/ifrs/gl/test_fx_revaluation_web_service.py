"""Tests for FXRevaluationWebService — Task 14.

The web service is a thin context builder that delegates to
``FXRevaluationService``. These tests assert the delegation contract and
the shape of the template context dict / pass-through return value.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.finance.gl.fx_revaluation import (
    FXRevaluationLine,
    FXRevaluationPreview,
    FXRevaluationResult,
)
from app.services.finance.gl.web.fx_revaluation_web import FXRevaluationWebService


class TestPreviewResponse:
    """``preview_response`` instantiates ``FXRevaluationService``, calls
    ``.preview(...)``, and returns a template-ready context dict whose keys
    match the names the route/template will consume."""

    def test_preview_response_delegates_and_builds_context(self) -> None:
        db = MagicMock()
        org_id = uuid4()
        period_id = uuid4()
        account_id = uuid4()

        line = FXRevaluationLine(
            account_id=account_id,
            currency_code="USD",
            closing_rate=Decimal("1500.0000"),
            book_value_functional=Decimal("1000000.00"),
            revalued_value_functional=Decimal("1050000.00"),
            delta_functional=Decimal("50000.00"),
            is_gain=True,
        )
        preview = FXRevaluationPreview(
            fiscal_period_id=period_id,
            period_end_date=date(2026, 3, 31),
            next_period_start_date=date(2026, 4, 1),
            lines=[line],
            total_gain_functional=Decimal("50000.00"),
            total_loss_functional=Decimal("0.00"),
            rates_used={"USD": Decimal("1500.0000")},
            warnings=["GBP has no closing rate; skipped."],
            prior_run_exists=False,
            prior_journal_ids=[],
        )

        with patch(
            "app.services.finance.gl.web.fx_revaluation_web.FXRevaluationService"
        ) as mock_service_cls:
            mock_service = MagicMock()
            mock_service.preview.return_value = preview
            mock_service_cls.return_value = mock_service

            ws = FXRevaluationWebService(db)
            ctx = ws.preview_response(
                organization_id=org_id,
                fiscal_period_id=period_id,
            )

            mock_service_cls.assert_called_once_with(db)
            mock_service.preview.assert_called_once_with(org_id, period_id)

        # Context dict must expose the keys the template consumes.
        assert ctx["preview"] is preview
        assert ctx["period_id"] == period_id
        assert ctx["prior_run_exists"] is False
        assert ctx["lines"] == [line]
        assert ctx["warnings"] == ["GBP has no closing rate; skipped."]
        assert ctx["rates_used"] == {"USD": Decimal("1500.0000")}
        assert ctx["total_gain"] == Decimal("50000.00")
        assert ctx["total_loss"] == Decimal("0.00")
        assert ctx["next_period_start_date"] == date(2026, 4, 1)


class TestPostResponse:
    """``post_response`` instantiates ``FXRevaluationService``, calls
    ``.post(...)`` with the user_id and reason, and returns the
    ``FXRevaluationResult`` directly so the route can flash messages or
    redirect."""

    def test_post_response_delegates_and_returns_result(self) -> None:
        db = MagicMock()
        org_id = uuid4()
        period_id = uuid4()
        user_id = uuid4()
        period_end_journal_id = uuid4()
        reversal_journal_id = uuid4()

        result = FXRevaluationResult(
            success=True,
            period_end_journal_id=period_end_journal_id,
            reversal_journal_id=reversal_journal_id,
            reversed_prior_journal_ids=[],
            total_gain_functional=Decimal("50000.00"),
            total_loss_functional=Decimal("0.00"),
            message="FX revaluation posted.",
            errors=[],
        )

        with patch(
            "app.services.finance.gl.web.fx_revaluation_web.FXRevaluationService"
        ) as mock_service_cls:
            mock_service = MagicMock()
            mock_service.post.return_value = result
            mock_service_cls.return_value = mock_service

            ws = FXRevaluationWebService(db)
            returned = ws.post_response(
                organization_id=org_id,
                fiscal_period_id=period_id,
                user_id=user_id,
                reason="Month-end revaluation for Mar 2026.",
            )

            mock_service_cls.assert_called_once_with(db)
            mock_service.post.assert_called_once_with(
                org_id,
                period_id,
                user_id,
                "Month-end revaluation for Mar 2026.",
            )

        assert returned is result
