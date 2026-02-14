"""
Tests for DotMac CRM Sync Service.

Tests the business logic for syncing CRM entities to the ERP system.
Uses mocking for database operations since sync models use PostgreSQL-specific features.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.sync.dotmac_crm import (
    CRMMaterialRequestItemPayload,
    CRMMaterialRequestPayload,
    CRMProjectPayload,
    CRMTicketPayload,
    CRMWorkOrderPayload,
    ExpenseTotals,
)
from app.services.sync.dotmac_crm_sync_service import (
    CRM_SYNC_STATUS_MAP,
    PROJECT_STATUS_MAP,
    TASK_STATUS_MAP,
    TICKET_STATUS_MAP,
    DotMacCRMSyncService,
)


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def service(mock_db):
    """Create a service instance with mocked db."""
    return DotMacCRMSyncService(mock_db)


@pytest.fixture
def org_id():
    """Sample organization ID."""
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


class TestStatusMappings:
    """Test the CRM status mappings are correctly defined."""

    def test_project_status_map_keys(self):
        """Project status map should have common CRM statuses."""
        expected_keys = {
            "planned",
            "active",
            "on_hold",
            "completed",
            "cancelled",
            "canceled",
        }
        assert expected_keys.issubset(set(PROJECT_STATUS_MAP.keys()))

    def test_ticket_status_map_keys(self):
        """Ticket status map should have common CRM statuses."""
        expected_keys = {"open", "active", "in_progress", "resolved", "closed"}
        assert expected_keys.issubset(set(TICKET_STATUS_MAP.keys()))

    def test_task_status_map_keys(self):
        """Task status map should have common CRM statuses."""
        expected_keys = {"draft", "scheduled", "active", "in_progress", "completed"}
        assert expected_keys.issubset(set(TASK_STATUS_MAP.keys()))

    def test_crm_sync_status_map_keys(self):
        """CRM sync status map should handle all common statuses."""
        expected_keys = {"active", "completed", "cancelled", "archived"}
        assert expected_keys.issubset(set(CRM_SYNC_STATUS_MAP.keys()))


class TestGenerateUniqueCode:
    """Test the unique code generation."""

    def test_generate_code_format(self, service):
        """Generated code should have prefix-hash format."""
        code = service._generate_unique_code("CRM", "test-uuid-123")
        assert code.startswith("CRM-")
        assert len(code) <= 20

    def test_generate_code_deterministic(self, service):
        """Same input should produce same code."""
        crm_id = "550e8400-e29b-41d4-a716-446655440000"
        code1 = service._generate_unique_code("CRM", crm_id)
        code2 = service._generate_unique_code("CRM", crm_id)
        assert code1 == code2

    def test_generate_code_different_inputs(self, service):
        """Different inputs should produce different codes."""
        code1 = service._generate_unique_code("CRM", "uuid-1")
        code2 = service._generate_unique_code("CRM", "uuid-2")
        assert code1 != code2

    def test_generate_code_max_length(self, service):
        """Code should respect max_len parameter."""
        code = service._generate_unique_code("PREFIX", "long-uuid-value", max_len=15)
        assert len(code) <= 15

    def test_generate_code_uppercase(self, service):
        """Hash portion should be uppercase."""
        code = service._generate_unique_code("CRM", "test")
        hash_part = code.split("-")[1]
        assert hash_part == hash_part.upper()


class TestSyncProject:
    """Test project sync operations."""

    def test_sync_new_project(self, service, org_id, mock_db):
        """Syncing a new project should create both Project and CRMSyncMapping."""
        # Arrange
        crm_id = str(uuid.uuid4())
        payload = CRMProjectPayload(
            crm_id=crm_id,
            name="Test Project",
            code="PROJ-001",
            status="active",
            customer_name="Test Customer",
        )

        # Mock _get_mapping to return None (new project)
        mock_db.scalar.return_value = None

        # Act
        with patch.object(service, "_get_mapping", return_value=None):
            with patch.object(service, "_create_project") as mock_create:
                mock_project = MagicMock()
                mock_project.project_id = uuid.uuid4()
                mock_create.return_value = mock_project

                service.sync_project(org_id, payload)

        # Assert
        mock_create.assert_called_once()
        mock_db.add.assert_called()  # CRMSyncMapping added

    def test_sync_existing_project(self, service, org_id, mock_db):
        """Syncing an existing project should update both records."""
        # Arrange
        crm_id = str(uuid.uuid4())
        existing_mapping = MagicMock()
        existing_mapping.local_entity_id = uuid.uuid4()

        existing_project = MagicMock()
        mock_db.get.return_value = existing_project

        payload = CRMProjectPayload(
            crm_id=crm_id,
            name="Updated Project Name",
            code="PROJ-001",
            status="completed",
        )

        # Act
        with patch.object(service, "_get_mapping", return_value=existing_mapping):
            with patch.object(service, "_update_project") as mock_update:
                with patch.object(service, "_update_mapping") as mock_update_mapping:
                    mapping = service.sync_project(org_id, payload)

        # Assert
        mock_update.assert_called_once_with(existing_project, payload)
        mock_update_mapping.assert_called_once()
        assert mapping == existing_mapping

    def test_sync_project_recreates_missing_local(self, service, org_id, mock_db):
        """Missing local project should be recreated and mapping updated."""
        crm_id = str(uuid.uuid4())
        existing_mapping = MagicMock()
        existing_mapping.local_entity_id = uuid.uuid4()
        mock_db.get.return_value = None

        payload = CRMProjectPayload(
            crm_id=crm_id,
            name="Recovered Project",
            code="PROJ-RECOVER",
            status="active",
        )

        with patch.object(service, "_get_mapping", return_value=existing_mapping):
            with patch.object(service, "_create_project") as mock_create:
                mock_project = MagicMock()
                mock_project.project_id = uuid.uuid4()
                mock_create.return_value = mock_project

                mapping = service.sync_project(org_id, payload)

        assert mapping.local_entity_id == mock_project.project_id
        mock_create.assert_called_once()


class TestSyncTicket:
    """Test ticket sync operations."""

    def test_sync_new_ticket(self, service, org_id, mock_db):
        """Syncing a new ticket should create both Ticket and CRMSyncMapping."""
        crm_id = str(uuid.uuid4())
        payload = CRMTicketPayload(
            crm_id=crm_id,
            subject="Test Ticket",
            ticket_number="TKT-001",
            status="open",
            customer_name="Test Customer",
        )

        with patch.object(service, "_get_mapping", return_value=None):
            with patch.object(service, "_create_ticket") as mock_create:
                mock_ticket = MagicMock()
                mock_ticket.ticket_id = uuid.uuid4()
                mock_create.return_value = mock_ticket

                service.sync_ticket(org_id, payload)

        mock_create.assert_called_once()
        mock_db.add.assert_called()

    def test_sync_ticket_recreates_missing_local(self, service, org_id, mock_db):
        """Missing local ticket should be recreated and mapping updated."""
        crm_id = str(uuid.uuid4())
        existing_mapping = MagicMock()
        existing_mapping.local_entity_id = uuid.uuid4()
        mock_db.get.return_value = None

        payload = CRMTicketPayload(
            crm_id=crm_id,
            subject="Recovered Ticket",
            ticket_number="TKT-RECOVER",
            status="open",
        )

        with patch.object(service, "_get_mapping", return_value=existing_mapping):
            with patch.object(service, "_create_ticket") as mock_create:
                mock_ticket = MagicMock()
                mock_ticket.ticket_id = uuid.uuid4()
                mock_create.return_value = mock_ticket

                mapping = service.sync_ticket(org_id, payload)

        assert mapping.local_entity_id == mock_ticket.ticket_id
        mock_create.assert_called_once()

    def test_sync_ticket_status_mapping(self, service, org_id, mock_db):
        """Ticket status should be mapped correctly from CRM status."""
        from app.models.support.ticket import TicketStatus

        # Test different status mappings
        assert TICKET_STATUS_MAP.get("open") == TicketStatus.OPEN
        assert TICKET_STATUS_MAP.get("resolved") == TicketStatus.RESOLVED
        assert TICKET_STATUS_MAP.get("closed") == TicketStatus.CLOSED

    def test_create_ticket_maps_description(self, service, org_id):
        """Ticket description should be mapped from CRM payload."""
        payload = CRMTicketPayload(
            crm_id=str(uuid.uuid4()),
            subject="Ticket with description",
            status="open",
            description="Customer reported intermittent outage",
        )

        ticket = service._create_ticket(org_id, payload)

        assert ticket.description == "Customer reported intermittent outage"

    def test_update_ticket_maps_description(self, service):
        """Existing ticket description should update from CRM payload."""
        ticket = MagicMock()
        payload = CRMTicketPayload(
            crm_id=str(uuid.uuid4()),
            subject="Updated subject",
            status="open",
            description="Updated from CRM",
        )

        service._update_ticket(ticket, payload)

        assert ticket.description == "Updated from CRM"

    def test_update_ticket_keeps_description_when_missing(self, service):
        """Description should not be cleared when CRM omits/nulls description."""
        ticket = MagicMock()
        ticket.description = "Keep me"
        payload = CRMTicketPayload(
            crm_id=str(uuid.uuid4()),
            subject="Updated subject",
            status="open",
            description=None,
        )

        service._update_ticket(ticket, payload)

        assert ticket.description == "Keep me"

    def test_build_ticket_crm_data_includes_new_fields(self, service):
        """crm_data should include comments/activity_log with metadata."""
        payload = CRMTicketPayload(
            crm_id=str(uuid.uuid4()),
            subject="Test",
            status="open",
            description="Desc",
            metadata={"source": "crm"},
            comments=[{"id": "c-1", "body": "First comment"}],
            activity_log=[{"id": "a-1", "action": "status_changed"}],
        )

        crm_data = service._build_ticket_crm_data(payload)

        assert crm_data is not None
        assert crm_data["source"] == "crm"
        assert crm_data["description"] == "Desc"
        assert crm_data["comments"][0]["id"] == "c-1"
        assert crm_data["activity_log"][0]["id"] == "a-1"

    def test_sync_ticket_comments_invalid_item_continues(self, service, org_id):
        """Malformed comment items should be captured as item-level errors."""
        ticket = MagicMock()
        ticket.ticket_id = uuid.uuid4()

        with patch.object(
            service, "_upsert_crm_comment_item", return_value=(MagicMock(), 0)
        ) as mock_upsert:
            processed, dedupe_hits, errors = service._sync_ticket_comments(
                org_id,
                ticket,
                [
                    {"body": "missing-id"},
                    {"id": "c-1", "body": "ok", "is_internal": False},
                ],
            )

        assert processed == 1
        assert dedupe_hits == 0
        assert len(errors) == 1
        mock_upsert.assert_called_once()

    def test_sync_ticket_activity_skips_duplicate_comment_entries(
        self, service, org_id
    ):
        """Activity comment entries duplicated in comments[] should be skipped."""
        ticket = MagicMock()
        ticket.ticket_id = uuid.uuid4()
        ticket.ticket_number = "TKT-001"

        with patch.object(service, "_upsert_crm_activity_item") as mock_upsert:
            processed, dedupe_hits, errors = service._sync_ticket_activity(
                org_id,
                ticket,
                raw_activity=[{"kind": "comment", "id": "c-1", "body": "same"}],
                raw_comments=[{"id": "c-1", "body": "same"}],
            )

        assert processed == 0
        assert dedupe_hits == 1
        assert errors == []
        mock_upsert.assert_not_called()

    def test_upsert_crm_comment_item_dedupe_hit(self, service, org_id, mock_db):
        """Existing sync mapping should update existing comment instead of inserting duplicate."""
        from app.models.sync.sync_entity import SyncStatus
        from app.schemas.sync.dotmac_crm import CRMTicketCommentItem

        ticket = MagicMock()
        ticket.ticket_id = uuid.uuid4()
        ticket.ticket_number = "TKT-001"

        existing_comment = MagicMock()
        existing_comment.comment_id = uuid.uuid4()

        existing_sync = MagicMock()
        existing_sync.target_id = existing_comment.comment_id
        existing_sync.sync_status = SyncStatus.SYNCED

        mock_db.scalar.return_value = existing_sync
        mock_db.get.return_value = existing_comment

        item = CRMTicketCommentItem(
            id="comment-1",
            body="Updated body",
            is_internal=True,
        )

        _, dedupe = service._upsert_crm_comment_item(org_id, ticket, item)

        assert dedupe == 1
        mock_db.add.assert_not_called()

    def test_upsert_crm_activity_item_dedupe_hit(self, service, org_id, mock_db):
        """Existing activity sync mapping should dedupe on replay."""
        from app.models.sync.sync_entity import SyncStatus
        from app.schemas.sync.dotmac_crm import CRMTicketActivityEntry

        ticket = MagicMock()
        ticket.ticket_id = uuid.uuid4()
        ticket.ticket_number = "TKT-001"

        existing_comment = MagicMock()
        existing_comment.comment_id = uuid.uuid4()

        existing_sync = MagicMock()
        existing_sync.target_id = existing_comment.comment_id
        existing_sync.sync_status = SyncStatus.SYNCED

        mock_db.scalar.return_value = existing_sync
        mock_db.get.return_value = existing_comment

        entry = CRMTicketActivityEntry(
            kind="event",
            id="event-1",
            event_type="status_changed",
            status="resolved",
            details={"from": "open", "to": "resolved"},
        )

        _, dedupe = service._upsert_crm_activity_item(org_id, ticket, entry)

        assert dedupe == 1
        mock_db.add.assert_not_called()

    def test_resolve_crm_person_id_accepts_employee_id(self, service, org_id, mock_db):
        """author_person_id fallback should resolve employee_id -> person_id."""
        person_id = uuid.uuid4()

        mock_employee = MagicMock()
        mock_employee.organization_id = org_id
        mock_employee.person_id = person_id

        # First db.get(Person, external_id) -> None; then db.get(Employee, external_id) -> employee
        mock_db.get.side_effect = [None, mock_employee]

        resolved = service._resolve_crm_person_id(org_id, str(uuid.uuid4()))

        assert resolved == person_id


class TestSyncWorkOrder:
    """Test work order (task) sync operations."""

    def test_sync_new_work_order_with_project(self, service, org_id, mock_db):
        """Work order with project reference should link to that project."""
        crm_id = str(uuid.uuid4())
        project_crm_id = str(uuid.uuid4())
        project_local_id = uuid.uuid4()

        payload = CRMWorkOrderPayload(
            crm_id=crm_id,
            title="Test Work Order",
            status="active",
            project_crm_id=project_crm_id,
        )

        with patch.object(service, "_get_mapping", return_value=None):
            with patch.object(
                service, "_resolve_project_id", return_value=project_local_id
            ):
                with patch.object(service, "_resolve_ticket_id", return_value=None):
                    with patch.object(
                        service, "_resolve_employee_id", return_value=None
                    ):
                        with patch.object(service, "_create_task") as mock_create:
                            mock_task = MagicMock()
                            mock_task.task_id = uuid.uuid4()
                            mock_create.return_value = mock_task

                            service.sync_work_order(org_id, payload)

        mock_create.assert_called_once()
        # Verify project_id was passed
        call_args = mock_create.call_args
        assert call_args[0][2] == project_local_id  # project_id argument

    def test_sync_work_order_without_project_creates_default(
        self, service, org_id, mock_db
    ):
        """Work order without project should use default project."""
        crm_id = str(uuid.uuid4())
        default_project_id = uuid.uuid4()

        payload = CRMWorkOrderPayload(
            crm_id=crm_id,
            title="Orphan Work Order",
            status="active",
        )

        with patch.object(service, "_get_mapping", return_value=None):
            with patch.object(service, "_resolve_project_id", return_value=None):
                with patch.object(service, "_resolve_ticket_id", return_value=None):
                    with patch.object(
                        service, "_resolve_employee_id", return_value=None
                    ):
                        with patch.object(
                            service,
                            "_get_or_create_default_project",
                            return_value=default_project_id,
                        ):
                            with patch.object(service, "_create_task") as mock_create:
                                mock_task = MagicMock()
                                mock_task.task_id = uuid.uuid4()
                                mock_create.return_value = mock_task

                                service.sync_work_order(org_id, payload)

        # Verify default project was used
        call_args = mock_create.call_args
        assert call_args[0][2] == default_project_id

    def test_sync_work_order_recreates_missing_local(self, service, org_id, mock_db):
        """Missing local work order should be recreated and mapping updated."""
        crm_id = str(uuid.uuid4())
        default_project_id = uuid.uuid4()
        existing_mapping = MagicMock()
        existing_mapping.local_entity_id = uuid.uuid4()
        mock_db.get.return_value = None

        payload = CRMWorkOrderPayload(
            crm_id=crm_id,
            title="Recovered Work Order",
            status="active",
        )

        with patch.object(service, "_get_mapping", return_value=existing_mapping):
            with patch.object(service, "_resolve_project_id", return_value=None):
                with patch.object(service, "_resolve_ticket_id", return_value=None):
                    with patch.object(
                        service, "_resolve_employee_id", return_value=None
                    ):
                        with patch.object(
                            service,
                            "_get_or_create_default_project",
                            return_value=default_project_id,
                        ):
                            with patch.object(service, "_create_task") as mock_create:
                                mock_task = MagicMock()
                                mock_task.task_id = uuid.uuid4()
                                mock_create.return_value = mock_task

                                mapping = service.sync_work_order(org_id, payload)

        assert mapping.local_entity_id == mock_task.task_id
        mock_create.assert_called_once()


class TestResolveEmployeeId:
    """Test employee ID resolution from email."""

    def test_resolve_employee_by_work_email(self, service, org_id, mock_db):
        """Should find employee by Person.email."""
        employee_id = uuid.uuid4()
        mock_db.scalar.return_value = employee_id

        result = service._resolve_employee_id(org_id, "john@company.com")

        assert result == employee_id
        mock_db.scalar.assert_called()

    def test_resolve_employee_none_email(self, service, org_id):
        """Should return None for None email."""
        result = service._resolve_employee_id(org_id, None)
        assert result is None

    def test_resolve_employee_empty_email(self, service, org_id):
        """Should return None for empty email."""
        result = service._resolve_employee_id(org_id, "")
        assert result is None


class TestListOperations:
    """Test list operations for UI dropdowns."""

    def test_list_projects_returns_correct_format(self, service, org_id, mock_db):
        """list_projects should return CRMProjectRead objects."""
        from app.models.sync.dotmac_crm_sync import CRMSyncStatus

        mock_mapping = MagicMock()
        mock_mapping.mapping_id = uuid.uuid4()
        mock_mapping.crm_id = "crm-123"
        mock_mapping.local_entity_id = uuid.uuid4()
        mock_mapping.display_name = "Test Project"
        mock_mapping.display_code = "PROJ-001"
        mock_mapping.crm_status = CRMSyncStatus.ACTIVE
        mock_mapping.customer_name = "Customer"

        mock_db.scalars.return_value.all.return_value = [mock_mapping]

        result = service.list_projects(org_id)

        assert len(result) == 1
        assert result[0].name == "Test Project"
        assert result[0].code == "PROJ-001"
        assert result[0].status == "ACTIVE"

    def test_list_projects_with_search(self, service, org_id, mock_db):
        """list_projects should filter by search term."""
        mock_db.scalars.return_value.all.return_value = []

        result = service.list_projects(org_id, search="test")

        assert result == []
        # Verify scalars was called (query was executed)
        mock_db.scalars.assert_called()

    def test_list_tickets_returns_correct_format(self, service, org_id, mock_db):
        """list_tickets should return CRMTicketRead objects."""
        from app.models.sync.dotmac_crm_sync import CRMSyncStatus

        mock_mapping = MagicMock()
        mock_mapping.mapping_id = uuid.uuid4()
        mock_mapping.crm_id = "crm-456"
        mock_mapping.local_entity_id = uuid.uuid4()
        mock_mapping.display_name = "Test Ticket Subject"
        mock_mapping.display_code = "TKT-001"
        mock_mapping.crm_status = CRMSyncStatus.ACTIVE
        mock_mapping.customer_name = "Customer"

        mock_db.scalars.return_value.all.return_value = [mock_mapping]

        result = service.list_tickets(org_id)

        assert len(result) == 1
        assert result[0].subject == "Test Ticket Subject"
        assert result[0].ticket_number == "TKT-001"

    def test_list_work_orders_returns_correct_format(self, service, org_id, mock_db):
        """list_work_orders should return CRMWorkOrderRead objects."""
        from app.models.sync.dotmac_crm_sync import CRMSyncStatus

        mock_mapping = MagicMock()
        mock_mapping.mapping_id = uuid.uuid4()
        mock_mapping.crm_id = "crm-789"
        mock_mapping.local_entity_id = uuid.uuid4()
        mock_mapping.display_name = "Test Work Order"
        mock_mapping.crm_status = CRMSyncStatus.ACTIVE

        mock_db.scalars.return_value.all.return_value = [mock_mapping]

        result = service.list_work_orders(org_id)

        assert len(result) == 1
        assert result[0].title == "Test Work Order"


class TestExpenseTotals:
    """Test expense totals calculation."""

    def test_get_expense_totals_for_project_not_found(self, service, org_id):
        """Should return None if mapping not found."""
        with patch.object(service, "_get_mapping", return_value=None):
            result = service.get_expense_totals_for_project(org_id, "nonexistent")

        assert result is None

    def test_get_expense_totals_for_ticket_not_found(self, service, org_id):
        """Should return None if mapping not found."""
        with patch.object(service, "_get_mapping", return_value=None):
            result = service.get_expense_totals_for_ticket(org_id, "nonexistent")

        assert result is None

    def test_get_expense_totals_for_work_order_not_found(self, service, org_id):
        """Should return None if mapping not found."""
        with patch.object(service, "_get_mapping", return_value=None):
            result = service.get_expense_totals_for_work_order(org_id, "nonexistent")

        assert result is None

    def test_calculate_expense_totals_empty(self, service, org_id, mock_db):
        """Empty results should return zeroed ExpenseTotals."""
        mock_db.execute.return_value.all.return_value = []

        result = service._calculate_expense_totals(org_id)

        assert isinstance(result, ExpenseTotals)
        assert result.draft == Decimal("0.00")
        assert result.submitted == Decimal("0.00")
        assert result.approved == Decimal("0.00")
        assert result.paid == Decimal("0.00")


class TestLookupHelpers:
    """Test lookup helper methods."""

    def test_get_local_project_id_found(self, service, org_id):
        """Should return local ID when mapping exists."""
        local_id = uuid.uuid4()
        mock_mapping = MagicMock()
        mock_mapping.local_entity_id = local_id

        with patch.object(service, "_get_mapping", return_value=mock_mapping):
            result = service.get_local_project_id(org_id, "crm-123")

        assert result == local_id

    def test_get_local_project_id_not_found(self, service, org_id):
        """Should return None when mapping doesn't exist."""
        with patch.object(service, "_get_mapping", return_value=None):
            result = service.get_local_project_id(org_id, "nonexistent")

        assert result is None

    def test_get_local_ticket_id_found(self, service, org_id):
        """Should return local ID when mapping exists."""
        local_id = uuid.uuid4()
        mock_mapping = MagicMock()
        mock_mapping.local_entity_id = local_id

        with patch.object(service, "_get_mapping", return_value=mock_mapping):
            result = service.get_local_ticket_id(org_id, "crm-456")

        assert result == local_id

    def test_get_local_task_id_found(self, service, org_id):
        """Should return local ID when mapping exists."""
        local_id = uuid.uuid4()
        mock_mapping = MagicMock()
        mock_mapping.local_entity_id = local_id

        with patch.object(service, "_get_mapping", return_value=mock_mapping):
            result = service.get_local_task_id(org_id, "crm-789")

        assert result == local_id


