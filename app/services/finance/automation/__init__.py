"""
Automation Services.

Services for automation features: recurring transactions, workflows,
custom fields, and document templates.
"""

from app.services.finance.automation.recurring import (
    GenerationResult,
    RecurringService,
    RecurringTemplateInput,
    recurring_service,
)
from app.services.finance.automation.workflow import (
    ActionResult,
    TriggerContext,
    WorkflowRuleInput,
    WorkflowService,
    workflow_service,
)
from app.services.finance.automation.custom_fields import (
    CustomFieldInput,
    CustomFieldsService,
    custom_fields_service,
)

__all__ = [
    # Recurring
    "RecurringService",
    "RecurringTemplateInput",
    "GenerationResult",
    "recurring_service",
    # Workflow
    "WorkflowService",
    "WorkflowRuleInput",
    "TriggerContext",
    "ActionResult",
    "workflow_service",
    # Custom Fields
    "CustomFieldsService",
    "CustomFieldInput",
    "custom_fields_service",
]
