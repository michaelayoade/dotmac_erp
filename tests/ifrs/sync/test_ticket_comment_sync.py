"""
Tests for Ticket Comment Sync from ERPNext.

Tests cover:
- CommunicationToCommentMapping.map_comment()
- CommunicationToCommentMapping.map_communication()
- Comment type mapping for various ERPNext comment_type values
- _resolve_person_by_email()
- _sync_comments() creation and deduplication
- _create_comment_if_new() dedup via SyncEntity
- fetch_records attaching _comments_raw and _communications_raw
- transform_record passing through comment data
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services.erpnext.mappings.support import CommunicationToCommentMapping

# ============ Comment Mapping Tests ============


class TestCommentMapping:
    """Tests for CommunicationToCommentMapping.map_comment()."""

    def test_regular_comment_maps_to_comment(self) -> None:
        """ERPNext Comment type maps to COMMENT."""
        record = {
            "name": "comment-001",
            "comment_type": "Comment",
            "comment_email": "user@example.com",
            "content": "This is a comment",
            "creation": "2026-01-15 10:30:00",
        }
        result = CommunicationToCommentMapping.map_comment(record)
        assert result["comment_type"] == "COMMENT"
        assert result["content"] == "This is a comment"
        assert result["sender_email"] == "user@example.com"
        assert result["source_doctype"] == "Comment"
        assert result["source_name"] == "comment-001"

    def test_info_comment_maps_to_system(self) -> None:
        """ERPNext Info type maps to SYSTEM."""
        record = {
            "name": "comment-002",
            "comment_type": "Info",
            "content": "Status changed to Open",
            "creation": "2026-01-15 10:30:00",
        }
        result = CommunicationToCommentMapping.map_comment(record)
        assert result["comment_type"] == "SYSTEM"

    def test_edit_maps_to_system(self) -> None:
        """ERPNext Edit type maps to SYSTEM."""
        record = {
            "name": "comment-003",
            "comment_type": "Edit",
            "content": "Field changed",
            "creation": "2026-01-15 10:30:00",
        }
        result = CommunicationToCommentMapping.map_comment(record)
        assert result["comment_type"] == "SYSTEM"

    def test_assigned_maps_to_system(self) -> None:
        """ERPNext Assigned type maps to SYSTEM."""
        record = {
            "name": "comment-004",
            "comment_type": "Assigned",
            "content": "Assigned to John",
            "creation": "2026-01-15 10:30:00",
        }
        result = CommunicationToCommentMapping.map_comment(record)
        assert result["comment_type"] == "SYSTEM"

    def test_unknown_type_maps_to_system(self) -> None:
        """Unknown comment types default to SYSTEM."""
        record = {
            "name": "comment-005",
            "comment_type": "CustomType",
            "content": "Some content",
            "creation": "2026-01-15 10:30:00",
        }
        result = CommunicationToCommentMapping.map_comment(record)
        assert result["comment_type"] == "SYSTEM"

    def test_empty_content_uses_type_label(self) -> None:
        """Empty content falls back to comment type label."""
        record = {
            "name": "comment-006",
            "comment_type": "Assigned",
            "content": "",
            "creation": "2026-01-15 10:30:00",
        }
        result = CommunicationToCommentMapping.map_comment(record)
        assert result["content"] == "[Assigned]"

    def test_none_content_uses_type_label(self) -> None:
        """None content falls back to comment type label."""
        record = {
            "name": "comment-007",
            "comment_type": "Info",
            "content": None,
            "creation": "2026-01-15 10:30:00",
        }
        result = CommunicationToCommentMapping.map_comment(record)
        assert result["content"] == "[Info]"

    def test_whitespace_content_is_stripped(self) -> None:
        """Content is stripped of leading/trailing whitespace."""
        record = {
            "name": "comment-008",
            "comment_type": "Comment",
            "content": "  Hello world  ",
            "creation": "2026-01-15 10:30:00",
        }
        result = CommunicationToCommentMapping.map_comment(record)
        assert result["content"] == "Hello world"

    def test_preserves_created_at(self) -> None:
        """creation timestamp is passed through as created_at."""
        record = {
            "name": "comment-009",
            "comment_type": "Comment",
            "content": "Test",
            "creation": "2026-01-15 14:30:00",
        }
        result = CommunicationToCommentMapping.map_comment(record)
        assert result["created_at"] == "2026-01-15 14:30:00"


# ============ Communication Mapping Tests ============


class TestCommunicationMapping:
    """Tests for CommunicationToCommentMapping.map_communication()."""

    def test_communication_maps_to_comment_type(self) -> None:
        """Communications always map to COMMENT type."""
        record = {
            "name": "comm-001",
            "communication_type": "Communication",
            "subject": "Re: Issue #123",
            "content": "Please check this issue.",
            "sender": "customer@example.com",
            "sent_or_received": "Received",
            "creation": "2026-01-15 11:00:00",
        }
        result = CommunicationToCommentMapping.map_communication(record)
        assert result["comment_type"] == "COMMENT"
        assert result["sender_email"] == "customer@example.com"
        assert result["source_doctype"] == "Communication"
        assert result["source_name"] == "comm-001"

    def test_subject_prepended_to_content(self) -> None:
        """Subject is prepended to content in bold when present."""
        record = {
            "name": "comm-002",
            "subject": "Important Update",
            "content": "Here is the update.",
            "sender": "user@example.com",
            "creation": "2026-01-15 11:00:00",
        }
        result = CommunicationToCommentMapping.map_communication(record)
        assert "**Important Update**" in result["content"]
        assert "Here is the update." in result["content"]

    def test_subject_not_duplicated_when_in_content(self) -> None:
        """Subject is not prepended if already in content."""
        record = {
            "name": "comm-003",
            "subject": "Bug fix",
            "content": "Bug fix applied to module X",
            "sender": "user@example.com",
            "creation": "2026-01-15 11:00:00",
        }
        result = CommunicationToCommentMapping.map_communication(record)
        # Subject is in content, so should NOT be prepended
        assert not result["content"].startswith("**Bug fix**")

    def test_empty_content_uses_label(self) -> None:
        """Empty communication content falls back to label."""
        record = {
            "name": "comm-004",
            "subject": "",
            "content": "",
            "sender": "user@example.com",
            "creation": "2026-01-15 11:00:00",
        }
        result = CommunicationToCommentMapping.map_communication(record)
        assert result["content"] == "[Communication]"


# ============ Person Resolution Tests ============


class TestResolvePersonByEmail:
    """Tests for _resolve_person_by_email in TicketSyncService."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.org_id = uuid.uuid4()
        self.user_id = uuid.uuid4()

    def _make_service(self):
        from app.services.erpnext.sync.support import TicketSyncService

        return TicketSyncService(self.db, self.org_id, self.user_id)

    def test_returns_none_for_none_email(self) -> None:
        """Returns None when email is None."""
        service = self._make_service()
        assert service._resolve_person_by_email(None) is None

    def test_returns_none_for_empty_email(self) -> None:
        """Returns None when email is empty string."""
        service = self._make_service()
        assert service._resolve_person_by_email("") is None

    def test_caches_results(self) -> None:
        """Subsequent calls for same email use cache."""
        service = self._make_service()
        person_id = uuid.uuid4()

        # First call — DB returns a person
        mock_person = MagicMock()
        mock_person.id = person_id
        self.db.execute.return_value.scalar_one_or_none.return_value = mock_person

        result1 = service._resolve_person_by_email("test@example.com")
        assert result1 == person_id

        # Second call — should use cache, not DB
        self.db.execute.reset_mock()
        result2 = service._resolve_person_by_email("test@example.com")
        assert result2 == person_id
        self.db.execute.assert_not_called()

    def test_caches_none_results(self) -> None:
        """Caches None results to avoid repeated lookups for unknown emails."""
        service = self._make_service()

        # Both Person and Employee queries return None
        self.db.execute.return_value.scalar_one_or_none.return_value = None

        result = service._resolve_person_by_email("unknown@example.com")
        assert result is None

        # Should be cached
        call_count = self.db.execute.call_count
        result2 = service._resolve_person_by_email("unknown@example.com")
        assert result2 is None
        assert self.db.execute.call_count == call_count  # No new DB calls


