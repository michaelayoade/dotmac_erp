"""
HR Entity Mappings from ERPNext to DotMac ERP.

Maps ERPNext HR DocTypes to DotMac HR schema:
- Department → hr.department
- Designation → hr.designation
- Employment Type → hr.employment_type
- Employee Grade → hr.employee_grade
- Employee → hr.employee
"""
from typing import Any

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    invert_bool,
    parse_date,
    parse_datetime,
    parse_decimal,
)


# ERPNext Employee Status to DotMac EmployeeStatus
EMPLOYEE_STATUS_MAP = {
    "Active": "ACTIVE",
    "Inactive": "SUSPENDED",
    "Left": "RESIGNED",
    "Suspended": "SUSPENDED",
    "Retired": "RETIRED",
}


# ERPNext Gender to DotMac Gender
GENDER_MAP = {
    "Male": "MALE",
    "Female": "FEMALE",
    "Other": "OTHER",
    "Prefer not to say": "PREFER_NOT_TO_SAY",
    "Genderqueer": "OTHER",
    "Non-Conforming": "OTHER",
    "Transgender": "OTHER",
}


def map_employee_status(value: Any) -> str:
    """Map ERPNext employee status."""
    if not value:
        return "DRAFT"
    return EMPLOYEE_STATUS_MAP.get(str(value), "ACTIVE")


def map_gender(value: Any) -> str:
    """Map ERPNext gender."""
    if not value:
        return "PREFER_NOT_TO_SAY"
    return GENDER_MAP.get(str(value), "OTHER")


