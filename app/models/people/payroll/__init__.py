"""
Payroll Models - People Module.

Provides salary components, structures, slips, bulk processing, and NTA 2025 PAYE tax.
"""

from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.people.payroll.payroll_entry import (
    PayrollEntry,
    PayrollEntryStatus,
)
from app.models.people.payroll.salary_assignment import (
    SalaryStructureAssignment,
)
from app.models.people.payroll.salary_component import (
    SalaryComponent,
    SalaryComponentType,
)
from app.models.people.payroll.salary_slip import (
    SalarySlip,
    SalarySlipDeduction,
    SalarySlipEarning,
    SalarySlipStatus,
)
from app.models.people.payroll.salary_structure import (
    PayrollFrequency,
    SalaryStructure,
    SalaryStructureDeduction,
    SalaryStructureEarning,
)
from app.models.people.payroll.tax_band import TaxBand

__all__ = [
    # Salary Component
    "SalaryComponent",
    "SalaryComponentType",
    # Salary Structure
    "PayrollFrequency",
    "SalaryStructure",
    "SalaryStructureDeduction",
    "SalaryStructureEarning",
    # Salary Slip
    "SalarySlip",
    "SalarySlipDeduction",
    "SalarySlipEarning",
    "SalarySlipStatus",
    # Payroll Entry
    "PayrollEntry",
    "PayrollEntryStatus",
    # Assignment
    "SalaryStructureAssignment",
    # NTA 2025 PAYE Tax
    "TaxBand",
    "EmployeeTaxProfile",
]
