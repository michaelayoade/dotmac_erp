from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_subscriber_import_does_not_overwrite_erp_tax_identity() -> None:
    source = (ROOT / "app/services/dotmac_sub/sync/_subscribers.py").read_text()

    assert "existing.tax_identification_number = sub.tax_id" not in source
    assert "tax_identification_number=sub.tax_id" not in source


def test_checked_in_boundary_declares_independent_replaceable_products() -> None:
    boundary = (ROOT / "docs/replaceable_application_boundary.md").read_text()

    assert "independent products" in boundary
    assert "may be replaced by Zoho" in boundary
    assert "Each application owns its own tax-identity system" in boundary
    assert "Neither application reads the other's database" in boundary