class TestPriorityMappings:
    """Test priority mapping helper methods."""

    def test_map_ticket_priority_low(self, service):
        """Should map 'low' to LOW priority."""
        from app.models.support.ticket import TicketPriority

        result = service._map_ticket_priority("low")
        assert result == TicketPriority.LOW

    def test_map_ticket_priority_high(self, service):
        """Should map 'high' to HIGH priority."""
        from app.models.support.ticket import TicketPriority

        result = service._map_ticket_priority("high")
        assert result == TicketPriority.HIGH

    def test_map_ticket_priority_critical_to_urgent(self, service):
        """Should map 'critical' to URGENT priority."""
        from app.models.support.ticket import TicketPriority

        result = service._map_ticket_priority("critical")
        assert result == TicketPriority.URGENT

    def test_map_ticket_priority_default(self, service):
        """Should default to MEDIUM for unknown priority."""
        from app.models.support.ticket import TicketPriority

        result = service._map_ticket_priority("unknown")
        assert result == TicketPriority.MEDIUM

    def test_map_ticket_priority_none(self, service):
        """Should default to MEDIUM for None priority."""
        from app.models.support.ticket import TicketPriority

        result = service._map_ticket_priority(None)
        assert result == TicketPriority.MEDIUM

    def test_map_task_priority_low(self, service):
        """Should map 'low' to LOW priority."""
        from app.models.pm.task import TaskPriority

        result = service._map_task_priority("low")
        assert result == TaskPriority.LOW

    def test_map_task_priority_urgent(self, service):
        """Should map 'urgent' to URGENT priority."""
        from app.models.pm.task import TaskPriority

        result = service._map_task_priority("urgent")
        assert result == TaskPriority.URGENT


