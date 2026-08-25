"""Real ERP API acceptance test for the Sub operational sync contract."""

from collections.abc import Generator
from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.service_principal import get_db_with_service_org, require_service_auth
from app.api.sync.dotmac_sub import router
from app.models.finance.core_org.project import Project
from app.models.finance.exp.expense_entry import PaymentMethod
from app.models.pm.task import Task
from app.models.sync.sync_entity import SyncEntity
from app.services.finance.exp.expense import ExpenseService
from app.services.finance.exp.web import ExpenseWebService
from app.services.people.self_service_web import SelfServiceWebService


def test_real_erp_v2_response_creates_sub_operational_projections(
    db, org_id, expense_account, user_id
):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_service_auth] = lambda: {
        "organization_id": org_id,
        "person_id": user_id,
        "scopes": ["sub:domain:write"],
    }

    def override_db() -> Generator[Session, None, None]:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db_with_service_org] = override_db
    client = TestClient(app)

    project_source_id = uuid4()
    task_source_id = uuid4()
    work_order_source_id = uuid4()
    request = {
        "projects": [
            {
                "source_id": str(project_source_id),
                "name": "Self-Care fibre rollout",
                "code": "SC-PROJ-1",
                "status": "active",
            }
        ],
        "project_tasks": [
            {
                "source_id": str(task_source_id),
                "project_source_id": str(project_source_id),
                "title": "Inspect customer fibre",
                "number": "SC-TASK-1",
                "status": "todo",
                "priority": "normal",
            }
        ],
        "work_orders": [
            {
                "source_id": str(work_order_source_id),
                "project_source_id": str(project_source_id),
                "title": "Install customer fibre",
                "status": "active",
            }
        ],
    }

    response = client.post("/api/v1/sync/sub/bulk", json=request)

    assert response.status_code == 200
    assert response.json() == {
        "contract_version": 2,
        "projects_synced": 1,
        "project_tasks_synced": 1,
        "work_orders_synced": 1,
        "errors": [],
    }

    projections = {
        projection.source_doctype: projection
        for projection in db.scalars(
            select(SyncEntity).where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "sub",
                SyncEntity.source_name.in_(
                    (
                        str(project_source_id),
                        str(task_source_id),
                        str(work_order_source_id),
                    )
                ),
            )
        )
    }
    assert set(projections) == {"project", "project_task", "work_order"}

    project = db.get(Project, projections["project"].target_id)
    task = db.get(Task, projections["project_task"].target_id)
    work_order = db.get(Task, projections["work_order"].target_id)
    assert project is not None
    assert task is not None
    assert work_order is not None
    assert task.project_id == project.project_id
    assert work_order.project_id == project.project_id
    assert project.project_code == "SC-PROJ-1"

    employee_tasks = SelfServiceWebService._get_tasks_for_dropdown(
        db, org_id, str(project.project_id)
    )
    assert {item["task_id"] for item in employee_tasks} == {
        str(task.task_id),
        str(work_order.task_id),
    }

    finance_context = ExpenseWebService.form_context(db, str(org_id))
    assert str(project.project_id) in {
        item["project_id"] for item in finance_context["projects"]
    }
    assert {str(task.task_id), str(work_order.task_id)} <= {
        item["task_id"] for item in finance_context["tasks"]
    }

    expense = ExpenseService.create(
        db,
        organization_id=str(org_id),
        expense_date=date(2026, 8, 14),
        expense_account_id=str(expense_account.account_id),
        amount=Decimal("1250.00"),
        description="Fibre inspection transport",
        payment_method=PaymentMethod.CASH,
        created_by=str(user_id),
        project_id=str(project.project_id),
        task_id=str(task.task_id),
    )
    assert expense.project_id == project.project_id
    assert expense.task_id == task.task_id

    replay = client.post("/api/v1/sync/sub/bulk", json=request)
    assert replay.status_code == 200
    assert replay.json()["errors"] == []
    assert (
        db.scalar(
            select(func.count())
            .select_from(SyncEntity)
            .where(
                SyncEntity.organization_id == org_id,
                SyncEntity.source_system == "sub",
                SyncEntity.source_name.in_(
                    (
                        str(project_source_id),
                        str(task_source_id),
                        str(work_order_source_id),
                    )
                ),
            )
        )
        == 3
    )
