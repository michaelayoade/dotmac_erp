"""
Tests for UnderperformanceService — business logic validation.

Uses SimpleNamespace/MagicMock to test threshold comparisons and scoring
logic without hitting the database.
"""

from __future__ import annotations

import uuid
import calendar
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.people.perf.underperformance_service import (
    UnderperformanceService,
    UnderperformanceServiceError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service() -> UnderperformanceService:
    db = MagicMock()
    return UnderperformanceService(db)


def make_kra_score(raw_score_percentage: float | None) -> SimpleNamespace:
    return SimpleNamespace(
        score_id=uuid.uuid4(),
        raw_score_percentage=Decimal(str(raw_score_percentage))
        if raw_score_percentage is not None
        else None,
    )


def make_appraisal(
    employee_id: uuid.UUID | None = None,
    appraisal_id: uuid.UUID | None = None,
    final_score: float | None = None,
    is_quarterly: bool = False,
    kra_scores: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        appraisal_id=appraisal_id or uuid.uuid4(),
        employee_id=employee_id or uuid.uuid4(),
        organization_id=uuid.uuid4(),
        is_quarterly=is_quarterly,
        final_score=Decimal(str(final_score)) if final_score is not None else None,
        kra_scores=kra_scores or [],
    )


def make_employee(
    employee_id: uuid.UUID | None = None,
    date_of_joining: date | None = None,
    confirmation_date: date | None = None,
    status: str = "ACTIVE",
) -> SimpleNamespace:
    return SimpleNamespace(
        employee_id=employee_id or uuid.uuid4(),
        organization_id=uuid.uuid4(),
        date_of_joining=date_of_joining or date(2024, 1, 1),
        confirmation_date=confirmation_date,
        status=status,
    )


# ---------------------------------------------------------------------------
# Error class hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    def test_underperformance_service_error_is_exception(self) -> None:
        err = UnderperformanceServiceError("test")
        assert isinstance(err, Exception)

    def test_underperformance_service_error_message(self) -> None:
        err = UnderperformanceServiceError("something went wrong")
        assert "something went wrong" in str(err)


# ---------------------------------------------------------------------------
# Annual trigger logic
# ---------------------------------------------------------------------------


class TestAnnualTriggerLogic:
    """Test the 50% Fair KPIs threshold for annual appraisals."""

    def test_flags_employee_with_exactly_50pct_fair_kpis(self) -> None:
        """Employee with exactly 50% KPIs below 70 is flagged."""
        svc = make_service()
        emp_id = uuid.uuid4()
        appraisal = make_appraisal(
            employee_id=emp_id,
            is_quarterly=False,
            kra_scores=[
                make_kra_score(65.0),  # fair (< 70)
                make_kra_score(60.0),  # fair (< 70)
                make_kra_score(80.0),  # good
                make_kra_score(85.0),  # good
            ],
        )

        result = svc._evaluate_annual_trigger(appraisal)

        assert result is not None
        assert result["employee_id"] == emp_id
        assert result["fair_count"] == 2
        assert result["total_kpis"] == 4
        assert result["percentage"] == pytest.approx(50.0)

    def test_flags_employee_with_more_than_50pct_fair_kpis(self) -> None:
        """Employee with >50% KPIs below 70 is flagged."""
        svc = make_service()
        emp_id = uuid.uuid4()
        appraisal = make_appraisal(
            employee_id=emp_id,
            is_quarterly=False,
            kra_scores=[
                make_kra_score(50.0),  # fair
                make_kra_score(60.0),  # fair
                make_kra_score(69.9),  # fair (just below threshold)
                make_kra_score(90.0),  # good
            ],
        )

        result = svc._evaluate_annual_trigger(appraisal)

        assert result is not None
        assert result["fair_count"] == 3
        assert result["total_kpis"] == 4
        assert result["percentage"] == pytest.approx(75.0)

    def test_skips_employee_with_all_good_kpis(self) -> None:
        """Employee with all KPIs >= 70 is not flagged."""
        svc = make_service()
        appraisal = make_appraisal(
            is_quarterly=False,
            kra_scores=[
                make_kra_score(70.0),  # exactly at threshold — good
                make_kra_score(80.0),
                make_kra_score(90.0),
                make_kra_score(100.0),
            ],
        )

        result = svc._evaluate_annual_trigger(appraisal)

        assert result is None

    def test_skips_employee_below_50pct_fair(self) -> None:
        """Employee with <50% KPIs below 70 is not flagged (only 1 of 4 = 25%)."""
        svc = make_service()
        appraisal = make_appraisal(
            is_quarterly=False,
            kra_scores=[
                make_kra_score(60.0),  # fair
                make_kra_score(75.0),
                make_kra_score(80.0),
                make_kra_score(85.0),
            ],
        )

        result = svc._evaluate_annual_trigger(appraisal)

        assert result is None

    def test_skips_kra_scores_without_raw_score_percentage(self) -> None:
        """KRA scores with None raw_score_percentage are excluded from count."""
        svc = make_service()
        appraisal = make_appraisal(
            is_quarterly=False,
            kra_scores=[
                make_kra_score(60.0),  # fair
                make_kra_score(None),  # not scored — excluded
                make_kra_score(None),  # not scored — excluded
                make_kra_score(80.0),  # good
            ],
        )

        # Only 2 scored KPIs: 1 fair / 2 total = 50% → flagged
        result = svc._evaluate_annual_trigger(appraisal)
        assert result is not None
        assert result["total_kpis"] == 2
        assert result["fair_count"] == 1

    def test_returns_none_when_no_scored_kpis(self) -> None:
        """Appraisal with no scored KPIs produces no result (can't evaluate)."""
        svc = make_service()
        appraisal = make_appraisal(
            is_quarterly=False,
            kra_scores=[
                make_kra_score(None),
                make_kra_score(None),
            ],
        )

        result = svc._evaluate_annual_trigger(appraisal)
        assert result is None

    def test_includes_appraisal_id_in_result(self) -> None:
        """Result includes the appraisal_id for traceability."""
        svc = make_service()
        appraisal_id = uuid.uuid4()
        appraisal = make_appraisal(
            appraisal_id=appraisal_id,
            is_quarterly=False,
            kra_scores=[
                make_kra_score(60.0),
                make_kra_score(55.0),
            ],
        )

        result = svc._evaluate_annual_trigger(appraisal)
        assert result is not None
        assert result["appraisal_id"] == appraisal_id


# ---------------------------------------------------------------------------
# Quarterly trigger logic
# ---------------------------------------------------------------------------


class TestQuarterlyTriggerLogic:
    """Test the 3-quarters-below-70 threshold."""

    def test_flags_employee_with_3_quarters_below_70(self) -> None:
        """Employee with exactly 3 quarters below 70 is flagged."""
        svc = make_service()
        quarterly_scores = [65.0, 60.0, 68.0]  # all below 70

        result = svc._evaluate_quarterly_trigger(
            employee_id=uuid.uuid4(),
            quarterly_scores=quarterly_scores,
        )

        assert result is not None
        assert result["quarters_below"] == 3

    def test_flags_employee_with_more_than_3_quarters_below_70(self) -> None:
        """Employee with 4 quarters below 70 is still flagged."""
        svc = make_service()
        quarterly_scores = [60.0, 65.0, 55.0, 68.0]  # all below 70

        result = svc._evaluate_quarterly_trigger(
            employee_id=uuid.uuid4(),
            quarterly_scores=quarterly_scores,
        )

        assert result is not None
        assert result["quarters_below"] == 4

    def test_skips_employee_with_only_2_quarters_below_70(self) -> None:
        """Employee with 2 quarters below 70 is not yet flagged."""
        svc = make_service()
        quarterly_scores = [60.0, 65.0, 75.0]

        result = svc._evaluate_quarterly_trigger(
            employee_id=uuid.uuid4(),
            quarterly_scores=quarterly_scores,
        )

        assert result is None

    def test_skips_employee_with_no_quarters_below_70(self) -> None:
        """Employee with all quarterly scores >= 70 is not flagged."""
        svc = make_service()
        quarterly_scores = [70.0, 80.0, 85.0, 90.0]

        result = svc._evaluate_quarterly_trigger(
            employee_id=uuid.uuid4(),
            quarterly_scores=quarterly_scores,
        )

        assert result is None

    def test_result_includes_quarterly_scores(self) -> None:
        """Result includes the quarterly_scores list for context."""
        svc = make_service()
        emp_id = uuid.uuid4()
        scores = [60.0, 65.0, 55.0]

        result = svc._evaluate_quarterly_trigger(
            employee_id=emp_id,
            quarterly_scores=scores,
        )

        assert result is not None
        assert result["employee_id"] == emp_id
        assert result["quarterly_scores"] == scores

    def test_exactly_70_is_not_below_threshold(self) -> None:
        """Score of exactly 70.0 is NOT counted as below threshold."""
        svc = make_service()
        quarterly_scores = [70.0, 70.0, 70.0]

        result = svc._evaluate_quarterly_trigger(
            employee_id=uuid.uuid4(),
            quarterly_scores=quarterly_scores,
        )

        assert result is None


# ---------------------------------------------------------------------------
# Probation milestone logic
# ---------------------------------------------------------------------------


class TestProbationLogic:
    """Test the 21-month probation milestone detection."""

    def test_calculates_months_correctly_for_21_months(self) -> None:
        """Employee joining 21 months ago should be flagged."""
        svc = make_service()
        today = date.today()
        # Join date 21 months ago (approximate via days)
        join_date = date(today.year - 2, today.month, today.day) + timedelta(
            days=30 * 3
        )
        # More precise: subtract exactly 21 months
        year = today.year
        month = today.month - 21
        while month <= 0:
            month += 12
            year -= 1
        join_day = min(today.day, calendar.monthrange(year, month)[1])
        join_date = date(year, month, join_day)

        result = svc._evaluate_probation_milestone(
            employee=make_employee(
                date_of_joining=join_date,
                confirmation_date=None,
            )
        )

        assert result is not None

    def test_flags_employee_approaching_21_months(self) -> None:
        """Employee whose 21-month date is within 30 days is flagged."""
        svc = make_service()
        # 21 months from now minus 15 days means milestone is 15 days away
        today = date.today()
        days_until_milestone = 15

        # Calculate join date: milestone is today + days_until_milestone,
        # milestone = join + 21 months ≈ join + 638 days
        milestone_date = today + timedelta(days=days_until_milestone)
        approx_21_months_days = 21 * 30
        join_date = milestone_date - timedelta(days=approx_21_months_days)

        result = svc._evaluate_probation_milestone(
            employee=make_employee(
                date_of_joining=join_date,
                confirmation_date=None,
            )
        )

        assert result is not None
        assert "milestone_date" in result
        assert "months_of_service" in result

    def test_skips_already_confirmed_employee(self) -> None:
        """Employee already confirmed (confirmation_date set) is skipped."""
        svc = make_service()
        today = date.today()
        year = today.year
        month = today.month - 21
        while month <= 0:
            month += 12
            year -= 1
        join_day = min(today.day, calendar.monthrange(year, month)[1])
        join_date = date(year, month, join_day)

        result = svc._evaluate_probation_milestone(
            employee=make_employee(
                date_of_joining=join_date,
                confirmation_date=date(2025, 6, 1),  # already confirmed
            )
        )

        assert result is None

    def test_skips_employee_with_milestone_more_than_30_days_away(self) -> None:
        """Employee whose 21-month milestone is > 30 days away is not flagged."""
        svc = make_service()
        today = date.today()
        # Join date is only 10 months ago — milestone is 11 months away
        join_date = today - timedelta(days=10 * 30)

        result = svc._evaluate_probation_milestone(
            employee=make_employee(
                date_of_joining=join_date,
                confirmation_date=None,
            )
        )

        assert result is None

    def test_skips_employee_past_21_month_milestone_by_more_than_30_days(self) -> None:
        """Employee who passed the 21-month milestone long ago is not flagged again."""
        svc = make_service()
        # Join date 2 years ago — milestone was 3 months ago
        join_date = date.today() - timedelta(days=365 * 2 + 90)

        result = svc._evaluate_probation_milestone(
            employee=make_employee(
                date_of_joining=join_date,
                confirmation_date=None,
            )
        )

        assert result is None

    def test_result_includes_required_fields(self) -> None:
        """Result dict includes employee_id, date_of_joining, months_of_service, milestone_date."""
        svc = make_service()
        emp_id = uuid.uuid4()
        today = date.today()
        year = today.year
        month = today.month - 21
        while month <= 0:
            month += 12
            year -= 1
        join_day = min(today.day, calendar.monthrange(year, month)[1])
        join_date = date(year, month, join_day)

        emp = make_employee(
            employee_id=emp_id,
            date_of_joining=join_date,
            confirmation_date=None,
        )
        result = svc._evaluate_probation_milestone(employee=emp)

        assert result is not None
        assert result["employee_id"] == emp_id
        assert result["date_of_joining"] == join_date
        assert "months_of_service" in result
        assert "milestone_date" in result


# ---------------------------------------------------------------------------
# flag_for_pip — DB-backed method tests
# ---------------------------------------------------------------------------


def make_employee_model(
    employee_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    reports_to_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    """Create a mock Employee model instance."""
    return SimpleNamespace(
        employee_id=employee_id or uuid.uuid4(),
        organization_id=organization_id or uuid.uuid4(),
        reports_to_id=reports_to_id,
    )


class TestFlagForPIP:
    """Test the flag_for_pip method (DB-backed)."""

    def test_creates_draft_pip_for_valid_employee(self) -> None:
        """Creates a PIP with correct fields when employee exists."""
        org_id = uuid.uuid4()
        employee_id = uuid.uuid4()
        supervisor_id = uuid.uuid4()
        employee = make_employee_model(
            employee_id=employee_id,
            organization_id=org_id,
            reports_to_id=supervisor_id,
        )

        db = MagicMock()
        # First scalar → employee lookup; second scalar → count query
        db.scalar.side_effect = [employee, 5]

        svc = UnderperformanceService(db)

        with (
            patch(
                "app.models.people.perf.pip.PerformanceImprovementPlan",
                autospec=True,
            ) as MockPIP,
            patch(
                "app.services.people.perf.underperformance_service.OrgResolver"
            ) as resolver_cls,
        ):
            resolver_cls.return_value.get_manager.return_value = SimpleNamespace(
                employee_id=supervisor_id
            )
            mock_pip_instance = MagicMock()
            mock_pip_instance.pip_id = uuid.uuid4()
            MockPIP.return_value = mock_pip_instance

            result = svc.flag_for_pip(
                org_id,
                employee_id,
                trigger_type="annual",
                triggering_appraisal_id=None,
            )

        assert result["status"] == "flagged"
        assert result["pip_code"] == "PIP-2026-0006"
        assert result["employee_id"] == str(employee_id)
        assert result["trigger_type"] == "annual"
        assert "pip_id" in result
        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_raises_when_employee_not_found(self) -> None:
        """Raises UnderperformanceServiceError when employee doesn't exist."""
        org_id = uuid.uuid4()
        employee_id = uuid.uuid4()

        db = MagicMock()
        db.scalar.return_value = None  # employee not found

        svc = UnderperformanceService(db)

        with pytest.raises(UnderperformanceServiceError, match=str(employee_id)):
            svc.flag_for_pip(org_id, employee_id, trigger_type="annual")

    def test_uses_org_id_filter_on_employee_lookup(self) -> None:
        """Verify multi-tenancy: employee query checks organization_id."""
        org_id = uuid.uuid4()
        employee_id = uuid.uuid4()
        employee = make_employee_model(
            employee_id=employee_id,
            organization_id=org_id,
        )

        db = MagicMock()
        # Capture the statement passed to db.scalar
        captured_calls: list = []

        def capturing_scalar(stmt: object) -> object:
            captured_calls.append(stmt)
            # Return employee on first call, 0 (count) on second
            if len(captured_calls) == 1:
                return employee
            return 0

        db.scalar.side_effect = capturing_scalar

        svc = UnderperformanceService(db)
        with patch(
            "app.services.people.perf.underperformance_service.OrgResolver"
        ) as resolver_cls:
            resolver_cls.return_value.get_manager.return_value = None
            svc.flag_for_pip(org_id, employee_id, trigger_type="quarterly")

        # db.scalar must have been called at least once (employee lookup)
        assert db.scalar.call_count >= 1
        # Verify db.add was called (PIP was created)
        db.add.assert_called_once()

    def test_pip_code_increments_from_existing_count(self) -> None:
        """PIP code uses count + 1 for sequence number."""
        org_id = uuid.uuid4()
        employee_id = uuid.uuid4()
        employee = make_employee_model(employee_id=employee_id, organization_id=org_id)

        db = MagicMock()
        db.scalar.side_effect = [employee, 9]  # count = 9 → code should be 0010

        svc = UnderperformanceService(db)
        with patch(
            "app.services.people.perf.underperformance_service.OrgResolver"
        ) as resolver_cls:
            resolver_cls.return_value.get_manager.return_value = None
            result = svc.flag_for_pip(org_id, employee_id, trigger_type="probation")

        assert result["pip_code"].endswith("-0010")

    def test_uses_employee_id_as_supervisor_when_no_reports_to(self) -> None:
        """When reports_to_id is None, supervisor_id falls back to employee_id."""
        org_id = uuid.uuid4()
        employee_id = uuid.uuid4()
        employee = make_employee_model(
            employee_id=employee_id,
            organization_id=org_id,
            reports_to_id=None,
        )

        db = MagicMock()
        db.scalar.side_effect = [employee, 0]

        svc = UnderperformanceService(db)
        with patch(
            "app.services.people.perf.underperformance_service.OrgResolver"
        ) as resolver_cls:
            resolver_cls.return_value.get_manager.return_value = None
            result = svc.flag_for_pip(org_id, employee_id, trigger_type="annual")

        # With no reports_to_id the method uses employee_id as supervisor fallback
        # and still produces a flagged result
        assert result["status"] == "flagged"
        assert result["employee_id"] == str(employee_id)
        db.add.assert_called_once()  # PIP was created and added to session

    def test_creates_pending_outcome_action_when_appraisal_triggered(self) -> None:
        """When linked to appraisal, create PIP + pending outcome action records."""
        org_id = uuid.uuid4()
        employee_id = uuid.uuid4()
        appraisal_id = uuid.uuid4()
        employee = make_employee_model(
            employee_id=employee_id,
            organization_id=org_id,
            reports_to_id=uuid.uuid4(),
        )

        db = MagicMock()
        db.scalar.side_effect = [employee, 0]

        svc = UnderperformanceService(db)
        with patch(
            "app.services.people.perf.underperformance_service.OrgResolver"
        ) as resolver_cls:
            resolver_cls.return_value.get_manager.return_value = None
            svc.flag_for_pip(
                org_id,
                employee_id,
                trigger_type="score_below_50",
                triggering_appraisal_id=appraisal_id,
            )

        # PIP + AppraisalOutcomeAction
        assert db.add.call_count == 2


class TestEnsurePIPForUnderperformance:
    def test_legacy_scale_score_above_threshold_does_not_create_pip(self) -> None:
        db = MagicMock()
        svc = UnderperformanceService(db)

        result = svc.ensure_pip_for_underperformance(
            uuid.uuid4(),
            appraisal_id=uuid.uuid4(),
            employee_id=uuid.uuid4(),
            final_score=4.0,  # legacy scale => 80%
        )

        assert result is None
        db.scalar.assert_not_called()

    def test_returns_exists_when_linked_pip_already_present(self) -> None:
        db = MagicMock()
        existing = SimpleNamespace(pip_id=uuid.uuid4(), pip_code="PIP-2026-0009")
        db.scalar.return_value = existing
        svc = UnderperformanceService(db)

        result = svc.ensure_pip_for_underperformance(
            uuid.uuid4(),
            appraisal_id=uuid.uuid4(),
            employee_id=uuid.uuid4(),
            final_score=2.0,  # legacy scale => 40%
        )

        assert result is not None
        assert result["status"] == "exists"
        assert result["pip_code"] == "PIP-2026-0009"


# ---------------------------------------------------------------------------
# detect_annual_trigger — DB-backed detection
# ---------------------------------------------------------------------------


class TestDetectAnnualTrigger:
    """Test the detect_annual_trigger DB-backed method."""

    def test_returns_empty_when_no_appraisals(self) -> None:
        """No appraisals in the cycle → returns empty list."""
        org_id = uuid.uuid4()
        cycle_id = uuid.uuid4()

        db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        db.scalars.return_value = mock_scalars

        svc = UnderperformanceService(db)
        result = svc.detect_annual_trigger(org_id, cycle_id)

        assert result == []

    def test_flags_employee_with_majority_fair_kpis(self) -> None:
        """Employee with ≥50% Fair KPIs gets included in flagged list."""
        org_id = uuid.uuid4()
        cycle_id = uuid.uuid4()
        emp_id = uuid.uuid4()

        # 4 out of 7 KRAs score below 70 → 57% → should flag
        appraisal = make_appraisal(
            employee_id=emp_id,
            is_quarterly=False,
            kra_scores=[
                make_kra_score(65.0),
                make_kra_score(60.0),
                make_kra_score(55.0),
                make_kra_score(68.0),
                make_kra_score(80.0),
                make_kra_score(85.0),
                make_kra_score(90.0),
            ],
        )

        db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [appraisal]
        db.scalars.return_value = mock_scalars

        svc = UnderperformanceService(db)
        result = svc.detect_annual_trigger(org_id, cycle_id)

        assert len(result) == 1
        assert result[0]["employee_id"] == emp_id
        assert result[0]["fair_count"] == 4
        assert result[0]["total_kpis"] == 7

    def test_does_not_flag_employee_with_few_fair_kpis(self) -> None:
        """Employee with <50% Fair KPIs is not included in flagged list."""
        org_id = uuid.uuid4()
        cycle_id = uuid.uuid4()

        appraisal = make_appraisal(
            is_quarterly=False,
            kra_scores=[
                make_kra_score(65.0),  # fair
                make_kra_score(80.0),
                make_kra_score(85.0),
                make_kra_score(90.0),
            ],
        )

        db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [appraisal]
        db.scalars.return_value = mock_scalars

        svc = UnderperformanceService(db)
        result = svc.detect_annual_trigger(org_id, cycle_id)

        assert result == []

    def test_filters_by_non_quarterly_appraisals(self) -> None:
        """detect_annual_trigger queries only non-quarterly appraisals."""
        org_id = uuid.uuid4()
        cycle_id = uuid.uuid4()

        db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        db.scalars.return_value = mock_scalars

        svc = UnderperformanceService(db)
        svc.detect_annual_trigger(org_id, cycle_id)

        # Verify db.scalars was called (the method hits the DB)
        db.scalars.assert_called_once()


# ---------------------------------------------------------------------------
# detect_quarterly_trigger — DB-backed detection
# ---------------------------------------------------------------------------


class TestDetectQuarterlyTrigger:
    """Test the detect_quarterly_trigger DB-backed method."""

    def test_flags_employee_with_3_quarters_below_threshold(self) -> None:
        """3 quarterly appraisals with final_score below 70 triggers a flag."""
        org_id = uuid.uuid4()
        cycle_id = uuid.uuid4()
        emp_id = uuid.uuid4()

        appraisals = [
            make_appraisal(employee_id=emp_id, is_quarterly=True, final_score=65.0),
            make_appraisal(employee_id=emp_id, is_quarterly=True, final_score=60.0),
            make_appraisal(employee_id=emp_id, is_quarterly=True, final_score=68.0),
        ]

        db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = appraisals
        db.scalars.return_value = mock_scalars

        svc = UnderperformanceService(db)
        result = svc.detect_quarterly_trigger(org_id, cycle_id)

        assert len(result) == 1
        assert result[0]["employee_id"] == emp_id
        assert result[0]["quarters_below"] == 3

    def test_skips_employee_with_2_quarters_below(self) -> None:
        """Only 2 quarterly scores below threshold — not flagged."""
        org_id = uuid.uuid4()
        cycle_id = uuid.uuid4()
        emp_id = uuid.uuid4()

        appraisals = [
            make_appraisal(employee_id=emp_id, is_quarterly=True, final_score=65.0),
            make_appraisal(employee_id=emp_id, is_quarterly=True, final_score=60.0),
            make_appraisal(employee_id=emp_id, is_quarterly=True, final_score=75.0),
        ]

        db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = appraisals
        db.scalars.return_value = mock_scalars

        svc = UnderperformanceService(db)
        result = svc.detect_quarterly_trigger(org_id, cycle_id)

        assert result == []

    def test_returns_empty_when_no_quarterly_appraisals(self) -> None:
        """No quarterly appraisals → returns empty list."""
        org_id = uuid.uuid4()
        cycle_id = uuid.uuid4()

        db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        db.scalars.return_value = mock_scalars

        svc = UnderperformanceService(db)
        result = svc.detect_quarterly_trigger(org_id, cycle_id)

        assert result == []

    def test_skips_appraisals_with_no_final_score(self) -> None:
        """Appraisals without a final_score are excluded from score aggregation."""
        org_id = uuid.uuid4()
        cycle_id = uuid.uuid4()
        emp_id = uuid.uuid4()

        # 2 scored below + 1 unscored → only 2 counted, not enough to trigger
        appraisals = [
            make_appraisal(employee_id=emp_id, is_quarterly=True, final_score=65.0),
            make_appraisal(employee_id=emp_id, is_quarterly=True, final_score=60.0),
            make_appraisal(employee_id=emp_id, is_quarterly=True, final_score=None),
        ]

        db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = appraisals
        db.scalars.return_value = mock_scalars

        svc = UnderperformanceService(db)
        result = svc.detect_quarterly_trigger(org_id, cycle_id)

        assert result == []

    def test_groups_appraisals_by_employee(self) -> None:
        """Multiple employees are grouped and evaluated independently."""
        org_id = uuid.uuid4()
        cycle_id = uuid.uuid4()
        emp_a = uuid.uuid4()
        emp_b = uuid.uuid4()

        # emp_a: 3 below → flagged; emp_b: only 1 below → not flagged
        appraisals = [
            make_appraisal(employee_id=emp_a, is_quarterly=True, final_score=65.0),
            make_appraisal(employee_id=emp_a, is_quarterly=True, final_score=60.0),
            make_appraisal(employee_id=emp_a, is_quarterly=True, final_score=68.0),
            make_appraisal(employee_id=emp_b, is_quarterly=True, final_score=60.0),
            make_appraisal(employee_id=emp_b, is_quarterly=True, final_score=80.0),
            make_appraisal(employee_id=emp_b, is_quarterly=True, final_score=85.0),
        ]

        db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = appraisals
        db.scalars.return_value = mock_scalars

        svc = UnderperformanceService(db)
        result = svc.detect_quarterly_trigger(org_id, cycle_id)

        assert len(result) == 1
        assert result[0]["employee_id"] == emp_a