class TestProjectTypeMapping:
    """Test project type mapping."""

    def test_map_project_type_internal(self, service):
        """Should map 'internal' to INTERNAL type."""
        from app.models.finance.core_org.project import ProjectType

        result = service._map_project_type("internal")
        assert result == ProjectType.INTERNAL

    def test_map_project_type_client(self, service):
        """Should map 'client' to CLIENT type."""
        from app.models.finance.core_org.project import ProjectType

        result = service._map_project_type("client")
        assert result == ProjectType.CLIENT

    def test_map_project_type_fiber(self, service):
        """Should map 'fiber' to FIBER_OPTICS_INSTALLATION type."""
        from app.models.finance.core_org.project import ProjectType

        result = service._map_project_type("fiber")
        assert result == ProjectType.FIBER_OPTICS_INSTALLATION

    def test_map_project_type_default(self, service):
        """Should default to CLIENT for unknown type."""
        from app.models.finance.core_org.project import ProjectType

        result = service._map_project_type("unknown")
        assert result == ProjectType.CLIENT

    def test_map_project_type_none(self, service):
        """Should default to CLIENT for None type."""
        from app.models.finance.core_org.project import ProjectType

        result = service._map_project_type(None)
        assert result == ProjectType.CLIENT


class TestInventorySync:
    """Test inventory sync methods for CRM."""

    def test_list_inventory_items_empty(self, service, org_id):
        """Should return empty list when no items match."""
        # Mock the database execute to return empty results
        service.db.execute.return_value.all.return_value = []
        service.db.scalar.return_value = 0

        result = service.list_inventory_items(org_id)

        assert result.items == []
        assert result.total_count == 0
        assert result.has_more is False

    @patch("app.services.inventory.balance.InventoryBalanceService")
    def test_list_inventory_items_with_data(self, mock_balance_class, service, org_id):
        """Should return items with stock data."""
        # Create mock item and category
        mock_item = MagicMock()
        mock_item.item_id = uuid.uuid4()
        mock_item.item_code = "ITEM001"
        mock_item.item_name = "Test Item"
        mock_item.description = "A test item"
        mock_item.base_uom = "PCS"
        mock_item.reorder_point = Decimal("10")
        mock_item.list_price = Decimal("100.00")
        mock_item.currency_code = "NGN"
        mock_item.barcode = "1234567890"

        mock_category = MagicMock()
        mock_category.category_code = "NETWORK"
        mock_category.category_name = "Network Equipment"

        # Mock database results
        service.db.execute.return_value.all.return_value = [(mock_item, mock_category)]
        service.db.scalar.return_value = 1

        # Mock batch stock levels (returns dict[UUID, tuple[on_hand, reserved]])
        mock_balance_class.get_batch_stock_levels.return_value = {
            mock_item.item_id: (Decimal("50"), Decimal("5")),
        }

        result = service.list_inventory_items(org_id, include_zero_stock=True)

        assert len(result.items) == 1
        item = result.items[0]
        assert item.item_code == "ITEM001"
        assert item.item_name == "Test Item"
        assert item.category_code == "NETWORK"
        assert item.quantity_on_hand == Decimal("50")
        assert item.quantity_reserved == Decimal("5")
        assert item.quantity_available == Decimal("45")

    def test_get_inventory_item_detail_not_found(self, service, org_id):
        """Should return None when item not found."""
        service.db.get.return_value = None

        result = service.get_inventory_item_detail(org_id, uuid.uuid4())

        assert result is None

    @patch("app.services.inventory.balance.InventoryBalanceService")
    def test_get_inventory_item_detail_success(
        self, mock_balance_class, service, org_id
    ):
        """Should return detailed item info with warehouse breakdown."""
        item_id = uuid.uuid4()
        category_id = uuid.uuid4()

        # Mock item
        mock_item = MagicMock()
        mock_item.item_id = item_id
        mock_item.item_code = "ROUTER001"
        mock_item.item_name = "Wireless Router"
        mock_item.description = "High-speed wireless router"
        mock_item.category_id = category_id
        mock_item.base_uom = "UNIT"
        mock_item.reorder_point = Decimal("5")
        mock_item.list_price = Decimal("25000.00")
        mock_item.currency_code = "NGN"
        mock_item.barcode = "RTR-001"
        mock_item.organization_id = org_id

        # Mock category
        mock_category = MagicMock()
        mock_category.category_code = "NETWORK"
        mock_category.category_name = "Network Equipment"

        # Mock stock summary
        mock_wh_balance = MagicMock()
        mock_wh_balance.warehouse_id = uuid.uuid4()
        mock_wh_balance.warehouse_code = "WH-MAIN"
        mock_wh_balance.warehouse_name = "Main Warehouse"
        mock_wh_balance.quantity_on_hand = Decimal("20")
        mock_wh_balance.quantity_reserved = Decimal("3")
        mock_wh_balance.quantity_available = Decimal("17")

        mock_summary = MagicMock()
        mock_summary.total_on_hand = Decimal("20")
        mock_summary.total_reserved = Decimal("3")
        mock_summary.total_available = Decimal("17")
        mock_summary.warehouses = [mock_wh_balance]

        # Configure mocks
        def mock_get(model, entity_id):
            if entity_id == item_id:
                return mock_item
            if entity_id == category_id:
                return mock_category
            return None

        service.db.get.side_effect = mock_get
        mock_balance_class.get_item_stock_summary.return_value = mock_summary

        result = service.get_inventory_item_detail(org_id, item_id)

        assert result is not None
        assert result.item_code == "ROUTER001"
        assert result.item_name == "Wireless Router"
        assert result.total_on_hand == Decimal("20")
        assert result.total_reserved == Decimal("3")
        assert result.total_available == Decimal("17")
        assert len(result.warehouses) == 1
        assert result.warehouses[0].warehouse_code == "WH-MAIN"
        assert result.warehouses[0].warehouse_name == "Main Warehouse"

    @patch("app.services.inventory.balance.InventoryBalanceService")
    def test_list_inventory_items_filtered_pagination(
        self, mock_balance_class, service, org_id
    ):
        """Filtered pagination should count and page based on available stock."""
        mock_item1 = MagicMock()
        mock_item1.item_id = uuid.uuid4()
        mock_item1.item_code = "ITEM001"
        mock_item1.item_name = "Item 1"
        mock_item1.description = None
        mock_item1.base_uom = "PCS"
        mock_item1.reorder_point = Decimal("0")
        mock_item1.list_price = None
        mock_item1.currency_code = "NGN"
        mock_item1.barcode = None

        mock_item2 = MagicMock()
        mock_item2.item_id = uuid.uuid4()
        mock_item2.item_code = "ITEM002"
        mock_item2.item_name = "Item 2"
        mock_item2.description = None
        mock_item2.base_uom = "PCS"
        mock_item2.reorder_point = Decimal("0")
        mock_item2.list_price = None
        mock_item2.currency_code = "NGN"
        mock_item2.barcode = None

        mock_item3 = MagicMock()
        mock_item3.item_id = uuid.uuid4()
        mock_item3.item_code = "ITEM003"
        mock_item3.item_name = "Item 3"
        mock_item3.description = None
        mock_item3.base_uom = "PCS"
        mock_item3.reorder_point = Decimal("0")
        mock_item3.list_price = None
        mock_item3.currency_code = "NGN"
        mock_item3.barcode = None

        mock_category = MagicMock()
        mock_category.category_code = "CAT"
        mock_category.category_name = "Category"

        page1 = MagicMock()
        page1.all.return_value = [
            (mock_item1, mock_category),
            (mock_item2, mock_category),
        ]
        page2 = MagicMock()
        page2.all.return_value = [(mock_item3, mock_category)]
        page3 = MagicMock()
        page3.all.return_value = []
        service.db.execute.side_effect = [page1, page2, page3]

        # Mock batch stock levels for each page of results
        # Page 1: item1 has 0 on-hand (filtered out), item2 has 5
        # Page 2: item3 has 10
        def batch_stock_side_effect(_db, _org, item_ids, _wh=None):
            stock = {}
            for iid in item_ids:
                if iid == mock_item1.item_id:
                    stock[iid] = (Decimal("0"), Decimal("0"))
                elif iid == mock_item2.item_id:
                    stock[iid] = (Decimal("5"), Decimal("0"))
                else:
                    stock[iid] = (Decimal("10"), Decimal("0"))
            return stock

        mock_balance_class.get_batch_stock_levels.side_effect = batch_stock_side_effect

        result = service.list_inventory_items(org_id, limit=1)

        assert result.total_count == 2  # item1 filtered out (0 available)
        assert result.has_more is True
        assert len(result.items) == 1
        assert result.items[0].item_code in {"ITEM002", "ITEM003"}

    def test_get_categories(self, service, org_id):
        """Should return list of categories."""
        # Mock execute result
        service.db.execute.return_value.all.return_value = [
            ("NETWORK", "Network Equipment"),
            ("CABLES", "Cables and Wiring"),
        ]

        result = service.get_categories(org_id)

        assert len(result) == 2
        assert result[0] == {"code": "NETWORK", "name": "Network Equipment"}
        assert result[1] == {"code": "CABLES", "name": "Cables and Wiring"}

    def test_get_warehouses(self, service, org_id):
        """Should return list of warehouses."""
        wh_id1 = uuid.uuid4()
        wh_id2 = uuid.uuid4()

        service.db.execute.return_value.all.return_value = [
            (wh_id1, "WH-MAIN", "Main Warehouse"),
            (wh_id2, "WH-FIELD", "Field Stock"),
        ]

        result = service.get_warehouses(org_id)

        assert len(result) == 2
        assert result[0]["code"] == "WH-MAIN"
        assert result[0]["name"] == "Main Warehouse"
        assert result[1]["code"] == "WH-FIELD"


