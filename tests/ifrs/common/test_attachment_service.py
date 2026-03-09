"""
Tests for AttachmentService.
"""

import os
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.common.attachment import AttachmentCategory
from app.services.file_upload import (
    FileUploadError,
    get_finance_attachment_upload,
)
from app.services.file_upload import (
    coerce_uuid as _coerce_uuid,
)
from app.services.file_upload import (
    compute_checksum_from_file as _compute_checksum,
)
from app.services.file_upload import (
    format_file_size as _format_file_size,
)
from app.services.finance.common.attachment import (
    AttachmentInput,
    AttachmentService,
    AttachmentView,
)
from tests.ifrs.common.conftest import MockAttachment

# Derive allowed types and max size from the canonical upload config
_finance_upload = get_finance_attachment_upload()
ALLOWED_CONTENT_TYPES = _finance_upload.config.allowed_content_types
MAX_FILE_SIZE = _finance_upload.config.max_size_bytes


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def user_id():
    """Create test user ID."""
    return uuid4()


@pytest.fixture
def entity_id():
    """Create test entity ID."""
    return uuid4()


@pytest.fixture
def sample_attachment_input(entity_id):
    """Create sample attachment input."""
    return AttachmentInput(
        entity_type="SUPPLIER_INVOICE",
        entity_id=str(entity_id),
        file_name="invoice_scan.pdf",
        content_type="application/pdf",
        category=AttachmentCategory.INVOICE,
        description="Scanned invoice document",
    )


@pytest.fixture
def mock_file_content():
    """Create mock file content."""
    content = b"Test file content for attachment testing"
    return BytesIO(content)


class TestCoerceUuid:
    """Tests for _coerce_uuid helper."""

    def test_coerce_uuid_from_string(self):
        """Test converting string to UUID."""
        uuid_str = "12345678-1234-5678-1234-567812345678"
        result = _coerce_uuid(uuid_str)
        assert str(result) == uuid_str

    def test_coerce_uuid_passthrough(self):
        """Test that UUID passes through unchanged."""
        original = uuid4()
        result = _coerce_uuid(original)
        assert result == original

    def test_coerce_uuid_invalid_raises(self):
        """Test that invalid UUID raises ValueError."""
        with pytest.raises(ValueError):
            _coerce_uuid("not-a-uuid")


class TestFormatFileSize:
    """Tests for _format_file_size helper."""

    def test_format_bytes(self):
        """Test formatting small file sizes in bytes."""
        assert _format_file_size(500) == "500 B"
        assert _format_file_size(0) == "0 B"

    def test_format_kilobytes(self):
        """Test formatting file sizes in KB."""
        assert _format_file_size(1024) == "1.0 KB"
        assert _format_file_size(2048) == "2.0 KB"
        assert _format_file_size(1536) == "1.5 KB"

    def test_format_megabytes(self):
        """Test formatting file sizes in MB."""
        assert _format_file_size(1024 * 1024) == "1.0 MB"
        assert _format_file_size(5 * 1024 * 1024) == "5.0 MB"
        assert _format_file_size(int(2.5 * 1024 * 1024)) == "2.5 MB"


