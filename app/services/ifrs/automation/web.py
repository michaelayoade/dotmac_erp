"""
Automation web view service.

Provides view-focused data for automation web routes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ifrs.automation import (
    RecurringTemplate,
    RecurringLog,
    RecurringEntityType,
    RecurringFrequency,
    RecurringStatus,
    RecurringLogStatus,
    WorkflowRule,
    WorkflowExecution,
    WorkflowEntityType,
    TriggerEvent,
    ActionType,
    ExecutionStatus,
    CustomFieldDefinition,
    CustomFieldEntityType,
    CustomFieldType,
    DocumentTemplate,
    TemplateType,
)
from app.services.ifrs.automation.recurring import (
    recurring_service,
    RecurringTemplateInput,
)
from app.services.ifrs.automation.workflow import (
    workflow_service,
    WorkflowRuleInput,
)
from app.services.ifrs.automation.custom_fields import (
    custom_fields_service,
    CustomFieldInput,
)
from app.services.common import coerce_uuid


def _format_date(value: Optional[date]) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _format_datetime(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# =============================================================================
# Recurring Templates
# =============================================================================


def _recurring_status_label(status: RecurringStatus) -> str:
    labels = {
        RecurringStatus.ACTIVE: "Active",
        RecurringStatus.PAUSED: "Paused",
        RecurringStatus.COMPLETED: "Completed",
        RecurringStatus.EXPIRED: "Expired",
        RecurringStatus.CANCELLED: "Cancelled",
    }
    return labels.get(status, status.value)


def _recurring_status_color(status: RecurringStatus) -> str:
    colors = {
        RecurringStatus.ACTIVE: "emerald",
        RecurringStatus.PAUSED: "amber",
        RecurringStatus.COMPLETED: "slate",
        RecurringStatus.EXPIRED: "slate",
        RecurringStatus.CANCELLED: "rose",
    }
    return colors.get(status, "slate")


def _frequency_label(frequency: RecurringFrequency) -> str:
    labels = {
        RecurringFrequency.DAILY: "Daily",
        RecurringFrequency.WEEKLY: "Weekly",
        RecurringFrequency.BIWEEKLY: "Every 2 Weeks",
        RecurringFrequency.MONTHLY: "Monthly",
        RecurringFrequency.QUARTERLY: "Quarterly",
        RecurringFrequency.SEMI_ANNUALLY: "Semi-Annually",
        RecurringFrequency.ANNUALLY: "Annually",
    }
    return labels.get(frequency, frequency.value)


def _entity_type_label(entity_type: RecurringEntityType) -> str:
    labels = {
        RecurringEntityType.INVOICE: "Invoice",
        RecurringEntityType.BILL: "Bill",
        RecurringEntityType.EXPENSE: "Expense",
        RecurringEntityType.JOURNAL: "Journal Entry",
    }
    return labels.get(entity_type, entity_type.value)


def _recurring_list_view(template: RecurringTemplate, generated_count: int) -> dict:
    return {
        "template_id": str(template.template_id),
        "template_name": template.template_name,
        "entity_type": _entity_type_label(template.entity_type),
        "entity_type_raw": template.entity_type.value,
        "frequency": _frequency_label(template.frequency),
        "status": _recurring_status_label(template.status),
        "status_color": _recurring_status_color(template.status),
        "next_run_date": _format_date(template.next_run_date),
        "start_date": _format_date(template.start_date),
        "end_date": _format_date(template.end_date),
        "occurrences_count": template.occurrences_count,
        "occurrences_limit": template.occurrences_limit,
        "generated_count": generated_count,
        "auto_post": template.auto_post,
        "is_active": template.status == RecurringStatus.ACTIVE,
    }


def _recurring_detail_view(template: RecurringTemplate, logs: List[RecurringLog]) -> dict:
    return {
        "template_id": str(template.template_id),
        "template_name": template.template_name,
        "description": template.description,
        "entity_type": _entity_type_label(template.entity_type),
        "entity_type_raw": template.entity_type.value,
        "template_data": template.template_data,
        "frequency": _frequency_label(template.frequency),
        "frequency_raw": template.frequency.value,
        "schedule_config": template.schedule_config,
        "start_date": _format_date(template.start_date),
        "end_date": _format_date(template.end_date),
        "next_run_date": _format_date(template.next_run_date),
        "occurrences_limit": template.occurrences_limit,
        "occurrences_count": template.occurrences_count,
        "last_generated_at": _format_datetime(template.last_generated_at),
        "auto_post": template.auto_post,
        "auto_send": template.auto_send,
        "days_before_due": template.days_before_due,
        "notify_on_generation": template.notify_on_generation,
        "notify_email": template.notify_email,
        "status": _recurring_status_label(template.status),
        "status_raw": template.status.value,
        "status_color": _recurring_status_color(template.status),
        "created_at": _format_datetime(template.created_at),
        "is_active": template.status == RecurringStatus.ACTIVE,
        "is_paused": template.status == RecurringStatus.PAUSED,
        "logs": [_recurring_log_view(log) for log in logs],
    }


def _recurring_log_view(log: RecurringLog) -> dict:
    status_colors = {
        RecurringLogStatus.SUCCESS: "emerald",
        RecurringLogStatus.FAILED: "rose",
        RecurringLogStatus.SKIPPED: "amber",
    }
    return {
        "log_id": str(log.log_id),
        "scheduled_date": _format_date(log.scheduled_date),
        "generated_at": _format_datetime(log.generated_at),
        "status": log.status.value,
        "status_color": status_colors.get(log.status, "slate"),
        "generated_entity_type": log.generated_entity_type,
        "generated_entity_id": str(log.generated_entity_id) if log.generated_entity_id else None,
        "generated_entity_number": log.generated_entity_number,
        "error_message": log.error_message,
    }


# =============================================================================
# Workflow Rules
# =============================================================================


def _workflow_entity_type_label(entity_type: WorkflowEntityType) -> str:
    labels = {
        WorkflowEntityType.INVOICE: "Invoice",
        WorkflowEntityType.BILL: "Bill",
        WorkflowEntityType.EXPENSE: "Expense",
        WorkflowEntityType.JOURNAL: "Journal Entry",
        WorkflowEntityType.PAYMENT: "Payment",
        WorkflowEntityType.CUSTOMER: "Customer",
        WorkflowEntityType.SUPPLIER: "Supplier",
        WorkflowEntityType.QUOTE: "Quote",
        WorkflowEntityType.SALES_ORDER: "Sales Order",
        WorkflowEntityType.PURCHASE_ORDER: "Purchase Order",
        WorkflowEntityType.BANK_TRANSACTION: "Bank Transaction",
        WorkflowEntityType.RECONCILIATION: "Reconciliation",
    }
    return labels.get(entity_type, entity_type.value)


def _trigger_event_label(event: TriggerEvent) -> str:
    labels = {
        TriggerEvent.ON_CREATE: "When Created",
        TriggerEvent.ON_UPDATE: "When Updated",
        TriggerEvent.ON_DELETE: "When Deleted",
        TriggerEvent.ON_STATUS_CHANGE: "When Status Changes",
        TriggerEvent.ON_FIELD_CHANGE: "When Field Changes",
        TriggerEvent.ON_APPROVAL: "When Approved",
        TriggerEvent.ON_REJECTION: "When Rejected",
        TriggerEvent.ON_DUE_DATE: "On Due Date",
        TriggerEvent.ON_OVERDUE: "When Overdue",
        TriggerEvent.ON_THRESHOLD: "When Threshold Met",
    }
    return labels.get(event, event.value)


def _action_type_label(action_type: ActionType) -> str:
    labels = {
        ActionType.SEND_EMAIL: "Send Email",
        ActionType.SEND_NOTIFICATION: "Send Notification",
        ActionType.VALIDATE: "Validate",
        ActionType.UPDATE_FIELD: "Update Field",
        ActionType.CREATE_TASK: "Create Task",
        ActionType.WEBHOOK: "Call Webhook",
        ActionType.BLOCK: "Block Action",
    }
    return labels.get(action_type, action_type.value)


def _action_type_icon(action_type: ActionType) -> str:
    icons = {
        ActionType.SEND_EMAIL: "envelope",
        ActionType.SEND_NOTIFICATION: "bell",
        ActionType.VALIDATE: "shield-check",
        ActionType.UPDATE_FIELD: "pencil",
        ActionType.CREATE_TASK: "clipboard-list",
        ActionType.WEBHOOK: "globe-alt",
        ActionType.BLOCK: "ban",
    }
    return icons.get(action_type, "cog")


def _workflow_list_view(rule: WorkflowRule) -> dict:
    return {
        "rule_id": str(rule.rule_id),
        "rule_name": rule.rule_name,
        "description": rule.description,
        "entity_type": _workflow_entity_type_label(rule.entity_type),
        "entity_type_raw": rule.entity_type.value,
        "trigger_event": _trigger_event_label(rule.trigger_event),
        "trigger_event_raw": rule.trigger_event.value,
        "action_type": _action_type_label(rule.action_type),
        "action_type_raw": rule.action_type.value,
        "action_icon": _action_type_icon(rule.action_type),
        "priority": rule.priority,
        "is_active": rule.is_active,
        "execution_count": rule.execution_count,
        "success_count": rule.success_count,
        "failure_count": rule.failure_count,
        "success_rate": (
            round(rule.success_count / rule.execution_count * 100, 1)
            if rule.execution_count > 0
            else 0
        ),
        "last_executed_at": _format_datetime(rule.last_executed_at),
    }


def _workflow_detail_view(rule: WorkflowRule, executions: List[WorkflowExecution]) -> dict:
    return {
        "rule_id": str(rule.rule_id),
        "rule_name": rule.rule_name,
        "description": rule.description,
        "entity_type": _workflow_entity_type_label(rule.entity_type),
        "entity_type_raw": rule.entity_type.value,
        "trigger_event": _trigger_event_label(rule.trigger_event),
        "trigger_event_raw": rule.trigger_event.value,
        "trigger_conditions": rule.trigger_conditions,
        "action_type": _action_type_label(rule.action_type),
        "action_type_raw": rule.action_type.value,
        "action_config": rule.action_config,
        "priority": rule.priority,
        "stop_on_match": rule.stop_on_match,
        "execute_async": rule.execute_async,
        "is_active": rule.is_active,
        "execution_count": rule.execution_count,
        "success_count": rule.success_count,
        "failure_count": rule.failure_count,
        "last_executed_at": _format_datetime(rule.last_executed_at),
        "created_at": _format_datetime(rule.created_at),
        "executions": [_execution_view(ex) for ex in executions],
    }


def _execution_status_color(status: ExecutionStatus) -> str:
    colors = {
        ExecutionStatus.PENDING: "slate",
        ExecutionStatus.RUNNING: "blue",
        ExecutionStatus.SUCCESS: "emerald",
        ExecutionStatus.FAILED: "rose",
        ExecutionStatus.SKIPPED: "amber",
        ExecutionStatus.BLOCKED: "orange",
    }
    return colors.get(status, "slate")


def _execution_view(execution: WorkflowExecution) -> dict:
    return {
        "execution_id": str(execution.execution_id),
        "entity_type": execution.entity_type,
        "entity_id": str(execution.entity_id),
        "trigger_event": execution.trigger_event,
        "triggered_at": _format_datetime(execution.triggered_at),
        "started_at": _format_datetime(execution.started_at),
        "completed_at": _format_datetime(execution.completed_at),
        "duration_ms": execution.duration_ms,
        "status": execution.status.value,
        "status_color": _execution_status_color(execution.status),
        "error_message": execution.error_message,
        "result": execution.result,
    }


# =============================================================================
# Custom Fields
# =============================================================================


def _custom_field_entity_type_label(entity_type: CustomFieldEntityType) -> str:
    labels = {
        CustomFieldEntityType.CUSTOMER: "Customer",
        CustomFieldEntityType.SUPPLIER: "Supplier",
        CustomFieldEntityType.INVOICE: "Invoice",
        CustomFieldEntityType.BILL: "Bill",
        CustomFieldEntityType.EXPENSE: "Expense",
        CustomFieldEntityType.QUOTE: "Quote",
        CustomFieldEntityType.SALES_ORDER: "Sales Order",
        CustomFieldEntityType.PURCHASE_ORDER: "Purchase Order",
        CustomFieldEntityType.ITEM: "Inventory Item",
        CustomFieldEntityType.PROJECT: "Project",
        CustomFieldEntityType.ASSET: "Fixed Asset",
        CustomFieldEntityType.JOURNAL: "Journal Entry",
        CustomFieldEntityType.PAYMENT: "Payment",
    }
    return labels.get(entity_type, entity_type.value)


def _field_type_label(field_type: CustomFieldType) -> str:
    labels = {
        CustomFieldType.TEXT: "Text",
        CustomFieldType.TEXTAREA: "Multi-line Text",
        CustomFieldType.NUMBER: "Number",
        CustomFieldType.DECIMAL: "Decimal",
        CustomFieldType.DATE: "Date",
        CustomFieldType.DATETIME: "Date & Time",
        CustomFieldType.BOOLEAN: "Yes/No",
        CustomFieldType.SELECT: "Dropdown",
        CustomFieldType.MULTISELECT: "Multi-Select",
        CustomFieldType.EMAIL: "Email",
        CustomFieldType.URL: "URL",
        CustomFieldType.PHONE: "Phone",
        CustomFieldType.CURRENCY: "Currency",
    }
    return labels.get(field_type, field_type.value)


def _custom_field_list_view(field: CustomFieldDefinition) -> dict:
    return {
        "field_id": str(field.field_id),
        "entity_type": _custom_field_entity_type_label(field.entity_type),
        "entity_type_raw": field.entity_type.value,
        "field_code": field.field_code,
        "field_name": field.field_name,
        "field_type": _field_type_label(field.field_type),
        "field_type_raw": field.field_type.value,
        "is_required": field.is_required,
        "display_order": field.display_order,
        "section_name": field.section_name,
        "is_active": field.is_active,
        "show_in_list": field.show_in_list,
        "show_in_form": field.show_in_form,
    }


def _custom_field_detail_view(field: CustomFieldDefinition) -> dict:
    return {
        "field_id": str(field.field_id),
        "entity_type": _custom_field_entity_type_label(field.entity_type),
        "entity_type_raw": field.entity_type.value,
        "field_code": field.field_code,
        "field_name": field.field_name,
        "description": field.description,
        "field_type": _field_type_label(field.field_type),
        "field_type_raw": field.field_type.value,
        "field_options": field.field_options,
        "is_required": field.is_required,
        "default_value": field.default_value,
        "validation_regex": field.validation_regex,
        "validation_message": field.validation_message,
        "min_value": field.min_value,
        "max_value": field.max_value,
        "max_length": field.max_length,
        "display_order": field.display_order,
        "section_name": field.section_name,
        "placeholder": field.placeholder,
        "help_text": field.help_text,
        "show_in_list": field.show_in_list,
        "show_in_form": field.show_in_form,
        "show_in_detail": field.show_in_detail,
        "show_in_print": field.show_in_print,
        "is_active": field.is_active,
        "created_at": _format_datetime(field.created_at),
    }


# =============================================================================
# Document Templates
# =============================================================================


def _template_type_label(template_type: TemplateType) -> str:
    labels = {
        TemplateType.INVOICE: "Invoice",
        TemplateType.CREDIT_NOTE: "Credit Note",
        TemplateType.QUOTE: "Quote",
        TemplateType.SALES_ORDER: "Sales Order",
        TemplateType.PURCHASE_ORDER: "Purchase Order",
        TemplateType.BILL: "Bill",
        TemplateType.RECEIPT: "Receipt",
        TemplateType.STATEMENT: "Statement",
        TemplateType.PAYMENT_RECEIPT: "Payment Receipt",
        TemplateType.EMAIL_INVOICE: "Email - Invoice",
        TemplateType.EMAIL_QUOTE: "Email - Quote",
        TemplateType.EMAIL_REMINDER: "Email - Reminder",
        TemplateType.EMAIL_OVERDUE: "Email - Overdue",
        TemplateType.EMAIL_PAYMENT: "Email - Payment",
        TemplateType.EMAIL_NOTIFICATION: "Email - Notification",
    }
    return labels.get(template_type, template_type.value)


def _template_type_category(template_type: TemplateType) -> str:
    if template_type.value.startswith("EMAIL_"):
        return "Email Templates"
    return "Document Templates"


def _document_template_list_view(template: DocumentTemplate) -> dict:
    return {
        "template_id": str(template.template_id),
        "template_type": _template_type_label(template.template_type),
        "template_type_raw": template.template_type.value,
        "template_name": template.template_name,
        "description": template.description,
        "is_default": template.is_default,
        "is_active": template.is_active,
        "version": template.version,
        "category": _template_type_category(template.template_type),
    }


def _document_template_detail_view(template: DocumentTemplate) -> dict:
    return {
        "template_id": str(template.template_id),
        "template_type": _template_type_label(template.template_type),
        "template_type_raw": template.template_type.value,
        "template_name": template.template_name,
        "description": template.description,
        "template_content": template.template_content,
        "css_styles": template.css_styles,
        "header_config": template.header_config,
        "footer_config": template.footer_config,
        "page_size": template.page_size,
        "page_orientation": template.page_orientation,
        "page_margins": template.page_margins,
        "email_subject": template.email_subject,
        "email_from_name": template.email_from_name,
        "is_default": template.is_default,
        "is_active": template.is_active,
        "version": template.version,
        "created_at": _format_datetime(template.created_at),
    }


# =============================================================================
# Web Service Class
# =============================================================================


class AutomationWebService:
    """Service for automation web views."""

    # -------------------------------------------------------------------------
    # Recurring Templates
    # -------------------------------------------------------------------------

    def list_recurring_context(
        self,
        db: Session,
        organization_id: str,
        entity_type: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """Get context for recurring templates list page."""
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * page_size

        # Build query
        query = select(RecurringTemplate).where(
            RecurringTemplate.organization_id == org_id
        )

        if entity_type:
            try:
                et = RecurringEntityType(entity_type)
                query = query.where(RecurringTemplate.entity_type == et)
            except ValueError:
                pass

        if status:
            try:
                st = RecurringStatus(status)
                query = query.where(RecurringTemplate.status == st)
            except ValueError:
                pass

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = db.execute(count_query).scalar() or 0

        # Get templates
        query = query.order_by(
            RecurringTemplate.status.asc(),
            RecurringTemplate.next_run_date.asc(),
        )
        query = query.offset(offset).limit(page_size)
        templates = list(db.execute(query).scalars().all())

        # Get generation counts
        template_ids = [t.template_id for t in templates]
        if template_ids:
            count_subq = (
                select(
                    RecurringLog.template_id,
                    func.count().label("count"),
                )
                .where(RecurringLog.template_id.in_(template_ids))
                .group_by(RecurringLog.template_id)
            )
            counts = {row.template_id: row.count for row in db.execute(count_subq)}
        else:
            counts = {}

        items = [
            _recurring_list_view(t, counts.get(t.template_id, 0))
            for t in templates
        ]

        return {
            "templates": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "entity_types": [
                {"value": et.value, "label": _entity_type_label(et)}
                for et in RecurringEntityType
            ],
            "statuses": [
                {"value": st.value, "label": _recurring_status_label(st)}
                for st in RecurringStatus
            ],
            "filters": {
                "entity_type": entity_type,
                "status": status,
            },
        }

    def recurring_form_context(
        self,
        db: Session,
        organization_id: str,
        template_id: Optional[str] = None,
    ) -> dict:
        """Get context for recurring template form."""
        context = {
            "template": None,
            "entity_types": [
                {"value": et.value, "label": _entity_type_label(et)}
                for et in RecurringEntityType
            ],
            "frequencies": [
                {"value": f.value, "label": _frequency_label(f)}
                for f in RecurringFrequency
            ],
            "is_edit": False,
        }

        if template_id:
            template = recurring_service.get(db, coerce_uuid(template_id))
            if template:
                logs = recurring_service.get_logs(db, template.template_id, limit=10)
                context["template"] = _recurring_detail_view(template, logs)
                context["is_edit"] = True

        return context

    def recurring_detail_context(
        self,
        db: Session,
        organization_id: str,
        template_id: str,
    ) -> dict:
        """Get context for recurring template detail page."""
        template = recurring_service.get(db, coerce_uuid(template_id))
        if not template:
            return {"template": None, "error": "Template not found"}

        logs = recurring_service.get_logs(db, template.template_id, limit=20)

        return {
            "template": _recurring_detail_view(template, logs),
        }

    def build_recurring_input(self, form_data: dict) -> RecurringTemplateInput:
        """Build RecurringTemplateInput from form data."""
        schedule_config = {}
        if form_data.get("day_of_month"):
            schedule_config["day_of_month"] = int(form_data["day_of_month"])
        if form_data.get("day_of_week"):
            schedule_config["day_of_week"] = int(form_data["day_of_week"])

        return RecurringTemplateInput(
            template_name=form_data["template_name"],
            entity_type=RecurringEntityType(form_data["entity_type"]),
            template_data=form_data.get("template_data", {}),
            frequency=RecurringFrequency(form_data["frequency"]),
            schedule_config=schedule_config,
            start_date=_parse_date(form_data.get("start_date")) or date.today(),
            end_date=_parse_date(form_data.get("end_date")),
            occurrences_limit=int(form_data["occurrences_limit"]) if form_data.get("occurrences_limit") else None,
            auto_post=form_data.get("auto_post") == "on",
            auto_send=form_data.get("auto_send") == "on",
            days_before_due=int(form_data.get("days_before_due", 30)),
            notify_on_generation=form_data.get("notify_on_generation") != "off",
            notify_email=form_data.get("notify_email"),
            description=form_data.get("description"),
        )

    # -------------------------------------------------------------------------
    # Workflow Rules
    # -------------------------------------------------------------------------

    def list_workflows_context(
        self,
        db: Session,
        organization_id: str,
        entity_type: Optional[str] = None,
        trigger_event: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """Get context for workflow rules list page."""
        org_id = coerce_uuid(organization_id)

        # Parse entity_type filter
        et = None
        if entity_type:
            try:
                et = WorkflowEntityType(entity_type)
            except ValueError:
                pass

        # Parse trigger_event filter
        te = None
        if trigger_event:
            try:
                te = TriggerEvent(trigger_event)
            except ValueError:
                pass

        rules = workflow_service.list(
            db,
            org_id,
            entity_type=et,
            trigger_event=te,
            is_active=is_active,
        )

        items = [_workflow_list_view(r) for r in rules]

        return {
            "rules": items,
            "total": len(items),
            "entity_types": [
                {"value": et.value, "label": _workflow_entity_type_label(et)}
                for et in WorkflowEntityType
            ],
            "trigger_events": [
                {"value": te.value, "label": _trigger_event_label(te)}
                for te in TriggerEvent
            ],
            "action_types": [
                {"value": at.value, "label": _action_type_label(at)}
                for at in ActionType
            ],
            "filters": {
                "entity_type": entity_type,
                "trigger_event": trigger_event,
                "is_active": is_active,
            },
        }

    def workflow_form_context(
        self,
        db: Session,
        organization_id: str,
        rule_id: Optional[str] = None,
    ) -> dict:
        """Get context for workflow rule form."""
        context = {
            "rule": None,
            "entity_types": [
                {"value": et.value, "label": _workflow_entity_type_label(et)}
                for et in WorkflowEntityType
            ],
            "trigger_events": [
                {"value": te.value, "label": _trigger_event_label(te)}
                for te in TriggerEvent
            ],
            "action_types": [
                {"value": at.value, "label": _action_type_label(at)}
                for at in ActionType
            ],
            "is_edit": False,
        }

        if rule_id:
            rule = workflow_service.get(db, coerce_uuid(rule_id))
            if rule:
                executions = workflow_service.get_executions(
                    db, rule_id=rule.rule_id, limit=10
                )
                context["rule"] = _workflow_detail_view(rule, executions)
                context["is_edit"] = True

        return context

    def workflow_detail_context(
        self,
        db: Session,
        organization_id: str,
        rule_id: str,
    ) -> dict:
        """Get context for workflow rule detail page."""
        rule = workflow_service.get(db, coerce_uuid(rule_id))
        if not rule:
            return {"rule": None, "error": "Rule not found"}

        executions = workflow_service.get_executions(
            db, rule_id=rule.rule_id, limit=20
        )

        return {
            "rule": _workflow_detail_view(rule, executions),
        }

    def build_workflow_input(self, form_data: dict) -> WorkflowRuleInput:
        """Build WorkflowRuleInput from form data."""
        return WorkflowRuleInput(
            rule_name=form_data["rule_name"],
            entity_type=WorkflowEntityType(form_data["entity_type"]),
            trigger_event=TriggerEvent(form_data["trigger_event"]),
            action_type=ActionType(form_data["action_type"]),
            trigger_conditions=form_data.get("trigger_conditions", {}),
            action_config=form_data.get("action_config", {}),
            description=form_data.get("description"),
            priority=int(form_data.get("priority", 100)),
            stop_on_match=form_data.get("stop_on_match") == "on",
            execute_async=form_data.get("execute_async") != "off",
        )

    # -------------------------------------------------------------------------
    # Custom Fields
    # -------------------------------------------------------------------------

    def list_custom_fields_context(
        self,
        db: Session,
        organization_id: str,
        entity_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """Get context for custom fields list page."""
        org_id = coerce_uuid(organization_id)

        fields = custom_fields_service.list_all(
            db,
            org_id,
            is_active=is_active,
            limit=page_size,
            offset=(page - 1) * page_size,
        )

        # Filter by entity type if specified
        if entity_type:
            try:
                et = CustomFieldEntityType(entity_type)
                fields = [f for f in fields if f.entity_type == et]
            except ValueError:
                pass

        items = [_custom_field_list_view(f) for f in fields]

        # Group by entity type
        grouped: Dict[str, List[dict]] = {}
        for item in items:
            key = item["entity_type"]
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(item)

        return {
            "fields": items,
            "grouped_fields": grouped,
            "total": len(items),
            "entity_types": [
                {"value": et.value, "label": _custom_field_entity_type_label(et)}
                for et in CustomFieldEntityType
            ],
            "field_types": [
                {"value": ft.value, "label": _field_type_label(ft)}
                for ft in CustomFieldType
            ],
            "filters": {
                "entity_type": entity_type,
                "is_active": is_active,
            },
        }

    def custom_field_form_context(
        self,
        db: Session,
        organization_id: str,
        field_id: Optional[str] = None,
    ) -> dict:
        """Get context for custom field form."""
        context = {
            "field": None,
            "entity_types": [
                {"value": et.value, "label": _custom_field_entity_type_label(et)}
                for et in CustomFieldEntityType
            ],
            "field_types": [
                {"value": ft.value, "label": _field_type_label(ft)}
                for ft in CustomFieldType
            ],
            "is_edit": False,
        }

        if field_id:
            field = custom_fields_service.get(db, coerce_uuid(field_id))
            if field:
                context["field"] = _custom_field_detail_view(field)
                context["is_edit"] = True

        return context

    def custom_field_detail_context(
        self,
        db: Session,
        organization_id: str,
        field_id: str,
    ) -> dict:
        """Get context for custom field detail page."""
        field = custom_fields_service.get(db, coerce_uuid(field_id))
        if not field:
            return {"field": None, "error": "Field not found"}

        return {
            "field": _custom_field_detail_view(field),
        }

    def build_custom_field_input(self, form_data: dict) -> CustomFieldInput:
        """Build CustomFieldInput from form data."""
        # Parse field options for SELECT/MULTISELECT
        field_options = None
        options_text = form_data.get("field_options_text", "")
        if options_text:
            options = [o.strip() for o in options_text.split("\n") if o.strip()]
            field_options = {"options": options}

        return CustomFieldInput(
            entity_type=CustomFieldEntityType(form_data["entity_type"]),
            field_code=form_data["field_code"],
            field_name=form_data["field_name"],
            field_type=CustomFieldType(form_data["field_type"]),
            description=form_data.get("description"),
            field_options=field_options,
            is_required=form_data.get("is_required") == "on",
            default_value=form_data.get("default_value"),
            validation_regex=form_data.get("validation_regex"),
            validation_message=form_data.get("validation_message"),
            min_value=form_data.get("min_value"),
            max_value=form_data.get("max_value"),
            max_length=int(form_data["max_length"]) if form_data.get("max_length") else None,
            display_order=int(form_data.get("display_order", 0)),
            section_name=form_data.get("section_name"),
            placeholder=form_data.get("placeholder"),
            help_text=form_data.get("help_text"),
            show_in_list=form_data.get("show_in_list") == "on",
            show_in_form=form_data.get("show_in_form") != "off",
            show_in_detail=form_data.get("show_in_detail") != "off",
            show_in_print=form_data.get("show_in_print") == "on",
        )

    # -------------------------------------------------------------------------
    # Document Templates
    # -------------------------------------------------------------------------

    def list_templates_context(
        self,
        db: Session,
        organization_id: str,
        template_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """Get context for document templates list page."""
        org_id = coerce_uuid(organization_id)

        query = select(DocumentTemplate).where(
            DocumentTemplate.organization_id == org_id
        )

        if template_type:
            try:
                tt = TemplateType(template_type)
                query = query.where(DocumentTemplate.template_type == tt)
            except ValueError:
                pass

        if is_active is not None:
            query = query.where(DocumentTemplate.is_active == is_active)

        query = query.order_by(
            DocumentTemplate.template_type,
            DocumentTemplate.template_name,
        )

        templates = list(db.execute(query).scalars().all())
        items = [_document_template_list_view(t) for t in templates]

        # Group by category
        doc_templates = [i for i in items if i["category"] == "Document Templates"]
        email_templates = [i for i in items if i["category"] == "Email Templates"]

        return {
            "templates": items,
            "doc_templates": doc_templates,
            "email_templates": email_templates,
            "total": len(items),
            "template_types": [
                {"value": tt.value, "label": _template_type_label(tt)}
                for tt in TemplateType
            ],
            "filters": {
                "template_type": template_type,
                "is_active": is_active,
            },
        }

    def template_form_context(
        self,
        db: Session,
        organization_id: str,
        template_id: Optional[str] = None,
    ) -> dict:
        """Get context for document template form."""
        context = {
            "template": None,
            "template_types": [
                {"value": tt.value, "label": _template_type_label(tt)}
                for tt in TemplateType
            ],
            "is_edit": False,
        }

        if template_id:
            template = db.get(DocumentTemplate, coerce_uuid(template_id))
            if template:
                context["template"] = _document_template_detail_view(template)
                context["is_edit"] = True

        return context

    def template_detail_context(
        self,
        db: Session,
        organization_id: str,
        template_id: str,
    ) -> dict:
        """Get context for document template detail page."""
        template = db.get(DocumentTemplate, coerce_uuid(template_id))
        if not template:
            return {"template": None, "error": "Template not found"}

        return {
            "template": _document_template_detail_view(template),
        }


# Singleton instance
automation_web_service = AutomationWebService()
