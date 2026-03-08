"""
Help Center content provider.

Centralized manuals, how-to journeys, troubleshooting, and role playbooks.
"""

from __future__ import annotations

import logging
import re
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
            {
                "title": "AP Invoice Processing",
                "outcome": "Receive supplier invoices, match to POs and receipts, approve and schedule payment.",
                "steps": [
                    "Record supplier invoice with line items and tax codes.",
                    "Match invoice against purchase order and goods receipt.",
                    "Submit for approval following threshold rules.",
                    "Schedule payment in a batch or as a standalone transaction.",
                ],
                "href": "/finance/ap/invoices",
            },
            {
                "title": "Bank Reconciliation",
                "outcome": "Import bank statements, match transactions, resolve differences, and post adjustments.",
                "steps": [
                    "Import bank statement file or enter transactions manually.",
                    "Auto-match and manually match transactions to book entries.",
                    "Investigate and resolve reconciling differences.",
                    "Post adjustments and finalize the reconciliation.",
                ],
                "href": "/finance/banking/reconciliations",
            },
            {
                "title": "Tax Period Filing",
                "outcome": "Review collected and paid taxes, generate returns, submit to authority, and close period.",
                "steps": [
                    "Review VAT collected and input tax for the period.",
                    "Generate tax return with supporting schedules.",
                    "Submit return to tax authority and record filing reference.",
                    "Close the tax period to prevent further postings.",
                ],
                "href": "/finance/tax/periods",
            },
            {
                "title": "Chart of Accounts Setup",
                "outcome": "Design account structure, create accounts, and set default mappings.",
                "steps": [
                    "Plan account hierarchy by asset, liability, equity, revenue, and expense.",
                    "Create accounts with correct types, currencies, and tax codes.",
                    "Set default accounts for AR, AP, bank, tax, and retained earnings.",
                    "Validate structure by posting test journal entries.",
                ],
                "href": "/finance/gl/accounts",
            },
            {
                "title": "Budget vs Actual Analysis",
                "outcome": "Set budgets by account and period, run variance reports, and investigate differences.",
                "steps": [
                    "Define budget lines for each account and fiscal period.",
                    "Run budget vs actual report to identify variances.",
                    "Drill into variance details by account and date range.",
                    "Document findings and adjust forecasts if needed.",
                ],
                "href": "/finance/reports/budget-vs-actual",
            },
            {
                "title": "Period Close Checklist",
                "outcome": "Complete all reconciliation tasks, review journals, close sub-ledgers, and close the period.",
                "steps": [
                    "Reconcile bank accounts and resolve outstanding items.",
                    "Review and post all pending journal entries.",
                    "Close AP and AR sub-ledgers for the period.",
                    "Run trial balance validation and close the fiscal period.",
                ],
                "href": "/finance/gl/periods",
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
            },
            {
                "symptom": "Trial balance does not balance",
                "checks": [
                    "Check for unposted or draft journal entries in the period.",
                    "Verify no one-sided entries were posted via data import.",
                    "Review sub-ledger to GL reconciliation reports.",
                ],
            },
            {
                "symptom": "Bank reconciliation difference won't resolve",
                "checks": [
                    "Confirm all statement lines are imported and none are duplicated.",
                    "Check for timing differences on cheques or transfers.",
                    "Review unmatched transactions from prior periods.",
                ],
            },
            {
                "symptom": "Tax period cannot be closed",
                "checks": [
                    "Verify all transactions in the period have valid tax codes.",
                    "Check for draft tax returns that need to be finalized.",
                    "Confirm the previous tax period is already closed.",
                ],
            },
            {
                "symptom": "Journal entry rejected — period closed",
                "checks": [
                    "Confirm the posting date falls within an open fiscal period.",
                    "Check if the period was recently closed by another user.",
                    "Request period reopen from an authorized administrator.",
                ],
            },
            {
                "symptom": "AR/AP aging report mismatch with GL",
                "checks": [
                    "Reconcile AR/AP control account balance to sub-ledger total.",
                    "Check for manual journal entries posted to control accounts.",
                    "Verify receipt/payment allocations are correctly applied.",
                ],
            },
        ],
    },
    "people": {
        "title": "People & HR",
        "summary": "Employee records, onboarding, attendance, payroll, and performance.",
        "manual_links": [
            {"label": "Dashboard", "href": "/people/dashboard"},
            {"label": "Employees", "href": "/people/hr/employees"},
            {"label": "Payroll Runs", "href": "/people/payroll/runs"},
            {"label": "Training Programs", "href": "/people/training/programs"},
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
            {
                "title": "Training Program to Completion",
                "outcome": "Define training plans, schedule delivery, and track attendance.",
                "steps": [
                    "Create the training program with objectives and duration.",
                    "Schedule an event and invite participants.",
                    "Track attendance, completion, and certificates issued.",
                ],
                "href": "/people/training/programs",
            },
            {
                "title": "Recruitment Pipeline",
                "outcome": "Create job openings, post, screen applicants, interview, offer, and hire.",
                "steps": [
                    "Create job opening with requirements and approval.",
                    "Post to job boards and collect applications.",
                    "Screen candidates and shortlist for interviews.",
                    "Conduct interviews, score, and make offer.",
                    "Convert accepted offer to employee record.",
                ],
                "href": "/people/recruit/openings",
            },
            {
                "title": "Leave Management",
                "outcome": "Configure leave types, allocate balances, process requests, and track usage.",
                "steps": [
                    "Set up leave types with accrual rules and policies.",
                    "Allocate leave balances to employees by type.",
                    "Process leave requests with approval workflows.",
                    "Monitor leave balances and usage reports.",
                ],
                "href": "/people/leave",
            },
            {
                "title": "Attendance Setup",
                "outcome": "Configure shifts, enable check-in, and review attendance exceptions.",
                "steps": [
                    "Define shift patterns and working schedules.",
                    "Enable employee check-in via web or device integration.",
                    "Review daily attendance logs and flag exceptions.",
                    "Generate attendance reports for payroll input.",
                ],
                "href": "/people/attendance",
            },
            {
                "title": "Performance Review Cycle",
                "outcome": "Create review periods, assign reviewers, collect ratings, and calibrate results.",
                "steps": [
                    "Define performance review period and criteria.",
                    "Assign reviewers and self-assessment templates.",
                    "Collect ratings and written feedback from all parties.",
                    "Calibrate scores across departments and finalize results.",
                ],
                "href": "/people/hr/employees",
            },
            {
                "title": "Discipline Process",
                "outcome": "Document incidents, conduct investigation, hold hearing, and record outcomes.",
                "steps": [
                    "Record the incident with date, witnesses, and description.",
                    "Initiate investigation and gather supporting evidence.",
                    "Schedule and conduct disciplinary hearing.",
                    "Record outcome, sanction, and appeal rights.",
                ],
                "href": "/people/hr/employees",
            },
            {
                "title": "Onboarding Workflow",
                "outcome": "Create onboarding templates, assign tasks to new hires, and track completion.",
                "steps": [
                    "Create onboarding template with task checklist.",
                    "Assign template to new employee during hiring.",
                    "Track task completion by employee and supervisor.",
                    "Close onboarding when all tasks are marked complete.",
                ],
                "href": "/people/hr/onboarding",
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
            },
            {
                "symptom": "Leave balance shows incorrect days",
                "checks": [
                    "Review leave allocation records for the current period.",
                    "Check for unapproved or cancelled leave requests affecting balance.",
                    "Verify accrual settings and proration rules are correct.",
                ],
            },
            {
                "symptom": "Payslip calculation does not match expectations",
                "checks": [
                    "Compare payroll structure components with employee profile.",
                    "Check for mid-period changes to salary or deductions.",
                    "Review tax bracket and statutory contribution settings.",
                ],
            },
            {
                "symptom": "Attendance check-in not recording",
                "checks": [
                    "Verify employee is assigned to an active shift schedule.",
                    "Check device or browser permissions for location/time.",
                    "Confirm attendance module is enabled in organization settings.",
                ],
            },
            {
                "symptom": "Onboarding tasks stuck in pending",
                "checks": [
                    "Verify task assignees have the correct permissions.",
                    "Check if prerequisite tasks need to be completed first.",
                    "Review onboarding template for circular dependencies.",
                ],
            },
            {
                "symptom": "Employee status cannot be changed",
                "checks": [
                    "Check for active payroll runs that include the employee.",
                    "Verify no pending leave or expense claims are open.",
                    "Confirm user has permission to change employee status.",
                ],
            },
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
            {
                "title": "Warehouse Setup",
                "outcome": "Create warehouses, define storage locations, and set default routing.",
                "steps": [
                    "Create warehouse with address and contact details.",
                    "Define storage locations and bin assignments.",
                    "Set default receipt and issue locations for each warehouse.",
                    "Configure inter-warehouse transfer routes.",
                ],
                "href": "/inventory/warehouses",
            },
            {
                "title": "Material Request Flow",
                "outcome": "Request materials, approve, issue from stock, and confirm receipt.",
                "steps": [
                    "Create material request with item quantities and purpose.",
                    "Submit for approval following departmental rules.",
                    "Issue stock from warehouse against approved request.",
                    "Confirm receipt by requesting department.",
                ],
                "href": "/inventory/material-requests",
            },
            {
                "title": "Stock Valuation Report",
                "outcome": "Choose valuation method, run reports, and reconcile with GL.",
                "steps": [
                    "Select valuation method (weighted average, FIFO, or standard).",
                    "Run stock valuation report for target date.",
                    "Compare inventory value to GL inventory control account.",
                    "Investigate and resolve any valuation discrepancies.",
                ],
                "href": "/inventory/reports",
            },
            {
                "title": "Lot and Serial Tracking",
                "outcome": "Enable tracking, assign identifiers on receipt, and trace through transactions.",
                "steps": [
                    "Enable lot or serial tracking on the item master.",
                    "Assign lot/serial numbers during goods receipt.",
                    "Track movement through issues, transfers, and adjustments.",
                    "Use traceability report to locate items by identifier.",
                ],
                "href": "/inventory/items",
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
            },
            {
                "symptom": "Stock quantity mismatch after count",
                "checks": [
                    "Compare count sheet quantities with system on-hand balance.",
                    "Check for transactions posted between count start and entry.",
                    "Review pending receipts or issues not yet processed.",
                ],
            },
            {
                "symptom": "Transfer stuck in transit status",
                "checks": [
                    "Verify the receiving warehouse has confirmed receipt.",
                    "Check for missing or incomplete transfer documents.",
                    "Review transit warehouse settings for auto-completion rules.",
                ],
            },
            {
                "symptom": "Valuation discrepancy between inventory and GL",
                "checks": [
                    "Run inventory valuation reconciliation report.",
                    "Check for manual journal entries posted to inventory accounts.",
                    "Verify cost updates were applied correctly to all transactions.",
                ],
            },
            {
                "symptom": "Item cannot be issued — insufficient stock",
                "checks": [
                    "Confirm stock on hand in the source warehouse and location.",
                    "Check for reserved or committed quantities on other orders.",
                    "Review lot or serial availability if tracking is enabled.",
                ],
            },
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
            {
                "title": "Vendor Evaluation",
                "outcome": "Set evaluation criteria, score vendors, rank performance, and select preferred suppliers.",
                "steps": [
                    "Define evaluation criteria and scoring weights.",
                    "Score vendor performance against each criterion.",
                    "Rank vendors by composite score and category.",
                    "Designate preferred vendors for future procurement.",
                ],
                "href": "/procurement/vendors",
            },
            {
                "title": "Purchase Order to Goods Receipt",
                "outcome": "Create PO, send to vendor, receive goods, and match to invoice.",
                "steps": [
                    "Create purchase order from approved requisition or manually.",
                    "Send PO to vendor and track acknowledgment.",
                    "Record goods receipt against PO at warehouse.",
                    "Match supplier invoice to PO and goods receipt.",
                ],
                "href": "/procurement/purchase-orders",
            },
            {
                "title": "Contract Lifecycle",
                "outcome": "Create contracts, negotiate terms, activate, and manage renewal or expiry.",
                "steps": [
                    "Create contract record with scope and commercial terms.",
                    "Route for review and approval by authorized signers.",
                    "Activate contract and track obligation milestones.",
                    "Manage renewal notifications or controlled expiry.",
                ],
                "href": "/procurement/contracts",
            },
            {
                "title": "Requisition Approval Workflow",
                "outcome": "Submit requisitions, route through approvers, and convert approved requests to POs.",
                "steps": [
                    "Create requisition with items, quantities, and justification.",
                    "Submit for approval and track through approval chain.",
                    "Handle rejections with comments and resubmission.",
                    "Convert approved requisitions to purchase orders.",
                ],
                "href": "/procurement/requisitions",
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
            },
            {
                "symptom": "Vendor cannot be selected in PO",
                "checks": [
                    "Verify vendor status is active and not suspended.",
                    "Check vendor category matches the procurement type.",
                    "Confirm vendor has valid payment terms configured.",
                ],
            },
            {
                "symptom": "PO exceeds budget allocation",
                "checks": [
                    "Review budget availability for the cost center.",
                    "Check if prior commitments have consumed the budget.",
                    "Request budget increase or reallocation before proceeding.",
                ],
            },
            {
                "symptom": "RFQ evaluation scores not calculating",
                "checks": [
                    "Verify evaluation criteria weights sum to 100%.",
                    "Check that all evaluators have submitted their scores.",
                    "Review scoring rules for minimum response requirements.",
                ],
            },
            {
                "symptom": "Goods received but PO still shows open",
                "checks": [
                    "Confirm goods receipt was posted against the correct PO.",
                    "Check if partial receipt leaves remaining quantity open.",
                    "Review PO completion settings for auto-close thresholds.",
                ],
            },
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
            {
                "title": "Support Team Setup",
                "outcome": "Create support teams, assign members, and configure routing rules.",
                "steps": [
                    "Create support team with name and description.",
                    "Assign team members and set lead/manager.",
                    "Configure auto-routing rules by category and priority.",
                    "Test routing by submitting sample tickets.",
                ],
                "href": "/support/teams",
            },
            {
                "title": "SLA Configuration",
                "outcome": "Define response and resolution targets, set escalation rules, and monitor compliance.",
                "steps": [
                    "Create SLA policies with response and resolution times.",
                    "Map SLA policies to ticket categories and priorities.",
                    "Configure escalation rules for approaching breaches.",
                    "Monitor SLA dashboard for compliance rates.",
                ],
                "href": "/support/dashboard",
            },
            {
                "title": "Escalation Workflow",
                "outcome": "Auto-escalate and manually escalate tickets with proper notification.",
                "steps": [
                    "Configure auto-escalation triggers based on SLA timers.",
                    "Set escalation recipients by tier and severity.",
                    "Manually escalate tickets with reason and reassignment.",
                    "Track escalation history and resolution outcomes.",
                ],
                "href": "/support/tickets",
            },
            {
                "title": "Ticket Category Management",
                "outcome": "Create categories, set defaults, and assign responsible teams.",
                "steps": [
                    "Create ticket categories aligned to business processes.",
                    "Set default priority and SLA for each category.",
                    "Assign responsible team for category-based routing.",
                    "Review category usage and refine mappings.",
                ],
                "href": "/support/tickets",
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
            },
            {
                "symptom": "SLA timer shows incorrect elapsed time",
                "checks": [
                    "Verify working hours and holiday calendar configuration.",
                    "Check if ticket was paused or status changed during SLA period.",
                    "Review SLA policy assignment for the ticket category.",
                ],
            },
            {
                "symptom": "Auto-assignment not routing tickets correctly",
                "checks": [
                    "Verify routing rules match the ticket category and priority.",
                    "Check team member availability and workload limits.",
                    "Review fallback assignment rules for unmatched tickets.",
                ],
            },
            {
                "symptom": "Ticket status cannot be changed",
                "checks": [
                    "Check if the ticket is locked by an active workflow.",
                    "Verify user has permission to change ticket status.",
                    "Review required fields that must be filled before transition.",
                ],
            },
            {
                "symptom": "Customer not seeing submitted tickets",
                "checks": [
                    "Verify the customer email matches their account record.",
                    "Check if the ticket is assigned to the correct organization.",
                    "Review customer portal access settings and permissions.",
                ],
            },
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
            {
                "title": "Milestone Management",
                "outcome": "Define milestones, track deliverables, and report completion status.",
                "steps": [
                    "Create milestones with target dates and deliverables.",
                    "Link tasks to milestones for progress tracking.",
                    "Update milestone status as deliverables are completed.",
                    "Generate milestone report for stakeholder review.",
                ],
                "href": "/projects",
            },
            {
                "title": "Project Reporting",
                "outcome": "Generate status reports, track KPIs, and communicate progress to stakeholders.",
                "steps": [
                    "Configure project dashboard with key metrics.",
                    "Run status report by project, department, or portfolio.",
                    "Review task completion rates and resource utilization.",
                    "Export reports for stakeholder distribution.",
                ],
                "href": "/projects",
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
            },
            {
                "symptom": "Task dependencies causing scheduling conflicts",
                "checks": [
                    "Review dependency chains for circular references.",
                    "Check predecessor task completion status.",
                    "Verify date constraints do not conflict with dependencies.",
                ],
            },
            {
                "symptom": "Project budget not reflecting actual costs",
                "checks": [
                    "Verify expense claims are linked to the correct project.",
                    "Check for time entries not yet submitted or approved.",
                    "Review cost rate configuration for project resources.",
                ],
            },
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
            {
                "title": "Maintenance Scheduling",
                "outcome": "Schedule preventive maintenance, track service history, and manage vendor relationships.",
                "steps": [
                    "Define maintenance schedules by vehicle type and mileage.",
                    "Create work orders for upcoming maintenance tasks.",
                    "Track completion, costs, and vendor performance.",
                    "Review maintenance history for fleet optimization.",
                ],
                "href": "/fleet/maintenance",
            },
            {
                "title": "Fuel Log Management",
                "outcome": "Record fuel purchases, track consumption, and analyze efficiency.",
                "steps": [
                    "Log fuel purchases with date, quantity, and cost.",
                    "Link fuel records to specific vehicles and trips.",
                    "Review fuel consumption reports by vehicle and period.",
                    "Identify vehicles with abnormal fuel usage patterns.",
                ],
                "href": "/fleet/vehicles",
            },
            {
                "title": "Incident Reporting",
                "outcome": "Report vehicle incidents, document damage, and track resolution.",
                "steps": [
                    "Create incident report with date, location, and description.",
                    "Upload supporting photos and witness statements.",
                    "Assign follow-up actions and insurance claims.",
                    "Track resolution and vehicle return to service.",
                ],
                "href": "/fleet/vehicles",
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
            },
            {
                "symptom": "Vehicle maintenance overdue alert not showing",
                "checks": [
                    "Verify maintenance schedule is configured for the vehicle.",
                    "Check mileage tracking is enabled and up to date.",
                    "Review notification settings for maintenance alerts.",
                ],
            },
            {
                "symptom": "Fuel consumption report shows zero values",
                "checks": [
                    "Confirm fuel log entries exist for the selected period.",
                    "Verify vehicle assignment on fuel records is correct.",
                    "Check report filters are not excluding valid records.",
                ],
            },
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
            {
                "title": "Appropriation Setup",
                "outcome": "Create budget appropriation lines aligned to government programs and periods.",
                "steps": [
                    "Define appropriation heads by program and economic classification.",
                    "Set approved budget amounts for each appropriation line.",
                    "Link appropriation lines to GL accounts for posting.",
                    "Activate appropriations for the fiscal period.",
                ],
                "href": "/public-sector/appropriations",
            },
            {
                "title": "Virement Process",
                "outcome": "Request, approve, and execute budget transfers between appropriation lines.",
                "steps": [
                    "Create virement request with source and destination lines.",
                    "Provide justification and attach supporting documents.",
                    "Route for approval through authorized budget officers.",
                    "Execute approved virement and update available balances.",
                ],
                "href": "/public-sector/virements",
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
            },
            {
                "symptom": "Virement request rejected without reason",
                "checks": [
                    "Review virement approval workflow for required fields.",
                    "Check if source appropriation has sufficient available balance.",
                    "Verify the approver has not set automatic rejection rules.",
                ],
            },
            {
                "symptom": "Fund balance shows negative available",
                "checks": [
                    "Review committed and actual expenditure against appropriation.",
                    "Check for unapproved commitments consuming budget.",
                    "Verify opening balances were correctly entered for the period.",
                ],
            },
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
            {
                "title": "Claim Submission Workflow",
                "outcome": "Create expense claims, attach receipts, submit for approval, and track status.",
                "steps": [
                    "Create expense claim with category and description.",
                    "Attach receipt images or documents for each line item.",
                    "Submit claim for manager approval.",
                    "Track approval status and respond to queries.",
                ],
                "href": "/expense/claims",
            },
            {
                "title": "Policy Limit Enforcement",
                "outcome": "Configure spending limits, validate claims against policy, and handle exceptions.",
                "steps": [
                    "Define expense policy limits by category and role.",
                    "System validates claim amounts against applicable limits.",
                    "Flag over-limit claims for additional approval.",
                    "Review exception requests and document decisions.",
                ],
                "href": "/expense/claims",
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
            },
            {
                "symptom": "Claim rejected for missing receipts",
                "checks": [
                    "Verify receipt attachments uploaded successfully.",
                    "Check file format and size limits for uploads.",
                    "Review policy requirements for receipt thresholds.",
                ],
            },
            {
                "symptom": "Reimbursement not processed after approval",
                "checks": [
                    "Verify claim status is fully approved, not partially.",
                    "Check if a payment batch has been created for the period.",
                    "Review AP integration settings for expense reimbursement.",
                ],
            },
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
            {
                "title": "Acting on Recommendations",
                "outcome": "Review coach recommendations, assign actions, and track improvement.",
                "steps": [
                    "Review prioritized recommendations by module.",
                    "Create action items from recommendations.",
                    "Track implementation progress and measure impact.",
                    "Provide feedback on recommendation accuracy.",
                ],
                "href": "/coach/insights",
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
            },
            {
                "title": "Branding Setup",
                "outcome": "Configure organization logo, colors, and display preferences.",
                "steps": [
                    "Upload organization logo for light and dark modes.",
                    "Set brand colors and accent preferences.",
                    "Configure date, number, and currency display formats.",
                    "Preview branding across module pages.",
                ],
                "href": "/settings",
            },
            {
                "title": "Access Control Configuration",
                "outcome": "Set up roles, permissions, and module access for users.",
                "steps": [
                    "Review default roles and their permission sets.",
                    "Create custom roles for specific job functions.",
                    "Assign roles to users based on responsibilities.",
                    "Test access by logging in as different user types.",
                ],
                "href": "/admin/roles",
            },
        ],
        "troubleshooting": [
            {
                "symptom": "Settings updates not reflected",
                "checks": [
                    "Confirm save success status on settings page.",
                    "Check user permission for settings access.",
                    "Verify module-specific page is reading updated values.",
                ],
            },
            {
                "symptom": "User cannot access assigned module",
                "checks": [
                    "Verify user role includes the required module scope.",
                    "Check if the module is enabled in organization settings.",
                    "Review session expiry and re-authenticate if needed.",
                ],
            },
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
            {
                "title": "View Payslips",
                "outcome": "Access and download monthly payslips and tax documents.",
                "steps": [
                    "Navigate to self-service payslips section.",
                    "Select pay period to view detailed breakdown.",
                    "Download payslip as PDF for personal records.",
                    "Review year-to-date earnings and deductions.",
                ],
                "href": "/people/self/payslips",
            },
            {
                "title": "Submit Expense Claim",
                "outcome": "Create and submit expense claims through self-service.",
                "steps": [
                    "Create new expense claim from self-service portal.",
                    "Add line items with amounts, categories, and receipts.",
                    "Submit claim and monitor approval progress.",
                    "Check reimbursement status after approval.",
                ],
                "href": "/people/self/expenses",
            },
            {
                "title": "Check Attendance Record",
                "outcome": "Review personal attendance history and request corrections.",
                "steps": [
                    "View daily attendance log with check-in and check-out times.",
                    "Identify missing or incorrect attendance entries.",
                    "Submit correction request with supporting explanation.",
                    "Track correction request approval status.",
                ],
                "href": "/people/self/attendance",
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
            },
            {
                "symptom": "Payslip not available for current month",
                "checks": [
                    "Verify payroll run has been completed and published.",
                    "Check if payslip publication is scheduled for a later date.",
                    "Confirm employee is included in the current payroll cycle.",
                ],
            },
            {
                "symptom": "Leave request not showing in approvals",
                "checks": [
                    "Verify the request was submitted, not just saved as draft.",
                    "Check if the designated approver is correctly configured.",
                    "Review workflow routing rules for leave type.",
                ],
            },
        ],
    },
}


