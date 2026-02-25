"""Extended tests for CoachService: feedback, dashboard, scope, reports, top_insights."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.models.coach.insight import CoachInsight
from app.models.coach.report import CoachReport
from app.services.coach.coach_service import CoachInsightScope, CoachService


def _insight(
    *,
    org_id: uuid.UUID,
    target_employee_id: uuid.UUID | None = None,
    severity: str = "INFO",
    category: str = "CASH_FLOW",
    audience: str = "FINANCE",
    status: str = "GENERATED",
    valid_until: date | None = None,
    created_at: datetime | None = None,
) -> CoachInsight:
    return CoachInsight(
        insight_id=uuid.uuid4(),
        organization_id=org_id,
        audience=audience,
        target_employee_id=target_employee_id,
        category=category,
        severity=severity,
        title=f"{severity} {category} insight",
        summary=f"Summary for {category}",
        detail=None,
        coaching_action="Take action",
        confidence=0.85,
        data_sources={},
        evidence={},
        status=status,
        delivered_at=None,
        read_at=None,
        dismissed_at=None,
        feedback=None,
        valid_until=valid_until or (date.today() + timedelta(days=7)),
        created_at=created_at or datetime.now(UTC),
    )


def _report(
    *,
    org_id: uuid.UUID,
    report_type: str = "weekly_finance",
    audience: str = "FINANCE",
    period_start: date | None = None,
    period_end: date | None = None,
) -> CoachReport:
    today = date.today()
    return CoachReport(
        report_id=uuid.uuid4(),
        organization_id=org_id,
        audience=audience,
        target_employee_id=None,
        report_type=report_type,
        period_start=period_start or (today - timedelta(days=7)),
        period_end=period_end or today,
        title="Test Report",
        executive_summary="Summary of the reporting period.",
        sections=[{"heading": "Overview", "body": "All good."}],
        key_metrics={"revenue": 100000},
        recommendations=[{"action": "Review cash flow", "priority": "high"}],
        created_at=datetime.now(UTC),
    )


# ── update_feedback ───────────────────────────────────────────────────────────


class TestUpdateFeedback:
    def test_happy_path_helpful(self, db_session) -> None:
        org_id = uuid.uuid4()
        insight = _insight(org_id=org_id)
        db_session.add(insight)
        db_session.commit()

        svc = CoachService(db_session)
        result = svc.update_feedback(org_id, insight.insight_id, "helpful")
        assert result.feedback == "helpful"
        assert result.read_at is not None
        assert result.status == "READ"

    def test_not_relevant_feedback(self, db_session) -> None:
        org_id = uuid.uuid4()
        insight = _insight(org_id=org_id)
        db_session.add(insight)
        db_session.commit()

        svc = CoachService(db_session)
        result = svc.update_feedback(org_id, insight.insight_id, "not_relevant")
        assert result.feedback == "not_relevant"

    def test_inaccurate_feedback(self, db_session) -> None:
        org_id = uuid.uuid4()
        insight = _insight(org_id=org_id)
        db_session.add(insight)
        db_session.commit()

        svc = CoachService(db_session)
        result = svc.update_feedback(org_id, insight.insight_id, "inaccurate")
        assert result.feedback == "inaccurate"

    def test_invalid_feedback_value_raises(self, db_session) -> None:
        org_id = uuid.uuid4()
        insight = _insight(org_id=org_id)
        db_session.add(insight)
        db_session.commit()

        svc = CoachService(db_session)
        with pytest.raises(ValueError, match="Invalid feedback"):
            svc.update_feedback(org_id, insight.insight_id, "bad_value")

    def test_insight_not_found_raises(self, db_session) -> None:
        org_id = uuid.uuid4()
        svc = CoachService(db_session)
        with pytest.raises(ValueError, match="not found"):
            svc.update_feedback(org_id, uuid.uuid4(), "helpful")

    def test_org_isolation(self, db_session) -> None:
        """Cannot update feedback for insight belonging to different org."""
        org1 = uuid.uuid4()
        org2 = uuid.uuid4()
        insight = _insight(org_id=org1)
        db_session.add(insight)
        db_session.commit()

        svc = CoachService(db_session)
        with pytest.raises(ValueError, match="not found"):
            svc.update_feedback(org2, insight.insight_id, "helpful")

    def test_already_read_does_not_reset_read_at(self, db_session) -> None:
        org_id = uuid.uuid4()
        original_read_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        insight = _insight(org_id=org_id, status="READ")
        insight.read_at = original_read_at
        db_session.add(insight)
        db_session.commit()

        svc = CoachService(db_session)
        result = svc.update_feedback(org_id, insight.insight_id, "helpful")
        assert result.feedback == "helpful"
        # SQLite drops tzinfo, so compare naive
        assert result.read_at is not None
        assert result.read_at.replace(tzinfo=None) == original_read_at.replace(
            tzinfo=None
        )


# ── dashboard_context ─────────────────────────────────────────────────────────


class TestDashboardContext:
    def test_empty_dashboard(self, db_session) -> None:
        org_id = uuid.uuid4()
        svc = CoachService(db_session)
        scope = CoachInsightScope(
            include_org_wide=True, employee_ids=None, audiences=None
        )
        ctx = svc.dashboard_context(org_id, scope)
        assert ctx["total_insights"] == 0
        assert ctx["top_insights"] == []
        assert ctx["severity_counts"] == {}
        assert ctx["category_counts"] == {}

    def test_dashboard_with_insights(self, db_session) -> None:
        org_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add_all(
            [
                _insight(
                    org_id=org_id,
                    severity="URGENT",
                    category="CASH_FLOW",
                    created_at=now - timedelta(hours=1),
                ),
                _insight(
                    org_id=org_id,
                    severity="WARNING",
                    category="REVENUE",
                    created_at=now - timedelta(hours=2),
                ),
                _insight(
                    org_id=org_id,
                    severity="INFO",
                    category="CASH_FLOW",
                    created_at=now - timedelta(hours=3),
                ),
            ]
        )
        db_session.commit()

        svc = CoachService(db_session)
        scope = CoachInsightScope(
            include_org_wide=True, employee_ids=None, audiences=None
        )
        ctx = svc.dashboard_context(org_id, scope)
        assert ctx["total_insights"] == 3
        assert ctx["severity_counts"]["URGENT"] == 1
        assert ctx["severity_counts"]["WARNING"] == 1
        assert ctx["severity_counts"]["INFO"] == 1
        assert ctx["category_counts"]["CASH_FLOW"] == 2
        assert ctx["category_counts"]["REVENUE"] == 1
        assert ctx["warning_count"] == 2  # WARNING + URGENT
        assert ctx["info_count"] == 1
        # Top insights ordered by severity desc
        top = ctx["top_insights"]
        assert len(top) == 3
        assert top[0].severity == "URGENT"

    def test_dashboard_empty_audiences_returns_empty(self, db_session) -> None:
        """Scope with empty audiences set means no access."""
        org_id = uuid.uuid4()
        db_session.add(_insight(org_id=org_id))
        db_session.commit()

        svc = CoachService(db_session)
        scope = CoachInsightScope(
            include_org_wide=True, employee_ids=None, audiences=set()
        )
        ctx = svc.dashboard_context(org_id, scope)
        assert ctx["total_insights"] == 0

    def test_dashboard_filters_by_audience(self, db_session) -> None:
        org_id = uuid.uuid4()
        db_session.add_all(
            [
                _insight(org_id=org_id, audience="FINANCE"),
                _insight(org_id=org_id, audience="HR"),
            ]
        )
        db_session.commit()

        svc = CoachService(db_session)
        scope = CoachInsightScope(
            include_org_wide=True, employee_ids=None, audiences={"FINANCE"}
        )
        ctx = svc.dashboard_context(org_id, scope)
        assert ctx["total_insights"] == 1


# ── _normalize_category ───────────────────────────────────────────────────────


class TestNormalizeCategory:
    def test_none(self) -> None:
        svc = CoachService.__new__(CoachService)
        assert svc._normalize_category(None) is None

    def test_empty(self) -> None:
        svc = CoachService.__new__(CoachService)
        assert svc._normalize_category("") is None

    def test_dash_to_underscore(self) -> None:
        svc = CoachService.__new__(CoachService)
        assert svc._normalize_category("data-quality") == "DATA_QUALITY"

    def test_already_uppercase(self) -> None:
        svc = CoachService.__new__(CoachService)
        assert svc._normalize_category("CASH_FLOW") == "CASH_FLOW"

    def test_mixed_case(self) -> None:
        svc = CoachService.__new__(CoachService)
        assert svc._normalize_category("Supply-Chain") == "SUPPLY_CHAIN"


# ── _audiences_for_roles ──────────────────────────────────────────────────────


class TestAudiencesForRoles:
    def _svc(self) -> CoachService:
        return CoachService.__new__(CoachService)

    def test_admin_sees_all(self) -> None:
        audiences = self._svc()._audiences_for_roles({"admin"})
        assert "FINANCE" in audiences
        assert "HR" in audiences
        assert "EXECUTIVE" in audiences
        assert "EMPLOYEE" in audiences

    def test_finance_manager_sees_finance(self) -> None:
        audiences = self._svc()._audiences_for_roles({"finance_manager"})
        assert "FINANCE" in audiences
        assert "HR" not in audiences

    def test_hr_director_sees_hr_and_executive(self) -> None:
        audiences = self._svc()._audiences_for_roles({"hr_director"})
        assert "HR" in audiences
        assert "EXECUTIVE" in audiences

    def test_employee_sees_employee(self) -> None:
        audiences = self._svc()._audiences_for_roles({"employee"})
        assert "EMPLOYEE" in audiences
        assert len(audiences) == 1

    def test_department_manager_sees_manager(self) -> None:
        audiences = self._svc()._audiences_for_roles({"department_manager"})
        assert "MANAGER" in audiences

    def test_unknown_role_returns_empty(self) -> None:
        audiences = self._svc()._audiences_for_roles({"unknown_role"})
        assert audiences == set()

    def test_multi_role_union(self) -> None:
        audiences = self._svc()._audiences_for_roles({"finance_manager", "hr_officer"})
        assert "FINANCE" in audiences
        assert "HR" in audiences

    def test_operations_role(self) -> None:
        audiences = self._svc()._audiences_for_roles({"operations_manager"})
        assert "OPERATIONS" in audiences


# ── list_reports / get_report ─────────────────────────────────────────────────


class TestReports:
    def test_list_reports_empty(self, db_session) -> None:
        org_id = uuid.uuid4()
        svc = CoachService(db_session)
        items, total = svc.list_reports(org_id)
        assert items == []
        assert total == 0

    def test_list_reports_pagination(self, db_session) -> None:
        org_id = uuid.uuid4()
        reports = []
        for i in range(5):
            r = _report(org_id=org_id)
            r.created_at = datetime.now(UTC) - timedelta(hours=i)
            reports.append(r)
        db_session.add_all(reports)
        db_session.commit()

        svc = CoachService(db_session)
        items, total = svc.list_reports(org_id, page=1, per_page=2)
        assert total == 5
        assert len(items) == 2

        items2, total2 = svc.list_reports(org_id, page=2, per_page=2)
        assert total2 == 5
        assert len(items2) == 2

    def test_list_reports_org_isolation(self, db_session) -> None:
        org1 = uuid.uuid4()
        org2 = uuid.uuid4()
        db_session.add_all([_report(org_id=org1), _report(org_id=org2)])
        db_session.commit()

        svc = CoachService(db_session)
        items, total = svc.list_reports(org1)
        assert total == 1

    def test_get_report_found(self, db_session) -> None:
        org_id = uuid.uuid4()
        report = _report(org_id=org_id)
        db_session.add(report)
        db_session.commit()

        svc = CoachService(db_session)
        result = svc.get_report(org_id, report.report_id)
        assert result is not None
        assert hasattr(result, "report_id") and result.report_id == report.report_id

    def test_get_report_not_found(self, db_session) -> None:
        org_id = uuid.uuid4()
        svc = CoachService(db_session)
        assert svc.get_report(org_id, uuid.uuid4()) is None

    def test_get_report_org_isolation(self, db_session) -> None:
        org1 = uuid.uuid4()
        org2 = uuid.uuid4()
        report = _report(org_id=org1)
        db_session.add(report)
        db_session.commit()

        svc = CoachService(db_session)
        assert svc.get_report(org2, report.report_id) is None


# ── top_insights_for_module ───────────────────────────────────────────────────


class TestTopInsightsForModule:
    def test_returns_matching_categories(self, db_session) -> None:
        org_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add_all(
            [
                _insight(
                    org_id=org_id,
                    category="CASH_FLOW",
                    severity="WARNING",
                    created_at=now,
                ),
                _insight(
                    org_id=org_id,
                    category="REVENUE",
                    severity="INFO",
                    created_at=now - timedelta(hours=1),
                ),
                _insight(
                    org_id=org_id,
                    category="EFFICIENCY",
                    severity="URGENT",
                    created_at=now - timedelta(hours=2),
                ),
            ]
        )
        db_session.commit()

        svc = CoachService(db_session)
        results = svc.top_insights_for_module(
            org_id, categories=["CASH_FLOW", "REVENUE"]
        )
        assert len(results) == 2
        categories = {i.category for i in results}
        assert "EFFICIENCY" not in categories

    def test_only_org_wide_insights(self, db_session) -> None:
        """top_insights_for_module only returns org-wide (target_employee_id=None)."""
        org_id = uuid.uuid4()
        emp = uuid.uuid4()
        db_session.add_all(
            [
                _insight(org_id=org_id, category="CASH_FLOW", target_employee_id=None),
                _insight(org_id=org_id, category="CASH_FLOW", target_employee_id=emp),
            ]
        )
        db_session.commit()

        svc = CoachService(db_session)
        results = svc.top_insights_for_module(org_id, categories=["CASH_FLOW"])
        assert len(results) == 1
        assert results[0].target_employee_id is None

    def test_respects_limit(self, db_session) -> None:
        org_id = uuid.uuid4()
        now = datetime.now(UTC)
        for i in range(5):
            db_session.add(
                _insight(
                    org_id=org_id,
                    category="CASH_FLOW",
                    created_at=now - timedelta(hours=i),
                )
            )
        db_session.commit()

        svc = CoachService(db_session)
        results = svc.top_insights_for_module(org_id, categories=["CASH_FLOW"], limit=2)
        assert len(results) == 2

    def test_excludes_expired(self, db_session) -> None:
        org_id = uuid.uuid4()
        db_session.add_all(
            [
                _insight(
                    org_id=org_id,
                    category="CASH_FLOW",
                    valid_until=date.today() + timedelta(days=1),
                ),
                _insight(
                    org_id=org_id,
                    category="CASH_FLOW",
                    valid_until=date.today() - timedelta(days=1),
                ),
            ]
        )
        db_session.commit()

        svc = CoachService(db_session)
        results = svc.top_insights_for_module(org_id, categories=["CASH_FLOW"])
        assert len(results) == 1


# ── Pagination edge cases ─────────────────────────────────────────────────────


class TestListInsightsEdgeCases:
    def test_invalid_pagination_raises(self, db_session) -> None:
        svc = CoachService(db_session)
        scope = CoachInsightScope(
            include_org_wide=True, employee_ids=None, audiences=None
        )
        with pytest.raises(ValueError, match="Invalid pagination"):
            svc.list_insights(uuid.uuid4(), scope, page=0, per_page=10)

        with pytest.raises(ValueError, match="Invalid pagination"):
            svc.list_insights(uuid.uuid4(), scope, page=1, per_page=0)

    def test_page_beyond_total_returns_empty(self, db_session) -> None:
        org_id = uuid.uuid4()
        db_session.add(_insight(org_id=org_id))
        db_session.commit()

        svc = CoachService(db_session)
        scope = CoachInsightScope(
            include_org_wide=True, employee_ids=None, audiences=None
        )
        items, total = svc.list_insights(org_id, scope, page=100, per_page=10)
        assert total == 1
        assert items == []
