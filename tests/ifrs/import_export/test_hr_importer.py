from __future__ import annotations

from datetime import date

from app.services.people.hr.import_export import EmployeeImporter


def test_employee_importer_creates_person(import_config, mock_db):
    mock_db.scalar.return_value = None

    importer = EmployeeImporter(mock_db, import_config)
    row = {
        "employee_code": "EMP-001",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "work_email": "ada@example.com",
        "date_of_joining": date(2024, 1, 15),
    }

    employee = importer.create_entity(row)

    assert employee.employee_code == "EMP-001"
    assert employee.person_id is not None
    assert employee.date_of_joining == date(2024, 1, 15)
    mock_db.add.assert_called_once()
    mock_db.flush.assert_called_once()
