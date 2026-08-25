"""Unit tests for the pure Sub-to-ERP translation policy."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.models.finance.core_org.project import ProjectType
from app.models.pm.task import TaskPriority
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


class TestTaskPriority:
    def test_known_and_default_values(self) -> None:
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


class TestVariationConflict:
    def test_matches_named_constraint(self) -> None:
        err = MagicMock()
        err.orig.diag.constraint_name = "uq_po_variation_id"
        assert sub_mappings.is_variation_id_conflict(err) is True

    def test_matches_text_fallback(self) -> None:
        err = MagicMock()
        err.orig.diag.constraint_name = None
        err.orig.pgcode = "23505"
        err.orig.__str__ = lambda self: (
            'duplicate key value violates "uq_po_variation_id"'
        )  # type: ignore[assignment]
        assert sub_mappings.is_variation_id_conflict(err) is True

    def test_unrelated_conflict_is_false(self) -> None:
        err = MagicMock()
        err.orig.diag.constraint_name = "uq_something_else"
        err.orig.pgcode = "23505"
        err.orig.__str__ = lambda self: (  # type: ignore[assignment]
            "duplicate key on uq_something_else"
        )
        assert sub_mappings.is_variation_id_conflict(err) is False
