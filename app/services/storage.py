"""
S3-compatible object storage service (MinIO).

Wraps the ``minio`` Python SDK to provide upload, download, delete,
and streaming for file objects.  The client is lazily created as a
module-level singleton so every caller shares one connection pool.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from app.config import settings

if TYPE_CHECKING:
    from minio import Minio

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_client: Minio | None = None
_bucket_ensured: bool = False


def _get_client() -> Minio:
    """Return the shared Minio client, creating it on first call."""
    global _client
    if _client is None:
        from minio import Minio

        parsed = urlparse(settings.s3_endpoint_url)
        endpoint = parsed.netloc or parsed.path  # host:port
        secure = parsed.scheme == "https"

        _client = Minio(
            endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
            secure=secure,
        )
        logger.info(
            "MinIO client created (endpoint=%s, bucket=%s, secure=%s)",
            endpoint,
            settings.s3_bucket_name,
            secure,
        )
    return _client


def _ensure_bucket() -> None:
    """Create the bucket if it does not already exist (idempotent)."""
    global _bucket_ensured
    if _bucket_ensured:
        return

    client = _get_client()
    bucket = settings.s3_bucket_name
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info("Created MinIO bucket '%s'", bucket)
        else:
            logger.debug("MinIO bucket '%s' already exists", bucket)
    except Exception:
        # TOCTOU: another process may have created the bucket between
        # bucket_exists() and make_bucket(). Re-check rather than fail.
        if not client.bucket_exists(bucket):
            raise
        logger.debug("MinIO bucket '%s' created concurrently", bucket)
    _bucket_ensured = True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class S3StorageService:
    """
    Thin wrapper around the ``minio`` Python SDK.

    All methods are synchronous.  For async contexts the caller should
    run them in a thread pool if needed.
    """

    def __init__(self) -> None:
        _ensure_bucket()

    @property
    def _client(self) -> Minio:
        return _get_client()

    @property
    def _bucket(self) -> str:
        return settings.s3_bucket_name

    # -- Upload -------------------------------------------------------------

    def upload(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        """Upload bytes to MinIO under *key*."""
        self._client.put_object(
            self._bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
        logger.info("S3 upload: %s (%d bytes)", key, len(data))

    # -- Download -----------------------------------------------------------

    def download(self, key: str) -> bytes:
        """Download an object and return its bytes."""
        response = None
        try:
            response = self._client.get_object(self._bucket, key)
            data: bytes = response.read()
            return data
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    # -- Stream -------------------------------------------------------------

    def stream(
        self,
        key: str,
        chunk_size: int = 64 * 1024,
    ) -> tuple[Iterator[bytes], str | None, int | None]:
        """
        Return a streaming iterator, content-type, and content-length.

        Usage with FastAPI::

            chunks, ct, cl = storage.stream("attachments/abc.pdf")
            return StreamingResponse(chunks, media_type=ct,
                                     headers={"Content-Length": str(cl)})
        """
        # stat to get metadata without downloading body
        stat = self._client.stat_object(self._bucket, key)
        content_type = stat.content_type
        content_length = stat.size

        response = self._client.get_object(self._bucket, key)

        def _iter() -> Iterator[bytes]:
            try:
                yield from response.stream(chunk_size)
            finally:
                response.close()
                response.release_conn()

        return _iter(), content_type, content_length

    # -- Delete -------------------------------------------------------------

    def delete(self, key: str) -> None:
        """Delete an object.  No error if the key does not exist."""
        self._client.remove_object(self._bucket, key)
        logger.info("S3 delete: %s", key)

    # -- Exists -------------------------------------------------------------

    def exists(self, key: str) -> bool:
        """Check whether an object exists."""
        try:
            self._client.stat_object(self._bucket, key)
            return True
        except self._s3_error:
            # minio raises S3Error with code NoSuchKey for missing objects
            return False

    @property
    def _s3_error(self) -> type[Exception]:
        """Lazy import of minio.error.S3Error for exception handling."""
        from minio.error import S3Error

        return S3Error


def get_storage() -> S3StorageService:
    """Factory — returns a ready-to-use storage service."""
    return S3StorageService()
