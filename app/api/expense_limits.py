"""
Expense Limit Enforcement API Router.

API endpoints for:
- Expense Limit Rules (spending caps)
- Approver Limits (approval authority)
- Limit Evaluations (audit trail)
- Period Usage (usage tracking)
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_permission
from app.db import SessionLocal
from app.models.expense import (
    LimitActionType,
    LimitPeriodType,
    LimitResultType,
    LimitScopeType,
)
from app.schemas.expense import (
    EligibleApprover,
    EmployeeUsageSummary,
    # Usage
    EvaluateLimitRequest,
    EvaluateLimitResponse,
    # Approver Limits
    ExpenseApproverLimitCreate,
    ExpenseApproverLimitListResponse,
    ExpenseApproverLimitRead,
    ExpenseApproverLimitUpdate,
    ExpenseLimitEvaluationListResponse,
    # Evaluations
    ExpenseLimitEvaluationRead,
    ExpenseLimitRuleBrief,
    # Limit Rules
    ExpenseLimitRuleCreate,
    ExpenseLimitRuleListResponse,
    ExpenseLimitRuleRead,
    ExpenseLimitRuleUpdate,
)
from app.services.common import PaginationParams
from app.services.expense import ExpenseLimitService, ExpenseService

router = APIRouter(
    prefix="/expense-limits",
    tags=["expense-limits"],
    dependencies=[Depends(require_tenant_permission("expense:access"))],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def parse_scope_type(value: str | None) -> LimitScopeType | None:
    """Parse scope type string to enum."""
    if not value:
        return None
    try:
        return LimitScopeType(value.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid scope_type: {value}. Valid values: {[e.value for e in LimitScopeType]}",
        )


def parse_period_type(value: str | None) -> LimitPeriodType | None:
    """Parse period type string to enum."""
    if not value:
        return None
    try:
        return LimitPeriodType(value.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid period_type: {value}. Valid values: {[e.value for e in LimitPeriodType]}",
        )


def parse_result_type(value: str | None) -> LimitResultType | None:
    """Parse result type string to enum."""
    if not value:
        return None
    try:
        return LimitResultType(value.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid result: {value}. Valid values: {[e.value for e in LimitResultType]}",
        )


# =============================================================================
# Limit Rules
# =============================================================================


@router.get("/rules", response_model=ExpenseLimitRuleListResponse)
def list_limit_rules(
    scope_type: str | None = Query(None, description="Filter by scope type"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    search: str | None = Query(None, description="Search by code or name"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:read")),
    db: Session = Depends(get_db),
):
    """List expense limit rules."""
    service = ExpenseLimitService(db)
    result = service.list_rules(
        org_id,
        scope_type=parse_scope_type(scope_type),
        is_active=is_active,
        search=search,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ExpenseLimitRuleListResponse(
        items=[ExpenseLimitRuleRead.model_validate(r) for r in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.get("/rules/{rule_id}", response_model=ExpenseLimitRuleRead)
def get_limit_rule(
    rule_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:read")),
    db: Session = Depends(get_db),
):
    """Get an expense limit rule by ID."""
    service = ExpenseLimitService(db)
    try:
        rule = service.get_rule(org_id, rule_id)
        return ExpenseLimitRuleRead.model_validate(rule)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post(
    "/rules", response_model=ExpenseLimitRuleRead, status_code=status.HTTP_201_CREATED
)
def create_limit_rule(
    data: ExpenseLimitRuleCreate,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:manage")),
    db: Session = Depends(get_db),
):
    """Create a new expense limit rule."""
    service = ExpenseLimitService(db)
    try:
        # Parse enum values
        scope_type = LimitScopeType(data.scope_type.upper())
        period_type = LimitPeriodType(data.period_type.upper())
        action_type = LimitActionType(data.action_type.upper())

        rule = service.create_rule(
            org_id,
            rule_code=data.rule_code,
            rule_name=data.rule_name,
            description=data.description,
            scope_type=scope_type,
            scope_id=data.scope_id,
            period_type=period_type,
            custom_period_days=data.custom_period_days,
            limit_amount=data.limit_amount,
            currency_code=data.currency_code,
            action_type=action_type,
            dimension_filters=data.dimension_filters.model_dump()
            if data.dimension_filters
            else None,
            action_config=data.action_config.model_dump()
            if data.action_config
            else None,
            priority=data.priority,
            effective_from=data.effective_from,
            effective_to=data.effective_to,
            is_active=data.is_active,
        )
        return ExpenseLimitRuleRead.model_validate(rule)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/rules/{rule_id}", response_model=ExpenseLimitRuleRead)
def update_limit_rule(
    rule_id: UUID,
    data: ExpenseLimitRuleUpdate,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:manage")),
    db: Session = Depends(get_db),
):
    """Update an expense limit rule."""
    service = ExpenseLimitService(db)
    try:
        update_data = data.model_dump(exclude_unset=True)

        # Parse action_type if provided
        if "action_type" in update_data and update_data["action_type"]:
            update_data["action_type"] = LimitActionType(
                update_data["action_type"].upper()
            )

        # Handle nested models
        if "dimension_filters" in update_data and update_data["dimension_filters"]:
            update_data["dimension_filters"] = update_data[
                "dimension_filters"
            ].model_dump()
        if "action_config" in update_data and update_data["action_config"]:
            update_data["action_config"] = update_data["action_config"].model_dump()

        rule = service.update_rule(org_id, rule_id, **update_data)
        return ExpenseLimitRuleRead.model_validate(rule)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_limit_rule(
    rule_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:manage")),
    db: Session = Depends(get_db),
):
    """Delete an expense limit rule."""
    service = ExpenseLimitService(db)
    try:
        service.delete_rule(org_id, rule_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# =============================================================================
# Approver Limits
# =============================================================================


@router.get("/approvers", response_model=ExpenseApproverLimitListResponse)
def list_approver_limits(
    scope_type: str | None = Query(None, description="Filter by scope type"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:read")),
    db: Session = Depends(get_db),
):
    """List expense approver limits."""
    service = ExpenseLimitService(db)
    result = service.list_approver_limits(
        org_id,
        scope_type=scope_type,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ExpenseApproverLimitListResponse(
        items=[ExpenseApproverLimitRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.get("/approvers/{approver_limit_id}", response_model=ExpenseApproverLimitRead)
def get_approver_limit(
    approver_limit_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:read")),
    db: Session = Depends(get_db),
):
    """Get an expense approver limit by ID."""
    service = ExpenseLimitService(db)
    try:
        limit = service.get_approver_limit(org_id, approver_limit_id)
        return ExpenseApproverLimitRead.model_validate(limit)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post(
    "/approvers",
    response_model=ExpenseApproverLimitRead,
    status_code=status.HTTP_201_CREATED,
)
def create_approver_limit(
    data: ExpenseApproverLimitCreate,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:manage")),
    db: Session = Depends(get_db),
):
    """Create a new expense approver limit."""
    service = ExpenseLimitService(db)
    try:
        limit = service.create_approver_limit(
            org_id,
            scope_type=data.scope_type,
            scope_id=data.scope_id,
            max_approval_amount=data.max_approval_amount,
            weekly_approval_budget=data.weekly_approval_budget,
            currency_code=data.currency_code,
            dimension_filters=data.dimension_filters.model_dump()
            if data.dimension_filters
            else None,
            escalate_to_employee_id=data.escalate_to_employee_id,
            escalate_to_grade_min_rank=data.escalate_to_grade_min_rank,
            can_approve_own_expenses=data.can_approve_own_expenses,
            is_active=data.is_active,
        )
        return ExpenseApproverLimitRead.model_validate(limit)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/approvers/{approver_limit_id}", response_model=ExpenseApproverLimitRead)
def update_approver_limit(
    approver_limit_id: UUID,
    data: ExpenseApproverLimitUpdate,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:manage")),
    db: Session = Depends(get_db),
):
    """Update an expense approver limit."""
    service = ExpenseLimitService(db)
    try:
        update_data = data.model_dump(exclude_unset=True)

        # Handle nested models
        if "dimension_filters" in update_data and update_data["dimension_filters"]:
            update_data["dimension_filters"] = update_data[
                "dimension_filters"
            ].model_dump()

        limit = service.update_approver_limit(org_id, approver_limit_id, **update_data)
        return ExpenseApproverLimitRead.model_validate(limit)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/approvers/{approver_limit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_approver_limit(
    approver_limit_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:manage")),
    db: Session = Depends(get_db),
):
    """Delete an expense approver limit."""
    service = ExpenseLimitService(db)
    try:
        service.delete_approver_limit(org_id, approver_limit_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# =============================================================================
# Evaluations (Audit Trail)
# =============================================================================


@router.get("/evaluations", response_model=ExpenseLimitEvaluationListResponse)
def list_evaluations(
    claim_id: UUID | None = Query(None, description="Filter by claim ID"),
    rule_id: UUID | None = Query(None, description="Filter by rule ID"),
    result: str | None = Query(None, description="Filter by result type"),
    from_date: date | None = Query(None, description="From date"),
    to_date: date | None = Query(None, description="To date"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:read")),
    db: Session = Depends(get_db),
):
    """List expense limit evaluations."""
    service = ExpenseLimitService(db)
    result_enum = parse_result_type(result)

    evaluations = service.list_evaluations(
        org_id,
        claim_id=claim_id,
        rule_id=rule_id,
        result=result_enum,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ExpenseLimitEvaluationListResponse(
        items=[ExpenseLimitEvaluationRead.model_validate(e) for e in evaluations.items],
        total=evaluations.total,
        offset=evaluations.offset,
        limit=evaluations.limit,
    )


# =============================================================================
# Evaluation & Eligible Approvers
# =============================================================================


@router.post("/evaluate", response_model=EvaluateLimitResponse)
def evaluate_claim_limits(
    data: EvaluateLimitRequest,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:read")),
    db: Session = Depends(get_db),
):
    """
    Evaluate expense limits for a claim.

    This is a preview endpoint that doesn't record the evaluation.
    Use this to check what would happen before actually submitting.
    """
    expense_service = ExpenseService(db)
    limit_service = ExpenseLimitService(db)

    try:
        claim = expense_service.get_claim(org_id, data.claim_id)
        result = limit_service.evaluate_claim(claim, preview_only=data.preview_only)

        # Convert eligible approvers
        eligible = [
            EligibleApprover(
                employee_id=a.employee_id,
                employee_name=a.employee_name,
                max_approval_amount=a.max_approval_amount,
                is_direct_manager=a.is_direct_manager,
                grade_rank=a.grade_rank,
            )
            for a in result.eligible_approvers
        ]

        return EvaluateLimitResponse(
            claim_id=claim.claim_id,
            claim_amount=claim.total_claimed_amount,
            result=result.result.value,
            result_message=result.message,
            triggered_rules=[
                ExpenseLimitRuleBrief(
                    rule_id=result.triggered_rule.rule_id,
                    rule_code=result.triggered_rule.rule_code,
                    rule_name=result.triggered_rule.rule_name,
                    limit_amount=result.triggered_rule.limit_amount,
                    action_type=result.triggered_rule.action_type.value,
                )
            ]
            if result.triggered_rule
            else [],
            eligible_approvers=eligible,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/eligible-approvers/{claim_id}")
def get_eligible_approvers(
    claim_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:claims:read")),
    db: Session = Depends(get_db),
):
    """
    Get eligible approvers for a claim.

    Returns a list of employees who have sufficient approval authority
    to approve this claim amount.
    """
    expense_service = ExpenseService(db)
    limit_service = ExpenseLimitService(db)

    try:
        return limit_service.get_eligible_approvers_for_claim(
            org_id=org_id,
            claim_id=claim_id,
            expense_service=expense_service,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# =============================================================================
# Usage Summary
# =============================================================================


@router.get("/usage/{employee_id}", response_model=EmployeeUsageSummary)
def get_employee_usage(
    employee_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    _auth: dict = Depends(require_tenant_permission("expense:policies:read")),
    db: Session = Depends(get_db),
):
    """
    Get expense usage summary for an employee.

    Includes current period usage and applicable limits.
    """
    service = ExpenseLimitService(db)
    try:
        summary = service.get_employee_usage_summary(org_id, employee_id)
        if not summary:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found"
            )
        return summary
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
