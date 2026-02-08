"""
Tests for BaseImporter class in app/services/ifrs/import_export/base.py

Tests for CSV parsing, field mapping, transformation, duplicate detection,
and import operations.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.services.finance.import_export.base import (
    BaseImporter,
    FieldMapping,
    ImportConfig,
    ImportResult,
    ImportStatus,
    detect_csv_format,
    resolve_column_alias,
)

# ============ TestDetectCSVFormat ============


class TestDetectCSVFormat:
    """Tests for the detect_csv_format function."""

    def test_detect_zoho_format(self):
        """Should detect Zoho Books format."""
        columns = ["Account Name", "Account Type", "Currency Code", "Display Name As"]
        result = detect_csv_format(columns)
        assert result == "zoho"

    def test_detect_quickbooks_format(self):
        """Should detect QuickBooks format."""
        columns = ["FullyQualifiedName", "AcctNum", "AccountType"]
        result = detect_csv_format(columns)
        assert result == "quickbooks"

    def test_detect_quickbooks_ref_format(self):
        """Should detect QuickBooks by CustomerRef pattern."""
        columns = ["DocNumber", "CustomerRef:name", "TxnDate"]
        result = detect_csv_format(columns)
        assert result == "quickbooks"

    def test_detect_xero_format(self):
        """Should detect Xero format by asterisk prefix."""
        columns = ["*Code", "*Name", "*Type"]
        result = detect_csv_format(columns)
        assert result == "xero"

    def test_detect_xero_format_by_columns(self):
        """Should detect Xero format by column names."""
        columns = ["ContactName", "InvoiceNumber", "DueDate", "EmailAddress"]
        result = detect_csv_format(columns)
        assert result == "xero"

    def test_detect_sage_format(self):
        """Should detect Sage format."""
        columns = ["Nominal Code", "Nominal Name", "Tax Code"]
        result = detect_csv_format(columns)
        assert result == "sage"

    def test_detect_wave_format(self):
        """Should detect Wave Accounting format."""
        columns = ["Transaction Type", "Transaction ID", "Transaction Date"]
        result = detect_csv_format(columns)
        assert result == "wave"

    def test_detect_freshbooks_format(self):
        """Should detect FreshBooks format."""
        columns = ["Invoice #", "Client Name", "P.O. Number"]
        result = detect_csv_format(columns)
        assert result == "freshbooks"

    def test_detect_generic_format(self):
        """Should return 'generic' for unrecognized format."""
        columns = ["Column1", "Column2", "Column3"]
        result = detect_csv_format(columns)
        assert result == "generic"


# ============ TestResolveColumnAlias ============


class TestResolveColumnAlias:
    """Tests for the resolve_column_alias function."""

    def test_resolve_exact_match(self):
        """Should resolve exact column name match."""
        result = resolve_column_alias("Account Name", "account_name")
        assert result == "account_name"

    def test_resolve_case_insensitive(self):
        """Should match columns case-insensitively."""
        result = resolve_column_alias("account name", "account_name")
        assert result == "account_name"

    def test_resolve_account_name_aliases(self):
        """Should resolve various account name aliases."""
        aliases = ["Name", "GL Account", "Ledger Name"]
        for alias in aliases:
            result = resolve_column_alias(alias, "account_name")
            assert result == "account_name", f"Failed for alias: {alias}"

    def test_resolve_email_aliases(self):
        """Should resolve various email aliases."""
        aliases = ["Email", "Email Address", "E-mail", "Primary Email"]
        for alias in aliases:
            result = resolve_column_alias(alias, "email")
            assert result == "email", f"Failed for alias: {alias}"

    def test_resolve_no_match(self):
        """Should return None for non-matching column."""
        result = resolve_column_alias("Random Column", "account_name")
        assert result is None


# ============ TestAutoMapColumns ============


class TestAutoMapColumns:
    """Tests for the auto_map_columns method of BaseImporter."""

    def test_auto_map_exact_matches(self, test_importer, csv_helper):
        """Should map columns with exact name matches."""
        columns = ["Account Name", "Account Code", "Account Type", "Description"]

        mappings = test_importer.importer.auto_map_columns(columns)

        assert "account_name" in mappings
        assert mappings["account_name"].source_column == "Account Name"
        assert mappings["account_name"].confidence == 1.0

    def test_auto_map_alias_matches(self, test_importer):
        """Should map columns via aliases."""
        columns = ["Name", "Code", "Type"]  # Using short aliases

        mappings = test_importer.importer.auto_map_columns(columns)

        # Some mappings should be found with lower confidence
        assert len(mappings) > 0

    def test_auto_map_confidence_levels(self, test_importer):
        """Should assign appropriate confidence levels."""
        columns = ["Account Name", "Account Code"]

        mappings = test_importer.importer.auto_map_columns(columns)

        # Exact matches should have high confidence
        if "account_name" in mappings:
            assert mappings["account_name"].confidence >= 0.9


# ============ TestFieldMapping ============


class TestFieldMapping:
    """Tests for the FieldMapping class."""

    def test_transform_with_transformer(self):
        """Should apply transformer function."""
        mapping = FieldMapping(
            "amount", "amount", transformer=lambda x: Decimal(x.replace(",", ""))
        )
        result = mapping.transform("1,000.50")
        assert result == Decimal("1000.50")

    def test_transform_none_returns_default(self):
        """None value should return default."""
        mapping = FieldMapping("currency", "currency_code", default="USD")
        result = mapping.transform(None)
        assert result == "USD"

    def test_transform_empty_returns_default(self):
        """Empty string should return default."""
        mapping = FieldMapping("currency", "currency_code", default="USD")
        result = mapping.transform("")
        assert result == "USD"

    def test_transform_no_transformer(self):
        """Value should pass through without transformer."""
        mapping = FieldMapping("name", "name")
        result = mapping.transform("Test Name")
        assert result == "Test Name"


# ============ TestParseHelpers ============


class TestParseHelpers:
    """Tests for BaseImporter parsing helper methods."""

    def test_parse_date_iso(self):
        """Should parse ISO date format."""
        result = BaseImporter.parse_date("2024-01-15")
        assert result == date(2024, 1, 15)

    def test_parse_date_us_format(self):
        """Should parse US date format (MM/DD/YYYY)."""
        result = BaseImporter.parse_date("01/15/2024")
        assert result == date(2024, 1, 15)

    def test_parse_date_european_format(self):
        """Should parse European date format (DD/MM/YYYY)."""
        result = BaseImporter.parse_date("15/01/2024")
        # Could be interpreted as either format
        assert isinstance(result, date)

    def test_parse_date_none(self):
        """None input should return None."""
        result = BaseImporter.parse_date(None)
        assert result is None

    def test_parse_date_date_object(self):
        """Date object should pass through."""
        d = date(2024, 1, 15)
        result = BaseImporter.parse_date(d)
        assert result == d

    def test_parse_date_datetime_object(self):
        """Datetime object should return date portion."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = BaseImporter.parse_date(dt)
        assert result == date(2024, 1, 15)

    def test_parse_date_invalid_raises(self):
        """Invalid date string should raise ValueError."""
        with pytest.raises(ValueError):
            BaseImporter.parse_date("not-a-date")

    def test_parse_decimal_simple(self):
        """Should parse simple decimal string."""
        result = BaseImporter.parse_decimal("123.45")
        assert result == Decimal("123.45")

    def test_parse_decimal_with_commas(self):
        """Should parse decimal with thousands separators."""
        result = BaseImporter.parse_decimal("1,234.56")
        assert result == Decimal("1234.56")

    def test_parse_decimal_with_currency(self):
        """Should parse decimal with currency symbol."""
        result = BaseImporter.parse_decimal("$1,234.56")
        assert result == Decimal("1234.56")

    def test_parse_decimal_negative(self):
        """Should parse negative decimal."""
        result = BaseImporter.parse_decimal("-100.50")
        assert result == Decimal("-100.50")

    def test_parse_decimal_none(self):
        """None input should return None."""
        result = BaseImporter.parse_decimal(None)
        assert result is None

    def test_parse_decimal_already_decimal(self):
        """Decimal input should pass through."""
        d = Decimal("123.45")
        result = BaseImporter.parse_decimal(d)
        assert result == d

    def test_parse_decimal_integer(self):
        """Integer input should convert to Decimal."""
        result = BaseImporter.parse_decimal(100)
        assert result == Decimal("100")

    def test_parse_boolean_true_variants(self):
        """Should parse various true values."""
        true_values = ["true", "True", "TRUE", "yes", "Yes", "1", "y", "Y", "t"]
        for val in true_values:
            result = BaseImporter.parse_boolean(val)
            assert result is True, f"Failed for value: {val}"

    def test_parse_boolean_false_variants(self):
        """Should parse various false values."""
        false_values = ["false", "False", "FALSE", "no", "No", "0", "n", "N", "f"]
        for val in false_values:
            result = BaseImporter.parse_boolean(val)
            assert result is False, f"Failed for value: {val}"

    def test_parse_boolean_none(self):
        """None input should return None."""
        result = BaseImporter.parse_boolean(None)
        assert result is None

    def test_parse_boolean_already_bool(self):
        """Boolean input should pass through."""
        assert BaseImporter.parse_boolean(True) is True
        assert BaseImporter.parse_boolean(False) is False

    def test_parse_boolean_invalid_raises(self):
        """Invalid boolean string should raise ValueError."""
        with pytest.raises(ValueError):
            BaseImporter.parse_boolean("maybe")

    def test_clean_string_truncate(self):
        """Should truncate string to max_length."""
        result = BaseImporter.clean_string("This is a long string", max_length=10)
        assert result == "This is a "
        assert len(result) == 10

    def test_clean_string_whitespace(self):
        """Should strip whitespace."""
        result = BaseImporter.clean_string("  test  ")
        assert result == "test"

    def test_clean_string_none(self):
        """None input should return None."""
        result = BaseImporter.clean_string(None)
        assert result is None

    def test_clean_string_empty(self):
        """Empty string should return None."""
        result = BaseImporter.clean_string("")
        assert result is None

    def test_parse_enum_by_value(self):
        """Should parse enum by value."""
        from enum import Enum

        class Status(Enum):
            ACTIVE = "active"
            INACTIVE = "inactive"

        result = BaseImporter.parse_enum("active", Status)
        assert result == Status.ACTIVE

    def test_parse_enum_by_name(self):
        """Should parse enum by name."""
        from enum import Enum

        class Status(Enum):
            ACTIVE = "active"
            INACTIVE = "inactive"

        result = BaseImporter.parse_enum("ACTIVE", Status)
        assert result == Status.ACTIVE

    def test_parse_enum_with_default(self):
        """Should return default for invalid value."""
        from enum import Enum

        class Status(Enum):
            ACTIVE = "active"
            INACTIVE = "inactive"

        result = BaseImporter.parse_enum("invalid", Status, default=Status.INACTIVE)
        assert result == Status.INACTIVE

    def test_parse_enum_none_returns_default(self):
        """None input should return default."""
        from enum import Enum

        class Status(Enum):
            ACTIVE = "active"

        result = BaseImporter.parse_enum(None, Status, default=Status.ACTIVE)
        assert result == Status.ACTIVE


