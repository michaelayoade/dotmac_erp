"""
Tests for app/services/bulk_actions.py

Tests for the BulkActionService base class that provides generic bulk operations
for delete, export, and status updates.
"""

import uuid
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.services.bulk_actions import BulkActionService
from app.schemas.bulk_actions import BulkActionResult


# ============ Concrete Test Implementation ============

class TestModel(Base):
    """Real SQLAlchemy model so that and_() receives proper column objects."""

    __tablename__ = "test_bulk_model"
    __table_args__ = {"extend_existing": True}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(default=True)
    name: Mapped[str] = mapped_column(String(255), nullable=True)


class ConcreteBulkService(BulkActionService[TestModel]):
    """Concrete implementation of BulkActionService for testing."""

    model = TestModel
    export_fields = [
        ("id", "ID"),
        ("name", "Name"),
        ("is_active", "Active"),
        ("related_entity.name", "Related Name"),
        ("related_entity.email", "Related Email"),
    ]

    def __init__(self, db, organization_id, user_id=None, can_delete_result=None):
        super().__init__(db, organization_id, user_id)
        self._can_delete_result = can_delete_result or (True, "")

    def can_delete(self, entity):
        """Return configurable can_delete result for testing."""
        if callable(self._can_delete_result):
            return self._can_delete_result(entity)
        return self._can_delete_result


# ============ TestBulkActionServiceInit ============

class TestBulkActionServiceInit:
    """Tests for BulkActionService initialization."""

    def test_init_coerces_org_id(self, mock_db):
        """Organization ID should be coerced to UUID."""
        org_id_str = "12345678-1234-5678-1234-567812345678"
        service = ConcreteBulkService(mock_db, org_id_str)

        assert isinstance(service.organization_id, uuid.UUID)
        assert str(service.organization_id) == org_id_str

    def test_init_coerces_user_id(self, mock_db, organization_id):
        """User ID should be coerced to UUID when provided."""
        user_id_str = "87654321-4321-8765-4321-876543218765"
        service = ConcreteBulkService(mock_db, organization_id, user_id_str)

        assert isinstance(service.user_id, uuid.UUID)
        assert str(service.user_id) == user_id_str

    def test_init_stores_db_session(self, mock_db, organization_id):
        """Database session should be stored on the service."""
        service = ConcreteBulkService(mock_db, organization_id)
        assert service.db is mock_db

    def test_init_user_id_optional(self, mock_db, organization_id):
        """User ID should be None if not provided."""
        service = ConcreteBulkService(mock_db, organization_id)
        assert service.user_id is None


# ============ TestGetBaseQuery ============

class TestGetBaseQuery:
    """Tests for the _get_base_query method."""

    def test_get_base_query_filters_by_org(self, mock_db, organization_id):
        """Query should filter by organization_id."""
        service = ConcreteBulkService(mock_db, organization_id)
        ids = [uuid.uuid4()]

        service._get_base_query(ids)

        mock_db.query.assert_called_once()

    def test_get_base_query_filters_by_ids(self, mock_db, organization_id):
        """Query should filter by the provided IDs."""
        service = ConcreteBulkService(mock_db, organization_id)
        ids = [uuid.uuid4(), uuid.uuid4()]

        service._get_base_query(ids)

        # Verify query was built
        mock_db.query.assert_called()

    def test_get_base_query_coerces_ids(self, mock_db, organization_id):
        """String IDs should be coerced to UUIDs."""
        service = ConcreteBulkService(mock_db, organization_id)
        id_str = "12345678-1234-5678-1234-567812345678"
        ids = [id_str]

        # Should not raise
        service._get_base_query(ids)


# ============ TestBulkDelete ============