CROSS_MODULE_WORKFLOWS: list[dict[str, Any]] = [
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
    {
        "title": "Expense-to-Reimbursement",
        "modules": ["expense", "finance"],
        "steps": [
            {"label": "Submit expense claim", "href": "/expense/claims"},
            {"label": "Approve claim", "href": "/expense/claims"},
            {"label": "Create AP payment", "href": "/finance/ap/payments"},
            {"label": "Execute reimbursement", "href": "/finance/ap/payments"},
        ],
    },
    {
        "title": "Project-to-Billing",
        "modules": ["projects", "finance"],
        "steps": [
            {"label": "Track project time and costs", "href": "/projects"},
            {"label": "Generate billing milestone", "href": "/projects"},
            {"label": "Create AR invoice", "href": "/finance/ar/invoices"},
            {"label": "Collect payment", "href": "/finance/ar/receipts"},
        ],
    },
    {
        "title": "Budget-to-Commitment",
        "modules": ["public_sector", "procurement", "finance"],
        "steps": [
            {
                "label": "Set appropriation budget",
                "href": "/public-sector/appropriations",
            },
            {
                "label": "Create procurement requisition",
                "href": "/procurement/requisitions",
            },
            {"label": "Record commitment", "href": "/public-sector/commitments"},
            {"label": "Post expenditure to GL", "href": "/finance/gl/journals"},
        ],
    },
    {
        "title": "Requisition-to-Goods-Receipt",
        "modules": ["procurement", "inventory", "finance"],
        "steps": [
            {"label": "Submit requisition", "href": "/procurement/requisitions"},
            {"label": "Create purchase order", "href": "/procurement/purchase-orders"},
            {"label": "Receive goods at warehouse", "href": "/inventory/transactions"},
            {"label": "Match and post AP invoice", "href": "/finance/ap/invoices"},
        ],
    },
    {
        "title": "Ticket-to-Resolution",
        "modules": ["support", "people"],
        "steps": [
            {"label": "Submit support ticket", "href": "/support/tickets"},
            {"label": "Assign to support team", "href": "/support/tickets"},
            {"label": "Investigate and resolve", "href": "/support/tickets"},
            {"label": "Record resolution and close", "href": "/support/tickets"},
        ],
    },
]