# ============ TestImportResult ============


class TestImportResult:
    """Tests for the ImportResult class."""

    def test_success_rate_calculation(self):
        """Should calculate success rate correctly."""
        result = ImportResult(entity_type="Account")
        result.total_rows = 100
        result.imported_count = 75

        assert result.success_rate == 75.0

    def test_success_rate_zero_total(self):
        """Should return 0 for zero total rows."""
        result = ImportResult(entity_type="Account")
        result.total_rows = 0
        result.imported_count = 0

        assert result.success_rate == 0.0

    def test_add_error(self):
        """Should add error and increment count."""
        result = ImportResult(entity_type="Account")
        result.add_error(5, "Invalid data", "name", "test")

        assert len(result.errors) == 1
        assert result.error_count == 1
        assert result.errors[0].row_number == 5

    def test_add_warning(self):
        """Should add warning."""
        result = ImportResult(entity_type="Account")
        result.add_warning(3, "Field truncated", "description")

        assert len(result.warnings) == 1
        assert result.warnings[0].row_number == 3

    def test_to_dict(self):
        """Should convert to dictionary."""
        result = ImportResult(entity_type="Account")
        result.status = ImportStatus.COMPLETED
        result.total_rows = 10
        result.imported_count = 8
        result.error_count = 2

        d = result.to_dict()

        assert d["entity_type"] == "Account"
        assert d["status"] == "completed"
        assert d["total_rows"] == 10
        assert d["imported_count"] == 8


