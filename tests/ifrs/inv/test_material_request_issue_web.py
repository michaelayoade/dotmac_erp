"""The issue form binds serial values to their request lines atomically."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.operations.inv_web import OperationsInventoryWebService


def test_issue_form_passes_serials_by_line_and_commits() -> None:
    db = MagicMock()
    auth = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())
    request_id, router_line, cable_line = uuid4(), uuid4(), uuid4()
    with patch(
        "app.services.inventory.material_request_issue.MaterialRequestIssueService.issue_available"
    ) as issue:
        response = (
            OperationsInventoryWebService().issue_material_request_lines_response(
                str(request_id),
                auth,
                db,
                [str(router_line), str(cable_line)],
                ["2", "5"],
                ["1", "0"],
                [],
                ['["RTR-2", "RTR-3"]', "[]"],
            )
        )
    assert response.status_code == 303
    assert "success=" in response.headers["location"]
    assert issue.call_args.kwargs["serial_numbers"] == {
        router_line: ["RTR-2", "RTR-3"],
        cable_line: [],
    }
    assert issue.call_args.args[4] == {
        router_line: Decimal("2"),
        cable_line: Decimal("5"),
    }
    db.commit.assert_called_once()


def test_issue_form_rolls_back_on_serial_failure() -> None:
    db = MagicMock()
    auth = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())
    request_id, line_id = uuid4(), uuid4()
    with patch(
        "app.services.inventory.material_request_issue.MaterialRequestIssueService.issue_available",
        side_effect=ValueError("Serial number is unavailable"),
    ):
        response = (
            OperationsInventoryWebService().issue_material_request_lines_response(
                str(request_id),
                auth,
                db,
                [str(line_id)],
                ["1"],
                ["0"],
                [],
                ['["RTR-1"]'],
            )
        )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
