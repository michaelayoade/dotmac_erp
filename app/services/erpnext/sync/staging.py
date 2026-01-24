"""
Staging Sync Services - ERPNext to Staging Tables.

Syncs ERPNext data to staging tables for validation before production import.
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.sync.staging import (
    StagingDepartment,
    StagingDesignation,
    StagingEmployee,
    StagingEmployeeGrade,
    StagingEmploymentType,
    StagingStatus,
    StagingSyncBatch,
)
from app.services.erpnext.client import ERPNextClient
from app.services.erpnext.mappings.hr import (
    DepartmentMapping,
    DesignationMapping,
    EmployeeGradeMapping,
    EmployeeMapping,
    EmploymentTypeMapping,
)

logger = logging.getLogger(__name__)


class StagingSyncResult:
    """Result of a staging sync operation."""

    def __init__(self):
        self.total = 0
        self.synced = 0
        self.errors: list[str] = []

    def add_error(self, message: str):
        self.errors.append(message)


class StagingSyncOrchestrator:
    """
    Orchestrates sync from ERPNext to staging tables.

    Flow:
    1. Create a sync batch
    2. Fetch data from ERPNext
    3. Transform and write to staging tables
    4. Update batch status
    """

    def __init__(
        self,
        db: Session,
        client: ERPNextClient,
        organization_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
    ):
        self.db = db
        self.client = client
        self.organization_id = organization_id
        self.user_id = user_id

        # Mappings
        self._dept_mapping = DepartmentMapping()
        self._desg_mapping = DesignationMapping()
        self._emptype_mapping = EmploymentTypeMapping()
        self._grade_mapping = EmployeeGradeMapping()
        self._emp_mapping = EmployeeMapping()

    def sync_to_staging(
        self,
        entity_types: Optional[list[str]] = None,
        clear_existing: bool = True,
    ) -> StagingSyncBatch:
        """
        Sync ERPNext data to staging tables.

        Args:
            entity_types: List of entity types to sync (default: all HR entities)
            clear_existing: Clear existing staging data for this org before sync

        Returns:
            StagingSyncBatch with sync statistics
        """
        if entity_types is None:
            entity_types = [
                "departments",
                "designations",
                "employment_types",
                "employee_grades",
                "employees",
            ]

        # Create batch record
        batch = StagingSyncBatch(
            organization_id=self.organization_id,
            source_system="erpnext",
            entity_types=entity_types,
            status="SYNCING",
            initiated_by=self.user_id,
        )
        self.db.add(batch)
        self.db.flush()

        logger.info(f"Starting staging sync batch {batch.batch_id}")

        total_records = 0
        results = {}

        try:
            # Clear existing staging data if requested
            if clear_existing:
                self._clear_staging_data()

            # Sync each entity type
            if "departments" in entity_types:
                result = self._sync_departments(batch.batch_id)
                results["departments"] = {"synced": result.synced, "errors": len(result.errors)}
                total_records += result.synced

            if "designations" in entity_types:
                result = self._sync_designations(batch.batch_id)
                results["designations"] = {"synced": result.synced, "errors": len(result.errors)}
                total_records += result.synced

            if "employment_types" in entity_types:
                result = self._sync_employment_types(batch.batch_id)
                results["employment_types"] = {"synced": result.synced, "errors": len(result.errors)}
                total_records += result.synced

            if "employee_grades" in entity_types:
                result = self._sync_employee_grades(batch.batch_id)
                results["employee_grades"] = {"synced": result.synced, "errors": len(result.errors)}
                total_records += result.synced

            if "employees" in entity_types:
                result = self._sync_employees(batch.batch_id)
                results["employees"] = {"synced": result.synced, "errors": len(result.errors)}
                total_records += result.synced

            # Update batch
            batch.status = "SYNCED"
            batch.total_records = total_records
            batch.synced_at = datetime.utcnow()
            batch.validation_summary = results

            self.db.commit()
            logger.info(f"Staging sync complete: {total_records} records")

        except Exception as e:
            logger.exception(f"Staging sync failed: {e}")
            batch.status = "FAILED"
            batch.notes = str(e)
            self.db.commit()
            raise

        return batch

    def _clear_staging_data(self):
        """Clear existing staging data for this organization."""
        self.db.query(StagingEmployee).filter(
            StagingEmployee.organization_id == self.organization_id,
            StagingEmployee.validation_status != StagingStatus.IMPORTED,
        ).delete()
        self.db.query(StagingEmployeeGrade).filter(
            StagingEmployeeGrade.organization_id == self.organization_id,
            StagingEmployeeGrade.validation_status != StagingStatus.IMPORTED,
        ).delete()
        self.db.query(StagingEmploymentType).filter(
            StagingEmploymentType.organization_id == self.organization_id,
            StagingEmploymentType.validation_status != StagingStatus.IMPORTED,
        ).delete()
        self.db.query(StagingDesignation).filter(
            StagingDesignation.organization_id == self.organization_id,
            StagingDesignation.validation_status != StagingStatus.IMPORTED,
        ).delete()
        self.db.query(StagingDepartment).filter(
            StagingDepartment.organization_id == self.organization_id,
            StagingDepartment.validation_status != StagingStatus.IMPORTED,
        ).delete()
        self.db.flush()

    def _sync_departments(self, batch_id: uuid.UUID) -> StagingSyncResult:
        """Sync departments to staging."""
        result = StagingSyncResult()
        logger.info("Syncing departments to staging...")

        for record in self.client.get_departments():
            try:
                result.total += 1
                transformed = self._dept_mapping.transform_record(record)

                staging = StagingDepartment(
                    organization_id=self.organization_id,
                    batch_id=batch_id,
                    source_name=record.get("name", ""),
                    source_modified=transformed.get("_source_modified"),
                    raw_data=record,
                    department_code=transformed.get("department_code", "")[:50],
                    department_name=transformed.get("department_name", "")[:140],
                    parent_department_name=transformed.get("_parent_source_name"),
                    is_active=transformed.get("is_active", True),
                )
                self.db.add(staging)
                result.synced += 1

            except Exception as e:
                logger.error(f"Error staging department {record.get('name')}: {e}")
                result.add_error(f"{record.get('name')}: {str(e)}")

        self.db.flush()
        logger.info(f"Departments staged: {result.synced}/{result.total}")
        return result

    def _sync_designations(self, batch_id: uuid.UUID) -> StagingSyncResult:
        """Sync designations to staging."""
        result = StagingSyncResult()
        logger.info("Syncing designations to staging...")

        for record in self.client.get_designations():
            try:
                result.total += 1
                transformed = self._desg_mapping.transform_record(record)

                staging = StagingDesignation(
                    organization_id=self.organization_id,
                    batch_id=batch_id,
                    source_name=record.get("name", ""),
                    source_modified=transformed.get("_source_modified"),
                    raw_data=record,
                    designation_code=transformed.get("designation_code", "")[:50],
                    designation_name=transformed.get("designation_name", "")[:140],
                    is_active=transformed.get("is_active", True),
                )
                self.db.add(staging)
                result.synced += 1

            except Exception as e:
                logger.error(f"Error staging designation {record.get('name')}: {e}")
                result.add_error(f"{record.get('name')}: {str(e)}")

        self.db.flush()
        logger.info(f"Designations staged: {result.synced}/{result.total}")
        return result

    def _sync_employment_types(self, batch_id: uuid.UUID) -> StagingSyncResult:
        """Sync employment types to staging."""
        result = StagingSyncResult()
        logger.info("Syncing employment types to staging...")

        for record in self.client.get_employment_types():
            try:
                result.total += 1
                transformed = self._emptype_mapping.transform_record(record)

                staging = StagingEmploymentType(
                    organization_id=self.organization_id,
                    batch_id=batch_id,
                    source_name=record.get("name", ""),
                    source_modified=transformed.get("_source_modified"),
                    raw_data=record,
                    type_code=transformed.get("type_code", "")[:30],
                    type_name=transformed.get("type_name", "")[:100],
                    is_active=transformed.get("is_active", True),
                )
                self.db.add(staging)
                result.synced += 1

            except Exception as e:
                logger.error(f"Error staging employment type {record.get('name')}: {e}")
                result.add_error(f"{record.get('name')}: {str(e)}")

        self.db.flush()
        logger.info(f"Employment types staged: {result.synced}/{result.total}")
        return result

    def _sync_employee_grades(self, batch_id: uuid.UUID) -> StagingSyncResult:
        """Sync employee grades to staging."""
        result = StagingSyncResult()
        logger.info("Syncing employee grades to staging...")

        for record in self.client.get_employee_grades():
            try:
                result.total += 1
                transformed = self._grade_mapping.transform_record(record)

                staging = StagingEmployeeGrade(
                    organization_id=self.organization_id,
                    batch_id=batch_id,
                    source_name=record.get("name", ""),
                    source_modified=transformed.get("_source_modified"),
                    raw_data=record,
                    grade_code=transformed.get("grade_code", "")[:30],
                    grade_name=transformed.get("grade_name", "")[:100],
                    default_base_pay=transformed.get("min_salary"),
                    is_active=transformed.get("is_active", True),
                )
                self.db.add(staging)
                result.synced += 1

            except Exception as e:
                logger.error(f"Error staging employee grade {record.get('name')}: {e}")
                result.add_error(f"{record.get('name')}: {str(e)}")

        self.db.flush()
        logger.info(f"Employee grades staged: {result.synced}/{result.total}")
        return result

    def _sync_employees(self, batch_id: uuid.UUID) -> StagingSyncResult:
        """Sync employees to staging."""
        result = StagingSyncResult()
        logger.info("Syncing employees to staging...")

        company = self.client.config.company
        for record in self.client.get_employees(company=company):
            try:
                result.total += 1
                transformed = self._emp_mapping.transform_record(record)

                # Determine preferred email
                company_email = record.get("company_email")
                personal_email = record.get("personal_email")
                preferred_email = company_email or personal_email

                staging = StagingEmployee(
                    organization_id=self.organization_id,
                    batch_id=batch_id,
                    source_name=record.get("name", ""),
                    source_modified=transformed.get("_source_modified"),
                    raw_data=record,
                    employee_code=transformed.get("employee_code", "")[:30],
                    employee_name=record.get("employee_name", "")[:200],
                    first_name=record.get("first_name", "")[:100] if record.get("first_name") else None,
                    last_name=record.get("last_name", "")[:100] if record.get("last_name") else None,
                    gender=transformed.get("gender"),
                    date_of_birth=transformed.get("date_of_birth"),
                    company_email=company_email,
                    personal_email=personal_email,
                    preferred_email=preferred_email,
                    cell_number=record.get("cell_number"),
                    department_name=record.get("department"),
                    designation_name=record.get("designation"),
                    employment_type_name=record.get("employment_type"),
                    grade_name=record.get("grade"),
                    reports_to_name=record.get("reports_to"),
                    date_of_joining=transformed.get("date_of_joining"),
                    date_of_leaving=transformed.get("date_of_leaving"),
                    status=transformed.get("status"),
                    bank_name=record.get("bank_name"),
                    bank_ac_no=record.get("bank_ac_no"),
                )
                self.db.add(staging)
                result.synced += 1

            except Exception as e:
                logger.error(f"Error staging employee {record.get('name')}: {e}")
                result.add_error(f"{record.get('name')}: {str(e)}")

        self.db.flush()
        logger.info(f"Employees staged: {result.synced}/{result.total}")
        return result