class TestBulkDelete:
    """Tests for the bulk_delete method."""

    @pytest.mark.asyncio
    async def test_bulk_delete_empty_ids_failure(self, mock_db, organization_id):
        """Empty ID list should return failure result."""
        service = ConcreteBulkService(mock_db, organization_id)

        result = await service.bulk_delete([])

        assert result.success_count == 0
        assert result.failed_count == 0
        assert "No IDs provided" in result.errors[0]

    @pytest.mark.asyncio
    async def test_bulk_delete_no_entities_failure(self, mock_db, organization_id):
        """Should return failure when no entities found."""
        service = ConcreteBulkService(mock_db, organization_id)
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = await service.bulk_delete([uuid.uuid4()])

        assert result.success_count == 0
        assert "No entities found" in result.errors[0]

    @pytest.mark.asyncio
    async def test_bulk_delete_all_success(self, mock_db, organization_id):
        """All entities should be deleted successfully."""
        entity1 = MagicMock()
        entity2 = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [entity1, entity2]

        service = ConcreteBulkService(mock_db, organization_id, can_delete_result=(True, ""))

        result = await service.bulk_delete([uuid.uuid4(), uuid.uuid4()])

        assert result.success_count == 2
        assert result.failed_count == 0
        assert mock_db.delete.call_count == 2
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_delete_partial_success(self, mock_db, organization_id):
        """Should handle partial deletion success."""
        entity1 = MagicMock()
        entity1.name = "Entity1"
        entity2 = MagicMock()
        entity2.name = "Entity2"
        mock_db.query.return_value.filter.return_value.all.return_value = [entity1, entity2]

        def can_delete_check(entity):
            if entity.name == "Entity1":
                return (True, "")
            return (False, "Cannot delete Entity2")

        service = ConcreteBulkService(
            mock_db, organization_id, can_delete_result=can_delete_check
        )

        result = await service.bulk_delete([uuid.uuid4(), uuid.uuid4()])

        assert result.success_count == 1
        assert result.failed_count == 1
        assert "Cannot delete Entity2" in result.errors

    @pytest.mark.asyncio
    async def test_bulk_delete_all_blocked(self, mock_db, organization_id):
        """Should handle all entities being blocked from deletion."""
        entity1 = MagicMock()
        entity2 = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [entity1, entity2]

        service = ConcreteBulkService(
            mock_db, organization_id, can_delete_result=(False, "Cannot delete")
        )

        result = await service.bulk_delete([uuid.uuid4(), uuid.uuid4()])

        assert result.success_count == 0
        assert result.failed_count == 2
        mock_db.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_bulk_delete_commits_on_success(self, mock_db, organization_id):
        """Should commit when at least one deletion succeeds."""
        entity = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [entity]

        service = ConcreteBulkService(mock_db, organization_id, can_delete_result=(True, ""))

        await service.bulk_delete([uuid.uuid4()])

        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_delete_no_commit_all_blocked(self, mock_db, organization_id):
        """Should not commit when all deletions are blocked."""
        entity = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [entity]

        service = ConcreteBulkService(
            mock_db, organization_id, can_delete_result=(False, "Blocked")
        )

        await service.bulk_delete([uuid.uuid4()])

        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_bulk_delete_exception_captured(self, mock_db, organization_id):
        """Exceptions during delete should be captured in errors."""
        entity = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [entity]
        mock_db.delete.side_effect = Exception("Database error")

        service = ConcreteBulkService(mock_db, organization_id, can_delete_result=(True, ""))

        result = await service.bulk_delete([uuid.uuid4()])

        assert result.failed_count == 1
        assert "Database error" in result.errors[0]


# ============ TestBulkUpdateStatus ============

class TestBulkUpdateStatus:
    """Tests for the bulk_update_status method."""

    @pytest.mark.asyncio
    async def test_bulk_update_status_success(self, mock_db, organization_id):
        """Should update status on all entities."""
        entity1 = MagicMock()
        entity1.status = "pending"
        entity2 = MagicMock()
        entity2.status = "pending"
        mock_db.query.return_value.filter.return_value.all.return_value = [entity1, entity2]

        service = ConcreteBulkService(mock_db, organization_id)

        result = await service.bulk_update_status(
            [uuid.uuid4(), uuid.uuid4()], "status", "approved"
        )

        assert result.success_count == 2
        assert result.failed_count == 0
        assert entity1.status == "approved"
        assert entity2.status == "approved"
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_update_status_missing_field(self, mock_db, organization_id):
        """Should fail when entity doesn't have the status field."""
        entity = MagicMock(spec=["id"])  # No status field
        mock_db.query.return_value.filter.return_value.all.return_value = [entity]

        service = ConcreteBulkService(mock_db, organization_id)

        result = await service.bulk_update_status([uuid.uuid4()], "status", "approved")

        assert result.failed_count == 1
        assert "no field" in result.errors[0]

    @pytest.mark.asyncio
    async def test_bulk_update_status_empty_ids(self, mock_db, organization_id):
        """Empty ID list should return failure result."""
        service = ConcreteBulkService(mock_db, organization_id)

        result = await service.bulk_update_status([], "status", "approved")

        assert result.success_count == 0
        assert "No IDs provided" in result.errors[0]

    @pytest.mark.asyncio
    async def test_bulk_update_status_partial_success(self, mock_db, organization_id):
        """Should handle partial update success."""
        entity1 = MagicMock()
        entity1.is_active = True
        entity2 = MagicMock(spec=["id"])  # No is_active field
        mock_db.query.return_value.filter.return_value.all.return_value = [entity1, entity2]

        service = ConcreteBulkService(mock_db, organization_id)

        result = await service.bulk_update_status(
            [uuid.uuid4(), uuid.uuid4()], "is_active", False
        )

        assert result.success_count == 1
        assert result.failed_count == 1