# ============ New CRM API Endpoint Tests ============


class TestListDepartments:
    """Test list_departments for CRM workforce sync."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_departments_empty(self, mock_select, service, org_id, mock_db):
        """Should return empty list when no departments exist."""
        mock_db.scalar.return_value = 0
        mock_db.scalars.return_value.unique.return_value.all.return_value = []

        result = service.list_departments(org_id)

        assert result.departments == []
        assert result.total == 0

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_departments_with_data(self, mock_select, service, org_id, mock_db):
        """Should return departments with manager and members."""
        from app.models.people.hr.employee import EmployeeStatus

        # Create mock person
        mock_person = MagicMock()
        mock_person.first_name = "John"
        mock_person.last_name = "Doe"
        mock_person.email = "john@company.com"

        mock_member_person = MagicMock()
        mock_member_person.first_name = "Jane"
        mock_member_person.last_name = "Smith"
        mock_member_person.email = "jane@company.com"

        mock_head_designation = MagicMock()
        mock_head_designation.designation_id = uuid.uuid4()
        mock_head_designation.designation_name = "Lead Engineer"

        mock_member_designation = MagicMock()
        mock_member_designation.designation_id = uuid.uuid4()
        mock_member_designation.designation_name = "Field Technician"

        # Create mock head employee
        mock_head = MagicMock()
        mock_head.employee_id = uuid.uuid4()
        mock_head.person = mock_person
        mock_head.designation = mock_head_designation
        mock_head.status = EmployeeStatus.ACTIVE

        # Create mock member employee
        mock_member = MagicMock()
        mock_member.employee_id = uuid.uuid4()
        mock_member.person = mock_member_person
        mock_member.designation = mock_member_designation
        mock_member.status = EmployeeStatus.ACTIVE

        # Create mock department
        mock_dept = MagicMock()
        mock_dept.department_code = "ENG"
        mock_dept.department_name = "Engineering"
        mock_dept.head = mock_head
        mock_dept.employees = [mock_head, mock_member]

        mock_db.scalar.return_value = 1
        mock_db.scalars.return_value.unique.return_value.all.return_value = [mock_dept]

        result = service.list_departments(org_id)

        assert len(result.departments) == 1
        dept = result.departments[0]
        assert dept.department_id == "ENG"
        assert dept.department_name == "Engineering"
        assert dept.department_type == "operations"
        assert dept.manager is not None
        assert dept.manager.full_name == "John Doe"
        assert dept.manager.email == "john@company.com"
        assert dept.manager.designation_name == "Lead Engineer"
        assert dept.manager.designation_id == mock_head_designation.designation_id
        assert dept.manager.role == "manager"
        assert len(dept.members) == 2

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_departments_pagination(self, mock_select, service, org_id, mock_db):
        """Should respect limit and offset parameters."""
        mock_db.scalar.return_value = 5
        mock_db.scalars.return_value.unique.return_value.all.return_value = []

        result = service.list_departments(org_id, limit=2, offset=2)

        assert result.total == 5
        assert result.limit == 2
        assert result.offset == 2


class TestListWorkforceEmployees:
    """Test list_workforce_employees for CRM staff lookup."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_workforce_employees_returns_required_fields(
        self, mock_select, service, org_id, mock_db
    ):
        """Should return employee_id and email with optional fields."""
        from app.models.people.hr.employee import EmployeeStatus

        person = MagicMock()
        person.first_name = "Chiedozie"
        person.last_name = "Obiechina"
        person.email = "c.obiechina@dotmac.ng"

        dept = MagicMock()
        dept.department_name = "Support"
        desg = MagicMock()
        desg.designation_name = "Engineer"

        emp = MagicMock()
        emp.employee_id = uuid.uuid4()
        emp.employee_code = "EMP-001"
        emp.person = person
        emp.department = dept
        emp.designation = desg
        emp.status = EmployeeStatus.ACTIVE

        mock_db.scalars.return_value.all.return_value = [emp]

        result = service.list_workforce_employees(org_id)

        assert result.total == 1
        assert len(result.employees) == 1
        row = result.employees[0]
        assert str(row.employee_id) == str(emp.employee_id)
        assert row.email == "c.obiechina@dotmac.ng"
        assert row.full_name == "Chiedozie Obiechina"
        assert row.department == "Support"
        assert row.designation == "Engineer"
        assert row.is_active is True

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_workforce_employees_pagination_and_filter(
        self, mock_select, service, org_id, mock_db
    ):
        """Should paginate and exclude employees without mappable email."""
        from app.models.people.hr.employee import EmployeeStatus

        def mk_emp(code: str, email: str | None):
            person = MagicMock()
            person.first_name = "A"
            person.last_name = code
            person.email = email
            emp = MagicMock()
            emp.employee_id = uuid.uuid4()
            emp.employee_code = code
            emp.person = person
            emp.department = None
            emp.designation = None
            emp.status = EmployeeStatus.ACTIVE
            return emp

        e1 = mk_emp("E1", "e1@x.com")
        e2 = mk_emp("E2", None)  # filtered out (no email)
        e3 = mk_emp("E3", "e3@x.com")
        mock_db.scalars.return_value.all.return_value = [e1, e2, e3]

        result = service.list_workforce_employees(org_id, limit=1, offset=1)

        assert result.total == 2
        assert result.limit == 1
        assert result.offset == 1
        assert result.has_more is False
        assert len(result.employees) == 1
        assert result.employees[0].email == "e3@x.com"


