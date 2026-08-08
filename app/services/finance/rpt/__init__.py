"""
Reporting Services.

This module provides services for financial reporting including report definitions,
financial statements, disclosure checklists, and scheduled reports per IAS 1.
"""

from app.services.finance.rpt.analysis_cube import (
    AnalysisCubeService,
    CubeQueryResult,
)
from app.services.finance.rpt.disclosure_checklist import (
    DisclosureChecklistService,
    DisclosureCompletionInput,
    DisclosureItemInput,
    DisclosureSummary,
    StandardSummary,
    disclosure_checklist_service,
)
from app.services.finance.rpt.financial_statement import (
    FinancialStatementResult,
    FinancialStatementService,
    StatementLineInput,
    StatementLineResult,
    financial_statement_service,
)
from app.services.finance.rpt.report_definition import (
    ReportColumn,
    ReportDefinitionInput,
    ReportDefinitionService,
    ReportFilter,
    report_definition_service,
)
from app.services.finance.rpt.report_instance import (
    ReportGenerationRequest,
    ReportGenerationResult,
    ReportInstanceService,
    report_instance_service,
)
from app.services.finance.rpt.report_scheduler import (
    ReportSchedulerService,
    ScheduleExecution,
    ScheduleInput,
    report_scheduler_service,
)

__all__ = [
    # Analysis Cubes
    "AnalysisCubeService",
    "CubeQueryResult",
    # Report Definition
    "ReportDefinitionService",
    "ReportDefinitionInput",
    "ReportColumn",
    "ReportFilter",
    "report_definition_service",
    # Financial Statement
    "FinancialStatementService",
    "StatementLineInput",
    "StatementLineResult",
    "FinancialStatementResult",
    "financial_statement_service",
    # Report Instance
    "ReportInstanceService",
    "ReportGenerationRequest",
    "ReportGenerationResult",
    "report_instance_service",
    # Disclosure Checklist
    "DisclosureChecklistService",
    "DisclosureItemInput",
    "DisclosureCompletionInput",
    "DisclosureSummary",
    "StandardSummary",
    "disclosure_checklist_service",
    # Report Scheduler
    "ReportSchedulerService",
    "ScheduleInput",
    "ScheduleExecution",
    "report_scheduler_service",
]

from app.services.setting_domain_declaration import ModuleSettingDomains  # noqa: E402

# Setting domain(s) this module owns — report presentation — format, page size, watermark.
# Validated by `app.services.setting_domains` at startup and at every write;
# see that module for why ownership lives here rather than in a central list.
SETTING_DOMAINS = ModuleSettingDomains(setting_domains=("reporting",))
