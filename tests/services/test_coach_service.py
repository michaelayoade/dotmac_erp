from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from app.models.coach.insight import CoachInsight
from app.services.coach.coach_service import CoachInsightScope, CoachService


def _insight(
    *,
    org_id: uuid.UUID,
    target_employee_id: uuid.UUID | None,
    severity: str,
    created_at: datetime,
    valid_until: date,
) -> CoachInsight:
    return CoachInsight(
        insight_id=uuid.uuid4(),
        organization_id=org_id,
        audience="FINANCE",
        target_employee_id=target_employee_id,
        category="CASH_FLOW",
        severity=severity,
        title=f"{severity} title",
        summary="summary",
        detail=None,
        coaching_action="do a thing",
        confidence=0.9,
        data_sources={},
        evidence={},
        status="GENERATED",
        delivered_at=None,
        read_at=None,
        dismissed_at=None,
        feedback=None,
        valid_until=valid_until,
        created_at=created_at,
    )


def test_list_insights_scopes_and_org_isolation(db_session):
    org1 = uuid.uuid4()
    org2 = uuid.uuid4()
    emp_a = uuid.uuid4()
    emp_b = uuid.uuid4()
    now = datetime.now(UTC)

    db_session.add_all(
        [
            _insight(
                org_id=org1,
                target_employee_id=None,
                severity="ATTENTION",
                created_at=now - timedelta(hours=3),
                valid_until=date.today() + timedelta(days=1),
            ),
            _insight(
                org_id=org1,
                target_employee_id=emp_a,
                severity="URGENT",
                created_at=now - timedelta(hours=2),
                valid_until=date.today() + timedelta(days=1),
            ),
            _insight(
                org_id=org1,
                target_employee_id=emp_b,
                severity="WARNING",
                created_at=now - timedelta(hours=1),
                valid_until=date.today() + timedelta(days=1),
            ),
            _insight(
                org_id=org2,
                target_employee_id=emp_a,
                severity="URGENT",
                created_at=now,
                valid_until=date.today() + timedelta(days=1),
            ),
        ]
    )
    db_session.commit()

    svc = CoachService(db_session)

    # Self-scope: org-wide + emp_a only (org1)
    scope = CoachInsightScope(
        include_org_wide=True, employee_ids={emp_a}, audiences=None
    )
    items, total = svc.list_insights(org1, scope, page=1, per_page=50)
    assert total == 2
    assert {i.target_employee_id for i in items} == {None, emp_a}
    assert items[0].severity == "URGENT"  # ordering by severity desc

    # Org-wide only
    scope_orgwide = CoachInsightScope(
        include_org_wide=True, employee_ids=set(), audiences=None
    )
    items2, total2 = svc.list_insights(org1, scope_orgwide, page=1, per_page=50)
    assert total2 == 1
    assert items2[0].target_employee_id is None

    # All employees in org1
    scope_all = CoachInsightScope(
        include_org_wide=True, employee_ids=None, audiences=None
    )
    items3, total3 = svc.list_insights(org1, scope_all, page=1, per_page=50)
    assert total3 == 3
    assert all(i.organization_id == org1 for i in items3)


def test_list_insights_excludes_expired_by_default(db_session):
    org1 = uuid.uuid4()
    emp_a = uuid.uuid4()
    now = datetime.now(UTC)

    db_session.add_all(
        [
            _insight(
                org_id=org1,
                target_employee_id=emp_a,
                severity="INFO",
                created_at=now,
                valid_until=date.today() - timedelta(days=1),
            ),
            _insight(
                org_id=org1,
                target_employee_id=emp_a,
                severity="INFO",
                created_at=now,
                valid_until=date.today() + timedelta(days=1),
            ),
        ]
    )
    db_session.commit()

    svc = CoachService(db_session)
    scope = CoachInsightScope(
        include_org_wide=True, employee_ids={emp_a}, audiences=None
    )

    items, total = svc.list_insights(org1, scope, page=1, per_page=50)
    assert total == 1

    items2, total2 = svc.list_insights(
        org1, scope, page=1, per_page=50, include_expired=True
    )
    assert total2 == 2


def test_list_insights_filters_by_audience(db_session):
    org1 = uuid.uuid4()
    emp_a = uuid.uuid4()
    now = datetime.now(UTC)

    db_session.add_all(
        [
            _insight(
                org_id=org1,
                target_employee_id=emp_a,
                severity="INFO",
                created_at=now,
                valid_until=date.today() + timedelta(days=1),
            )
        ]
    )
    db_session.commit()

    svc = CoachService(db_session)
    scope = CoachInsightScope(
        include_org_wide=True,
        employee_ids={emp_a},
        audiences={"HR"},
    )
    items, total = svc.list_insights(org1, scope, page=1, per_page=50)
    assert total == 0
    assert items == []