class DepartmentMapping(DocTypeMapping):
    """Map ERPNext Department to DotMac ERP hr.department."""

    def __init__(self):
        super().__init__(
            source_doctype="Department",
            target_table="hr.department",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="department_name",
                    target="department_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="parent_department",
                    target="_parent_source_name",
                    required=False,
                ),
                FieldMapping(
                    source="disabled",
                    target="is_active",
                    required=False,
                    default=True,
                    transformer=invert_bool,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with department code generation."""
        result = super().transform_record(record)

        # Generate department code from ERPNext name
        # ERPNext dept names like "Sales - Company" -> "SALES"
        dept_name = record.get("department_name", record.get("name", ""))
        # Split on " - " and take first part, then sanitize
        code_base = dept_name.split(" - ")[0] if " - " in dept_name else dept_name
        result["department_code"] = (
            clean_string(code_base.upper().replace(" ", "_"), 20) or "DEPT"
        )

        return result


class DesignationMapping(DocTypeMapping):
    """Map ERPNext Designation to DotMac ERP hr.designation."""

    def __init__(self):
        super().__init__(
            source_doctype="Designation",
            target_table="hr.designation",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="designation_name",
                    target="designation_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with designation code generation."""
        result = super().transform_record(record)

        # Generate designation code from name
        name = record.get("designation_name") or record.get("name", "")
        # Sanitize and truncate
        code = name.upper().replace(" ", "_").replace("-", "_")
        result["designation_code"] = clean_string(code, 20) or "DESG"
        result["is_active"] = True

        return result


class EmploymentTypeMapping(DocTypeMapping):
    """Map ERPNext Employment Type to DotMac ERP hr.employment_type.

    Note: In ERPNext Employment Type DocType, the 'name' field IS the type name
    (e.g., "Full-time", "Part-time"). There is no separate 'employment_type_name' field.
    """

    def __init__(self):
        super().__init__(
            source_doctype="Employment Type",
            target_table="hr.employment_type",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                # In Employment Type, 'name' is the type name
                FieldMapping(
                    source="name",
                    target="type_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with employment type code generation."""
        result = super().transform_record(record)

        # Generate type code from name
        # ERPNext: "Full-time" -> "FULL_TIME"
        name = record.get("name", "")
        code = name.upper().replace(" ", "_").replace("-", "_")
        result["type_code"] = clean_string(code, 20) or "TYPE"
        result["is_active"] = True

        return result


class EmployeeGradeMapping(DocTypeMapping):
    """Map ERPNext Employee Grade to DotMac ERP hr.employee_grade."""

    def __init__(self):
        super().__init__(
            source_doctype="Employee Grade",
            target_table="hr.employee_grade",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="name",  # ERPNext uses name as grade identifier
                    target="grade_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="default_base_pay",
                    target="min_salary",
                    required=False,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with grade code generation."""
        result = super().transform_record(record)

        # Generate grade code from name
        name = record.get("name", "")
        code = name.upper().replace(" ", "_").replace("-", "_")
        result["grade_code"] = clean_string(code, 20) or "GRADE"
        result["is_active"] = True

        # Default rank based on salary if available
        base_pay = result.get("min_salary")
        if base_pay:
            # Higher pay = higher rank
            result["rank"] = int(base_pay / 10000) if base_pay > 0 else 0
        else:
            result["rank"] = 0

        return result


class EmployeeMapping(DocTypeMapping):
    """Map ERPNext Employee to DotMac ERP hr.employee."""

    def __init__(self):
        super().__init__(
            source_doctype="Employee",
            target_table="hr.employee",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="employee_name",
                    target="employee_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 200),
                ),
                FieldMapping(
                    source="first_name",
                    target="first_name",
                    required=False,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="middle_name",
                    target="middle_name",
                    required=False,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="last_name",
                    target="last_name",
                    required=False,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="gender",
                    target="gender",
                    required=False,
                    transformer=map_gender,
                ),
                FieldMapping(
                    source="date_of_birth",
                    target="date_of_birth",
                    required=False,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="date_of_joining",
                    target="date_of_joining",
                    required=False,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="relieving_date",
                    target="date_of_leaving",
                    required=False,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="status",
                    target="status",
                    required=False,
                    default="ACTIVE",
                    transformer=map_employee_status,
                ),
                # FK references stored as ERPNext names for later resolution
                FieldMapping(
                    source="department",
                    target="_department_source_name",
                    required=False,
                ),
                FieldMapping(
                    source="designation",
                    target="_designation_source_name",
                    required=False,
                ),
                FieldMapping(
                    source="employment_type",
                    target="_employment_type_source_name",
                    required=False,
                ),
                FieldMapping(
                    source="grade",
                    target="_grade_source_name",
                    required=False,
                ),
                FieldMapping(
                    source="reports_to",
                    target="_reports_to_source_name",
                    required=False,
                ),
                # Contact info (for matching to Person)
                FieldMapping(
                    source="cell_number",
                    target="phone",
                    required=False,
                    transformer=lambda v: clean_string(v, 20),
                ),
                FieldMapping(
                    source="personal_email",
                    target="personal_email",
                    required=False,
                    transformer=lambda v: clean_string(v, 255),
                ),
                FieldMapping(
                    source="company_email",
                    target="work_email",
                    required=False,
                    transformer=lambda v: clean_string(v, 255),
                ),
                FieldMapping(
                    source="prefered_email",
                    target="_preferred_email",  # Used for Person matching
                    required=False,
                    transformer=lambda v: clean_string(v, 255),
                ),
                # Bank details
                FieldMapping(
                    source="bank_name",
                    target="bank_name",
                    required=False,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="bank_ac_no",
                    target="bank_account_number",
                    required=False,
                    transformer=lambda v: clean_string(v, 30),
                ),
                # Cost center (for later resolution)
                FieldMapping(
                    source="cost_center",
                    target="_cost_center_source_name",
                    required=False,
                ),
                FieldMapping(
                    source="payroll_cost_center",
                    target="_payroll_cost_center_source_name",
                    required=False,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with employee code generation."""
        result = super().transform_record(record)

        # Generate employee code from ERPNext ID
        # ERPNext: "HR-EMP-00001" -> "EMP00001" or just use truncated name
        erpnext_name = record.get("name", "")
        if erpnext_name.startswith("HR-EMP-"):
            code = erpnext_name.replace("HR-EMP-", "EMP")
        else:
            code = erpnext_name.upper().replace("-", "").replace(" ", "")
        result["employee_code"] = clean_string(code, 20) or "EMP"

        return result
