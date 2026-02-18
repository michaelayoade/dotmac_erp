"""
Tests for the S3StorageService (app/services/storage.py).

Uses mocked minio client — no real S3/MinIO connection needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import storage as storage_mod
from app.services.storage import S3StorageService


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset module-level singleton state between tests."""
    storage_mod._client = None
    storage_mod._bucket_ensured = False
    yield
    storage_mod._client = None
    storage_mod._bucket_ensured = False


@pytest.fixture
def mock_minio_client():
    """Provide a MagicMock posing as a minio.Minio client."""
    client = MagicMock()
    # bucket_exists returns True by default (bucket already exists)
    client.bucket_exists.return_value = True
    return client


@pytest.fixture
def svc(mock_minio_client):
    """Return an S3StorageService with mocked minio client."""
    with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
        service = S3StorageService()
    return service


class TestUpload:
    def test_upload_puts_object(self, svc, mock_minio_client):
        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            svc.upload("avatars/test.jpg", b"fake-image", "image/jpeg")

        mock_minio_client.put_object.assert_called_once()
        call_args = mock_minio_client.put_object.call_args
        # minio put_object(bucket, key, data_stream, length=, content_type=)
        assert call_args[0][1] == "avatars/test.jpg"  # key (positional arg 1)
        assert call_args.kwargs["content_type"] == "image/jpeg"
        assert call_args.kwargs["length"] == len(b"fake-image")

    def test_upload_without_content_type_uses_default(self, svc, mock_minio_client):
        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            svc.upload("docs/file.bin", b"bytes")

        call_args = mock_minio_client.put_object.call_args
        assert call_args.kwargs["content_type"] == "application/octet-stream"


class TestDownload:
    def test_download_returns_bytes(self, svc, mock_minio_client):
        response = MagicMock()
        response.read.return_value = b"file-contents"
        mock_minio_client.get_object.return_value = response

        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            data = svc.download("attachments/abc.pdf")

        assert data == b"file-contents"
        mock_minio_client.get_object.assert_called_once()
        response.close.assert_called_once()
        response.release_conn.assert_called_once()


class TestStream:
    def test_stream_yields_chunks(self, svc, mock_minio_client):
        # stat_object returns an object with content_type and size
        stat = MagicMock()
        stat.content_type = "application/pdf"
        stat.size = 12
        mock_minio_client.stat_object.return_value = stat

        # get_object returns a urllib3-like response
        response = MagicMock()
        response.stream.return_value = iter([b"chunk1", b"chunk2"])
        mock_minio_client.get_object.return_value = response

        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            chunks_iter, ct, cl = svc.stream("docs/report.pdf")
            chunks = list(chunks_iter)

        assert chunks == [b"chunk1", b"chunk2"]
        assert ct == "application/pdf"
        assert cl == 12
        response.close.assert_called_once()
        response.release_conn.assert_called_once()


class TestDelete:
    def test_delete_calls_remove_object(self, svc, mock_minio_client):
        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            svc.delete("avatars/old.jpg")

        mock_minio_client.remove_object.assert_called_once()
        call_args = mock_minio_client.remove_object.call_args
        assert call_args[0][1] == "avatars/old.jpg"  # key (positional arg 1)


class TestExists:
    def test_exists_returns_true(self, svc, mock_minio_client):
        mock_minio_client.stat_object.return_value = MagicMock()

        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            assert svc.exists("avatars/photo.jpg") is True

    def test_exists_returns_false_on_missing(self, svc, mock_minio_client):
        from minio.error import S3Error

        mock_minio_client.stat_object.side_effect = S3Error(
            "NoSuchKey", "Object does not exist", "", "", "", ""
        )

        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            assert svc.exists("avatars/missing.jpg") is False


class TestEnsureBucket:
    def test_creates_bucket_when_missing(self, mock_minio_client):
        """Should create bucket when bucket_exists returns False."""
        mock_minio_client.bucket_exists.return_value = False

        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            S3StorageService()

        mock_minio_client.make_bucket.assert_called_once()

    def test_skips_create_when_exists(self, mock_minio_client):
        """Should not create bucket when it already exists."""
        mock_minio_client.bucket_exists.return_value = True

        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            S3StorageService()

        mock_minio_client.make_bucket.assert_not_called()

    def test_ensure_bucket_called_once(self, mock_minio_client):
        """Second instantiation should skip the bucket_exists check."""
        mock_minio_client.bucket_exists.return_value = True

        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            S3StorageService()
            mock_minio_client.bucket_exists.reset_mock()
            S3StorageService()

        mock_minio_client.bucket_exists.assert_not_called()
