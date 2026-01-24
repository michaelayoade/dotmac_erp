"""
People → Finance Integration Adapters.

This module contains adapters that bridge People and Finance modules:
- ExpenseAPAdapter: Expense Claims → AP (Supplier Invoices)
- PayrollGLAdapter: Salary Slips → GL (Journal Entries)
"""

from app.services.people.integrations.expense_ap_adapter import ExpenseAPAdapter
from app.services.people.integrations.payroll_gl_adapter import PayrollGLAdapter

__all__ = [
    "ExpenseAPAdapter",
    "PayrollGLAdapter",
]
