"""
Expense Services.
"""

from app.services.ifrs.exp.expense import ExpenseService, expense_service
from app.services.ifrs.exp.web import ExpenseWebService, expense_web_service

__all__ = [
    "ExpenseService",
    "expense_service",
    "ExpenseWebService",
    "expense_web_service",
]
