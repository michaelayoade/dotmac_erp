from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_LEGACY_MODULE_NAME = "app.services.admin._legacy_web_module"
_LEGACY_WEB_PATH = Path(__file__).resolve().parent.parent / "web.py"


def load_legacy_admin_web_module():
    module = sys.modules.get(_LEGACY_MODULE_NAME)
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location(_LEGACY_MODULE_NAME, _LEGACY_WEB_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load legacy admin web module from {_LEGACY_WEB_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[_LEGACY_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


legacy_admin_web_module = load_legacy_admin_web_module()
LegacyAdminWebService = legacy_admin_web_module.AdminWebService