class TestComputeChecksum:
    """Tests for _compute_checksum helper."""

    def test_compute_checksum_returns_hex(self):
        """Test that checksum returns hex string."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            f.flush()
            try:
                result = _compute_checksum(f.name)
                assert isinstance(result, str)
                assert len(result) == 64  # SHA-256 hex length
            finally:
                os.unlink(f.name)

    def test_compute_checksum_deterministic(self):
        """Test that same content produces same checksum."""
        with tempfile.NamedTemporaryFile(delete=False) as f1:
            f1.write(b"identical content")
            f1.flush()
            with tempfile.NamedTemporaryFile(delete=False) as f2:
                f2.write(b"identical content")
                f2.flush()
                try:
                    result1 = _compute_checksum(f1.name)
                    result2 = _compute_checksum(f2.name)
                    assert result1 == result2
                finally:
                    os.unlink(f1.name)
                    os.unlink(f2.name)


class TestGetUploadPath:
    """Tests for get_upload_path method."""

    def test_get_upload_path_creates_directory(self, org_id):
        """Test that upload path creates directory structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_svc = MagicMock()
            mock_svc.base_path = Path(tmpdir)
            with patch(
                "app.services.finance.common.attachment._upload_service",
                return_value=mock_svc,
            ):
                path = AttachmentService.get_upload_path(org_id, "SUPPLIER_INVOICE")
                assert path.exists()
                assert str(org_id) in str(path)
                assert "supplier_invoice" in str(path)

    def test_get_upload_path_lowercases_entity_type(self, org_id):
        """Test that entity type is lowercased in path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_svc = MagicMock()
            mock_svc.base_path = Path(tmpdir)
            with patch(
                "app.services.finance.common.attachment._upload_service",
                return_value=mock_svc,
            ):
                path = AttachmentService.get_upload_path(org_id, "PURCHASE_ORDER")
                assert "purchase_order" in str(path)


class TestSaveFile:
    """Tests for save_file method."""

    def test_save_file_success(self, mock_db, org_id, user_id, sample_attachment_input):
        """Test successful file save."""
        file_content = BytesIO(b"PDF file content here")

        mock_upload_result = MagicMock()
        mock_upload_result.relative_path = "org/invoice/file.pdf"
        mock_upload_result.file_size = 21
        mock_upload_result.checksum = "abc123"

        mock_svc = MagicMock()
        mock_svc.save.return_value = mock_upload_result

        with (
            patch(
                "app.services.finance.common.attachment._upload_service",
                return_value=mock_svc,
            ),
            patch(
                "app.services.finance.common.attachment.Attachment"
            ) as MockAttachmentClass,
        ):
            mock_attachment = MockAttachment(
                organization_id=org_id,
                entity_id=uuid4(),
                uploaded_by=user_id,
            )
            MockAttachmentClass.return_value = mock_attachment

            AttachmentService.save_file(
                mock_db, org_id, sample_attachment_input, file_content, user_id
            )

            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()
            mock_db.refresh.assert_called_once()

    def test_save_file_invalid_content_type_fails(
        self, mock_db, org_id, user_id, entity_id
    ):
        """Test that invalid content type is rejected."""
        invalid_input = AttachmentInput(
            entity_type="SUPPLIER_INVOICE",
            entity_id=str(entity_id),
            file_name="malware.exe",
            content_type="application/x-msdownload",
            category=AttachmentCategory.OTHER,
        )
        file_content = BytesIO(b"binary content")

        with pytest.raises(ValueError) as exc:
            AttachmentService.save_file(
                mock_db, org_id, invalid_input, file_content, user_id
            )

        assert "not allowed" in str(exc.value)

    def test_save_file_exceeds_max_size_fails(
        self, mock_db, org_id, user_id, entity_id
    ):
        """Test that files exceeding max size are rejected."""
        valid_input = AttachmentInput(
            entity_type="SUPPLIER_INVOICE",
            entity_id=str(entity_id),
            file_name="large_file.pdf",
            content_type="application/pdf",
            category=AttachmentCategory.INVOICE,
        )
        # Create file larger than max size
        large_content = BytesIO(b"x" * (MAX_FILE_SIZE + 1000))

        mock_svc = MagicMock()
        mock_svc.save.side_effect = FileUploadError("File too large")

        with patch(
            "app.services.finance.common.attachment._upload_service",
            return_value=mock_svc,
        ):
            with pytest.raises(ValueError) as exc:
                AttachmentService.save_file(
                    mock_db, org_id, valid_input, large_content, user_id
                )

            assert "too large" in str(exc.value).lower()


class TestGetAttachment:
    """Tests for get method."""

    def test_get_existing_attachment(self, mock_db):
        """Test getting existing attachment."""
        attachment = MockAttachment()
        mock_db.get.return_value = attachment

        with patch("app.services.finance.common.attachment.Attachment"):
            result = AttachmentService.get(
                mock_db, attachment.organization_id, str(attachment.attachment_id)
            )

        assert result == attachment

    def test_get_nonexistent_attachment_returns_none(self, mock_db, org_id):
        """Test getting non-existent attachment returns None."""
        mock_db.get.return_value = None

        with patch("app.services.finance.common.attachment.Attachment"):
            result = AttachmentService.get(mock_db, org_id, str(uuid4()))

        assert result is None


class TestGetFilePath:
    """Tests for get_file_path method."""

    def test_get_file_path_returns_full_path(self):
        """Test that full path is returned."""
        attachment = MockAttachment(file_path="org123/invoice/file.pdf")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the expected file so resolve_safe_path succeeds
            target = Path(tmpdir) / "org123" / "invoice"
            target.mkdir(parents=True, exist_ok=True)
            (target / "file.pdf").touch()

            mock_svc = MagicMock()
            mock_svc.base_path = Path(tmpdir)
            with patch(
                "app.services.finance.common.attachment._upload_service",
                return_value=mock_svc,
            ):
                result = AttachmentService.get_file_path(attachment)

            assert str(result) == str(Path(tmpdir) / "org123/invoice/file.pdf")


class TestListForEntity:
    """Tests for list_for_entity method."""

    def test_list_for_entity_returns_attachments(self, mock_db, org_id, entity_id):
        """Test listing attachments for an entity."""
        attachments = [
            MockAttachment(organization_id=org_id, entity_id=entity_id),
            MockAttachment(organization_id=org_id, entity_id=entity_id),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = attachments
        mock_db.scalars.return_value.all.return_value = attachments

        result = AttachmentService.list_for_entity(
            mock_db, org_id, "SUPPLIER_INVOICE", entity_id
        )

        assert result == attachments
        assert len(result) == 2

    def test_list_for_entity_empty(self, mock_db, org_id, entity_id):
        """Test listing returns empty for entity with no attachments."""
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = []
        mock_db.scalars.return_value.all.return_value = []

        result = AttachmentService.list_for_entity(
            mock_db, org_id, "SUPPLIER_INVOICE", entity_id
        )

        assert result == []


class TestDeleteAttachment:
    """Tests for delete method."""

    def test_delete_existing_attachment(self, mock_db, org_id):
        """Test deleting existing attachment."""
        attachment = MockAttachment(organization_id=org_id)

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = attachment
        mock_db.scalars.return_value.first.return_value = attachment

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            f.flush()

            with (
                patch.object(
                    AttachmentService, "get_file_path", return_value=Path(f.name)
                ),
            ):
                result = AttachmentService.delete(
                    mock_db, str(attachment.attachment_id), org_id
                )

            assert result is True
            mock_db.delete.assert_called_once_with(attachment)
            mock_db.commit.assert_called_once()

    def test_delete_nonexistent_attachment_returns_false(self, mock_db, org_id):
        """Test deleting non-existent attachment returns False."""
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None
        mock_db.scalars.return_value.first.return_value = None

        result = AttachmentService.delete(mock_db, str(uuid4()), org_id)

        assert result is False
        mock_db.delete.assert_not_called()


class TestCountForEntity:
    """Tests for count_for_entity method."""

    def test_count_for_entity_returns_count(self, mock_db, org_id, entity_id):
        """Test counting attachments for an entity."""
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.scalar.return_value = 5
        mock_db.scalar.return_value = 5

        result = AttachmentService.count_for_entity(
            mock_db, org_id, "SUPPLIER_INVOICE", entity_id
        )

        assert result == 5

    def test_count_for_entity_zero_when_none(self, mock_db, org_id, entity_id):
        """Test counting returns zero when no attachments."""
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.scalar.return_value = None
        mock_db.scalar.return_value = None

        result = AttachmentService.count_for_entity(
            mock_db, org_id, "SUPPLIER_INVOICE", entity_id
        )

        assert result == 0


class TestToView:
    """Tests for to_view method."""

    def test_to_view_creates_view_model(self):
        """Test converting attachment to view model."""
        attachment = MockAttachment(
            file_name="document.pdf",
            file_size=2048,
            content_type="application/pdf",
            category=AttachmentCategory.INVOICE,
            description="Test description",
        )

        result = AttachmentService.to_view(attachment)

        assert isinstance(result, AttachmentView)
        assert result.file_name == "document.pdf"
        assert result.file_size == 2048
        assert result.content_type == "application/pdf"
        assert result.category == "INVOICE"
        assert result.description == "Test description"
        assert str(attachment.attachment_id) in result.download_url

    def test_to_view_custom_base_url(self):
        """Test to_view with custom base URL."""
        attachment = MockAttachment()

        result = AttachmentService.to_view(attachment, base_url="/ar")

        assert result.download_url.startswith("/ar/attachments/")


class TestAllowedContentTypes:
    """Tests for content type configuration."""

    def test_pdf_allowed(self):
        """Test that PDF is allowed."""
        assert "application/pdf" in ALLOWED_CONTENT_TYPES

    def test_images_allowed(self):
        """Test that common image types are allowed."""
        assert "image/jpeg" in ALLOWED_CONTENT_TYPES
        assert "image/png" in ALLOWED_CONTENT_TYPES
        assert "image/gif" in ALLOWED_CONTENT_TYPES
        assert "image/webp" in ALLOWED_CONTENT_TYPES

    def test_office_docs_allowed(self):
        """Test that Office documents are allowed."""
        assert "application/msword" in ALLOWED_CONTENT_TYPES
        assert (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            in ALLOWED_CONTENT_TYPES
        )
        assert "application/vnd.ms-excel" in ALLOWED_CONTENT_TYPES
        assert (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            in ALLOWED_CONTENT_TYPES
        )

    def test_text_files_allowed(self):
        """Test that text files are allowed."""
        assert "text/plain" in ALLOWED_CONTENT_TYPES
        assert "text/csv" in ALLOWED_CONTENT_TYPES

    def test_executable_not_allowed(self):
        """Test that executables are not allowed."""
        assert "application/x-msdownload" not in ALLOWED_CONTENT_TYPES
        assert "application/x-executable" not in ALLOWED_CONTENT_TYPES


class TestAttachmentInput:
    """Tests for AttachmentInput dataclass."""

    def test_attachment_input_defaults(self, entity_id):
        """Test AttachmentInput default values."""
        input_data = AttachmentInput(
            entity_type="SUPPLIER_INVOICE",
            entity_id=str(entity_id),
            file_name="test.pdf",
            content_type="application/pdf",
        )

        assert input_data.category == AttachmentCategory.OTHER
        assert input_data.description is None

    def test_attachment_input_with_all_fields(self, entity_id):
        """Test AttachmentInput with all fields."""
        input_data = AttachmentInput(
            entity_type="SUPPLIER_INVOICE",
            entity_id=str(entity_id),
            file_name="invoice.pdf",
            content_type="application/pdf",
            category=AttachmentCategory.INVOICE,
            description="Monthly invoice scan",
        )

        assert input_data.entity_type == "SUPPLIER_INVOICE"
        assert input_data.file_name == "invoice.pdf"
        assert input_data.category == AttachmentCategory.INVOICE
        assert input_data.description == "Monthly invoice scan"