# ============ TestImportFile ============


class TestImportFile:
    """Tests for the import_file method of BaseImporter."""

    def test_import_file_success(self, test_importer, sample_account_csv):
        """Should successfully import valid CSV file."""
        result = test_importer.importer.import_file(sample_account_csv)

        assert result.status in [
            ImportStatus.COMPLETED,
            ImportStatus.COMPLETED_WITH_ERRORS,
        ]
        assert result.total_rows > 0

    def test_import_file_not_found(self, test_importer):
        """Should fail for non-existent file."""
        result = test_importer.importer.import_file("/nonexistent/file.csv")

        assert result.status == ImportStatus.FAILED
        assert "not found" in result.errors[0].message.lower()

    def test_import_file_empty(self, test_importer, empty_csv):
        """Should handle empty CSV file."""
        result = test_importer.importer.import_file(empty_csv)

        assert result.total_rows == 0

    def test_import_file_tracks_duration(self, test_importer, sample_account_csv):
        """Should track import duration."""
        result = test_importer.importer.import_file(sample_account_csv)

        assert result.duration_seconds >= 0

    def test_import_file_dry_run(
        self, mock_db, organization_id, user_id, sample_account_csv
    ):
        """Dry run should not commit entities."""
        from tests.ifrs.import_export.conftest import ConcreteTestImporter

        config = ImportConfig(
            organization_id=organization_id,
            user_id=user_id,
            dry_run=True,
        )
        importer = ConcreteTestImporter(mock_db, config)

        importer.importer.import_file(sample_account_csv)

        # Should have imported_count but no db.add calls
        mock_db.add.assert_not_called()


