# Register additional task modules used via .delay()
from app.tasks.analytics import (
    refresh_cash_flow_metrics,
    refresh_compliance_metrics,
    refresh_efficiency_metrics,
    refresh_revenue_metrics,
    refresh_supply_chain_metrics,
    refresh_workforce_metrics,
)
from app.tasks.audit import log_audit_event
from app.tasks.automation import (
    execute_workflow_action,
    process_recurring_templates,
    process_scheduled_workflow_rules,
)
from app.tasks.banking import auto_match_unreconciled_statements
from app.tasks.coach import (
    generate_daily_ap_due_insights,
    generate_daily_ar_overdue_insights,
    generate_daily_banking_health_insights,
    generate_daily_cash_flow_insights,
    generate_daily_compliance_insights,
    generate_daily_data_quality_insights,
    generate_daily_efficiency_insights,
    generate_daily_expense_approval_insights,
    generate_daily_revenue_insights,
    generate_daily_supply_chain_insights,
    generate_daily_workforce_insights,
    generate_weekly_finance_report,
)
from app.tasks.data_health import (
    auto_post_approved_invoices,
    cleanup_old_notifications,
    cleanup_stale_drafts,
    fix_unbalanced_posted_journals,
    process_stuck_outbox_events,
    rebuild_account_balances,
    reconcile_invoice_statuses,
    reconcile_payment_allocations,
    run_data_health_check,
)
from app.tasks.email import send_email_async
from app.tasks.expense import (
    calculate_expense_analytics,
    poll_stuck_expense_transfers,
    post_approved_expense,
    post_cash_advance_disbursement,
    process_expense_approval_reminders,
    refresh_period_usage_cache,
    settle_cash_advance_with_claim,
)
from app.tasks.finance import (
    sync_paystack_transactions,
)
from app.tasks.fleet import (
    process_document_expiry_notifications,
)
from app.tasks.hr import (
    calculate_hr_analytics,
    process_birthday_notifications,
    process_certification_expiry_notifications,
    process_contract_expiry_notifications,
    process_performance_review_reminders,
    process_probation_ending_notifications,
    process_work_anniversary_notifications,
)
from app.tasks.outbox_relay import (
    cleanup_published_outbox_events,
    relay_outbox_events,
)
from app.tasks.payroll import (
    process_payroll_entry_notifications,
    send_payslip_email,
)
from app.tasks.performance import (
    activate_cycle,
    calculate_cycle_progress,
    check_upcoming_deadlines,
    complete_cycle,
    generate_cycle_appraisals,
    process_cycle_phase_transitions,
    sync_all_cycle_progress,
)
from app.tasks.splynx import (
    cleanup_stale_splynx_sync_history,
    run_scheduled_splynx_sync,
    run_splynx_daily_reconciliation,
    run_splynx_full_reconciliation,
    run_splynx_incremental_sync,
)
from app.tasks.sync import (
    push_expense_claim_to_erpnext,
    run_full_erpnext_sync,
    run_incremental_erpnext_sync,
    scheduled_expense_sync,
    scheduled_hr_sync,
    sync_single_entity_type,
)

__all__ = [
    # ERPNext sync tasks
    "run_full_erpnext_sync",
    "run_incremental_erpnext_sync",
    "sync_single_entity_type",
    "scheduled_hr_sync",
    "scheduled_expense_sync",
    "push_expense_claim_to_erpnext",
    "run_scheduled_splynx_sync",
    "run_splynx_incremental_sync",
    "run_splynx_daily_reconciliation",
    "run_splynx_full_reconciliation",
    "cleanup_stale_splynx_sync_history",
    # Expense module tasks
    "refresh_period_usage_cache",
    "process_expense_approval_reminders",
    "post_approved_expense",
    "post_cash_advance_disbursement",
    "settle_cash_advance_with_claim",
    "calculate_expense_analytics",
    "poll_stuck_expense_transfers",
    # HR module tasks
    "process_probation_ending_notifications",
    "process_contract_expiry_notifications",
    "process_work_anniversary_notifications",
    "process_birthday_notifications",
    "process_performance_review_reminders",
    "process_certification_expiry_notifications",
    "calculate_hr_analytics",
    # Fleet module tasks
    "process_document_expiry_notifications",
    # Performance module tasks
    "process_cycle_phase_transitions",
    "generate_cycle_appraisals",
    "calculate_cycle_progress",
    "check_upcoming_deadlines",
    "sync_all_cycle_progress",
    "activate_cycle",
    "complete_cycle",
    # Audit tasks
    "log_audit_event",
    # Payroll tasks
    "send_payslip_email",
    "process_payroll_entry_notifications",
    # Email tasks
    "send_email_async",
    # Automation tasks
    "execute_workflow_action",
    "process_recurring_templates",
    "process_scheduled_workflow_rules",
    # Finance tasks
    "sync_paystack_transactions",
    # Banking tasks
    "auto_match_unreconciled_statements",
    # Analytics tasks
    "refresh_cash_flow_metrics",
    "refresh_compliance_metrics",
    "refresh_efficiency_metrics",
    "refresh_revenue_metrics",
    "refresh_supply_chain_metrics",
    "refresh_workforce_metrics",
    # Coach tasks
    "generate_daily_ap_due_insights",
    "generate_daily_ar_overdue_insights",
    "generate_daily_banking_health_insights",
    "generate_daily_cash_flow_insights",
    "generate_daily_compliance_insights",
    "generate_daily_data_quality_insights",
    "generate_daily_efficiency_insights",
    "generate_daily_expense_approval_insights",
    "generate_daily_revenue_insights",
    "generate_daily_supply_chain_insights",
    "generate_daily_workforce_insights",
    "generate_weekly_finance_report",
    # Data health tasks
    "cleanup_old_notifications",
    "process_stuck_outbox_events",
    "reconcile_invoice_statuses",
    "auto_post_approved_invoices",
    "cleanup_stale_drafts",
    "rebuild_account_balances",
    "reconcile_payment_allocations",
    "fix_unbalanced_posted_journals",
    "run_data_health_check",
    # Outbox relay tasks
    "relay_outbox_events",
    "cleanup_published_outbox_events",
]
