"""
Help Center content provider.

Centralized manuals, how-to journeys, troubleshooting, and role playbooks.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)


EXPECTED_MODULE_KEYS = [
    "finance",
    "people",
    "inventory",
    "procurement",
    "support",
    "projects",
    "fleet",
    "public_sector",
    "expense",
    "coach",
    "settings",
    "self_service",
]


MODULE_GUIDES: dict[str, dict[str, Any]] = {
    "finance": {
        "title": "Finance",
        "summary": "General Ledger, AP/AR, Banking, Tax, and statutory reporting.",
        "manual_links": [
            {"label": "Dashboard", "href": "/finance/dashboard"},
            {"label": "Reports", "href": "/finance/reports"},
            {"label": "Chart of Accounts", "href": "/finance/gl/accounts"},
        ],
        "journeys": [
            {
                "title": "Month-End Close",
                "outcome": "Close fiscal periods with validated postings and reports.",
                "steps": [
                    "Review unposted journals and outstanding reversals.",
                    "Validate trial balance and reconciliation reports.",
                    "Close period and publish financial statements.",
                ],
                "href": "/finance/gl/periods",
            },
            {
                "title": "Receivables to Cash",
                "outcome": "Issue invoices, collect receipts, and monitor AR aging.",
                "steps": [
                    "Create and submit AR invoices.",
                    "Record receipts and apply against invoices.",
                    "Track overdue balances in AR aging.",
                ],
                "href": "/finance/ar/invoices",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "Period close fails",
                "checks": [
                    "Ensure all journals are posted.",
                    "Confirm no blocked approval workflows are pending.",
                    "Review error message details in period close screen.",
                ],
            }
        ],
    },
    "people": {
        "title": "People & HR",
        "summary": "Employee records, onboarding, attendance, payroll, and performance.",
        "manual_links": [
            {"label": "Dashboard", "href": "/people/dashboard"},
            {"label": "Employees", "href": "/people/hr/employees"},
            {"label": "Payroll Runs", "href": "/people/payroll/runs"},
        ],
        "journeys": [
            {
                "title": "Employee Lifecycle",
                "outcome": "Manage onboarding, profile updates, and transitions.",
                "steps": [
                    "Create employee profile and assign department/designation.",
                    "Track onboarding tasks and required documents.",
                    "Manage transfers, promotions, and status changes.",
                ],
                "href": "/people/hr/employees",
            },
            {
                "title": "Payroll Execution",
                "outcome": "Prepare, validate, and finalize payroll safely.",
                "steps": [
                    "Open payroll run and include eligible employees.",
                    "Validate slips, taxes, and statutory contributions.",
                    "Approve run and publish payslips.",
                ],
                "href": "/people/payroll/runs",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "Missing employee in payroll",
                "checks": [
                    "Verify employee status is active.",
                    "Check payroll structure and effective dates.",
                    "Confirm assignment to current payroll cycle.",
                ],
            }
        ],
    },
    "inventory": {
        "title": "Inventory",
        "summary": "Items, warehouses, stock movement, counts, and valuation.",
        "manual_links": [
            {"label": "Items", "href": "/inventory/items"},
            {"label": "Warehouses", "href": "/inventory/warehouses"},
            {"label": "Transactions", "href": "/inventory/transactions"},
        ],
        "journeys": [
            {
                "title": "Item Setup to Stock Use",
                "outcome": "Configure items and track receipts/issues correctly.",
                "steps": [
                    "Create item with category and tracking settings.",
                    "Receive stock into warehouse.",
                    "Issue/transfer stock and review transaction detail.",
                ],
                "href": "/inventory/items",
            },
            {
                "title": "Cycle Count and Adjustment",
                "outcome": "Count inventory and post controlled adjustments.",
                "steps": [
                    "Create count plan for target warehouses.",
                    "Enter counted quantities and variance notes.",
                    "Post approved adjustments and review impact.",
                ],
                "href": "/inventory/counts",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "Negative stock detected",
                "checks": [
                    "Confirm backdated issues were not posted before receipts.",
                    "Review lot/serial assignments on issue transactions.",
                    "Run stock-on-hand report for affected item.",
                ],
            }
        ],
    },
    "procurement": {
        "title": "Procurement",
        "summary": "Planning, requisitions, RFQs, evaluations, and contracts.",
        "manual_links": [
            {"label": "Dashboard", "href": "/procurement"},
            {"label": "Requisitions", "href": "/procurement/requisitions"},
            {"label": "RFQs", "href": "/procurement/rfqs"},
        ],
        "journeys": [
            {
                "title": "Requisition to RFQ",
                "outcome": "Move approved demand into vendor sourcing workflow.",
                "steps": [
                    "Create and submit requisition.",
                    "Approve requisition according to thresholds.",
                    "Generate RFQ and invite vendors.",
                ],
                "href": "/procurement/requisitions",
            },
            {
                "title": "RFQ to Contract",
                "outcome": "Evaluate bids and award signed contracts.",
                "steps": [
                    "Review RFQ responses and score evaluations.",
                    "Finalize selected vendor and commercial terms.",
                    "Create contract and track obligations.",
                ],
                "href": "/procurement/contracts",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "RFQ cannot be issued",
                "checks": [
                    "Verify requisition approval status.",
                    "Confirm required vendor data is complete.",
                    "Check threshold policy and permissions.",
                ],
            }
        ],
    },
    "support": {
        "title": "Support",
        "summary": "Ticket intake, SLA tracking, assignment, and resolution.",
        "manual_links": [
            {"label": "Tickets", "href": "/support/tickets"},
            {"label": "SLA Dashboard", "href": "/support/dashboard"},
            {"label": "Teams", "href": "/support/teams"},
        ],
        "journeys": [
            {
                "title": "Ticket Triage",
                "outcome": "Classify and route new tickets to the right team.",
                "steps": [
                    "Review priority, category, and customer context.",
                    "Assign to team/member with availability.",
                    "Set status and SLA tracking milestones.",
                ],
                "href": "/support/tickets",
            },
            {
                "title": "Resolution Workflow",
                "outcome": "Resolve tickets with auditable comments and attachments.",
                "steps": [
                    "Work updates through ticket comments.",
                    "Attach supporting evidence and customer communication.",
                    "Resolve and verify closure quality.",
                ],
                "href": "/support/dashboard",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "Ticket breaches SLA unexpectedly",
                "checks": [
                    "Verify SLA response/resolution defaults in settings.",
                    "Check assignment timestamp and status transitions.",
                    "Review holiday/working-hour assumptions if applicable.",
                ],
            }
        ],
    },
    "projects": {
        "title": "Projects",
        "summary": "Project delivery, milestones, tasks, and utilization tracking.",
        "manual_links": [
            {"label": "Projects", "href": "/projects"},
            {"label": "Tasks", "href": "/projects/tasks"},
            {"label": "Templates", "href": "/projects/templates"},
        ],
        "journeys": [
            {
                "title": "Project Setup and Planning",
                "outcome": "Define scope, milestones, and owners before execution.",
                "steps": [
                    "Create project with dates and ownership.",
                    "Load template or create milestones/tasks.",
                    "Assign resources and publish plan.",
                ],
                "href": "/projects",
            },
            {
                "title": "Task Delivery and Reporting",
                "outcome": "Execute tasks and monitor progress against milestones.",
                "steps": [
                    "Update task status and dependencies.",
                    "Track time/effort and blockers.",
                    "Review project dashboard and utilization report.",
                ],
                "href": "/projects/tasks",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "Project progress appears stale",
                "checks": [
                    "Confirm task statuses are being updated.",
                    "Review milestone completion criteria.",
                    "Check assignment and workload visibility.",
                ],
            }
        ],
    },
    "fleet": {
        "title": "Fleet",
        "summary": "Vehicles, maintenance, reservations, incidents, and fuel logs.",
        "manual_links": [
            {"label": "Dashboard", "href": "/fleet"},
            {"label": "Vehicles", "href": "/fleet/vehicles"},
            {"label": "Maintenance", "href": "/fleet/maintenance"},
        ],
        "journeys": [
            {
                "title": "Vehicle Readiness",
                "outcome": "Keep vehicles compliant and ready for operations.",
                "steps": [
                    "Register vehicle profile and required documents.",
                    "Schedule preventive maintenance tasks.",
                    "Track incidents and close corrective actions.",
                ],
                "href": "/fleet/vehicles",
            },
            {
                "title": "Reservation Operations",
                "outcome": "Manage vehicle allocation with utilization visibility.",
                "steps": [
                    "Create reservation requests.",
                    "Approve and assign available vehicles.",
                    "Review reservation history and exceptions.",
                ],
                "href": "/fleet/reservations",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "Vehicle cannot be reserved",
                "checks": [
                    "Check overlapping reservations.",
                    "Verify maintenance lockout status.",
                    "Confirm required driver/license fields are complete.",
                ],
            }
        ],
    },
    "public_sector": {
        "title": "Public Sector",
        "summary": "Funds, appropriations, commitments, virements, and controls.",
        "manual_links": [
            {"label": "Dashboard", "href": "/public-sector"},
            {"label": "Funds", "href": "/public-sector/funds"},
            {"label": "Appropriations", "href": "/public-sector/appropriations"},
        ],
        "journeys": [
            {
                "title": "Budget Control Setup",
                "outcome": "Establish funding control before spending begins.",
                "steps": [
                    "Create and activate funds.",
                    "Set appropriation lines by period/program.",
                    "Enable commitment checks for transactions.",
                ],
                "href": "/public-sector/appropriations",
            },
            {
                "title": "Commitment to Virement",
                "outcome": "Manage spending commitments and controlled reallocations.",
                "steps": [
                    "Capture commitments against available budget.",
                    "Monitor available balances.",
                    "Execute approved virements where needed.",
                ],
                "href": "/public-sector/commitments",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "Commitment blocked despite budget",
                "checks": [
                    "Validate appropriation period is active.",
                    "Confirm fund and program mapping.",
                    "Review prior commitments and encumbrances.",
                ],
            }
        ],
    },
    "expense": {
        "title": "Expense",
        "summary": "Claims, approvals, reimbursements, and policy limits.",
        "manual_links": [
            {"label": "Expense Dashboard", "href": "/expense"},
            {"label": "Claims Dashboard", "href": "/expense/claims"},
            {"label": "Claims List", "href": "/expense/claims/list"},
        ],
        "journeys": [
            {
                "title": "Claim Review Workflow",
                "outcome": "Process claims with policy and approval controls.",
                "steps": [
                    "Review submitted claims and required receipts.",
                    "Approve or reject using tiered rules.",
                    "Post approved claims to finance workflow.",
                ],
                "href": "/expense/claims",
            },
            {
                "title": "Reimbursement Cycle",
                "outcome": "Complete claim settlement and audit trail.",
                "steps": [
                    "Group approved claims for payment.",
                    "Execute reimbursement and capture references.",
                    "Verify paid status and reporting totals.",
                ],
                "href": "/expense/claims/list",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "Claim cannot be submitted",
                "checks": [
                    "Check required receipt attachments.",
                    "Validate claim totals against limits.",
                    "Confirm employee approver configuration.",
                ],
            }
        ],
    },
    "coach": {
        "title": "Coach",
        "summary": "Operational insights, advisory reports, and feedback loops.",
        "manual_links": [
            {"label": "Dashboard", "href": "/coach/"},
            {"label": "Insights", "href": "/coach/insights"},
            {"label": "Reports", "href": "/coach/reports"},
        ],
        "journeys": [
            {
                "title": "Insight Monitoring",
                "outcome": "Review high-risk operational patterns quickly.",
                "steps": [
                    "Open dashboard and review active insights.",
                    "Drill into insight details and evidence.",
                    "Record feedback/actions for teams.",
                ],
                "href": "/coach/insights",
            },
            {
                "title": "Report Follow-Up",
                "outcome": "Turn findings into execution plans.",
                "steps": [
                    "Open generated reports by category.",
                    "Prioritize findings by severity and impact.",
                    "Assign follow-up actions in operational modules.",
                ],
                "href": "/coach/reports",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "No insights displayed",
                "checks": [
                    "Confirm coach feature is enabled.",
                    "Check permission scopes for insights/reports.",
                    "Validate source data freshness in modules.",
                ],
            }
        ],
    },
    "settings": {
        "title": "Settings",
        "summary": "Organization-level defaults and module-specific configuration.",
        "manual_links": [
            {"label": "Settings Hub", "href": "/settings"},
            {"label": "Support Settings", "href": "/settings/support"},
            {"label": "Inventory Settings", "href": "/settings/inventory"},
        ],
        "journeys": [
            {
                "title": "Module Configuration Rollout",
                "outcome": "Apply policy defaults safely across modules.",
                "steps": [
                    "Review current module configuration values.",
                    "Update policy thresholds and defaults.",
                    "Validate behavior in related operational screens.",
                ],
                "href": "/settings",
            }
        ],
        "troubleshooting": [
            {
                "symptom": "Settings updates not reflected",
                "checks": [
                    "Confirm save success status on settings page.",
                    "Check user permission for settings access.",
                    "Verify module-specific page is reading updated values.",
                ],
            }
        ],
    },
    "self_service": {
        "title": "Self Service",
        "summary": "Employee attendance, leave, payslips, expenses, and tickets.",
        "manual_links": [
            {"label": "Attendance", "href": "/people/self/attendance"},
            {"label": "Leave", "href": "/people/self/leave"},
            {"label": "Expenses", "href": "/people/self/expenses"},
        ],
        "journeys": [
            {
                "title": "Employee Request Journey",
                "outcome": "Submit and track leave/expense requests end-to-end.",
                "steps": [
                    "Create request with required supporting information.",
                    "Submit and monitor approval status.",
                    "Review final outcome and comments.",
                ],
                "href": "/people/self/leave",
            },
            {
                "title": "Personal Work Hub",
                "outcome": "Manage attendance, tasks, tickets, and payslips.",
                "steps": [
                    "Check attendance and tax/payslip records.",
                    "Update assigned tasks and support tickets.",
                    "Track pending approvals requiring action.",
                ],
                "href": "/people/self/tasks",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "Cannot access self-service pages",
                "checks": [
                    "Verify self-service scope is assigned.",
                    "Ensure person is mapped to an employee record.",
                    "Confirm session is active and not expired.",
                ],
            }
        ],
    },
}


CROSS_MODULE_WORKFLOWS = [
    {
        "title": "Procure-to-Pay",
        "modules": ["procurement", "finance", "inventory"],
        "steps": [
            {"label": "Create requisition", "href": "/procurement/requisitions"},
            {"label": "Issue RFQ and award", "href": "/procurement/rfqs"},
            {"label": "Record AP invoice", "href": "/finance/ap/invoices"},
            {"label": "Execute payment", "href": "/finance/ap/payments"},
        ],
    },
    {
        "title": "Order-to-Cash",
        "modules": ["finance", "projects"],
        "steps": [
            {"label": "Create sales order", "href": "/finance/sales-orders"},
            {"label": "Issue invoice", "href": "/finance/ar/invoices"},
            {"label": "Receive payment", "href": "/finance/ar/receipts"},
            {"label": "Review AR aging", "href": "/finance/ar/aging"},
        ],
    },
    {
        "title": "Hire-to-Payroll",
        "modules": ["people", "self_service"],
        "steps": [
            {"label": "Create employee", "href": "/people/hr/employees"},
            {"label": "Complete onboarding", "href": "/people/hr/onboarding"},
            {"label": "Run payroll", "href": "/people/payroll/runs"},
            {"label": "Publish payslips", "href": "/people/self/payslips"},
        ],
    },
]


ROLE_PLAYBOOKS = [
    {
        "key": "admin",
        "title": "System Administrator",
        "when": lambda is_admin, roles, scopes, modules: is_admin,
        "journeys": [
            {"label": "Configure module defaults", "href": "/settings"},
            {"label": "Review access and governance", "href": "/admin/roles"},
            {"label": "Monitor integrations and sync", "href": "/admin/sync"},
        ],
    },
    {
        "key": "finance_operator",
        "title": "Finance Operations",
        "when": lambda is_admin, roles, scopes, modules: "finance" in modules,
        "journeys": [
            {"label": "Run period close", "href": "/finance/gl/periods"},
            {"label": "Monitor AP and AR aging", "href": "/finance/reports"},
            {"label": "Reconcile banking", "href": "/finance/banking/reconciliations"},
        ],
    },
    {
        "key": "hr_operator",
        "title": "HR & Payroll",
        "when": lambda is_admin, roles, scopes, modules: "people" in modules,
        "journeys": [
            {"label": "Manage employee lifecycle", "href": "/people/hr/employees"},
            {"label": "Execute payroll run", "href": "/people/payroll/runs"},
            {"label": "Review attendance and leave", "href": "/people/attendance"},
        ],
    },
    {
        "key": "ops_manager",
        "title": "Operations Manager",
        "when": lambda is_admin, roles, scopes, modules: bool(
            {"inventory", "fleet", "procurement", "projects"} & set(modules)
        ),
        "journeys": [
            {"label": "Track inventory health", "href": "/inventory/reports"},
            {"label": "Manage procurement pipeline", "href": "/procurement"},
            {"label": "Monitor project execution", "href": "/projects"},
        ],
    },
    {
        "key": "employee",
        "title": "Employee Self-Service",
        "when": lambda is_admin, roles, scopes, modules: "self_service" in modules,
        "journeys": [
            {"label": "Submit leave request", "href": "/people/self/leave"},
            {"label": "Submit expense claim", "href": "/people/self/expenses"},
            {"label": "Access payslips", "href": "/people/self/payslips"},
        ],
    },
]


def _normalized_modules(accessible_modules: Iterable[str]) -> list[str]:
    module_set = {m for m in accessible_modules if m in MODULE_GUIDES}
    return [m for m in EXPECTED_MODULE_KEYS if m in module_set]


def _merge_help_overrides(
    payload: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge optional JSON overrides into the computed help payload."""
    if not isinstance(overrides, dict):
        return payload

    merged = deepcopy(payload)
    list_keys = [
        "manuals",
        "journeys",
        "troubleshooting",
        "cross_module_workflows",
        "role_playbooks",
    ]
    for key in list_keys:
        value = overrides.get(key)
        if isinstance(value, list):
            merged[key] = value

    coverage = overrides.get("coverage_modules")
    if isinstance(coverage, list) and all(isinstance(item, str) for item in coverage):
        merged["coverage_modules"] = coverage

    return merged