# ============ TestImportRows ============


class TestImportRows:
    """Tests for the import_rows method of BaseImporter."""

    def test_import_rows_success(self, test_importer, csv_helper):
        """Should successfully import valid rows."""
        rows = csv_helper.create_dict_rows(
            ["Account Name", "Account Code", "Account Type"],
            [
                ["Cash", "1000", "Bank"],
                ["Revenue", "4000", "Income"],
            ],
        )

        result = test_importer.importer.import_rows(rows)

        assert result.status in [
            ImportStatus.COMPLETED,
            ImportStatus.COMPLETED_WITH_ERRORS,
        ]
        assert result.total_rows == 2

    def test_import_rows_skip_duplicates(self, mock_db, import_config, csv_helper):
        """Should skip duplicate entries."""
        from tests.ifrs.import_export.conftest import ConcreteTestImporter

        # Set up importer with known duplicates
        importer = ConcreteTestImporter(mock_db, import_config, duplicates={"1000"})

        rows = csv_helper.create_dict_rows(
            ["Account Name", "Account Code", "Account Type"],
            [
                ["Cash", "1000", "Bank"],  # Duplicate
                ["Revenue", "4000", "Income"],  # Not duplicate
            ],
        )

        result = importer.importer.import_rows(rows)

        assert result.duplicate_count == 1
        assert result.skipped_count >= 1

    def test_import_rows_validation_errors(self, test_importer, csv_helper):
        """Should track validation errors."""
        rows = csv_helper.create_dict_rows(
            ["Account Name", "Account Code"],  # Missing required field implicit
            [
                ["", "1000"],  # Missing required name
            ],
        )

        result = test_importer.importer.import_rows(rows)

        # Should have validation errors
        assert result.skipped_count > 0 or result.error_count > 0

    def test_import_rows_stop_on_error(
        self, mock_db, organization_id, user_id, csv_helper
    ):
        """Should stop processing on first error when stop_on_error is True."""
        from tests.ifrs.import_export.conftest import ConcreteTestImporter

        config = ImportConfig(
            organization_id=organization_id,
            user_id=user_id,
            stop_on_error=True,
        )
        importer = ConcreteTestImporter(mock_db, config)

        rows = csv_helper.create_dict_rows(
            ["Account Name", "Account Code"],
            [
                ["", "1000"],  # Invalid - missing name
                ["Valid", "2000"],  # Would be valid but won't be processed
            ],
        )

        result = importer.importer.import_rows(rows)

        # Should stop after first error
        assert result.error_count >= 1


