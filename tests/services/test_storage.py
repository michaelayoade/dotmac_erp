"""
Tests for the S3StorageService (app/services/storage.py).

Uses mocked minio client — no real S3/MinIO connection needed.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from dotmac_files import StorageConflict, StorageUnavailable, list_objects
from dotmac_kernel.cache import PlatformScope

from app.services import storage as storage_mod
from app.services.storage import (
    DotmacFilesS3Provider,
    S3StorageService,
    StorageReadUnavailable,
)


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
        # `minio` is an optional dependency in this environment; use the service's
        # resolved S3 error type instead of importing it directly.
        S3Error = svc._s3_error

        mock_minio_client.stat_object.side_effect = S3Error(
            MagicMock(), "NoSuchKey", "Object does not exist", "", "", ""
        )

        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            assert svc.exists("avatars/missing.jpg") is False

    def test_exists_retries_provider_failure_without_turning_it_into_absence(
        self, svc, mock_minio_client
    ):
        S3Error = svc._s3_error
        failure = S3Error(
            MagicMock(),
            "ServiceUnavailable",
            "Provider is unavailable",
            "",
            "",
            "",
        )
        mock_minio_client.stat_object.side_effect = failure

        with (
            patch.object(storage_mod, "_get_client", return_value=mock_minio_client),
            pytest.raises(StorageReadUnavailable) as raised,
        ):
            svc.exists("avatars/photo.jpg")

        assert str(raised.value) == "Object storage is temporarily unavailable"
        assert mock_minio_client.stat_object.call_count == 2

    def test_exists_retries_a_transport_failure_once(self, svc, mock_minio_client):
        mock_minio_client.stat_object.side_effect = [
            ConnectionError("connection closed"),
            MagicMock(),
        ]

        with patch.object(storage_mod, "_get_client", return_value=mock_minio_client):
            assert svc.exists("avatars/photo.jpg") is True

        assert mock_minio_client.stat_object.call_count == 2


class TestEnsureBucket:
    def test_read_provider_lists_without_bucket_creation(self, mock_minio_client):
        """The dry-run construction has no bucket-existence/write path."""
        observed_at = datetime(2026, 9, 27, tzinfo=UTC)
        mock_minio_client.list_objects.return_value = [
            SimpleNamespace(
                object_name="platform/files/opaque-id",
                size=3,
                last_modified=observed_at,
            )
        ]
        mock_minio_client.bucket_exists.return_value = False

        with (
            patch.object(storage_mod, "_get_client", return_value=mock_minio_client),
            patch.object(
                storage_mod,
                "_ensure_bucket",
                side_effect=AssertionError("dry-run reached bucket creation"),
            ) as ensure_bucket,
        ):
            provider = storage_mod.get_dotmac_files_read_provider()
            observations = list_objects(provider, scope=PlatformScope())

        assert len(observations) == 1
        assert observations[0].key == "platform/files/opaque-id"
        mock_minio_client.list_objects.assert_called_once_with(
            storage_mod.settings.s3_bucket_name,
            prefix="platform/files/",
            recursive=True,
        )
        ensure_bucket.assert_not_called()
        mock_minio_client.bucket_exists.assert_not_called()
        mock_minio_client.make_bucket.assert_not_called()

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


class _ProviderS3Error(Exception):
    def __init__(self, code: str, detail: str = "provider detail") -> None:
        self.code = code
        super().__init__(detail)


class _ProviderStorage:
    def __init__(self) -> None:
        self._client = MagicMock()
        self._bucket = "erp-files"
        self._s3_error = _ProviderS3Error


class TestDotmacFilesProvider:
    def test_put_writes_checksum_metadata_after_proving_key_absent(self) -> None:
        storage = _ProviderStorage()
        storage._client.stat_object.side_effect = _ProviderS3Error("NoSuchKey")
        provider = DotmacFilesS3Provider(storage)  # type: ignore[arg-type]

        provider.put(
            "tenants/t/files/id",
            io.BytesIO(b"csv"),
            content_type="text/csv",
            size_bytes=3,
            checksum_sha256="sha256:digest",
        )

        storage._client.put_object.assert_called_once()
        assert storage._client.put_object.call_args.kwargs["metadata"] == {
            "checksum-sha256": "sha256:digest"
        }

    def test_put_accepts_only_an_identical_immutable_replay(self) -> None:
        storage = _ProviderStorage()
        storage._client.stat_object.return_value = SimpleNamespace(
            size=3,
            metadata={"x-amz-meta-checksum-sha256": "sha256:digest"},
        )
        provider = DotmacFilesS3Provider(storage)  # type: ignore[arg-type]

        provider.put(
            "tenants/t/files/id",
            io.BytesIO(b"csv"),
            content_type="text/csv",
            size_bytes=3,
            checksum_sha256="sha256:digest",
        )
        storage._client.put_object.assert_not_called()

        with pytest.raises(StorageConflict):
            provider.put(
                "tenants/t/files/id",
                io.BytesIO(b"other"),
                content_type="text/csv",
                size_bytes=5,
                checksum_sha256="sha256:other",
            )

    def test_open_releases_the_provider_connection(self) -> None:
        storage = _ProviderStorage()
        response = MagicMock()
        response.read.side_effect = [b"csv", b""]
        storage._client.get_object.return_value = response
        provider = DotmacFilesS3Provider(storage)  # type: ignore[arg-type]

        with provider.open("tenants/t/files/id") as opened:
            assert opened.read() == b"csv"

        response.close.assert_called_once()
        response.release_conn.assert_called_once()

    def test_provider_errors_never_expose_the_sdk_detail(self) -> None:
        storage = _ProviderStorage()
        storage._client.get_object.side_effect = _ProviderS3Error(
            "ServiceUnavailable", "credential=do-not-copy"
        )
        provider = DotmacFilesS3Provider(storage)  # type: ignore[arg-type]

        with pytest.raises(StorageUnavailable) as raised:
            provider.open("tenants/t/files/id")

        assert str(raised.value) == "stored object is unavailable"
        assert "do-not-copy" not in repr(raised.value)


class TestDeleteReviewedFileOrphan:
    """``delete_reviewed_file_orphan`` is the sole single-key deletion seam."""

    def test_refuses_when_the_live_provider_code_does_not_match_the_plan(
        self, monkeypatch
    ) -> None:
        from dotmac_files import ProviderMismatch

        class _OtherProvider:
            code = "other_code"

        monkeypatch.setattr(
            storage_mod, "get_dotmac_files_provider", lambda: _OtherProvider()
        )

        with pytest.raises(ProviderMismatch):
            storage_mod.delete_reviewed_file_orphan(
                scope=PlatformScope(),
                key="platform/files/x",
                expected_provider_code="erp_s3",
                older_than=datetime(2026, 9, 20, tzinfo=UTC),
                is_referenced=lambda _key: False,
            )

    def test_deletes_through_the_module_primitive_when_the_code_matches(
        self, monkeypatch
    ) -> None:
        from dotmac_files import OrphanRecheckResult

        calls: list[tuple[object, object, str, str, datetime, object]] = []

        def fake_recheck(
            provider, *, scope, key, expected_provider_code, older_than, is_referenced
        ):
            calls.append(
                (
                    provider,
                    scope,
                    key,
                    expected_provider_code,
                    older_than,
                    is_referenced,
                )
            )
            return OrphanRecheckResult("deleted")

        monkeypatch.setattr(
            "dotmac_files.recheck_and_delete_orphan", fake_recheck, raising=False
        )
        provider = DotmacFilesS3Provider(_ProviderStorage())  # type: ignore[arg-type]
        monkeypatch.setattr(storage_mod, "get_dotmac_files_provider", lambda: provider)

        scope = PlatformScope()
        older_than = datetime(2026, 9, 20, tzinfo=UTC)

        def reference_check(_key: str) -> bool:
            return False

        result = storage_mod.delete_reviewed_file_orphan(
            scope=scope,
            key="platform/files/x",
            expected_provider_code=provider.code,
            older_than=older_than,
            is_referenced=reference_check,
        )

        assert result == OrphanRecheckResult("deleted")
        assert calls == [
            (
                provider,
                scope,
                "platform/files/x",
                provider.code,
                older_than,
                reference_check,
            )
        ]
