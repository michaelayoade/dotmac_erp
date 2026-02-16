"""Starter seed for subscription and billing modules copied from dotmac_sm.

This package keeps a local snapshot and provides helper utilities so
integration work can proceed incrementally without breaking runtime imports.
"""

from app.starter.subscription_billing.manifest import (
    SEED_GROUPS,
    SeedFile,
    all_seed_files,
    seed_root,
    validate_seed_files,
)

__all__ = [
    "SeedFile",
    "SEED_GROUPS",
    "seed_root",
    "all_seed_files",
    "validate_seed_files",
]
