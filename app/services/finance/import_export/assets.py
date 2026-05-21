"""
Fixed Assets Importer.

Imports fixed assets from CSV data into the IFRS-based fixed asset system.
"""

import logging
import re
from difflib import get_close_matches
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.core_org.location import Location
from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_category import AssetCategory, DepreciationMethod
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person
from app.services.finance.platform.sequence import SequenceService

from .base import BaseImporter, FieldMapping, ImportConfig

logger = logging.getLogger(__name__)
_CURRENCY_QUANTUM = Decimal("0.01")
_PLACEHOLDER_SERIALS = frozenset({"nil", "n/a", "na", "none", "null"})


def _normalize_header(header: str) -> str:
    """Normalize a source header for case-insensitive matching."""
    return " ".join(str(header).strip().replace("_", " ").split()).casefold()


def _normalize_match_text(value: Any) -> str:
    """Normalize free-text values for resilient import matching."""
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    text = text.replace("&", " and ")
    text = re.sub(r"[,\-\.]+", " ", text)
    text = re.sub(r"\band\b", " and ", text)
    text = " ".join(text.split())
    return text


def _get_row_value(row: dict[str, Any], *candidates: str) -> Any:
    """Get a row value by header name without caring about header case."""
    normalized = {_normalize_header(key): value for key, value in row.items()}
    for candidate in candidates:
        value = normalized.get(_normalize_header(candidate))
        if value not in (None, ""):
            return value
    return None


def _normalize_asset_serial(value: Any) -> str | None:
    """Return a clean serial number, treating placeholders as missing."""
    serial = str(value or "").strip()
    if not serial:
        return None
    if serial.casefold() in _PLACEHOLDER_SERIALS:
        return None
    return serial


class AssetCategoryImporter(BaseImporter[AssetCategory]):
    """
    Importer for asset categories.

    Creates categories from unique asset types/classes in the source data.
    """

    entity_name = "Asset Category"
    model_class = AssetCategory

    def __init__(
        self,
        db: Session,
        config: ImportConfig,
        asset_account_id: UUID,
        accumulated_depreciation_account_id: UUID,
        depreciation_expense_account_id: UUID,
        gain_loss_disposal_account_id: UUID,
    ):
        super().__init__(db, config)
        self.asset_account_id = asset_account_id
        self.accumulated_depreciation_account_id = accumulated_depreciation_account_id
        self.depreciation_expense_account_id = depreciation_expense_account_id
        self.gain_loss_disposal_account_id = gain_loss_disposal_account_id
        self._category_cache: dict[str, UUID] = {}

    def get_field_mappings(self) -> list[FieldMapping]:
        return []

    def get_unique_key(self, row: dict[str, Any]) -> str:
        value = _get_row_value(row, "Asset Category", "Asset Class") or "General Assets"
        return str(value).strip()

    def check_duplicate(self, row: dict[str, Any]) -> AssetCategory | None:
        category_name = self.get_unique_key(row)
        category_code = self._make_category_code(category_name)

        if category_code in self._category_cache:
            return self.db.get(AssetCategory, self._category_cache[category_code])

        existing = self.db.execute(
            select(AssetCategory).where(
                AssetCategory.organization_id == self.config.organization_id,
                AssetCategory.category_code == category_code,
            )
        ).scalar_one_or_none()

        if existing:
            resolved = existing
            if existing.parent_category_id:
                parent = self.db.get(AssetCategory, existing.parent_category_id)
                if parent and parent.organization_id == self.config.organization_id:
                    resolved = parent
            self._category_cache[category_code] = resolved.category_id
            return resolved

        return existing

    def create_entity(self, row: dict[str, Any]) -> AssetCategory:
        category_name = self.get_unique_key(row)
        category_code = self._make_category_code(category_name)

        # Determine depreciation parameters based on category name
        useful_life, residual_percent = self._get_default_depreciation(category_name)

        category = AssetCategory(
            category_id=uuid4(),
            organization_id=self.config.organization_id,
            category_code=category_code,
            category_name=category_name[:100],
            description=f"Imported asset category: {category_name}",
            depreciation_method=DepreciationMethod.STRAIGHT_LINE,
            useful_life_months=useful_life,
            residual_value_percent=Decimal(str(residual_percent)),
            asset_account_id=self.asset_account_id,
            accumulated_depreciation_account_id=self.accumulated_depreciation_account_id,
            depreciation_expense_account_id=self.depreciation_expense_account_id,
            gain_loss_disposal_account_id=self.gain_loss_disposal_account_id,
            capitalization_threshold=Decimal("0"),
            revaluation_model_allowed=False,
            is_active=True,
        )

        self._category_cache[category_code] = category.category_id
        return category

    def _make_category_code(self, name: str) -> str:
        return name.upper().replace(" ", "_").replace("&", "AND")[:30]

    def _get_default_depreciation(self, category_name: str) -> tuple:
        """Get default useful life (months) and residual % based on category."""
        name_lower = category_name.lower()

        # Common asset category defaults
        if "vehicle" in name_lower or "car" in name_lower:
            return (60, 10)  # 5 years, 10% residual
        elif "computer" in name_lower or "it" in name_lower or "laptop" in name_lower:
            return (36, 0)  # 3 years, 0% residual
        elif "furniture" in name_lower or "fixture" in name_lower:
            return (84, 5)  # 7 years, 5% residual
        elif "equipment" in name_lower or "machinery" in name_lower:
            return (120, 5)  # 10 years, 5% residual
        elif "building" in name_lower or "property" in name_lower:
            return (480, 10)  # 40 years, 10% residual
        elif "land" in name_lower:
            return (0, 100)  # No depreciation
        elif "office" in name_lower:
            return (60, 5)  # 5 years, 5% residual
        else:
            return (60, 5)  # Default: 5 years, 5% residual

    def get_category_id(self, category_name: str) -> UUID | None:
        code = self._make_category_code(category_name)
        return self._category_cache.get(code)

    @staticmethod
    def _extract_category_name(row: dict[str, Any]) -> str:
        return str(
            row.get("Asset Category")
            or row.get("Asset Class")
            or row.get("Category")
            or row.get("category_name")
            or row.get("asset_class_alt")
            or row.get("category_alt")
            or "General Assets"
        ).strip()

    def ensure_categories(self, rows: list[dict[str, Any]]) -> None:
        """Ensure all required categories exist."""
        unique_categories = set()
        for row in rows:
            cat_name = self._extract_category_name(row)
            if cat_name:
                unique_categories.add(cat_name)

        for cat_name in unique_categories:
            row = {"Asset Category": cat_name}
            if not self.check_duplicate(row):
                category = self.create_entity(row)
                self.db.add(category)
                self.db.flush()


