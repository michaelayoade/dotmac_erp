from __future__ import annotations

from app.starter.subscription_billing import (
    SEED_GROUPS,
    all_seed_files,
    seed_root,
    validate_seed_files,
)


def test_seed_root_exists() -> None:
    assert seed_root().exists()


def test_seed_group_counts() -> None:
    assert set(SEED_GROUPS.keys()) == {"api", "models", "schemas", "services"}
    assert len(SEED_GROUPS["api"]) == 2
    assert len(SEED_GROUPS["models"]) >= 5
    assert len(SEED_GROUPS["schemas"]) >= 4
    assert len(SEED_GROUPS["services"]) >= 20


def test_all_seed_files_exist() -> None:
    root = seed_root()
    for item in all_seed_files():
        assert (root / item.relative_path).exists(), item.relative_path


def test_seed_files_are_syntax_valid() -> None:
    errors = validate_seed_files()
    assert errors == []
