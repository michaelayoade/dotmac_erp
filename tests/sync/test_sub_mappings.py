"""Unit tests for the pure Sub → ERP translation layer (``sub_mappings``).

These functions are consumed by ``DotMacSubSyncService`` so the translation
policy is independently testable without a DB session. The service's
``_map_*`` instance methods delegate here, so this also guards that contract.
"""

from __future__ import annotations

import pytest

from app.models.finance.core_org.project import ProjectType
from app.models.pm.task import TaskPriority
from app.models.support.ticket import TicketPriority
from app.services.sync import sub_mappings


class TestProjectType:
    def test_known_values(self) -> None:
        assert sub_mappings.map_project_type("internal") == ProjectType.INTERNAL
        assert sub_mappings.map_project_type("client") == ProjectType.CLIENT
        assert (
            sub_mappings.map_project_type("fiber")
            == ProjectType.FIBER_OPTICS_INSTALLATION
        )

    def test_case_insensitive(self) -> None:
        assert sub_mappings.map_project_type("INTERNAL") == ProjectType.INTERNAL

    def test_unknown_and_none_default_to_client(self) -> None:
        assert sub_mappings.map_project_type("nope") == ProjectType.CLIENT
        assert sub_mappings.map_project_type(None) == ProjectType.CLIENT


class TestPriorities:
    def test_ticket_priority(self) -> None:
        assert sub_mappings.map_ticket_priority("low") == TicketPriority.LOW
        assert sub_mappings.map_ticket_priority("critical") == TicketPriority.URGENT
        assert sub_mappings.map_ticket_priority(None) == TicketPriority.MEDIUM
        assert sub_mappings.map_ticket_priority("unknown") == TicketPriority.MEDIUM

    def test_task_priority(self) -> None:
        assert sub_mappings.map_task_priority("urgent") == TaskPriority.URGENT
        assert sub_mappings.map_task_priority(None) == TaskPriority.MEDIUM
        assert sub_mappings.map_task_priority("unknown") == TaskPriority.MEDIUM


class TestMaterialRequestMappers:
    def test_type_valid(self) -> None:
        from app.models.inventory.material_request import MaterialRequestType

        assert (
            sub_mappings.map_sub_material_request_type("purchase")
            == MaterialRequestType.PURCHASE
        )

    def test_type_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid request_type"):
            sub_mappings.map_sub_material_request_type("bogus")

    def test_status_valid(self) -> None:
        from app.models.inventory.material_request import MaterialRequestStatus

        assert (
            sub_mappings.map_sub_material_request_status("DRAFT")
            == MaterialRequestStatus.DRAFT
        )

    def test_status_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid status"):
            sub_mappings.map_sub_material_request_status("bogus")
