"""
Fixed Assets Importer.

Imports fixed assets from CSV data into the IFRS-based fixed asset system.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_category import AssetCategory, DepreciationMethod

from .base import BaseImporter, FieldMapping, ImportConfig

logger = logging.getLogger(__name__)


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
        self._category_cache: Dict[str, UUID] = {}

    def get_field_mappings(self) -> List[FieldMapping]:
        return []

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        value = row.get("Asset Category") or row.get("Asset Class") or "General Assets"
        return str(value).strip()

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[AssetCategory]:
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
            self._category_cache[category_code] = existing.category_id

        return existing

    def create_entity(self, row: Dict[str, Any]) -> AssetCategory:
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

    def get_category_id(self, category_name: str) -> Optional[UUID]:
        code = self._make_category_code(category_name)
        return self._category_cache.get(code)

    def ensure_categories(self, rows: List[Dict[str, Any]]) -> None:
        """Ensure all required categories exist."""
        unique_categories = set()
        for row in rows:
            cat_name = (
                row.get("Asset Category") or row.get("Asset Class") or "General Assets"
            )
            if cat_name:
                unique_categories.add(cat_name.strip())

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
    - Currency Code / Currency: Currency (default: NGN)
    - Useful Life / Life (Years) / Useful Life Months: Depreciation period
    - Residual Value / Salvage Value: Residual/salvage value
    - Depreciation Method / Method: SL, DB, etc.
    - Serial Number / Serial: Serial number
    - Location / Department: Physical location
    - Status: ACTIVE, DISPOSED, etc.
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
        self._code_counter = 0
        self._category_importer = AssetCategoryImporter(
            db,
            config,
            asset_account_id,
            accumulated_depreciation_account_id,
            depreciation_expense_account_id,
            gain_loss_disposal_account_id,
        )

    def get_field_mappings(self) -> List[FieldMapping]:
        """Define flexible field mappings supporting various CSV formats."""
        return [
            # Name
            FieldMapping("Asset Name", "asset_name", required=False),
            FieldMapping("Name", "name_alt", required=False),
            FieldMapping("Description", "description", required=False),
            # Code/Number
            FieldMapping("Asset Number", "asset_number", required=False),
            FieldMapping("Asset Code", "asset_code_alt", required=False),
            FieldMapping("Tag Number", "tag_number_alt", required=False),
            # Category
            FieldMapping("Asset Category", "category_name", required=False),
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
                "Currency Code", "currency_code", required=False, default="NGN"
            ),
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
                "Residual Value",
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
            FieldMapping("Method", "method_alt", required=False),
            FieldMapping(
                "Accumulated Depreciation",
                "accumulated_depreciation",
                required=False,
                transformer=self.parse_decimal,
            ),
            # Physical
            FieldMapping("Serial Number", "serial_number", required=False),
            FieldMapping("Serial", "serial_alt", required=False),
            FieldMapping("Barcode", "barcode", required=False),
            FieldMapping("Manufacturer", "manufacturer", required=False),
            FieldMapping("Model", "model", required=False),
            FieldMapping("Location", "location", required=False),
            FieldMapping("Department", "department_alt", required=False),
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
            FieldMapping(
                "Is Active", "is_active", required=False, transformer=self.parse_boolean
            ),
            # Reference
            FieldMapping("Supplier", "supplier_name", required=False),
            FieldMapping("Invoice Reference", "invoice_reference", required=False),
            FieldMapping("Invoice Number", "invoice_number_alt", required=False),
        ]

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        """Unique key is asset number or code."""
        code = (
            row.get("Asset Number")
            or row.get("Asset Code")
            or row.get("Tag Number")
            or ""
        ).strip()
        if code:
            return code
        # Fallback to name + date
        name = (row.get("Asset Name") or row.get("Name") or "").strip()
        return name

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[Asset]:
        """Check if asset already exists."""
        key = self.get_unique_key(row)
        if not key:
            return None

        existing = self.db.execute(
            select(Asset).where(
                Asset.organization_id == self.config.organization_id,
                Asset.asset_number == key,
            )
        ).scalar_one_or_none()

        return existing

    def create_entity(self, row: Dict[str, Any]) -> Asset:
        """Create a new asset from transformed row data."""
        # Get asset name
        asset_name = (
            row.get("asset_name")
            or row.get("name_alt")
            or row.get("description")
            or "Unknown Asset"
        ).strip()

        # Get asset number
        asset_number = (
            row.get("asset_number")
            or row.get("asset_code_alt")
            or row.get("tag_number_alt")
            or ""
        ).strip()
        if not asset_number:
            self._code_counter += 1
            asset_number = f"FA{self._code_counter:06d}"

        # Get category
        category_name = (
            row.get("category_name")
            or row.get("asset_class_alt")
            or row.get("category_alt")
            or "General Assets"
        )
        category_id = self._category_importer.get_category_id(category_name)

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

        currency_code = (row.get("currency_code") or row.get("currency_alt") or "NGN")[
            :3
        ]

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
        status_str = row.get("status_str", "ACTIVE")
        status = self._parse_status(status_str)

        asset = Asset(
            asset_id=uuid4(),
            organization_id=self.config.organization_id,
            asset_number=asset_number[:30],
            asset_name=asset_name[:200],
            description=row.get("description"),
            category_id=category_id,
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
            serial_number=row.get("serial_number") or row.get("serial_alt"),
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

    def _parse_status(self, status_str: str) -> AssetStatus:
        """Parse asset status string."""
        status_map = {
            "ACTIVE": AssetStatus.ACTIVE,
            "DRAFT": AssetStatus.DRAFT,
            "DISPOSED": AssetStatus.DISPOSED,
            "SOLD": AssetStatus.DISPOSED,
            "FULLY_DEPRECIATED": AssetStatus.FULLY_DEPRECIATED,
            "IMPAIRED": AssetStatus.IMPAIRED,
            "UNDER_CONSTRUCTION": AssetStatus.UNDER_CONSTRUCTION,
            "WIP": AssetStatus.UNDER_CONSTRUCTION,
        }
        return status_map.get(status_str.upper().replace(" ", "_"), AssetStatus.ACTIVE)

    def import_file(self, file_path):
        """Override to ensure categories are created first."""
        import csv
        from pathlib import Path

        file_path = Path(file_path)
        if not file_path.exists():
            self.result.add_error(0, f"File not found: {file_path}", None)
            return self.result

        with open(file_path, "r", encoding=self.config.encoding) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # Ensure categories exist
        self._category_importer.ensure_categories(rows)
        self.db.flush()

        return super().import_rows(rows)
