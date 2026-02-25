"""Canonical hook event names for extensibility integrations."""

# Accounts Receivable
AR_INVOICE_CREATED = "ar.invoice.created"
AR_INVOICE_SUBMITTED = "ar.invoice.submitted"
AR_PAYMENT_POSTED = "ar.payment.posted"

# Accounts Payable
AP_INVOICE_CREATED = "ap.invoice.created"
AP_PAYMENT_POSTED = "ap.payment.posted"

# General Ledger
GL_JOURNAL_POSTED = "gl.journal.posted"

# Sales / Order Management
SALES_ORDER_CONFIRMED = "sales.order.confirmed"
SALES_ORDER_CANCELLED = "sales.order.cancelled"
SHIPMENT_CREATED = "shipment.created"

# Inventory
INVENTORY_TRANSACTION_POSTED = "inventory.transaction.posted"
INVENTORY_STOCK_RESERVED = "inventory.stock.reserved"
INVENTORY_STOCK_RELEASED = "inventory.stock.released"

# Banking
BANK_RECONCILIATION_COMPLETED = "bank.reconciliation.completed"

# People / HR
HR_EMPLOYEE_CREATED = "hr.employee.created"

# Expense
EXPENSE_CLAIM_SUBMITTED = "expense.claim.submitted"
EXPENSE_CLAIM_APPROVED = "expense.claim.approved"

__all__ = [
    "AR_INVOICE_CREATED",
    "AR_INVOICE_SUBMITTED",
    "AR_PAYMENT_POSTED",
    "AP_INVOICE_CREATED",
    "AP_PAYMENT_POSTED",
    "GL_JOURNAL_POSTED",
    "SALES_ORDER_CONFIRMED",
    "SALES_ORDER_CANCELLED",
    "SHIPMENT_CREATED",
    "INVENTORY_TRANSACTION_POSTED",
    "INVENTORY_STOCK_RESERVED",
    "INVENTORY_STOCK_RELEASED",
    "BANK_RECONCILIATION_COMPLETED",
    "HR_EMPLOYEE_CREATED",
    "EXPENSE_CLAIM_SUBMITTED",
    "EXPENSE_CLAIM_APPROVED",
]
