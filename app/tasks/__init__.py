from app.tasks.sync import (
    run_full_erpnext_sync,
    run_incremental_erpnext_sync,
    sync_single_entity_type,
    scheduled_hr_sync,
    scheduled_expense_sync,
    push_expense_claim_to_erpnext,
)

from app.tasks.expense import (
    refresh_period_usage_cache,
    process_expense_approval_reminders,
    post_approved_expense,
    post_cash_advance_disbursement,
    settle_cash_advance_with_claim,
    calculate_expense_analytics,
    poll_stuck_expense_transfers,
)

from app.tasks.hr import (
    process_probation_ending_notifications,
    process_contract_expiry_notifications,
    process_work_anniversary_notifications,
    process_birthday_notifications,
    process_performance_review_reminders,
    process_certification_expiry_notifications,
    calculate_hr_analytics,
)

from app.tasks.performance import (
    process_cycle_phase_transitions,
    generate_cycle_appraisals,
    calculate_cycle_progress,
    check_upcoming_deadlines,
    sync_all_cycle_progress,
    activate_cycle,
    complete_cycle,
)

from app.tasks.finance import (
    sync_paystack_transactions,
)

# Register additional task modules used via .delay()
from app.tasks.audit import log_audit_event
from app.tasks.payroll import (
    send_payslip_email,
    process_payroll_entry_notifications,
)
from app.tasks.email import send_email_async

__all__ = [
    # ERPNext sync tasks
    "run_full_erpnext_sync",
    "run_incremental_erpnext_sync",
    "sync_single_entity_type",
    "scheduled_hr_sync",
    "scheduled_expense_sync",
    "push_expense_claim_to_erpnext",
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
    # Finance tasks
    "sync_paystack_transactions",
]