# ============ Comment Sync Tests ============


class TestSyncComments:
    """Tests for _sync_comments and _create_comment_if_new."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.org_id = uuid.uuid4()
        self.user_id = uuid.uuid4()

    def _make_service(self):
        from app.services.erpnext.sync.support import TicketSyncService

        return TicketSyncService(self.db, self.org_id, self.user_id)

    def _mock_ticket(self) -> MagicMock:
        ticket = MagicMock()
        ticket.ticket_id = uuid.uuid4()
        ticket.ticket_number = "ISS-001"
        return ticket

    def test_creates_comment_from_erpnext_comment(self) -> None:
        """Creates TicketComment from an ERPNext Comment record."""
        service = self._make_service()
        ticket = self._mock_ticket()

        # No existing SyncEntity
        self.db.execute.return_value.scalar_one_or_none.return_value = None

        comments = [
            {
                "name": "comment-001",
                "comment_type": "Comment",
                "comment_email": "user@example.com",
                "content": "Hello",
                "creation": "2026-01-15 10:30:00",
            }
        ]

        with patch.object(service, "_resolve_person_by_email", return_value=None):
            count = service._sync_comments(ticket, comments, [])

        assert count == 1

    def test_creates_comment_from_communication(self) -> None:
        """Creates TicketComment from an ERPNext Communication record."""
        service = self._make_service()
        ticket = self._mock_ticket()

        self.db.execute.return_value.scalar_one_or_none.return_value = None

        comms = [
            {
                "name": "comm-001",
                "communication_type": "Communication",
                "subject": "Re: Issue",
                "content": "Response here",
                "sender": "customer@example.com",
                "creation": "2026-01-15 11:00:00",
            }
        ]

        with patch.object(service, "_resolve_person_by_email", return_value=None):
            count = service._sync_comments(ticket, [], comms)

        assert count == 1

    def test_dedup_skips_already_synced(self) -> None:
        """Skips comments that already have a SYNCED SyncEntity."""
        service = self._make_service()
        ticket = self._mock_ticket()

        from app.models.sync import SyncStatus

        # Simulate existing synced SyncEntity
        existing_sync = MagicMock()
        existing_sync.sync_status = SyncStatus.SYNCED
        self.db.execute.return_value.scalar_one_or_none.return_value = existing_sync

        comments = [
            {
                "name": "comment-already-synced",
                "comment_type": "Comment",
                "content": "Already synced",
                "creation": "2026-01-15 10:30:00",
            }
        ]

        with patch.object(service, "_resolve_person_by_email", return_value=None):
            count = service._sync_comments(ticket, comments, [])

        assert count == 0

    def test_both_comments_and_communications(self) -> None:
        """Processes both comments and communications in one call."""
        service = self._make_service()
        ticket = self._mock_ticket()

        self.db.execute.return_value.scalar_one_or_none.return_value = None

        comments = [
            {
                "name": "comment-001",
                "comment_type": "Comment",
                "content": "Note 1",
                "creation": "2026-01-15 10:30:00",
            }
        ]
        comms = [
            {
                "name": "comm-001",
                "subject": "",
                "content": "Email reply",
                "sender": "user@example.com",
                "creation": "2026-01-15 11:00:00",
            }
        ]

        with patch.object(service, "_resolve_person_by_email", return_value=None):
            count = service._sync_comments(ticket, comments, comms)

        assert count == 2

    def test_empty_source_name_skipped(self) -> None:
        """Comments with empty source_name are skipped."""
        service = self._make_service()
        ticket = self._mock_ticket()

        comments = [
            {
                "name": "",
                "comment_type": "Comment",
                "content": "No name",
                "creation": "2026-01-15 10:30:00",
            }
        ]

        with patch.object(service, "_resolve_person_by_email", return_value=None):
            count = service._sync_comments(ticket, comments, [])

        assert count == 0


# ============ Fetch Records Tests ============


class TestFetchRecordsWithComments:
    """Tests that fetch_records attaches comment data to ticket records."""

    def test_fetch_attaches_comments_and_communications(self) -> None:
        """fetch_records fetches comments and communications per ticket."""
        from app.services.erpnext.sync.support import TicketSyncService

        db = MagicMock()
        service = TicketSyncService(db, uuid.uuid4(), uuid.uuid4())

        mock_client = MagicMock()
        ticket_record = {"name": "ISS-001", "subject": "Bug"}
        mock_client.get_issues.return_value = [ticket_record]
        mock_client.get_comments_for_doc.return_value = [{"name": "c1"}]
        mock_client.get_communications_for_doc.return_value = [{"name": "m1"}]

        records = list(service.fetch_records(mock_client))

        assert len(records) == 1
        assert records[0]["_comments_raw"] == [{"name": "c1"}]
        assert records[0]["_communications_raw"] == [{"name": "m1"}]

    def test_transform_passes_through_comment_data(self) -> None:
        """transform_record preserves _comments_raw and _communications_raw."""
        from app.services.erpnext.sync.support import TicketSyncService

        db = MagicMock()
        service = TicketSyncService(db, uuid.uuid4(), uuid.uuid4())

        record = {
            "name": "ISS-001",
            "subject": "Test Bug",
            "description": "Something broke",
            "status": "Open",
            "priority": "Medium",
            "raised_by": "user@example.com",
            "owner": "admin@example.com",
            "opening_date": "2026-01-15",
            "resolution_date": None,
            "resolution_details": None,
            "project": None,
            "customer": None,
            "modified": "2026-01-15 10:00:00",
            "_comments_raw": [{"name": "c1"}],
            "_communications_raw": [{"name": "m1"}],
        }

        result = service.transform_record(record)
        assert result["_comments_raw"] == [{"name": "c1"}]
        assert result["_communications_raw"] == [{"name": "m1"}]


# ============ Comment Type Full Coverage ============


class TestAllCommentTypeMappings:
    """Ensure all known ERPNext comment_type values map correctly."""

    @pytest.mark.parametrize(
        "erpnext_type,expected",
        [
            ("Comment", "COMMENT"),
            ("Info", "SYSTEM"),
            ("Edit", "SYSTEM"),
            ("Created", "SYSTEM"),
            ("Deleted", "SYSTEM"),
            ("Label", "SYSTEM"),
            ("Assignment Completed", "SYSTEM"),
            ("Assigned", "SYSTEM"),
            ("Shared", "SYSTEM"),
            ("Unshared", "SYSTEM"),
            ("Attachment", "SYSTEM"),
            ("Attachment Removed", "SYSTEM"),
            ("Relinked", "SYSTEM"),
            ("Bot", "SYSTEM"),
        ],
    )
    def test_comment_type_mapping(self, erpnext_type: str, expected: str) -> None:
        """Each ERPNext comment_type maps to the correct DotMac CommentType."""
        record = {
            "name": f"comment-{erpnext_type}",
            "comment_type": erpnext_type,
            "content": "Test",
            "creation": "2026-01-15 10:30:00",
        }
        result = CommunicationToCommentMapping.map_comment(record)
        assert result["comment_type"] == expected


# ============ Issue Type → Category Resolution Tests ============


class TestIssueTypeCategoryResolution:
    """Tests for _resolve_or_create_category in TicketSyncService."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.org_id = uuid.uuid4()
        self.user_id = uuid.uuid4()

    def _make_service(self):
        from app.services.erpnext.sync.support import TicketSyncService

        return TicketSyncService(self.db, self.org_id, self.user_id)

    def test_returns_none_for_none_issue_type(self) -> None:
        """Returns None when issue_type is None."""
        service = self._make_service()
        assert service._resolve_or_create_category(None) is None

    def test_returns_none_for_empty_issue_type(self) -> None:
        """Returns None when issue_type is empty string."""
        service = self._make_service()
        assert service._resolve_or_create_category("") is None

    def test_reuses_existing_category(self) -> None:
        """Returns existing category_id when category_code matches."""
        service = self._make_service()
        cat_id = uuid.uuid4()

        mock_cat = MagicMock()
        mock_cat.category_id = cat_id
        self.db.execute.return_value.scalar_one_or_none.return_value = mock_cat

        result = service._resolve_or_create_category("Application Issue")
        assert result == cat_id
        # Should NOT have called db.add (existing category)
        self.db.add.assert_not_called()

    def test_auto_creates_category(self) -> None:
        """Auto-creates a TicketCategory when none exists."""
        service = self._make_service()

        # No existing category
        self.db.execute.return_value.scalar_one_or_none.return_value = None

        service._resolve_or_create_category("Network Issue")
        # Should have called db.add and db.flush
        self.db.add.assert_called_once()
        self.db.flush.assert_called()
        # The created category should have the right code and name
        created_cat = self.db.add.call_args[0][0]
        assert created_cat.category_code == "NETWORK-ISSUE"
        assert created_cat.category_name == "Network Issue"
        assert created_cat.organization_id == self.org_id

    def test_code_slugification(self) -> None:
        """Issue type is slugified: uppercase, spaces→dashes, truncated to 20 chars."""
        service = self._make_service()
        self.db.execute.return_value.scalar_one_or_none.return_value = None

        service._resolve_or_create_category("Some Very Long Issue Type Name Here")
        created_cat = self.db.add.call_args[0][0]
        # 20 char limit
        assert len(created_cat.category_code) <= 20
        assert created_cat.category_code == "SOME-VERY-LONG-ISSUE"

    def test_caches_results(self) -> None:
        """Subsequent calls for same issue_type use cache."""
        service = self._make_service()
        cat_id = uuid.uuid4()

        mock_cat = MagicMock()
        mock_cat.category_id = cat_id
        self.db.execute.return_value.scalar_one_or_none.return_value = mock_cat

        # First call
        result1 = service._resolve_or_create_category("Bug")
        assert result1 == cat_id

        # Reset mock to verify no DB call on second invocation
        self.db.execute.reset_mock()
        result2 = service._resolve_or_create_category("Bug")
        assert result2 == cat_id
        self.db.execute.assert_not_called()

    def test_whitespace_stripped(self) -> None:
        """Leading/trailing whitespace is stripped from issue_type."""
        service = self._make_service()
        self.db.execute.return_value.scalar_one_or_none.return_value = None

        service._resolve_or_create_category("  Bug Report  ")
        created_cat = self.db.add.call_args[0][0]
        assert created_cat.category_code == "BUG-REPORT"
        assert created_cat.category_name == "Bug Report"

    def test_issue_type_mapping_in_transform(self) -> None:
        """issue_type field flows through TicketMapping.transform_record."""
        from app.services.erpnext.mappings.support import TicketMapping

        mapping = TicketMapping()
        record = {
            "name": "ISS-001",
            "subject": "Bug",
            "description": "Something broke",
            "status": "Open",
            "priority": "Medium",
            "issue_type": "Application Issue",
            "raised_by": "user@example.com",
            "owner": "admin@example.com",
            "opening_date": "2026-01-15",
            "resolution_date": None,
            "resolution_details": None,
            "project": None,
            "customer": None,
            "modified": "2026-01-15 10:00:00",
        }
        result = mapping.transform_record(record)
        assert result["_issue_type"] == "Application Issue"

    def test_hd_ticket_type_in_transform(self) -> None:
        """ticket_type field flows through HDTicketMapping.transform_record."""
        from app.services.erpnext.mappings.support import TicketMapping

        mapping = TicketMapping(doctype="HD Ticket")
        record = {
            "name": 515,
            "subject": "25113",
            "description": "Link down",
            "status": "Closed",
            "priority": "Low",
            "ticket_type": "Customer Link Disconection",
            "raised_by": "user@example.com",
            "owner": "admin@example.com",
            "opening_date": "2025-01-10",
            "resolution_date": None,
            "resolution_details": None,
            "customer": None,
            "modified": "2025-01-10 12:21:41",
        }
        result = mapping.transform_record(record)
        assert result["_issue_type"] == "Customer Link Disconection"

    def test_issue_type_none_in_transform(self) -> None:
        """Missing issue_type results in None _issue_type."""
        from app.services.erpnext.mappings.support import TicketMapping

        mapping = TicketMapping()
        record = {
            "name": "ISS-002",
            "subject": "Bug",
            "status": "Open",
            "priority": "Medium",
            "raised_by": "user@example.com",
            "owner": "admin@example.com",
            "opening_date": "2026-01-15",
            "modified": "2026-01-15 10:00:00",
        }
        result = mapping.transform_record(record)
        assert result.get("_issue_type") is None

    def test_create_entity_sets_category_id(self) -> None:
        """create_entity populates category_id from issue_type."""
        service = self._make_service()
        cat_id = uuid.uuid4()

        # Mock the category resolution
        with patch.object(
            service, "_resolve_or_create_category", return_value=cat_id
        ) as mock_resolve:
            data: dict[str, object] = {
                "ticket_number": "ISS-001",
                "subject": "Test ticket",
                "description": "A test",
                "status": "OPEN",
                "priority": "MEDIUM",
                "raised_by_email": "user@example.com",
                "opening_date": "2026-01-15",
                "resolution_date": None,
                "resolution": None,
                "_project_source_name": None,
                "_customer_source_name": None,
                "_owner_email": None,
                "_issue_type": "Application Issue",
                "_source_modified": None,
                "_source_name": "ISS-001",
                "_comments_raw": [],
                "_communications_raw": [],
            }
            ticket = service.create_entity(data)
        mock_resolve.assert_called_once_with("Application Issue")
        assert ticket.category_id == cat_id

    def test_update_entity_sets_category_id(self) -> None:
        """update_entity populates category_id from issue_type."""
        service = self._make_service()
        cat_id = uuid.uuid4()

        existing = MagicMock()
        existing.ticket_number = "ISS-001"
        existing.customer_id = None
        existing.raised_by_id = None
        existing.assigned_to_id = None

        with patch.object(
            service, "_resolve_or_create_category", return_value=cat_id
        ) as mock_resolve:
            data: dict[str, object] = {
                "subject": "Test ticket",
                "description": "A test",
                "status": "OPEN",
                "priority": "MEDIUM",
                "raised_by_email": "user@example.com",
                "opening_date": "2026-01-15",
                "resolution_date": None,
                "resolution": None,
                "_project_source_name": None,
                "_customer_source_name": None,
                "_owner_email": None,
                "_issue_type": "Network Issue",
                "_source_modified": None,
                "_source_name": "ISS-001",
                "_comments_raw": [],
                "_communications_raw": [],
            }
            service.update_entity(existing, data)
        mock_resolve.assert_called_once_with("Network Issue")
        assert existing.category_id == cat_id

    def test_create_entity_no_category_when_none(self) -> None:
        """create_entity sets category_id=None when issue_type is None."""
        service = self._make_service()

        with patch.object(service, "_resolve_or_create_category", return_value=None):
            data: dict[str, object] = {
                "ticket_number": "ISS-002",
                "subject": "No category",
                "description": None,
                "status": "OPEN",
                "priority": "MEDIUM",
                "raised_by_email": None,
                "opening_date": "2026-01-15",
                "resolution_date": None,
                "resolution": None,
                "_project_source_name": None,
                "_customer_source_name": None,
                "_owner_email": None,
                "_issue_type": None,
                "_source_modified": None,
                "_source_name": "ISS-002",
                "_comments_raw": [],
                "_communications_raw": [],
            }
            ticket = service.create_entity(data)

        assert ticket.category_id is None