# ============ TestBulkActivateDeactivate ============

class TestBulkActivateDeactivate:
    """Tests for bulk_activate and bulk_deactivate methods."""

    @pytest.mark.asyncio
    async def test_bulk_activate_calls_update_status(self, mock_db, organization_id):
        """bulk_activate should call bulk_update_status with is_active=True."""
        entity = MagicMock()
        entity.is_active = False
        mock_db.query.return_value.filter.return_value.all.return_value = [entity]

        service = ConcreteBulkService(mock_db, organization_id)
        ids = [uuid.uuid4()]

        result = await service.bulk_activate(ids)

        assert entity.is_active is True

    @pytest.mark.asyncio
    async def test_bulk_deactivate_calls_update_status(self, mock_db, organization_id):
        """bulk_deactivate should call bulk_update_status with is_active=False."""
        entity = MagicMock()
        entity.is_active = True
        mock_db.query.return_value.filter.return_value.all.return_value = [entity]

        service = ConcreteBulkService(mock_db, organization_id)
        ids = [uuid.uuid4()]

        result = await service.bulk_deactivate(ids)

        assert entity.is_active is False


# ============ TestBulkExport ============

class TestBulkExport:
    """Tests for the bulk_export method."""

    @pytest.mark.asyncio
    async def test_bulk_export_empty_ids_raises_400(self, mock_db, organization_id):
        """Empty ID list should raise HTTPException with 400."""
        service = ConcreteBulkService(mock_db, organization_id)

        with pytest.raises(HTTPException) as exc_info:
            await service.bulk_export([])

        assert exc_info.value.status_code == 400
        assert "No IDs provided" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_bulk_export_no_entities_raises_404(self, mock_db, organization_id):
        """Should raise 404 when no entities found."""
        mock_db.query.return_value.filter.return_value.all.return_value = []
        service = ConcreteBulkService(mock_db, organization_id)

        with pytest.raises(HTTPException) as exc_info:
            await service.bulk_export([uuid.uuid4()])

        assert exc_info.value.status_code == 404
        assert "No entities found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_bulk_export_csv_headers(self, mock_db, organization_id):
        """CSV export should include correct headers."""
        entity = MagicMock()
        entity.id = uuid.uuid4()
        entity.name = "Test"
        entity.is_active = True
        entity.related_entity = None
        mock_db.query.return_value.filter.return_value.all.return_value = [entity]

        service = ConcreteBulkService(mock_db, organization_id)

        response = await service.bulk_export([uuid.uuid4()])

        # Get the CSV content from the async body_iterator
        content = ""
        async for chunk in response.body_iterator:
            content = chunk if isinstance(chunk, str) else chunk.decode()

        lines = content.strip().split("\n")
        headers = lines[0]

        assert "ID" in headers
        assert "Name" in headers
        assert "Active" in headers
        assert "Related Name" in headers

    @pytest.mark.asyncio
    async def test_bulk_export_csv_data(self, mock_db, organization_id):
        """CSV export should include entity data."""
        entity = MagicMock()
        entity.id = uuid.uuid4()
        entity.name = "Test Entity"
        entity.is_active = True
        entity.related_entity = None
        mock_db.query.return_value.filter.return_value.all.return_value = [entity]

        service = ConcreteBulkService(mock_db, organization_id)

        response = await service.bulk_export([uuid.uuid4()])

        content = ""
        async for chunk in response.body_iterator:
            content = chunk if isinstance(chunk, str) else chunk.decode()

        assert "Test Entity" in content
        assert "True" in content

    @pytest.mark.asyncio
    async def test_bulk_export_streaming_response(self, mock_db, organization_id):
        """Export should return a StreamingResponse."""
        from fastapi.responses import StreamingResponse

        entity = MagicMock()
        entity.id = uuid.uuid4()
        entity.name = "Test"
        entity.is_active = True
        entity.related_entity = None
        mock_db.query.return_value.filter.return_value.all.return_value = [entity]

        service = ConcreteBulkService(mock_db, organization_id)

        response = await service.bulk_export([uuid.uuid4()])

        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/csv"

    @pytest.mark.asyncio
    async def test_bulk_export_content_disposition(self, mock_db, organization_id):
        """Export should have Content-Disposition header with filename."""
        entity = MagicMock()
        entity.id = uuid.uuid4()
        entity.name = "Test"
        entity.is_active = True
        entity.related_entity = None
        mock_db.query.return_value.filter.return_value.all.return_value = [entity]

        service = ConcreteBulkService(mock_db, organization_id)

        response = await service.bulk_export([uuid.uuid4()])

        assert "Content-Disposition" in response.headers
        assert "attachment" in response.headers["Content-Disposition"]
        assert ".csv" in response.headers["Content-Disposition"]