class TestListCompanies:
    """Test list_companies for CRM contacts sync."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_companies_empty(self, mock_select, service, org_id, mock_db):
        """Should return empty list when no companies exist."""
        mock_db.scalar.return_value = 0
        mock_db.scalars.return_value.all.return_value = []

        result = service.list_companies(org_id)

        assert result.companies == []
        assert result.total == 0
        assert result.has_more is False

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_companies_with_data(self, mock_select, service, org_id, mock_db):
        """Should return company contacts with correct mapping."""
        mock_customer = MagicMock()
        mock_customer.customer_id = uuid.uuid4()
        mock_customer.customer_code = "CUST-001"
        mock_customer.legal_name = "Acme Corp"
        mock_customer.tax_identification_number = "12345678"
        mock_customer.billing_address = {"city": "Lagos"}
        mock_customer.primary_contact = {"name": "Boss", "email": "boss@acme.com"}
        mock_customer.crm_id = "crm-abc-123"

        mock_db.scalar.return_value = 1
        mock_db.scalars.return_value.all.return_value = [mock_customer]

        result = service.list_companies(org_id)

        assert len(result.companies) == 1
        company = result.companies[0]
        assert company.customer_code == "CUST-001"
        assert company.legal_name == "Acme Corp"
        assert company.tax_id == "12345678"
        assert company.crm_id == "crm-abc-123"

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_companies_has_more(self, mock_select, service, org_id, mock_db):
        """Should set has_more when more results exist beyond limit."""
        mock_c1 = MagicMock()
        mock_c1.customer_id = uuid.uuid4()
        mock_c1.customer_code = "C1"
        mock_c1.legal_name = "Company 1"
        mock_c1.tax_identification_number = None
        mock_c1.billing_address = None
        mock_c1.primary_contact = None
        mock_c1.crm_id = None

        mock_c2 = MagicMock()
        mock_c2.customer_id = uuid.uuid4()
        mock_c2.customer_code = "C2"
        mock_c2.legal_name = "Company 2"
        mock_c2.tax_identification_number = None
        mock_c2.billing_address = None
        mock_c2.primary_contact = None
        mock_c2.crm_id = None

        mock_db.scalar.return_value = 2
        # Return limit+1 items to trigger has_more
        mock_db.scalars.return_value.all.return_value = [mock_c1, mock_c2]

        result = service.list_companies(org_id, limit=1)

        assert result.has_more is True
        assert len(result.companies) == 1


class TestListPeopleContacts:
    """Test list_people_contacts for CRM contacts sync."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_people_empty(self, mock_select, service, org_id, mock_db):
        """Should return empty list when no individual customers exist."""
        mock_db.scalar.return_value = 0
        mock_db.scalars.return_value.all.return_value = []

        result = service.list_people_contacts(org_id)

        assert result.contacts == []
        assert result.total == 0

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_people_extracts_email_phone(
        self, mock_select, service, org_id, mock_db
    ):
        """Should extract email/phone from primary_contact JSONB."""
        mock_customer = MagicMock()
        mock_customer.customer_id = uuid.uuid4()
        mock_customer.customer_code = "IND-001"
        mock_customer.legal_name = "John Individual"
        mock_customer.primary_contact = {
            "email": "john@example.com",
            "phone": "+2341234567",
        }
        mock_customer.crm_id = None

        mock_db.scalar.return_value = 1
        mock_db.scalars.return_value.all.return_value = [mock_customer]

        result = service.list_people_contacts(org_id)

        assert len(result.contacts) == 1
        contact = result.contacts[0]
        assert contact.legal_name == "John Individual"
        assert contact.email == "john@example.com"
        assert contact.phone == "+2341234567"

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_list_people_handles_null_primary_contact(
        self, mock_select, service, org_id, mock_db
    ):
        """Should handle None primary_contact gracefully."""
        mock_customer = MagicMock()
        mock_customer.customer_id = uuid.uuid4()
        mock_customer.customer_code = "IND-002"
        mock_customer.legal_name = "No Contact Info"
        mock_customer.primary_contact = None
        mock_customer.crm_id = None

        mock_db.scalar.return_value = 1
        mock_db.scalars.return_value.all.return_value = [mock_customer]

        result = service.list_people_contacts(org_id)

        assert len(result.contacts) == 1
        assert result.contacts[0].email is None
        assert result.contacts[0].phone is None


