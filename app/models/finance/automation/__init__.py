"""
Automation Models.

Models for automation features: recurring transactions, workflows,
custom fields, and document templates.
"""

from app.models.finance.automation.recurring_template import (
    RecurringEntityType,
    RecurringFrequency,
    RecurringStatus,
    RecurringTemplate,
)
from app.models.finance.automation.recurring_log import (
    RecurringLog,
    RecurringLogStatus,
)
from app.models.finance.automation.workflow_rule import (
    ActionType,
    TriggerEvent,
    WorkflowEntityType,
    WorkflowRule,
)
from app.models.finance.automation.workflow_execution import (
    ExecutionStatus,
    WorkflowExecution,
)
from app.models.finance.automation.custom_field import (
    CustomFieldDefinition,
    CustomFieldEntityType,
    CustomFieldType,
)
from app.models.finance.automation.document_template import (
    DocumentTemplate,
    TemplateType,
)
from app.models.finance.automation.generated_document import (
    DocumentStatus,
    GeneratedDocument,
    OutputFormat,
)

__all__ = [
    # Recurring
    "RecurringTemplate",
    "RecurringEntityType",
    "RecurringFrequency",
    "RecurringStatus",
    "RecurringLog",
    "RecurringLogStatus",
    # Workflow
    "WorkflowRule",
    "WorkflowEntityType",
    "TriggerEvent",
    "ActionType",
    "WorkflowExecution",
    "ExecutionStatus",
    # Custom Fields
    "CustomFieldDefinition",
    "CustomFieldEntityType",
    "CustomFieldType",
    # Document Templates
    "DocumentTemplate",
    "TemplateType",
    # Generated Documents
    "GeneratedDocument",
    "DocumentStatus",
    "OutputFormat",
]
