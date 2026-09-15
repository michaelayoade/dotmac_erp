"""Service/query contract tests. PostgreSQL/RLS acceptance is a separate gate."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.services.people.perf.kpi_dashboard_contract import (
    PREFERENCE_KEY,
    DashboardAccessError,
    DashboardConfig,
    DashboardValidationError,
    SavedViewNotFound,
)
from app.services.people.perf.kpi_dashboard_service import (
    DashboardScope,
    KPIDashboardService,
)


@pytest.fixture
def scope():
    return DashboardScope(
        uuid4(),
        uuid4(),
        False,
        ({"id": str(uuid4()), "name": "Engineering", "active": True},),
        "Africa/Lagos",
    )


@pytest.fixture
def db():
    return MagicMock()


def test_query_tenant_constrains_source_and_both_joins(db, scope):
    stmt = KPIDashboardService(db)._base_query(
        scope, DashboardConfig(), date(2026, 9, 15)
    )
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "kpi.organization_id =" in sql
    assert "employee.organization_id =" in sql
    assert "people.organization_id =" in sql
    assert "department.organization_id =" in sql
    assert "employee.department_id IN" in sql
    assert list(compiled.params.values()).count(scope.organization_id) == 4
    assert date(2026, 9, 1) in compiled.params.values()
    assert date(2026, 9, 30) in compiled.params.values()


def test_search_is_literal_and_bound(db, scope):
    stmt = KPIDashboardService(db)._base_query(
        scope, DashboardConfig(search="a%b_c"), date(2026, 9, 15)
    )
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert "a%b_c" not in str(compiled)
    assert "a/%b/_c" in compiled.params.values()


def test_forbidden_department_fails_before_any_result_query(db, scope):
    with pytest.raises(DashboardAccessError):
        KPIDashboardService(db).dashboard(
            scope, DashboardConfig(department_ids=(str(uuid4()),))
        )
    db.execute.assert_not_called()


def test_profile_write_is_own_org_own_person_and_row_locked(db, scope):
    db.scalars.return_value.one_or_none.return_value = SimpleNamespace(metadata_={})
    KPIDashboardService(db).save_view(scope, DashboardConfig(), "My dashboard")
    stmt = db.scalars.call_args.args[0]
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert "FOR UPDATE" in str(compiled)
    assert scope.organization_id in compiled.params.values()
    assert scope.person_id in compiled.params.values()
    assert stmt.get_execution_options()["populate_existing"] is True
    db.flush.assert_called_once()
    db.commit.assert_not_called()


def test_saved_view_round_trip_preserves_unrelated_preferences(db, scope):
    person = SimpleNamespace(metadata_={"unrelated": {"theme": "dark"}})
    db.scalars.return_value.one_or_none.return_value = person
    service = KPIDashboardService(db)
    config = DashboardConfig.parse({"widgets": ["overdue", "coverage"]})
    identifier = service.save_view(scope, config, "Engineering")
    assert service.get_view(scope, identifier) == ("Engineering", config)
    assert person.metadata_["unrelated"] == {"theme": "dark"}
    service.save_view(scope, config, "Revised", view_id=identifier)
    assert len(service.list_views(scope)) == 1
    service.delete_view(scope, identifier)
    assert service.list_views(scope) == []
    assert person.metadata_["unrelated"] == {"theme": "dark"}
    db.commit.assert_not_called()


def test_unknown_view_never_updates_or_deletes_another_view(db, scope):
    db.scalars.return_value.one_or_none.return_value = SimpleNamespace(metadata_={})
    service = KPIDashboardService(db)
    identifier = str(uuid4())
    with pytest.raises(SavedViewNotFound):
        service.get_view(scope, identifier)
    with pytest.raises(SavedViewNotFound):
        service.save_view(scope, DashboardConfig(), "Unknown", view_id=identifier)
    with pytest.raises(SavedViewNotFound):
        service.delete_view(scope, identifier)
    db.flush.assert_not_called()


def test_max_views_and_case_insensitive_names(db, scope):
    db.scalars.return_value.one_or_none.return_value = SimpleNamespace(metadata_={})
    service = KPIDashboardService(db)
    identifier = service.save_view(scope, DashboardConfig(), "View 0")
    with pytest.raises(DashboardValidationError):
        service.save_view(scope, DashboardConfig(), "view 0")
    for number in range(1, 10):
        service.save_view(scope, DashboardConfig(), f"View {number}")
    with pytest.raises(DashboardValidationError):
        service.save_view(scope, DashboardConfig(), "Eleventh")
    service.save_view(scope, DashboardConfig(), "Updated", view_id=identifier)
    assert len(service.list_views(scope)) == 10


def test_saved_department_scope_is_rechecked_after_access_changes(db, scope):
    revoked_department = str(uuid4())
    identifier = str(uuid4())
    db.scalars.return_value.one_or_none.return_value = SimpleNamespace(
        metadata_={
            PREFERENCE_KEY: {
                "version": 1,
                "views": [
                    {
                        "id": identifier,
                        "name": "Revoked",
                        "config": DashboardConfig(
                            department_ids=(revoked_department,)
                        ).to_dict(),
                    }
                ],
            },
        }
    )
    with pytest.raises(DashboardAccessError):
        KPIDashboardService(db).get_view(scope, identifier)


def test_missing_profile_denies_even_when_scope_exists(db, scope):
    db.scalars.return_value.one_or_none.return_value = None
    with pytest.raises(DashboardAccessError):
        KPIDashboardService(db).list_views(scope)


def test_scope_uses_active_actor_and_current_department_head(db, scope):
    actor = SimpleNamespace(employee_id=uuid4())
    department = SimpleNamespace(
        department_id=uuid4(), department_name="Engineering", is_active=True
    )
    db.scalars.side_effect = [
        MagicMock(
            **{"one_or_none.return_value": SimpleNamespace(timezone="Africa/Lagos")}
        ),
        MagicMock(**{"one_or_none.return_value": actor}),
        MagicMock(**{"all.return_value": [department]}),
    ]
    result = KPIDashboardService(db).resolve_scope(
        scope.organization_id, scope.person_id, ["payroll_admin"]
    )
    assert result.organization_wide is False
    employee_query = db.scalars.call_args_list[1].args[0].compile()
    assert scope.person_id in employee_query.params.values()
    assert "ACTIVE" in employee_query.params.values()
    department_query = db.scalars.call_args_list[2].args[0].compile()
    assert actor.employee_id in department_query.params.values()
    assert "head_id =" in str(department_query)
    assert result.departments[0]["id"] == str(department.department_id)


def test_no_active_department_head_assignment_denies(db, scope):
    db.scalars.side_effect = [
        MagicMock(**{"one_or_none.return_value": SimpleNamespace(timezone="UTC")}),
        MagicMock(**{"one_or_none.return_value": None}),
    ]
    with pytest.raises(DashboardAccessError):
        KPIDashboardService(db).resolve_scope(
            scope.organization_id, scope.person_id, []
        )


def test_dashboard_does_not_invent_missing_actuals_or_average_mixed_units(db, scope):
    counts = {
        "tracked": 26,
        "owners": 2,
        "achieved": 1,
        "at_risk": 2,
        "overdue": 3,
        "reported": 13,
        "on_track": 0,
        "below_target": 1,
        "missing": 13,
        "latest_record_update": None,
    }
    db.execute.side_effect = [
        MagicMock(**{"mappings.return_value.one.return_value": counts}),
        MagicMock(**{"mappings.return_value.all.return_value": []}),
        MagicMock(
            **{
                "mappings.return_value.all.return_value": [
                    {
                        "actual_value": None,
                        "target_value": Decimal("20"),
                        "lower_is_better": False,
                        "status": "ACTIVE",
                        "period_end": date(2026, 9, 30),
                        "employee_name": "Test",
                        "employee_code": "EMP1",
                    },
                    {
                        "actual_value": Decimal("0"),
                        "target_value": Decimal("20"),
                        "lower_is_better": False,
                        "status": "ACTIVE",
                        "period_end": date(2026, 9, 30),
                        "employee_name": "Test",
                        "employee_code": "EMP1",
                    },
                ]
            }
        ),
    ]
    service = KPIDashboardService(db)
    service._employee_review = MagicMock(return_value={})
    result = service.dashboard(
        scope,
        DashboardConfig(),
        page=999,
        now=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    assert result["summary"]["coverage"] == Decimal("50.0")
    assert result["kpis"][0]["actual_value"] is None
    assert result["kpis"][1]["actual_value"] == Decimal("0")
    assert result["page"] == result["total_pages"] == 2
    summary_sql = str(db.execute.call_args_list[0].args[0].compile())
    assert "avg(" not in summary_sql.lower()
    assert "count(scoped_kpis.actual_value)" in summary_sql