def build_help_center_payload(
    *,
    accessible_modules: Iterable[str],
    roles: Iterable[str],
    scopes: Iterable[str],
    is_admin: bool,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build help center payload filtered by module and role access."""
    modules = _normalized_modules(accessible_modules)
    role_list = [str(role) for role in roles]
    scope_list = [str(scope) for scope in scopes]

    module_sections = []
    manuals = []
    journeys = []
    troubleshooting = []

    for module in modules:
        guide = deepcopy(MODULE_GUIDES[module])
        guide["module_key"] = module
        search_terms = [
            guide["title"],
            guide["summary"],
            " ".join(item["label"] for item in guide["manual_links"]),
        ]
        guide["search_blob"] = " ".join(search_terms).lower()
        module_sections.append(guide)

        manuals.append(
            {
                "module_key": module,
                "title": f"{guide['title']} Manual",
                "summary": guide["summary"],
                "links": guide["manual_links"],
                "search_blob": guide["search_blob"],
            }
        )

        for journey in guide["journeys"]:
            journey_item = deepcopy(journey)
            journey_item["module_key"] = module
            journey_item["module_title"] = guide["title"]
            journey_item["search_blob"] = (
                f"{guide['title']} {journey_item['title']} "
                f"{journey_item['outcome']} {' '.join(journey_item['steps'])}"
            ).lower()
            journeys.append(journey_item)

        for issue in guide["troubleshooting"]:
            issue_item = deepcopy(issue)
            issue_item["module_key"] = module
            issue_item["module_title"] = guide["title"]
            issue_item["search_blob"] = (
                f"{guide['title']} {issue_item['symptom']} "
                f"{' '.join(issue_item['checks'])}"
            ).lower()
            troubleshooting.append(issue_item)

    cross_module = []
    for workflow in CROSS_MODULE_WORKFLOWS:
        if set(workflow["modules"]).issubset(set(modules)):
            item = deepcopy(workflow)
            item["search_blob"] = (
                f"{item['title']} {' '.join(step['label'] for step in item['steps'])}"
            ).lower()
            cross_module.append(item)

    role_playbooks = []
    for playbook in ROLE_PLAYBOOKS:
        include = playbook["when"](is_admin, role_list, scope_list, modules)
        if include:
            item = deepcopy(playbook)
            item.pop("when", None)
            item["search_blob"] = (
                f"{item['title']} {' '.join(j['label'] for j in item['journeys'])}"
            ).lower()
            role_playbooks.append(item)

    payload = {
        "module_sections": module_sections,
        "manuals": manuals,
        "journeys": journeys,
        "troubleshooting": troubleshooting,
        "cross_module_workflows": cross_module,
        "role_playbooks": role_playbooks,
        "coverage_modules": modules,
    }
    return _merge_help_overrides(payload, overrides)
