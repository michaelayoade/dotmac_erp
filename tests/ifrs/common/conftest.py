"""
Fixtures for Common Services Tests.

Mock objects for testing attachment and other common services.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock

import pytest

from app.models.ifrs.common.attachment import AttachmentCategory


class MockAttachment:
    """Mock Attachment model."""

    def __init__(
        self,
        attachment_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        entity_type: str = "SUPPLIER_INVOICE",
        entity_id: uuid.UUID = None,
        file_name: str = "test_document.pdf",
        file_path: str = "org123/supplier_invoice/abc123.pdf",
        file_size: int = 1024,
        content_type: str = "application/pdf",
        category: AttachmentCategory = AttachmentCategory.INVOICE,
        description: Optional[str] = None,
        storage_provider: str = "LOCAL",
        checksum: Optional[str] = None,
        uploaded_by: uuid.UUID = None,
        uploaded_at: datetime = None,
        created_at: datetime = None,
    ):
        self.attachment_id = attachment_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.entity_type = entity_type
        self.entity_id = entity_id or uuid.uuid4()
        self.file_name = file_name
        self.file_path = file_path
        self.file_size = file_size
        self.content_type = content_type
        self.category = category
        self.description = description
        self.storage_provider = storage_provider
        self.checksum = checksum or "abc123def456"
        self.uploaded_by = uploaded_by or uuid.uuid4()
        self.uploaded_at = uploaded_at or datetime.now(timezone.utc)
        self.created_at = created_at or datetime.now(timezone.utc)


@pytest.fixture
def organization_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    """Generate a test user ID."""
    return uuid.uuid4()


@pytest.fixture
def entity_id() -> uuid.UUID:
    """Generate a test entity ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    session = MagicMock()
    session.query = MagicMock(return_value=session)
    session.filter = MagicMock(return_value=session)
    session.first = MagicMock(return_value=None)
    session.all = MagicMock(return_value=[])
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.get = MagicMock(return_value=None)
    session.execute = MagicMock()
    session.scalar = MagicMock(return_value=0)
    return session


@pytest.fixture
def mock_attachment(organization_id, entity_id, user_id) -> MockAttachment:
    """Create a mock attachment."""
    return MockAttachment(
        organization_id=organization_id,
        entity_id=entity_id,
        uploaded_by=user_id,
    )


@pytest.fixture
def mock_file_content():
    """Create mock file content."""
    from io import BytesIO
    content = b"Test file content for attachment"
    return BytesIO(content)
