"""ERP-owned composition boundary for the shared Dotmac UI contract."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import dotmac_ui
from dotmac_ui.assets import ASSET_NAMESPACE

UI_ASSET_MOUNT: Final[str] = f"/static/{ASSET_NAMESPACE}"
UI_ASSET_DIRECTORY: Final[Path] = dotmac_ui.static_dir() / ASSET_NAMESPACE
UI_TEMPLATE_DIRECTORY: Final[Path] = dotmac_ui.template_dir()
UI_STYLESHEET_URL: Final[str] = dotmac_ui.stylesheet_url()


def ui_template_globals() -> dict[str, str]:
    """Return stable template values published by the UI package."""
    return {"dotmac_ui_stylesheet_url": UI_STYLESHEET_URL}


__all__ = [
    "UI_ASSET_DIRECTORY",
    "UI_ASSET_MOUNT",
    "UI_STYLESHEET_URL",
    "UI_TEMPLATE_DIRECTORY",
    "ui_template_globals",
]