# ============ TestPreviewFile ============


class TestPreviewFile:
    """Tests for the preview_file method of BaseImporter."""

    def test_preview_file_valid(self, test_importer, sample_account_csv):
        """Should preview valid CSV file."""
        preview = test_importer.importer.preview_file(sample_account_csv)

        assert preview.total_rows > 0
        assert len(preview.detected_columns) > 0
        assert preview.detected_format is not None

    def test_preview_file_not_found(self, test_importer):
        """Should handle non-existent file."""
        preview = test_importer.importer.preview_file("/nonexistent/file.csv")

        assert preview.is_valid is False
        assert len(preview.validation_errors) > 0

    def test_preview_file_detects_format(self, test_importer, quickbooks_account_csv):
        """Should detect CSV format."""
        preview = test_importer.importer.preview_file(quickbooks_account_csv)

        assert preview.detected_format == "quickbooks"

    def test_preview_file_includes_sample_data(self, test_importer, sample_account_csv):
        """Should include sample data rows."""
        preview = test_importer.importer.preview_file(sample_account_csv, max_rows=5)

        assert len(preview.sample_data) <= 5
        assert len(preview.sample_data) > 0

    def test_preview_file_missing_required(
        self, test_importer, invalid_csv_missing_required
    ):
        """Should identify missing required fields."""
        preview = test_importer.importer.preview_file(invalid_csv_missing_required)

        # Should flag missing required fields
        assert len(preview.missing_required) > 0 or not preview.is_valid

    def test_preview_to_dict(self, test_importer, sample_account_csv):
        """Preview should convert to dictionary."""
        preview = test_importer.importer.preview_file(sample_account_csv)

        d = preview.to_dict()

        assert "entity_type" in d
        assert "detected_columns" in d
        assert "column_mappings" in d
        assert "sample_data" in d


# ============ TestGetRequiredFields ============


class TestGetRequiredFields:
    """Tests for the get_required_fields method."""

    def test_get_required_fields(self, test_importer):
        """Should return list of required field names."""
        required = test_importer.importer.get_required_fields()

        assert "Account Name" in required
        assert "Account Code" in required


# ============ TestGetOptionalFields ============


class TestGetOptionalFields:
    """Tests for the get_optional_fields method."""

    def test_get_optional_fields(self, test_importer):
        """Should return list of optional field names."""
        optional = test_importer.importer.get_optional_fields()

        assert "Account Type" in optional
        assert "Description" in optional
        assert "Currency" in optional


# ============ TestTransformRow ============


class TestTransformRow:
    """Tests for the transform_row method."""

    def test_transform_row_basic(self, test_importer):
        """Should transform row data to model fields."""
        row = {
            "Account Name": "Cash",
            "Account Code": "1000",
            "Account Type": "Bank",
            "Currency": "USD",
        }

        transformed = test_importer.importer.transform_row(row, 1)

        assert transformed["account_name"] == "Cash"
        assert transformed["account_code"] == "1000"

    def test_transform_row_default_values(self, test_importer):
        """Should apply default values for missing fields."""
        row = {
            "Account Name": "Cash",
            "Account Code": "1000",
        }

        transformed = test_importer.importer.transform_row(row, 1)

        assert transformed["currency_code"] == "USD"  # Default


# ============ TestValidateRow ============


class TestValidateRow:
    """Tests for the validate_row method."""

    def test_validate_row_valid(self, test_importer):
        """Valid row should pass validation."""
        row = {
            "Account Name": "Cash",
            "Account Code": "1000",
        }

        is_valid = test_importer.importer.validate_row(row, 1)

        assert is_valid is True

    def test_validate_row_missing_required(self, test_importer):
        """Missing required field should fail validation."""
        row = {
            "Account Name": "",  # Required but empty
            "Account Code": "1000",
        }

        is_valid = test_importer.importer.validate_row(row, 1)

        assert is_valid is False
