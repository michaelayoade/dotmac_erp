from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SeedFile:
    group: str
    relative_path: str


SEED_GROUPS: dict[str, tuple[str, ...]] = {
    "api": (
        "api/billing.py",
        "api/catalog.py",
    ),
    "models": (
        "models/billing.py",
        "models/catalog.py",
        "models/subscriber.py",
        "models/subscription_change.py",
        "models/subscription_engine.py",
    ),
    "schemas": (
        "schemas/billing.py",
        "schemas/catalog.py",
        "schemas/subscriber.py",
        "schemas/subscription_engine.py",
    ),
    "services": (
        "services/billing_automation.py",
        "services/subscriber.py",
        "services/subscription_changes.py",
        "services/subscription_engine.py",
        "services/billing/__init__.py",
        "services/billing/_common.py",
        "services/billing/configuration.py",
        "services/billing/credit_notes.py",
        "services/billing/invoices.py",
        "services/billing/ledger.py",
        "services/billing/payments.py",
        "services/billing/providers.py",
        "services/billing/reporting.py",
        "services/billing/runs.py",
        "services/billing/tax.py",
        "services/catalog/__init__.py",
        "services/catalog/add_ons.py",
        "services/catalog/credentials.py",
        "services/catalog/nas.py",
        "services/catalog/offer_addons.py",
        "services/catalog/offers.py",
        "services/catalog/policies.py",
        "services/catalog/profiles.py",
        "services/catalog/radius.py",
        "services/catalog/subscriptions.py",
        "services/catalog/validation.py",
    ),
}


def seed_root() -> Path:
    return Path(__file__).resolve().parent / "dotmac_sm"


def all_seed_files() -> list[SeedFile]:
    files: list[SeedFile] = []
    for group, paths in SEED_GROUPS.items():
        files.extend(SeedFile(group=group, relative_path=path) for path in paths)
    return files


def validate_seed_files() -> list[str]:
    """Return validation errors for missing or syntactically-invalid seed files."""
    errors: list[str] = []
    root = seed_root()
    for item in all_seed_files():
        path = root / item.relative_path
        if not path.exists():
            errors.append(f"Missing file: {item.relative_path}")
            continue
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            errors.append(f"Empty file: {item.relative_path}")
            continue
        try:
            ast.parse(text)
        except SyntaxError as exc:
            errors.append(f"Syntax error in {item.relative_path}: {exc.msg}")
    return errors
