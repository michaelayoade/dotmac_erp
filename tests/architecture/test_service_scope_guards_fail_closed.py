"""Service scope guards cannot reintroduce empty-list full authority."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_GUARD_FILES = (
    _ROOT / "app/api/service_principal.py",
    _ROOT / "app/api/sync/dotmac_sub.py",
)


def _has_legacy_fail_open(source: str) -> bool:
    return "if scopes and" in source


def test_service_scope_guards_do_not_condition_authorization_on_nonempty_scopes():
    offenders = [
        str(path.relative_to(_ROOT))
        for path in _GUARD_FILES
        if _has_legacy_fail_open(path.read_text(encoding="utf-8"))
    ]

    assert not offenders, (
        "an empty service-principal scope list must fail closed; the legacy "
        f"fail-open predicate returned in: {offenders}"
    )


def test_service_scope_guard_detects_a_planted_fail_open_mutation(tmp_path):
    planted = tmp_path / "service_principal.py"
    planted.write_text(
        "scopes = auth.get('scopes') or []\n"
        "if scopes and scope not in scopes:\n"
        "    refuse()\n",
        encoding="utf-8",
    )

    assert _has_legacy_fail_open(planted.read_text(encoding="utf-8"))