class AssetImporter(BaseImporter[Asset]):
    """
    Importer for fixed assets from CSV data.

    Expected CSV columns (flexible - maps common naming conventions):
    - Asset Name / Name / Description: Asset name (required)
    - Asset Number / Asset Code / Tag Number: Asset identifier
    - Asset Category / Asset Class / Category: Category for the asset
    - Acquisition Date / Purchase Date / Date Acquired: Date of acquisition
    - Acquisition Cost / Cost / Purchase Price: Original cost
    - Currency Code / Currency: Currency (defaults to configured organization currency)
    - Useful Life / Life (Years) / Useful Life Months: Depreciation period
    - Residual Value / Salvage Value: Residual/salvage value
    - Depreciation Method / Method: SL, DB, etc.
    - Serial Number / Serial: Serial number
    - Location / Department: Physical location
    - Status: IN_USE, RETIRED, etc.
    """

    entity_name = "Fixed Asset"
    model_class = Asset

    def __init__(
        self,
        db: Session,
        config: ImportConfig,
        asset_account_id: UUID,
        accumulated_depreciation_account_id: UUID,
        depreciation_expense_account_id: UUID,
        gain_loss_disposal_account_id: UUID,
    ):
        super().__init__(db, config)
        self._category_importer = AssetCategoryImporter(
            db,
            config,
            asset_account_id,
            accumulated_depreciation_account_id,
            depreciation_expense_account_id,
            gain_loss_disposal_account_id,
        )
        self._location_cache: dict[str, UUID | None] = {}
        self._seen_serial_numbers: set[str] = set()
        self._department_lookup_loaded = False
        self._department_by_id: dict[str, tuple[UUID, str]] = {}
        self._department_by_code: dict[str, tuple[UUID, str]] = {}
        self._department_by_normalized_name: dict[str, list[tuple[UUID, str]]] = {}
        self._employee_lookup_loaded = False
        self._employee_by_id: dict[str, tuple[UUID, str, str | None]] = {}
        self._employee_by_code: dict[str, tuple[UUID, str, str | None]] = {}
        self._employee_by_email: dict[str, list[tuple[UUID, str, str | None]]] = {}

    def get_field_mappings(self) -> list[FieldMapping]:
        """Define flexible field mappings supporting various CSV formats."""
        return [
            # Name
            FieldMapping("Asset Name", "asset_name", required=False),
            FieldMapping("asset_name", "asset_name", required=False),
            FieldMapping("Name", "name_alt", required=False),
            FieldMapping("Description", "description", required=False),
            FieldMapping("description", "description", required=False),
            # Code/Number
            FieldMapping("Asset Number", "asset_number", required=False),
            FieldMapping("asset_number", "asset_number", required=False),
            FieldMapping("Asset Code", "asset_code_alt", required=False),
            FieldMapping("Tag Number", "tag_number_alt", required=False),
            # Category
            FieldMapping("Asset Category", "category_name", required=False),
            FieldMapping("category_name", "category_name", required=False),
            FieldMapping("Asset Class", "asset_class_alt", required=False),
            FieldMapping("Category", "category_alt", required=False),
            # Acquisition
            FieldMapping(
                "Acquisition Date",
                "acquisition_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "acquisition_date",
                "acquisition_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Purchase Date",
                "purchase_date_alt",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Date Acquired",
                "date_acquired_alt",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Acquisition Cost",
                "acquisition_cost",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "acquisition_cost",
                "acquisition_cost",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Cost", "cost_alt", required=False, transformer=self.parse_decimal
            ),
            FieldMapping(
                "Purchase Price",
                "purchase_price_alt",
                required=False,
                transformer=self.parse_decimal,
            ),
            # Currency
            FieldMapping(
                "Currency Code",
                "currency_code",
                required=False,
                default=settings.default_functional_currency_code,
            ),
            FieldMapping("currency_code", "currency_code", required=False),
            FieldMapping("Currency", "currency_alt", required=False),
            # Depreciation
            FieldMapping(
                "Useful Life",
                "useful_life_years",
                required=False,
                transformer=lambda v: int(float(v)) if v else None,
            ),
            FieldMapping(
                "Life (Years)",
                "life_years_alt",
                required=False,
                transformer=lambda v: int(float(v)) if v else None,
            ),
            FieldMapping(
                "Useful Life Months",
                "useful_life_months",
                required=False,
                transformer=lambda v: int(float(v)) if v else None,
            ),
            FieldMapping(
                "useful_life_months",
                "useful_life_months",
                required=False,
                transformer=lambda v: int(float(v)) if v else None,
            ),
            FieldMapping(
                "Residual Value",
                "residual_value",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "residual_value",
                "residual_value",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Salvage Value",
                "salvage_value_alt",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Depreciation Method", "depreciation_method_str", required=False
            ),
            FieldMapping(
                "depreciation_method", "depreciation_method_str", required=False
            ),
            FieldMapping("Method", "method_alt", required=False),
            FieldMapping(
                "Accumulated Depreciation",
                "accumulated_depreciation",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "accumulated_depreciation",
                "accumulated_depreciation",
                required=False,
                transformer=self.parse_decimal,
            ),
            # Physical
            FieldMapping("Serial Number", "serial_number", required=False),
            FieldMapping("serial_number", "serial_number", required=False),
            FieldMapping("Serial", "serial_alt", required=False),
            FieldMapping("Barcode", "barcode", required=False),
            FieldMapping("Manufacturer", "manufacturer", required=False),
            FieldMapping("manufacturer", "manufacturer", required=False),
            FieldMapping("Model", "model", required=False),
            FieldMapping("model", "model", required=False),
            FieldMapping("Location", "location", required=False),
            FieldMapping("location_name", "location", required=False),
            FieldMapping("Department", "department_name", required=False),
            FieldMapping("Department Name", "department_name_alt", required=False),
            FieldMapping("department", "department_name_alt", required=False),
            FieldMapping("Department Code", "department_code", required=False),
            FieldMapping("Department ID", "department_id", required=False),
            FieldMapping("Assign To", "assign_to", required=False),
            FieldMapping("Employee Email", "employee_email", required=False),
            FieldMapping("Employee Code", "employee_code", required=False),
            FieldMapping("Employee ID", "employee_id", required=False),
            # Insurance
            FieldMapping(
                "Insured Value",
                "insured_value",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping("Insurance Policy", "insurance_policy_number", required=False),
            # Status
            FieldMapping("Status", "status_str", required=False),
            FieldMapping("status", "status_str", required=False),
            FieldMapping(
                "Is Active", "is_active", required=False, transformer=self.parse_boolean
            ),
            # Reference
            FieldMapping("Supplier", "supplier_name", required=False),
            FieldMapping("Invoice Reference", "invoice_reference", required=False),
            FieldMapping("Invoice Number", "invoice_number_alt", required=False),
        ]

    def get_unique_key(self, row: dict[str, Any]) -> str:
        """Unique key is serial number when present."""
        serial = _normalize_asset_serial(_get_row_value(row, "Serial Number", "Serial"))
        if serial:
            return serial.casefold()
        return ""

    def _normalize_source_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Canonicalize known source headers so import mapping is case-insensitive."""
        canonical_headers = {
            mapping.source_field for mapping in self.get_field_mappings()
        }
        canonical_by_normalized = {
            _normalize_header(header): header for header in canonical_headers
        }
        normalized_row: dict[str, Any] = {}

        for key, value in row.items():
            canonical_key = canonical_by_normalized.get(_normalize_header(key), key)
            normalized_row[canonical_key] = value

        return normalized_row

    def _prepare_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize incoming rows before category creation and field mapping."""
        return [self._normalize_source_row(row) for row in rows]

    def check_duplicate(self, row: dict[str, Any]) -> Asset | None:
        """Check if asset already exists by serial number."""
        key = self.get_unique_key(row)
        if not key:
            return None

        if key in self._seen_serial_numbers:
            return Asset()
        self._seen_serial_numbers.add(key)

        existing = self.db.execute(
            select(Asset).where(
                Asset.organization_id == self.config.organization_id,
                func.lower(func.btrim(Asset.serial_number)) == key,
            )
        ).scalar_one_or_none()

        return existing

    def create_entity(self, row: dict[str, Any]) -> Asset:
        """Create a new asset from transformed row data."""
        # Get asset name
        asset_name = (
            row.get("asset_name")
            or row.get("name_alt")
            or row.get("description")
            or "Unknown Asset"
        ).strip()

        # Always generate the next asset number from the sequence.
        asset_number = SequenceService.get_next_number(
            self.db,
            self.config.organization_id,
            SequenceType.ASSET,
        )

        # Get category
        category_name = self._category_importer._extract_category_name(row)
        category_id = self._category_importer.get_category_id(category_name)
        if category_id is None:
            category_row = {"Asset Category": category_name}
            category = self._category_importer.check_duplicate(category_row)
            if not category:
                category = self._category_importer.create_entity(category_row)
                self.db.add(category)
                self.db.flush()
            category_id = category.category_id
        location_id = self._resolve_location_id(row.get("location"))
        department_name = row.get("department_name") or row.get("department_name_alt")
        department_id = self._resolve_department_id(
            department_id_value=row.get("department_id"),
            department_code=row.get("department_code"),
            department_name=department_name,
        )
        custodian_employee_id = self._resolve_employee_id(
            employee_id_value=row.get("employee_id"),
            employee_code=row.get("employee_code"),
            employee_email=row.get("employee_email") or row.get("assign_to"),
            department_id=department_id,
        )

        # Get acquisition details
        acquisition_date = (
            row.get("acquisition_date")
            or row.get("purchase_date_alt")
            or row.get("date_acquired_alt")
            or date.today()
        )
        acquisition_cost = (
            row.get("acquisition_cost")
            or row.get("cost_alt")
            or row.get("purchase_price_alt")
            or Decimal("0")
        )
        acquisition_cost = self._round_currency(acquisition_cost)

        currency_code = (
            row.get("currency_code")
            or row.get("currency_alt")
            or settings.default_functional_currency_code
        )[:3]

        # Get depreciation parameters
        useful_life_months = row.get("useful_life_months")
        if not useful_life_months:
            years = row.get("useful_life_years") or row.get("life_years_alt")
            if years:
                useful_life_months = years * 12
            else:
                useful_life_months = 60  # Default 5 years

        residual_value = (
            row.get("residual_value") or row.get("salvage_value_alt") or Decimal("0")
        )
        accumulated_depreciation = row.get("accumulated_depreciation") or Decimal("0")

        # Parse depreciation method
        method_str = (
            row.get("depreciation_method_str")
            or row.get("method_alt")
            or "STRAIGHT_LINE"
        )
        depreciation_method = self._parse_depreciation_method(method_str)

        # Calculate net book value
        if isinstance(acquisition_cost, Decimal) and isinstance(
            accumulated_depreciation, Decimal
        ):
            net_book_value = acquisition_cost - accumulated_depreciation
        else:
            net_book_value = acquisition_cost or Decimal("0")

        # Determine status
        status_str = row.get("status_str") or "IN_USE"
        status = self._parse_status(status_str)
        serial_number = _normalize_asset_serial(
            row.get("serial_number") or row.get("serial_alt")
        )

        asset = Asset(
            asset_id=uuid4(),
            organization_id=self.config.organization_id,
            asset_number=asset_number[:30],
            asset_name=asset_name[:200],
            description=row.get("description"),
            category_id=category_id,
            location_id=location_id,
            custodian_employee_id=custodian_employee_id,
            acquisition_date=acquisition_date,
            in_service_date=acquisition_date,
            acquisition_cost=acquisition_cost,
            currency_code=currency_code,
            functional_currency_cost=acquisition_cost,
            depreciation_method=depreciation_method,
            useful_life_months=useful_life_months,
            remaining_life_months=useful_life_months,
            residual_value=residual_value,
            depreciation_start_date=acquisition_date,
            accumulated_depreciation=accumulated_depreciation,
            net_book_value=net_book_value,
            impairment_loss=Decimal("0"),
            status=status,
            serial_number=serial_number,
            barcode=row.get("barcode"),
            manufacturer=row.get("manufacturer"),
            model=row.get("model"),
            insured_value=row.get("insured_value"),
            insurance_policy_number=row.get("insurance_policy_number"),
            invoice_reference=row.get("invoice_reference")
            or row.get("invoice_number_alt"),
            is_component_parent=False,
            created_by_user_id=self.config.user_id,
        )

        return asset

    def _load_department_lookup(self) -> None:
        if self._department_lookup_loaded:
            return

        departments = (
            self.db.execute(
                select(Department).where(
                    Department.organization_id == self.config.organization_id,
                    Department.is_active.is_(True),
                )
            )
            .scalars()
            .all()
        )
        for department in departments:
            display_name = department.department_name
            self._department_by_id[str(department.department_id)] = (
                department.department_id,
                display_name,
            )
            self._department_by_code[department.department_code.casefold()] = (
                department.department_id,
                display_name,
            )
            normalized_name = _normalize_match_text(display_name)
            self._department_by_normalized_name.setdefault(normalized_name, []).append(
                (department.department_id, display_name)
            )
        self._department_lookup_loaded = True

    def _load_employee_lookup(self) -> None:
        if self._employee_lookup_loaded:
            return

        employee_rows = self.db.execute(
            select(
                Employee.employee_id,
                Employee.employee_code,
                Employee.department_id,
                Person.email,
                Person.first_name,
                Person.last_name,
                Person.display_name,
            )
            .join(Person, Person.id == Employee.person_id)
            .where(
                Employee.organization_id == self.config.organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
        ).all()

        for row in employee_rows:
            full_name = (
                row.display_name
                or f"{row.first_name or ''} {row.last_name or ''}".strip()
                or row.email
            )
            department_id = str(row.department_id) if row.department_id else None
            entry = (row.employee_id, full_name, department_id)
            self._employee_by_id[str(row.employee_id)] = entry
            if row.employee_code:
                self._employee_by_code[row.employee_code.casefold()] = entry
            if row.email:
                normalized_email = str(row.email).strip().casefold()
                self._employee_by_email.setdefault(normalized_email, []).append(entry)
        self._employee_lookup_loaded = True

    def _resolve_department_id(
        self,
        *,
        department_id_value: Any = None,
        department_code: Any = None,
        department_name: Any = None,
    ) -> UUID | None:
        self._load_department_lookup()

        raw_id = str(department_id_value or "").strip()
        if raw_id:
            match = self._department_by_id.get(raw_id)
            if match:
                return match[0]

        raw_code = str(department_code or "").strip()
        if raw_code:
            match = self._department_by_code.get(raw_code.casefold())
            if match:
                return match[0]

        raw_name = str(department_name or "").strip()
        if not raw_name:
            return None

        normalized_name = _normalize_match_text(raw_name)
        matches = self._department_by_normalized_name.get(normalized_name, [])
        if len(matches) == 1:
            return matches[0][0]
        if len(matches) > 1:
            options = ", ".join(sorted(name for _, name in matches))
            raise ValueError(f'Ambiguous department "{raw_name}". Matches: {options}')

        suggestions = self._closest_department_suggestions(normalized_name)
        if suggestions:
            raise ValueError(
                f'Department "{raw_name}" not found. Did you mean: {", ".join(suggestions)}?'
            )
        raise ValueError(f'Department "{raw_name}" not found.')

    def _resolve_employee_id(
        self,
        *,
        employee_id_value: Any = None,
        employee_code: Any = None,
        employee_email: Any = None,
        department_id: UUID | None = None,
    ) -> UUID | None:
        self._load_employee_lookup()

        raw_id = str(employee_id_value or "").strip()
        if raw_id:
            match = self._employee_by_id.get(raw_id)
            if match:
                self._ensure_employee_department_match(
                    employee_name=match[1],
                    employee_department_id=match[2],
                    department_id=department_id,
                )
                return match[0]

        raw_code = str(employee_code or "").strip()
        if raw_code:
            match = self._employee_by_code.get(raw_code.casefold())
            if match:
                self._ensure_employee_department_match(
                    employee_name=match[1],
                    employee_department_id=match[2],
                    department_id=department_id,
                )
                return match[0]

        raw_email = str(employee_email or "").strip()
        if not raw_email:
            return None

        matches = self._employee_by_email.get(raw_email.casefold(), [])
        if len(matches) == 1:
            match = matches[0]
            self._ensure_employee_department_match(
                employee_name=match[1],
                employee_department_id=match[2],
                department_id=department_id,
            )
            return match[0]
        if len(matches) > 1:
            options = ", ".join(sorted(match[1] for match in matches))
            raise ValueError(f'Ambiguous employee "{raw_email}". Matches: {options}')

        suggestions = self._closest_employee_suggestions(raw_email.casefold())
        if suggestions:
            raise ValueError(
                f'Employee "{raw_email}" not found. Did you mean: {", ".join(suggestions)}?'
            )
        raise ValueError(f'Employee "{raw_email}" not found.')

    def _ensure_employee_department_match(
        self,
        *,
        employee_name: str,
        employee_department_id: str | None,
        department_id: UUID | None,
    ) -> None:
        if department_id is None:
            return
        if employee_department_id != str(department_id):
            raise ValueError(
                f'Employee "{employee_name}" does not belong to the selected department'
            )

    def _closest_department_suggestions(self, normalized_name: str) -> list[str]:
        suggestion_keys = get_close_matches(
            normalized_name,
            list(self._department_by_normalized_name.keys()),
            n=3,
            cutoff=0.6,
        )
        suggestions: list[str] = []
        for key in suggestion_keys:
            for _, display_name in self._department_by_normalized_name.get(key, []):
                if display_name not in suggestions:
                    suggestions.append(display_name)
        return suggestions[:3]

    def _closest_employee_suggestions(self, normalized_email: str) -> list[str]:
        matches = get_close_matches(
            normalized_email,
            list(self._employee_by_email.keys()),
            n=3,
            cutoff=0.6,
        )
        return matches[:3]

    @staticmethod
    def _round_currency(value: Decimal | Any) -> Decimal:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        return amount.quantize(_CURRENCY_QUANTUM, rounding=ROUND_HALF_UP)

    def _resolve_location_id(self, raw_location: Any) -> UUID | None:
        """Resolve free-text location value to an organization location_id."""
        text = str(raw_location or "").strip()
        if not text:
            return None

        key = text.lower()
        if key in self._location_cache:
            return self._location_cache[key]

        location = (
            self.db.execute(
                select(Location).where(
                    Location.organization_id == self.config.organization_id,
                    Location.is_active.is_(True),
                    (
                        Location.location_name.ilike(text)
                        | Location.location_code.ilike(text)
                    ),
                )
            )
            .scalars()
            .first()
        )

        if not location:
            location = (
                self.db.execute(
                    select(Location).where(
                        Location.organization_id == self.config.organization_id,
                        Location.is_active.is_(True),
                        (
                            Location.location_name.ilike(f"%{text}%")
                            | Location.location_code.ilike(f"%{text}%")
                        ),
                    )
                )
                .scalars()
                .first()
            )

        resolved = location.location_id if location else None
        self._location_cache[key] = resolved
        return resolved

    def _parse_depreciation_method(self, method_str: str) -> str:
        """Parse depreciation method string."""
        method_map = {
            "STRAIGHT_LINE": "STRAIGHT_LINE",
            "SL": "STRAIGHT_LINE",
            "STRAIGHT LINE": "STRAIGHT_LINE",
            "DECLINING_BALANCE": "DECLINING_BALANCE",
            "DB": "DECLINING_BALANCE",
            "DECLINING BALANCE": "DECLINING_BALANCE",
            "DOUBLE_DECLINING": "DOUBLE_DECLINING",
            "DDB": "DOUBLE_DECLINING",
            "DOUBLE DECLINING": "DOUBLE_DECLINING",
            "SUM_OF_YEARS": "SUM_OF_YEARS",
            "SYD": "SUM_OF_YEARS",
            "UNITS_OF_PRODUCTION": "UNITS_OF_PRODUCTION",
            "UOP": "UNITS_OF_PRODUCTION",
        }
        return method_map.get(method_str.upper().replace("-", "_"), "STRAIGHT_LINE")

    def _parse_status(self, status_str: str | None) -> AssetStatus:
        """Parse asset status string."""
        normalized = str(status_str or "NOT_IN_USE").strip().upper().replace(" ", "_")
        status_map = {
            "ACTIVE": AssetStatus.IN_USE,
            "IN_USE.": AssetStatus.IN_USE,
            "IN_USE": AssetStatus.IN_USE,
            "DRAFT": AssetStatus.NOT_IN_USE,
            "NOT_IN_USE": AssetStatus.NOT_IN_USE,
            "DISPOSED": AssetStatus.RETIRED,
            "SOLD": AssetStatus.RETIRED,
            "RETIRED": AssetStatus.RETIRED,
            "FULLY_DEPRECIATED": AssetStatus.FULLY_DEPRECIATED,
            "IMPAIRED": AssetStatus.FAULTY,
            "FAULTY": AssetStatus.FAULTY,
            "UNDER_CONSTRUCTION": AssetStatus.IN_STORE,
            "IN_STORE": AssetStatus.IN_STORE,
            "WIP": AssetStatus.IN_STORE,
            "UNDER_REPAIR": AssetStatus.UNDER_REPAIR,
        }
        return status_map.get(normalized, AssetStatus.NOT_IN_USE)

    def import_file(self, file_path):
        """Override to ensure categories are created first."""
        import csv
        from pathlib import Path

        file_path = Path(file_path)
        if not file_path.exists():
            self.result.add_error(0, f"File not found: {file_path}", None)
            return self.result

        with open(file_path, encoding=self.config.encoding) as f:
            reader = csv.DictReader(f)
            rows = self._prepare_rows(list(reader))

        # Ensure categories exist
        self._category_importer.ensure_categories(rows)
        self.db.flush()

        return super().import_rows(rows)

    def import_rows(self, rows: list[dict[str, Any]]):
        """Override list import to normalize headers and create categories first."""
        rows = self._prepare_rows(rows)
        self._category_importer.ensure_categories(rows)
        self.db.flush()
        return super().import_rows(rows)

    def import_xlsx_file(self, file_path):
        """Override XLSX import to ensure categories are created first."""
        from pathlib import Path

        file_path = Path(file_path)
        if not file_path.exists():
            self.result.add_error(0, f"File not found: {file_path}", None)
            return self.result

        rows = self._prepare_rows(self.parse_xlsx_file(file_path))
        self._category_importer.ensure_categories(rows)
        self.db.flush()

        return super().import_rows(rows)
