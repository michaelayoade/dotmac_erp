from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

DEFAULT_UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB


def get_env_max_bytes(env_name: str, default_bytes: int) -> int:
    raw = os.getenv(env_name, str(default_bytes))
    try:
        value = int(raw)
    except ValueError:
        return default_bytes
    return value if value > 0 else default_bytes


async def read_upload_bytes(
    file: UploadFile,
    max_bytes: int,
    *,
    error_detail: str,
) -> bytes:
    size = 0
    chunks: list[bytes] = []
    while True:
        chunk = await file.read(DEFAULT_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status_code=400, detail=error_detail)
        chunks.append(chunk)
        if len(chunk) < DEFAULT_UPLOAD_CHUNK_SIZE:
            break
    return b"".join(chunks)


async def write_upload_to_path(
    file: UploadFile,
    path: Path,
    max_bytes: int,
    *,
    error_detail: str,
) -> int:
    size = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "wb") as handle:
            while True:
                chunk = await file.read(DEFAULT_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=400, detail=error_detail)
                handle.write(chunk)
                if len(chunk) < DEFAULT_UPLOAD_CHUNK_SIZE:
                    break
    except HTTPException:
        if path.exists():
            path.unlink(missing_ok=True)
        raise
    return size


async def write_upload_to_temp(
    file: UploadFile,
    *,
    suffix: str,
    max_bytes: int,
    error_detail: str,
) -> str:
    size = 0
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            while True:
                chunk = await file.read(DEFAULT_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=400, detail=error_detail)
                tmp.write(chunk)
                if len(chunk) < DEFAULT_UPLOAD_CHUNK_SIZE:
                    break
    except HTTPException:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        raise
    return tmp_path