class TestCreateMaterialRequest:
    """Test create_material_request for CRM material request sync."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_create_material_request_idempotent(
        self, mock_select, service, org_id, mock_db
    ):
        """Should return existing request when omni_id already exists."""
        from app.models.inventory.material_request import MaterialRequestStatus

        existing_mr = MagicMock()
        existing_mr.request_id = uuid.uuid4()
        existing_mr.request_number = "MAT-MR-2026-00001"
        existing_mr.status = MaterialRequestStatus.SUBMITTED

        # First scalar call returns existing
        mock_db.scalar.return_value = existing_mr

        payload = CRMMaterialRequestPayload(
            omni_id="crm-mr-123",
            request_type="ISSUE",
            items=[
                CRMMaterialRequestItemPayload(
                    item_code="ITEM001", quantity=Decimal("5")
                )
            ],
        )

        result = service.create_material_request(org_id, payload)

        assert result.request_id == existing_mr.request_id
        assert result.request_number == "MAT-MR-2026-00001"
        assert result.omni_id == "crm-mr-123"
        # Should not call flush (no new records created)
        mock_db.add.assert_not_called()

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_create_material_request_happy_path(
        self, mock_select, service, org_id, mock_db
    ):
        """Should create MR with items and return response."""

        # No existing MR (first scalar returns None for idempotency check)
        # Second scalar: item lookup returns mock item
        mock_item = MagicMock()
        mock_item.item_id = uuid.uuid4()

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No existing MR
            return mock_item  # Item lookup

        mock_db.scalar.side_effect = scalar_side_effect

        # Simulate flush() populating request_id on added MR objects
        added_objects: list = []
        mock_db.add.side_effect = lambda obj: added_objects.append(obj)

        def simulate_flush() -> None:
            for obj in added_objects:
                if hasattr(obj, "request_id") and obj.request_id is None:
                    obj.request_id = uuid.uuid4()

        mock_db.flush.side_effect = simulate_flush

        # Mock numbering service
        with patch(
            "app.services.finance.common.numbering.SyncNumberingService"
        ) as mock_numbering_cls:
            mock_numbering = MagicMock()
            mock_numbering.generate_next_number.return_value = "MAT-MR-2026-00001"
            mock_numbering_cls.return_value = mock_numbering

            payload = CRMMaterialRequestPayload(
                omni_id="crm-mr-new-456",
                request_type="ISSUE",
                items=[
                    CRMMaterialRequestItemPayload(
                        item_code="CABLE-01", quantity=Decimal("10"), uom="METER"
                    )
                ],
                remarks="Needed for installation",
                schedule_date="2026-03-01",
            )

            result = service.create_material_request(org_id, payload)

        assert result.request_number == "MAT-MR-2026-00001"
        assert result.status == "SUBMITTED"
        assert result.omni_id == "crm-mr-new-456"
        assert result.request_id is not None
        # Should add MR header + 1 line item
        assert mock_db.add.call_count >= 2

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_create_material_request_invalid_item(
        self, mock_select, service, org_id, mock_db
    ):
        """Should raise ValueError when item_code not found."""
        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No existing MR
            return None  # Item not found

        mock_db.scalar.side_effect = scalar_side_effect

        payload = CRMMaterialRequestPayload(
            omni_id="crm-mr-bad-item",
            request_type="ISSUE",
            items=[
                CRMMaterialRequestItemPayload(
                    item_code="NONEXISTENT", quantity=Decimal("1")
                )
            ],
        )

        with pytest.raises(ValueError, match="Item not found: NONEXISTENT"):
            service.create_material_request(org_id, payload)

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_create_material_request_invalid_type(
        self, mock_select, service, org_id, mock_db
    ):
        """Should raise ValueError for unknown request_type."""
        mock_db.scalar.return_value = None  # No existing MR

        payload = CRMMaterialRequestPayload(
            omni_id="crm-mr-bad-type",
            request_type="UNKNOWN",
            items=[
                CRMMaterialRequestItemPayload(
                    item_code="ITEM001", quantity=Decimal("1")
                )
            ],
        )

        with pytest.raises(ValueError, match="Invalid request_type"):
            service.create_material_request(org_id, payload)

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_create_material_request_project_ticket_linking(
        self, mock_select, service, org_id, mock_db
    ):
        """Should resolve project and ticket CRM IDs."""
        mock_item = MagicMock()
        mock_item.item_id = uuid.uuid4()
        project_id = uuid.uuid4()
        ticket_id = uuid.uuid4()

        call_count = 0

        def scalar_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No existing MR
            return mock_item  # Item lookup

        mock_db.scalar.side_effect = scalar_side_effect

        # Simulate flush() populating request_id on added MR objects
        added_objects: list = []
        mock_db.add.side_effect = lambda obj: added_objects.append(obj)

        def simulate_flush() -> None:
            for obj in added_objects:
                if hasattr(obj, "request_id") and obj.request_id is None:
                    obj.request_id = uuid.uuid4()

        mock_db.flush.side_effect = simulate_flush

        with patch.object(
            service, "_resolve_project_id", return_value=project_id
        ) as mock_resolve_proj:
            with patch.object(
                service, "_resolve_ticket_id", return_value=ticket_id
            ) as mock_resolve_ticket:
                with patch.object(service, "_resolve_employee_id", return_value=None):
                    with patch(
                        "app.services.finance.common.numbering.SyncNumberingService"
                    ) as mock_numbering_cls:
                        mock_numbering = MagicMock()
                        mock_numbering.generate_next_number.return_value = (
                            "MAT-MR-2026-00002"
                        )
                        mock_numbering_cls.return_value = mock_numbering

                        payload = CRMMaterialRequestPayload(
                            omni_id="crm-mr-linked",
                            request_type="PURCHASE",
                            items=[
                                CRMMaterialRequestItemPayload(
                                    item_code="ITEM001", quantity=Decimal("3")
                                )
                            ],
                            project_crm_id="proj-crm-123",
                            ticket_crm_id="ticket-crm-456",
                        )

                        service.create_material_request(org_id, payload)

        mock_resolve_proj.assert_called_once_with(org_id, "proj-crm-123")
        mock_resolve_ticket.assert_called_once_with(org_id, "ticket-crm-456")


class TestGetMaterialRequestByCrmId:
    """Test get_material_request_by_crm_id."""

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_not_found_returns_none(self, mock_select, service, org_id, mock_db):
        """Should return None when no MR matches the omni_id."""
        mock_db.scalar.return_value = None

        result = service.get_material_request_by_crm_id(org_id, "nonexistent")

        assert result is None

    @patch("app.services.sync.dotmac_crm_sync_service.select")
    def test_found_returns_status(self, mock_select, service, org_id, mock_db):
        """Should return full status with items when found."""
        from app.models.inventory.material_request import (
            MaterialRequestStatus,
            MaterialRequestType,
        )

        item_id = uuid.uuid4()

        mock_line = MagicMock()
        mock_line.inventory_item_id = item_id
        mock_line.requested_qty = Decimal("10")
        mock_line.ordered_qty = Decimal("0")
        mock_line.uom = "PCS"

        mock_mr = MagicMock()
        mock_mr.request_id = uuid.uuid4()
        mock_mr.request_number = "MAT-MR-2026-00001"
        mock_mr.status = MaterialRequestStatus.SUBMITTED
        mock_mr.request_type = MaterialRequestType.ISSUE
        mock_mr.items = [mock_line]
        mock_mr.created_at = datetime(2026, 2, 10, tzinfo=UTC)

        # First scalar: MR lookup
        # Then execute for item names
        mock_db.scalar.return_value = mock_mr
        mock_db.execute.return_value.all.return_value = [(item_id, "Fiber Cable")]

        result = service.get_material_request_by_crm_id(org_id, "crm-mr-123")

        assert result is not None
        assert result.request_number == "MAT-MR-2026-00001"
        assert result.status == "SUBMITTED"
        assert result.request_type == "ISSUE"
        assert len(result.items) == 1
        assert result.items[0].item_name == "Fiber Cable"
        assert result.items[0].requested_qty == Decimal("10")


class TestEnhancedBulkPayloads:
    """Test that enhanced payload fields are accepted."""

    def test_project_payload_service_team_fields(self):
        """CRMProjectPayload should accept new service team fields."""
        payload = CRMProjectPayload(
            crm_id="test-123",
            name="Test Project",
            status="active",
            service_team_name="Team Alpha",
            service_team_department_id="dept-123",
        )
        assert payload.service_team_name == "Team Alpha"
        assert payload.service_team_department_id == "dept-123"

    def test_project_payload_backward_compatible(self):
        """CRMProjectPayload should work without new fields."""
        payload = CRMProjectPayload(
            crm_id="test-123",
            name="Test Project",
            status="active",
        )
        assert payload.service_team_name is None
        assert payload.service_team_department_id is None

    def test_ticket_payload_assigned_emails(self):
        """CRMTicketPayload should accept assigned_employee_emails."""
        payload = CRMTicketPayload(
            crm_id="test-456",
            subject="Test Ticket",
            status="open",
            service_team_name="Team Beta",
            assigned_employee_emails=["john@co.com", "jane@co.com"],
        )
        assert payload.service_team_name == "Team Beta"
        assert len(payload.assigned_employee_emails) == 2

    def test_ticket_payload_backward_compatible(self):
        """CRMTicketPayload should work without new fields."""
        payload = CRMTicketPayload(
            crm_id="test-456",
            subject="Test Ticket",
            status="open",
        )
        assert payload.assigned_employee_emails == []
        assert payload.description is None
        assert payload.comments == []
        assert payload.activity_log == []

    def test_ticket_payload_accepts_comments_activity_log(self):
        """CRMTicketPayload should accept new comments and activity_log fields."""
        payload = CRMTicketPayload(
            crm_id="test-456",
            subject="Test Ticket",
            status="open",
            description="Ticket description",
            comments=[{"id": "comment-1", "body": "Investigating"}],
            activity_log=[{"id": "activity-1", "event": "created"}],
        )
        assert payload.description == "Ticket description"
        assert payload.comments[0]["id"] == "comment-1"
        assert payload.activity_log[0]["id"] == "activity-1"

    def test_ticket_payload_accepts_alias_fields(self):
        """CRMTicketPayload should accept alternate CRM key names."""
        payload = CRMTicketPayload.model_validate(
            {
                "crm_id": "test-456",
                "subject": "Test Ticket",
                "status": "open",
                "body": "Alt description",
                "ticket_comments": [{"id": "comment-1"}],
                "activityLog": [{"kind": "event", "id": "a-1"}],
            }
        )
        assert payload.description == "Alt description"
        assert payload.comments[0]["id"] == "comment-1"
        assert payload.activity_log[0]["id"] == "a-1"

    def test_work_order_payload_assigned_emails(self):
        """CRMWorkOrderPayload should accept assigned_employee_emails."""
        payload = CRMWorkOrderPayload(
            crm_id="test-789",
            title="Test WO",
            status="active",
            assigned_employee_emails=["tech@co.com"],
        )
        assert len(payload.assigned_employee_emails) == 1

    def test_work_order_payload_backward_compatible(self):
        """CRMWorkOrderPayload should work without new fields."""
        payload = CRMWorkOrderPayload(
            crm_id="test-789",
            title="Test WO",
            status="active",
        )
        assert payload.assigned_employee_emails == []