ROLE_PLAYBOOKS: list[dict[str, Any]] = [
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
        "key": "learning_admin",
        "title": "Learning & Development",
        "when": lambda is_admin, roles, scopes, modules: "people" in modules,
        "journeys": [
            {"label": "Maintain training catalog", "href": "/people/training/programs"},
            {"label": "Schedule training events", "href": "/people/training/events"},
            {
                "label": "Review completion analytics",
                "href": "/people/training/reports/completion",
            },
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
    {
        "key": "procurement_officer",
        "title": "Procurement Officer",
        "when": lambda is_admin, roles, scopes, modules: "procurement" in modules,
        "journeys": [
            {"label": "Process requisitions", "href": "/procurement/requisitions"},
            {"label": "Manage RFQs and evaluations", "href": "/procurement/rfqs"},
            {
                "label": "Track contracts and obligations",
                "href": "/procurement/contracts",
            },
        ],
    },
    {
        "key": "inventory_officer",
        "title": "Inventory Officer",
        "when": lambda is_admin, roles, scopes, modules: "inventory" in modules,
        "journeys": [
            {"label": "Manage item master data", "href": "/inventory/items"},
            {"label": "Process stock transactions", "href": "/inventory/transactions"},
            {"label": "Run cycle counts", "href": "/inventory/counts"},
        ],
    },
    {
        "key": "project_manager",
        "title": "Project Manager",
        "when": lambda is_admin, roles, scopes, modules: "projects" in modules,
        "journeys": [
            {"label": "Plan and set up projects", "href": "/projects"},
            {"label": "Track tasks and milestones", "href": "/projects/tasks"},
            {"label": "Review project reports", "href": "/projects"},
        ],
    },
    {
        "key": "budget_officer",
        "title": "Public Sector Budget Officer",
        "when": lambda is_admin, roles, scopes, modules: "public_sector" in modules,
        "journeys": [
            {"label": "Manage appropriations", "href": "/public-sector/appropriations"},
            {"label": "Process virements", "href": "/public-sector/virements"},
            {
                "label": "Monitor commitment controls",
                "href": "/public-sector/commitments",
            },
        ],
    },
    {
        "key": "support_lead",
        "title": "Support Team Lead",
        "when": lambda is_admin, roles, scopes, modules: "support" in modules,
        "journeys": [
            {"label": "Monitor SLA compliance", "href": "/support/dashboard"},
            {"label": "Manage team workload", "href": "/support/teams"},
            {"label": "Review escalations", "href": "/support/tickets"},
        ],
    },
]


GLOSSARY: list[dict[str, Any]] = [
    {
        "term": "General Ledger (GL)",
        "definition": "The central accounting record that contains all financial transactions organized by account.",
        "category": "finance",
        "see_also": "Chart of Accounts, Journal Entry",
    },
    {
        "term": "Chart of Accounts",
        "definition": "A structured list of all accounts used by an organization to classify financial transactions.",
        "category": "finance",
        "see_also": "General Ledger",
    },
    {
        "term": "Journal Entry",
        "definition": "A record of a financial transaction with equal debits and credits posted to GL accounts.",
        "category": "finance",
        "see_also": "General Ledger, Trial Balance",
    },
    {
        "term": "Trial Balance",
        "definition": "A report listing all GL account balances to verify that total debits equal total credits.",
        "category": "finance",
        "see_also": "General Ledger",
    },
    {
        "term": "Accounts Receivable (AR)",
        "definition": "Money owed to the organization by customers for goods or services delivered.",
        "category": "finance",
        "see_also": "Invoice, Receipt",
    },
    {
        "term": "Accounts Payable (AP)",
        "definition": "Money the organization owes to suppliers for goods or services received.",
        "category": "finance",
        "see_also": "Supplier Invoice, Payment",
    },
    {
        "term": "Fiscal Period",
        "definition": "A time segment (usually monthly) within a fiscal year used for financial reporting and controls.",
        "category": "finance",
        "see_also": "Period Close",
    },
    {
        "term": "Period Close",
        "definition": "The process of finalizing all transactions for a fiscal period and preventing further postings.",
        "category": "finance",
        "see_also": "Fiscal Period",
    },
    {
        "term": "Bank Reconciliation",
        "definition": "The process of matching book transactions with bank statement entries to identify and resolve differences.",
        "category": "finance",
        "see_also": "Banking",
    },
    {
        "term": "Aging Report",
        "definition": "A report showing outstanding receivables or payables organized by how long they have been unpaid.",
        "category": "finance",
        "see_also": "AR, AP",
    },
    {
        "term": "Credit Note",
        "definition": "A document issued to reduce the amount owed by a customer, typically for returns or corrections.",
        "category": "finance",
        "see_also": "Invoice",
    },
    {
        "term": "Debit Note",
        "definition": "A document issued to a supplier to reduce the amount owed, typically for returns or corrections.",
        "category": "finance",
        "see_also": "Supplier Invoice",
    },
    {
        "term": "Requisition",
        "definition": "A formal request to procure goods or services, subject to approval before a purchase order is created.",
        "category": "procurement",
        "see_also": "Purchase Order, RFQ",
    },
    {
        "term": "RFQ (Request for Quotation)",
        "definition": "A document sent to vendors inviting them to submit price quotes for specified goods or services.",
        "category": "procurement",
        "see_also": "Requisition, Purchase Order",
    },
    {
        "term": "Purchase Order (PO)",
        "definition": "A formal document authorizing a vendor to supply goods or services at agreed terms.",
        "category": "procurement",
        "see_also": "Requisition, Goods Receipt",
    },
    {
        "term": "Goods Receipt",
        "definition": "A record confirming that goods ordered via a purchase order have been physically received.",
        "category": "procurement",
        "see_also": "Purchase Order",
    },
    {
        "term": "SLA (Service Level Agreement)",
        "definition": "A defined target for response and resolution times for support tickets based on priority.",
        "category": "support",
        "see_also": "Ticket, Escalation",
    },
    {
        "term": "Escalation",
        "definition": "The process of raising a ticket to a higher support tier when SLA targets are at risk.",
        "category": "support",
        "see_also": "SLA",
    },
    {
        "term": "Payroll Run",
        "definition": "The periodic process of calculating and distributing employee compensation.",
        "category": "people",
        "see_also": "Payslip",
    },
    {
        "term": "Payslip",
        "definition": "A document showing an employee's earnings, deductions, and net pay for a pay period.",
        "category": "people",
        "see_also": "Payroll Run",
    },
    {
        "term": "Leave Allocation",
        "definition": "The number of leave days assigned to an employee for a specific leave type and period.",
        "category": "people",
        "see_also": "Leave Request",
    },
    {
        "term": "Onboarding",
        "definition": "The structured process of integrating a new employee into the organization with assigned tasks.",
        "category": "people",
        "see_also": "Employee Lifecycle",
    },
    {
        "term": "Expense Claim",
        "definition": "A request for reimbursement of business expenses incurred by an employee.",
        "category": "expense",
        "see_also": "Reimbursement",
    },
    {
        "term": "Appropriation",
        "definition": "A budgetary allocation of funds for a specific purpose within a public sector organization.",
        "category": "public_sector",
        "see_also": "Commitment, Virement",
    },
    {
        "term": "Commitment",
        "definition": "A recorded obligation to spend funds, reducing available budget before actual expenditure.",
        "category": "public_sector",
        "see_also": "Appropriation",
    },
    {
        "term": "Virement",
        "definition": "An authorized transfer of budget allocation from one appropriation line to another.",
        "category": "public_sector",
        "see_also": "Appropriation",
    },
    {
        "term": "Stock Valuation",
        "definition": "The monetary value of inventory on hand, calculated using methods like FIFO or weighted average.",
        "category": "inventory",
        "see_also": "Cycle Count",
    },
    {
        "term": "Cycle Count",
        "definition": "A periodic physical count of a subset of inventory items to verify system accuracy.",
        "category": "inventory",
        "see_also": "Stock Valuation",
    },
    {
        "term": "Material Request",
        "definition": "A formal request to issue or transfer inventory items from a warehouse.",
        "category": "inventory",
        "see_also": "Stock Issue",
    },
    {
        "term": "Milestone",
        "definition": "A significant checkpoint in a project timeline marking the completion of a deliverable.",
        "category": "projects",
        "see_also": "Task",
    },
]

RELEASE_NOTES: list[dict[str, Any]] = [
    {
        "version": "2026.3",
        "date": "07 Mar 2026",
        "type": "minor",
        "summary": "Help center overhaul with sidebar navigation, expanded content, and enhanced article experience.",
        "features": [
            "Help center sidebar with module browsing, training tracks, and quick links.",
            "Expanded help content to 150+ articles across all 12 modules.",
            "10 training tracks covering all major workflows and roles.",
            "Enhanced article sections with warning, note, tip, checklist, and success callouts.",
            "Sticky table of contents for long articles.",
            "Previous/next article navigation within modules.",
            "Glossary of ERP terms with filtering and search.",
            "Print-optimized article layout.",
        ],
        "improvements": [
            "Cross-module workflow guides expanded to 8 workflows.",
            "Role playbooks expanded to 11 role-based guides.",
            "Search results now include all new content types.",
        ],
        "fixes": [
            "Fixed AR aging date arithmetic causing dashboard errors.",
            "Resolved trial balance fallback to posted ledger lines.",
        ],
    },
    {
        "version": "2026.2",
        "date": "15 Feb 2026",
        "type": "minor",
        "summary": "UI polish and dark mode improvements across all modules.",
        "features": [
            "Shadcn-inspired focus rings and pill badges.",
            "Snappier transitions across all interactive elements.",
        ],
        "improvements": [
            "Consistent dark mode pairing on all status badges.",
            "Improved mobile responsiveness for data tables.",
        ],
        "fixes": [
            "Fixed absolute redirect URL handling on logout.",
            "Resilient cookie settings for cross-origin deployments.",
        ],
    },
]

SEARCH_SYNONYMS: dict[str, list[str]] = {
    "payslip": ["payroll", "salary", "wages", "compensation", "pay"],
    "bill": ["invoice", "supplier invoice", "ap invoice"],
    "invoice": ["bill", "receipt", "ar invoice"],
    "receipt": ["payment", "collection", "cash receipt"],
    "employee": ["staff", "worker", "team member", "personnel"],
    "leave": ["time off", "vacation", "absence", "pto", "holiday"],
    "attendance": ["check-in", "clock in", "time tracking", "presence"],
    "claim": ["expense", "reimbursement", "expense claim"],
    "vendor": ["supplier", "provider"],
    "customer": ["client", "buyer"],
    "budget": ["appropriation", "allocation", "forecast"],
    "reconciliation": ["matching", "bank reconciliation", "reconcile"],
    "journal": ["journal entry", "je", "posting"],
    "aging": ["overdue", "outstanding", "past due"],
    "onboarding": ["new hire", "induction", "orientation"],
    "ticket": ["issue", "case", "support request", "incident"],
    "sla": ["service level", "response time", "resolution time"],
    "po": ["purchase order", "order"],
    "rfq": ["request for quotation", "quote request", "bid"],
    "virement": ["budget transfer", "reallocation"],
    "depreciation": ["amortization", "asset depreciation"],
}


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
        "articles",
        "tracks",
        "featured_articles",
        "module_hubs",
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
    payload = _merge_help_overrides(payload, overrides)
    payload.update(
        build_help_experience_payload(
            accessible_modules=modules,
            roles=role_list,
            scopes=scope_list,
            is_admin=is_admin,
            overrides=overrides,
        )
    )
    return payload


FEATURED_ARTICLE_SLUGS = [
    "finance-month-end-close",
    "finance-receivables-to-cash",
    "people-employee-lifecycle",
    "people-payroll-execution",
    "people-training-program-to-completion",
    "inventory-item-setup-to-stock-use",
    "procurement-requisition-to-rfq",
    "support-ticket-triage",
    "support-resolution-workflow",
    "self_service-employee-request-journey",
    "finance-ap-invoice-processing",
    "finance-bank-reconciliation",
    "people-recruitment-pipeline",
    "people-leave-management",
    "inventory-warehouse-setup",
    "procurement-purchase-order-to-goods-receipt",
]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "article"


def _module_prerequisites(module_title: str) -> list[str]:
    return [
        f"Confirm you can access the {module_title} module and its core pages.",
        "Verify your role allows create, review, or approval actions for this workflow.",
        "Gather the master data or documents required before you start.",
    ]


def _generic_success_checks(module_title: str) -> list[str]:
    return [
        f"The expected record or transaction is visible in {module_title}.",
        "Status, ownership, and dates were updated as expected.",
        "Downstream users can continue the process without manual rework.",
    ]


def _build_article(
    *,
    slug: str,
    title: str,
    summary: str,
    module_key: str,
    module_title: str,
    content_type: str,
    audience: str,
    estimated_minutes: int,
    prerequisites: list[str],
    sections: list[dict[str, Any]],
    related_links: list[dict[str, str]],
) -> dict[str, Any]:
    section_text = []
    for section in sections:
        section_text.append(section["title"])
        section_text.extend(section.get("items", []))
        body = section.get("body")
        if body:
            section_text.append(body)

    search_blob = " ".join(
        [
            title,
            summary,
            module_title,
            content_type,
            audience,
            " ".join(section_text),
            " ".join(link["label"] for link in related_links),
        ]
    ).lower()

    return {
        "slug": slug,
        "href": f"/help/articles/{slug}",
        "title": title,
        "summary": summary,
        "module_key": module_key,
        "module_title": module_title,
        "content_type": content_type,
        "content_type_label": content_type.replace("_", " ").title(),
        "audience": audience,
        "estimated_minutes": estimated_minutes,
        "prerequisites": prerequisites,
        "sections": sections,
        "related_links": related_links,
        "search_blob": search_blob,
    }


def _quick_start_article(module_key: str, guide: dict[str, Any]) -> dict[str, Any]:
    module_title = guide["title"]
    manual_links = guide["manual_links"]
    journeys = guide["journeys"]
    summary = f"Start using {module_title} with the core pages, first actions, and success checks."
    return _build_article(
        slug=f"{module_key}-quick-start",
        title=f"{module_title} Quick Start",
        summary=summary,
        module_key=module_key,
        module_title=module_title,
        content_type="quick_start",
        audience="All users with access to this module",
        estimated_minutes=8,
        prerequisites=_module_prerequisites(module_title),
        sections=[
            {
                "title": "What This Area Covers",
                "body": guide["summary"],
            },
            {
                "title": "Start Here",
                "items": [link["label"] for link in manual_links],
            },
            {
                "title": "Core Workflows To Learn First",
                "items": [journey["title"] for journey in journeys],
            },
            {
                "title": "Success Checks",
                "items": _generic_success_checks(module_title),
            },
        ],
        related_links=manual_links,
    )


def _journey_article(
    module_key: str, guide: dict[str, Any], journey: dict[str, Any]
) -> dict[str, Any]:
    module_title = guide["title"]
    slug = f"{module_key}-{_slugify(journey['title'])}"
    return _build_article(
        slug=slug,
        title=journey["title"],
        summary=journey["outcome"],
        module_key=module_key,
        module_title=module_title,
        content_type="workflow",
        audience=f"{module_title} operators",
        estimated_minutes=max(6, len(journey["steps"]) * 3),
        prerequisites=_module_prerequisites(module_title),
        sections=[
            {
                "title": "Outcome",
                "body": journey["outcome"],
            },
            {
                "title": "Step-by-Step",
                "items": journey["steps"],
            },
            {
                "title": "Common Mistakes To Avoid",
                "items": [
                    "Skipping prerequisite setup or master data checks.",
                    "Moving to the next step before status and assignments are saved.",
                    "Not reviewing downstream reports or approvals after completion.",
                ],
            },
            {
                "title": "What To Check When You Finish",
                "items": _generic_success_checks(module_title),
            },
        ],
        related_links=[
            {"label": "Open Workflow Screen", "href": journey["href"]},
            *guide["manual_links"][:2],
        ],
    )


def _troubleshooting_article(
    module_key: str,
    guide: dict[str, Any],
    issue: dict[str, Any],
) -> dict[str, Any]:
    module_title = guide["title"]
    slug = f"{module_key}-{_slugify(issue['symptom'])}"
    return _build_article(
        slug=slug,
        title=issue["symptom"],
        summary=f"Diagnose and fix a common {module_title} issue.",
        module_key=module_key,
        module_title=module_title,
        content_type="troubleshooting",
        audience=f"{module_title} users and reviewers",
        estimated_minutes=7,
        prerequisites=_module_prerequisites(module_title),
        sections=[
            {
                "title": "Likely Causes",
                "items": issue["checks"],
            },
            {
                "title": "How To Diagnose It",
                "items": [
                    "Confirm the record status and the most recent user action.",
                    "Review required related data, assignments, and dates.",
                    "Check whether approvals, permissions, or downstream dependencies are blocking progress.",
                ],
            },
            {
                "title": "Fix Steps",
                "items": issue["checks"],
            },
            {
                "title": "When To Escalate",
                "items": [
                    "Escalate if the issue persists after all checks are complete.",
                    "Include the record reference, current status, and the exact screen where the failure occurs.",
                ],
            },
        ],
        related_links=guide["manual_links"],
    )


def _cross_module_article(workflow: dict[str, Any]) -> dict[str, Any]:
    modules = workflow["modules"]
    module_title = ", ".join(MODULE_GUIDES[module]["title"] for module in modules)
    slug = _slugify(workflow["title"])
    return _build_article(
        slug=slug,
        title=workflow["title"],
        summary=f"Run the full {workflow['title']} process across the connected modules.",
        module_key="cross_module",
        module_title=module_title,
        content_type="cross_module_workflow",
        audience="Cross-functional operators and approvers",
        estimated_minutes=max(10, len(workflow["steps"]) * 3),
        prerequisites=[
            "Confirm access to every module in this workflow.",
            "Ensure upstream records are approved before creating downstream transactions.",
            "Align owners for each handoff between teams.",
        ],
        sections=[
            {
                "title": "Modules Involved",
                "items": [MODULE_GUIDES[module]["title"] for module in modules],
            },
            {
                "title": "Workflow Steps",
                "items": [step["label"] for step in workflow["steps"]],
            },
            {
                "title": "Handoff Risks",
                "items": [
                    "Missing approvals before the next module starts work.",
                    "Incomplete references between source and downstream records.",
                    "Operational teams assuming finance or HR posting happened automatically.",
                ],
            },
        ],
        related_links=workflow["steps"],
    )


def _build_articles_for_modules(modules: list[str]) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    for module in modules:
        guide = MODULE_GUIDES[module]
        articles.append(_quick_start_article(module, guide))
        articles.extend(
            _journey_article(module, guide, journey) for journey in guide["journeys"]
        )
        articles.extend(
            _troubleshooting_article(module, guide, issue)
            for issue in guide["troubleshooting"]
        )

    available_modules = set(modules)
    for workflow in CROSS_MODULE_WORKFLOWS:
        if set(workflow["modules"]).issubset(available_modules):
            articles.append(_cross_module_article(workflow))

    articles.sort(key=lambda article: (article["module_title"], article["title"]))
    return articles


def _build_tracks(modules: list[str], is_admin: bool) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    if is_admin and "settings" in modules:
        tracks.append(
            {
                "slug": "admin-foundations",
                "title": "Administrator Foundations",
                "summary": "Set up modules, validate governance, and understand cross-module workflows.",
                "audience": "System administrators",
                "steps": [
                    {
                        "label": "Settings Quick Start",
                        "article_slug": "settings-quick-start",
                    },
                    {
                        "label": "Finance Quick Start",
                        "article_slug": "finance-quick-start",
                    },
                    {"label": "Procure-to-Pay", "article_slug": "procure-to-pay"},
                ],
            }
        )
    if "finance" in modules:
        tracks.append(
            {
                "slug": "finance-operations-foundations",
                "title": "Finance Operations Foundations",
                "summary": "Learn the main finance flows from setup through close.",
                "audience": "Finance operators",
                "steps": [
                    {
                        "label": "Finance Quick Start",
                        "article_slug": "finance-quick-start",
                    },
                    {
                        "label": "Chart of Accounts Setup",
                        "article_slug": "finance-chart-of-accounts-setup",
                    },
                    {
                        "label": "Month-End Close",
                        "article_slug": "finance-month-end-close",
                    },
                    {
                        "label": "Receivables to Cash",
                        "article_slug": "finance-receivables-to-cash",
                    },
                    {
                        "label": "AP Invoice Processing",
                        "article_slug": "finance-ap-invoice-processing",
                    },
                    {
                        "label": "Bank Reconciliation",
                        "article_slug": "finance-bank-reconciliation",
                    },
                    {
                        "label": "Period Close Checklist",
                        "article_slug": "finance-period-close-checklist",
                    },
                ],
            }
        )
    if "people" in modules:
        tracks.append(
            {
                "slug": "people-operations-foundations",
                "title": "People Operations Foundations",
                "summary": "Cover the employee lifecycle, payroll, and training operations.",
                "audience": "HR and payroll operators",
                "steps": [
                    {
                        "label": "People Quick Start",
                        "article_slug": "people-quick-start",
                    },
                    {
                        "label": "Employee Lifecycle",
                        "article_slug": "people-employee-lifecycle",
                    },
                    {
                        "label": "Recruitment Pipeline",
                        "article_slug": "people-recruitment-pipeline",
                    },
                    {
                        "label": "Leave Management",
                        "article_slug": "people-leave-management",
                    },
                    {
                        "label": "Payroll Execution",
                        "article_slug": "people-payroll-execution",
                    },
                    {
                        "label": "Training Program to Completion",
                        "article_slug": "people-training-program-to-completion",
                    },
                    {
                        "label": "Onboarding Workflow",
                        "article_slug": "people-onboarding-workflow",
                    },
                ],
            }
        )
    if "support" in modules:
        tracks.append(
            {
                "slug": "support-desk-foundations",
                "title": "Support Desk Foundations",
                "summary": "Run ticket intake, assignment, and closure with SLA awareness.",
                "audience": "Support agents and leads",
                "steps": [
                    {
                        "label": "Support Quick Start",
                        "article_slug": "support-quick-start",
                    },
                    {"label": "Ticket Triage", "article_slug": "support-ticket-triage"},
                    {
                        "label": "Resolution Workflow",
                        "article_slug": "support-resolution-workflow",
                    },
                ],
            }
        )
    if "inventory" in modules:
        tracks.append(
            {
                "slug": "inventory-operations-foundations",
                "title": "Inventory Operations Foundations",
                "summary": "Learn warehouse management, stock transactions, and inventory control.",
                "audience": "Inventory and warehouse operators",
                "steps": [
                    {
                        "label": "Inventory Quick Start",
                        "article_slug": "inventory-quick-start",
                    },
                    {
                        "label": "Warehouse Setup",
                        "article_slug": "inventory-warehouse-setup",
                    },
                    {
                        "label": "Item Setup to Stock Use",
                        "article_slug": "inventory-item-setup-to-stock-use",
                    },
                    {
                        "label": "Cycle Count and Adjustment",
                        "article_slug": "inventory-cycle-count-and-adjustment",
                    },
                ],
            }
        )
    if "procurement" in modules:
        tracks.append(
            {
                "slug": "procurement-operations-foundations",
                "title": "Procurement Operations Foundations",
                "summary": "Master the full procurement cycle from requisition to contract.",
                "audience": "Procurement officers and buyers",
                "steps": [
                    {
                        "label": "Procurement Quick Start",
                        "article_slug": "procurement-quick-start",
                    },
                    {
                        "label": "Requisition to RFQ",
                        "article_slug": "procurement-requisition-to-rfq",
                    },
                    {
                        "label": "PO to Goods Receipt",
                        "article_slug": "procurement-purchase-order-to-goods-receipt",
                    },
                    {
                        "label": "Contract Lifecycle",
                        "article_slug": "procurement-contract-lifecycle",
                    },
                ],
            }
        )
    if "projects" in modules:
        tracks.append(
            {
                "slug": "project-management-foundations",
                "title": "Project Management Foundations",
                "summary": "Set up projects, manage tasks and milestones, and report on progress.",
                "audience": "Project managers and team leads",
                "steps": [
                    {
                        "label": "Projects Quick Start",
                        "article_slug": "projects-quick-start",
                    },
                    {
                        "label": "Project Setup and Planning",
                        "article_slug": "projects-project-setup-and-planning",
                    },
                    {
                        "label": "Task Delivery and Reporting",
                        "article_slug": "projects-task-delivery-and-reporting",
                    },
                    {
                        "label": "Milestone Management",
                        "article_slug": "projects-milestone-management",
                    },
                ],
            }
        )
    if "expense" in modules:
        tracks.append(
            {
                "slug": "expense-management-foundations",
                "title": "Expense Management Foundations",
                "summary": "Process expense claims, enforce policies, and manage reimbursements.",
                "audience": "Finance teams and expense approvers",
                "steps": [
                    {
                        "label": "Expense Quick Start",
                        "article_slug": "expense-quick-start",
                    },
                    {
                        "label": "Claim Submission Workflow",
                        "article_slug": "expense-claim-submission-workflow",
                    },
                    {
                        "label": "Claim Review Workflow",
                        "article_slug": "expense-claim-review-workflow",
                    },
                    {
                        "label": "Policy Limit Enforcement",
                        "article_slug": "expense-policy-limit-enforcement",
                    },
                ],
            }
        )
    if "self_service" in modules:
        tracks.append(
            {
                "slug": "employee-self-service-foundations",
                "title": "Employee Self-Service Foundations",
                "summary": "Learn to use self-service for leave, attendance, payslips, and expenses.",
                "audience": "All employees",
                "steps": [
                    {
                        "label": "Self Service Quick Start",
                        "article_slug": "self_service-quick-start",
                    },
                    {
                        "label": "Employee Request Journey",
                        "article_slug": "self_service-employee-request-journey",
                    },
                    {
                        "label": "View Payslips",
                        "article_slug": "self_service-view-payslips",
                    },
                    {
                        "label": "Check Attendance Record",
                        "article_slug": "self_service-check-attendance-record",
                    },
                ],
            }
        )
    if "public_sector" in modules:
        tracks.append(
            {
                "slug": "public-sector-budget-foundations",
                "title": "Public Sector Budget Foundations",
                "summary": "Master appropriation setup, commitment control, and the virement process.",
                "audience": "Budget officers and public sector finance teams",
                "steps": [
                    {
                        "label": "Public Sector Quick Start",
                        "article_slug": "public_sector-quick-start",
                    },
                    {
                        "label": "Budget Control Setup",
                        "article_slug": "public_sector-budget-control-setup",
                    },
                    {
                        "label": "Commitment to Virement",
                        "article_slug": "public_sector-commitment-to-virement",
                    },
                    {
                        "label": "Appropriation Setup",
                        "article_slug": "public_sector-appropriation-setup",
                    },
                ],
            }
        )
    return tracks


def build_help_experience_payload(
    *,
    accessible_modules: Iterable[str],
    roles: Iterable[str],
    scopes: Iterable[str],
    is_admin: bool,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build article-first help payload used by the web experience."""
    del roles, scopes
    modules = _normalized_modules(accessible_modules)
    articles = _build_articles_for_modules(modules)
    articles_by_slug = {article["slug"]: article for article in articles}
    featured_articles = [
        articles_by_slug[slug]
        for slug in FEATURED_ARTICLE_SLUGS
        if slug in articles_by_slug
    ]
    tracks = _build_tracks(modules, is_admin)
    for track in tracks:
        enriched_steps = []
        for step in track["steps"]:
            article = articles_by_slug.get(step["article_slug"])
            if article:
                enriched_steps.append(
                    {
                        **step,
                        "href": article["href"],
                        "summary": article["summary"],
                    }
                )
        track["steps"] = enriched_steps
        track["step_count"] = len(enriched_steps)

    module_hubs = []
    for module in modules:
        guide = MODULE_GUIDES[module]
        module_articles = [
            article for article in articles if article["module_key"] == module
        ]
        module_hubs.append(
            {
                "module_key": module,
                "title": guide["title"],
                "summary": guide["summary"],
                "href": f"/help/module/{module}",
                "article_count": len(module_articles),
                "quick_links": guide["manual_links"],
            }
        )

    payload = {
        "articles": articles,
        "articles_by_slug": articles_by_slug,
        "featured_articles": featured_articles,
        "tracks": tracks,
        "module_hubs": module_hubs,
        "search_action": "/help/search",
    }
    payload = _merge_help_overrides(payload, overrides)
    payload["articles_by_slug"] = {
        article["slug"]: article for article in payload.get("articles", [])
    }
    if payload.get("featured_articles"):
        payload["featured_articles"] = [
            article
            for article in payload["featured_articles"]
            if isinstance(article, dict) and article.get("slug")
        ]
    return payload


def get_help_article_by_slug(
    *,
    accessible_modules: Iterable[str],
    roles: Iterable[str],
    scopes: Iterable[str],
    is_admin: bool,
    slug: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload = build_help_experience_payload(
        accessible_modules=accessible_modules,
        roles=roles,
        scopes=scopes,
        is_admin=is_admin,
        overrides=overrides,
    )
    articles_by_slug = payload.get("articles_by_slug")
    if not isinstance(articles_by_slug, dict):
        return None

    article = articles_by_slug.get(slug)
    return article if isinstance(article, dict) else None


def search_help_articles(
    *,
    accessible_modules: Iterable[str],
    roles: Iterable[str],
    scopes: Iterable[str],
    is_admin: bool,
    query: str | None,
    module_key: str | None = None,
    content_type: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = build_help_experience_payload(
        accessible_modules=accessible_modules,
        roles=roles,
        scopes=scopes,
        is_admin=is_admin,
        overrides=overrides,
    )
    normalized_query = (query or "").strip().lower()
    results = payload["articles"]
    if module_key:
        results = [
            article for article in results if article["module_key"] == module_key
        ]
    if content_type:
        results = [
            article for article in results if article["content_type"] == content_type
        ]
    if normalized_query:
        # Expand query with synonyms
        search_terms = {normalized_query}
        for term, synonyms in SEARCH_SYNONYMS.items():
            if normalized_query == term or normalized_query in synonyms:
                search_terms.add(term)
                search_terms.update(synonyms)

        # Score and filter results
        scored: list[tuple[int, dict[str, Any]]] = []
        for article in results:
            blob = article["search_blob"]
            title_lower = article["title"].lower()
            score = 0
            for term in search_terms:
                if term in title_lower:
                    score += 10  # Title match is highest value
                if term in blob:
                    score += 1
            if score > 0:
                scored.append((score, article))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [article for _, article in scored]

    return {
        **payload,
        "query": query or "",
        "results": results,
        "result_count": len(results),
        "selected_module": module_key or "",
        "selected_content_type": content_type or "",
    }


def build_help_module_hub(
    *,
    accessible_modules: Iterable[str],
    roles: Iterable[str],
    scopes: Iterable[str],
    is_admin: bool,
    module_key: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload = build_help_experience_payload(
        accessible_modules=accessible_modules,
        roles=roles,
        scopes=scopes,
        is_admin=is_admin,
        overrides=overrides,
    )
    if module_key not in {hub["module_key"] for hub in payload["module_hubs"]}:
        return None
    guide = MODULE_GUIDES[module_key]
    return {
        **payload,
        "module_key": module_key,
        "module_title": guide["title"],
        "module_summary": guide["summary"],
        "quick_links": guide["manual_links"],
        "module_articles": [
            article
            for article in payload["articles"]
            if article["module_key"] == module_key
        ],
    }


def get_help_track_by_slug(
    *,
    accessible_modules: Iterable[str],
    roles: Iterable[str],
    scopes: Iterable[str],
    is_admin: bool,
    slug: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload = build_help_experience_payload(
        accessible_modules=accessible_modules,
        roles=roles,
        scopes=scopes,
        is_admin=is_admin,
        overrides=overrides,
    )
    tracks = payload.get("tracks")
    if not isinstance(tracks, list):
        return None

    for track in tracks:
        if isinstance(track, dict) and track.get("slug") == slug:
            return track
    return None
