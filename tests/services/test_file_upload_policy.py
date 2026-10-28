"""Staging-policy derivation for legacy upload configs.

Regression coverage for the 2026-10-27 backfill/production failure:
configs that declare content types only (finance attachments,
discipline documents) produced an empty ``allowed_extensions``, which
``dotmac_files.FilePolicy`` refuses — every staged upload in those
families raised ``ValueError``.
"""

from __future__ import annotations

from app.services.file_upload import (
    FileUploadConfig,
    get_finance_attachment_upload,
    policy_from_upload_config,
    resolve_declared_media_type,
)


def _config(**overrides) -> FileUploadConfig:
    values = {
        "base_dir": "uploads/test",
        "allowed_content_types": frozenset({"application/pdf", "image/jpeg"}),
        "max_size_bytes": 1024,
    }
    values.update(overrides)
    return FileUploadConfig(**values)


def test_policy_derives_extensions_from_content_types_when_undeclared():
    policy = policy_from_upload_config(_config())

    assert policy.allowed_extensions == frozenset({".pdf", ".jpg", ".jpeg"})
    assert policy.allowed_media_types == frozenset({"application/pdf", "image/jpeg"})
    assert policy.max_bytes == 1024


def test_policy_keeps_explicit_extensions():
    policy = policy_from_upload_config(_config(allowed_extensions=frozenset({".pdf"})))

    assert policy.allowed_extensions == frozenset({".pdf"})


def test_finance_attachment_config_yields_a_usable_policy():
    # The production finance config declares content types only.
    policy = policy_from_upload_config(get_finance_attachment_upload().config)

    assert ".pdf" in policy.allowed_extensions
    assert ".jpg" in policy.allowed_extensions
    assert ".docx" in policy.allowed_extensions
    assert policy.allowed_media_types


def test_resolve_declared_media_type_prefers_explicit_content_type():
    assert (
        resolve_declared_media_type("scan.pdf", "Image/JPEG; charset=binary")
        == "image/jpeg"
    )


def test_resolve_declared_media_type_guesses_from_filename():
    assert resolve_declared_media_type("resume.PDF", None) == "application/pdf"
    assert resolve_declared_media_type("photo.jpg", "") == "image/jpeg"


def test_resolve_declared_media_type_knows_ooxml_without_mime_db():
    # Slim images have no /etc/mime.types, so mimetypes returns None for
    # .docx/.xlsx there; the project's own map must answer first.
    assert (
        resolve_declared_media_type("cv.docx", None)
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert (
        resolve_declared_media_type("sheet.xlsx", "")
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_resolve_declared_media_type_unknown_stays_empty():
    assert resolve_declared_media_type("blob", None) == ""
    assert resolve_declared_media_type(None, None) == ""