# ============ TestGetExportValue ============

class TestGetExportValue:
    """Tests for the _get_export_value method."""

    def test_get_export_value_simple_field(self, mock_db, organization_id):
        """Should get simple field value."""
        entity = MagicMock()
        entity.name = "Test Entity"

        service = ConcreteBulkService(mock_db, organization_id)
        value = service._get_export_value(entity, "name")

        assert value == "Test Entity"

    def test_get_export_value_nested_field(self, mock_db, organization_id):
        """Should get nested field value using dot notation."""
        related = MagicMock()
        related.email = "test@example.com"
        entity = MagicMock()
        entity.related_entity = related

        service = ConcreteBulkService(mock_db, organization_id)
        value = service._get_export_value(entity, "related_entity.email")

        assert value == "test@example.com"

    def test_get_export_value_none_returns_empty(self, mock_db, organization_id):
        """None values should return empty string."""
        entity = MagicMock()
        entity.name = None

        service = ConcreteBulkService(mock_db, organization_id)
        value = service._get_export_value(entity, "name")

        assert value == ""

    def test_get_export_value_list_json_dumps(self, mock_db, organization_id):
        """List values should be JSON serialized."""
        entity = MagicMock()
        entity.tags = ["tag1", "tag2", "tag3"]

        service = ConcreteBulkService(mock_db, organization_id)
        value = service._get_export_value(entity, "tags")

        assert value == '["tag1", "tag2", "tag3"]'

    def test_get_export_value_dict_json_dumps(self, mock_db, organization_id):
        """Dict values should be JSON serialized."""
        entity = MagicMock()
        entity.metadata = {"key": "value", "count": 42}

        service = ConcreteBulkService(mock_db, organization_id)
        value = service._get_export_value(entity, "metadata")

        assert '"key": "value"' in value
        assert '"count": 42' in value

    def test_get_export_value_deep_nested(self, mock_db, organization_id):
        """Should handle deeply nested attributes."""
        level3 = MagicMock()
        level3.value = "deep_value"
        level2 = MagicMock()
        level2.level3 = level3
        level1 = MagicMock()
        level1.level2 = level2
        entity = MagicMock()
        entity.level1 = level1

        service = ConcreteBulkService(mock_db, organization_id)
        value = service._get_export_value(entity, "level1.level2.level3.value")

        assert value == "deep_value"

    def test_get_export_value_missing_intermediate_returns_empty(
        self, mock_db, organization_id
    ):
        """Missing intermediate attribute should return empty string."""
        entity = MagicMock()
        entity.related_entity = None

        service = ConcreteBulkService(mock_db, organization_id)
        value = service._get_export_value(entity, "related_entity.name")

        assert value == ""

    def test_get_export_value_boolean(self, mock_db, organization_id):
        """Boolean values should be converted to string."""
        entity = MagicMock()
        entity.is_active = True

        service = ConcreteBulkService(mock_db, organization_id)
        value = service._get_export_value(entity, "is_active")

        assert value == "True"

    def test_get_export_value_uuid(self, mock_db, organization_id):
        """UUID values should be converted to string."""
        entity = MagicMock()
        entity.id = uuid.UUID("12345678-1234-5678-1234-567812345678")

        service = ConcreteBulkService(mock_db, organization_id)
        value = service._get_export_value(entity, "id")

        assert value == "12345678-1234-5678-1234-567812345678"

    def test_get_export_value_number(self, mock_db, organization_id):
        """Numeric values should be converted to string."""
        entity = MagicMock()
        entity.amount = 1234.56

        service = ConcreteBulkService(mock_db, organization_id)
        value = service._get_export_value(entity, "amount")

        assert value == "1234.56"


# ============ TestGetExportFilename ============

class TestGetExportFilename:
    """Tests for the _get_export_filename method."""

    def test_get_export_filename_format(self, mock_db, organization_id):
        """Filename should follow the expected format."""
        service = ConcreteBulkService(mock_db, organization_id)

        filename = service._get_export_filename()

        assert filename.startswith("export_")
        assert filename.endswith(".csv")

    def test_get_export_filename_includes_timestamp(self, mock_db, organization_id):
        """Filename should include a timestamp."""
        service = ConcreteBulkService(mock_db, organization_id)

        filename = service._get_export_filename()

        # Should have format like export_20240115_143022.csv
        parts = filename.replace(".csv", "").split("_")
        assert len(parts) == 3  # export, date, time
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 6  # HHMMSS
