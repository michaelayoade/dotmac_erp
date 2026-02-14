def test_preview_any_file_xls_without_xlrd_returns_validation_error(
    test_importer, tmp_path
):
    xls_path = tmp_path / "sample.xls"
    xls_path.write_bytes(b"not-a-real-xls-file")

    preview = test_importer.importer.preview_any_file(xls_path)

    assert preview.is_valid is False
    assert preview.validation_errors
    assert "XLS" in preview.validation_errors[0]


def test_import_any_file_xls_without_xlrd_fails_gracefully(test_importer, tmp_path):
    xls_path = tmp_path / "sample.xls"
    xls_path.write_bytes(b"not-a-real-xls-file")

    result = test_importer.importer.import_any_file(xls_path)

    assert result.status.value == "failed"
    assert result.error_count >= 1
    assert any("XLS parsing error" in str(err.message) for err in result.errors)
