"""The platform-owned registry never latches an empty load.

`app/services/setting_scopes.py::_ensure_loaded` is the entry to a security
check that fails OPEN when the registry is empty: `is_platform_owned` answers
`False` for every key, which disables the `DomainSetting` write listener and
all four read overrides at once.

The window it has to survive is an import-ordering one. `import` is a no-op
once the module is in `sys.modules`, and a module lands there at the START of
its execution, while `settings_spec` registers at the END of its. So a
`DomainSetting` constructed underneath a partially-executed `settings_spec`
import reaches `_ensure_loaded`, imports nothing, and sees an empty registry.
The bug was what happened next: `_loaded = True` regardless, which made that
one unlucky ordering permanent for the process.

These tests assert the two halves of the fix — the load does not latch, and it
is retryable — by reproducing the window's observable state directly: the real
module already in `sys.modules` (so the import IS the no-op) with nothing
registered.

They mutate module globals, so every one of them runs inside `_registry_state`,
which restores the process's real registry whatever happens.
"""

from __future__ import annotations

import pytest

from app.models.domain_settings import SettingDomain
from app.services import setting_scopes

# Imported for its PRESENCE in `sys.modules`, not for anything it exports. The
# window under test is the one where `_ensure_loaded`'s own import does nothing
# because the module is already there; without this line a lone run of this file
# would have `_ensure_loaded` execute `settings_spec` for real, register the
# declarations, and never enter the window at all.
import app.services.settings_spec  # noqa: F401


@pytest.fixture()
def _registry_state():
    """Snapshot and restore the module's registry and its load flags.

    In-place mutation on the way out, not rebinding: the set is a module global
    other code closes over, and swapping the object would leave a stale one
    behind.
    """
    saved = set(setting_scopes._PLATFORM_OWNED)
    saved_loaded = setting_scopes._loaded
    saved_loading = setting_scopes._loading
    try:
        yield
    finally:
        setting_scopes._PLATFORM_OWNED.clear()
        setting_scopes._PLATFORM_OWNED.update(saved)
        setting_scopes._loaded = saved_loaded
        setting_scopes._loading = saved_loading


def _enter_the_window() -> None:
    """Put the module in exactly the state the partial import produces."""
    setting_scopes._PLATFORM_OWNED.clear()
    setting_scopes._loaded = False
    setting_scopes._loading = False


def test_a_no_op_import_that_registered_nothing_does_not_latch(_registry_state):
    _enter_the_window()

    with pytest.raises(RuntimeError):
        setting_scopes._ensure_loaded()

    assert setting_scopes._loaded is False, (
        "an empty load marked itself complete. Every later call short-circuits "
        "on that flag, so the registry stays empty for the life of the process "
        "and every key answers 'not platform-owned'"
    )
    assert setting_scopes._loading is False, "the re-entry guard was left set"


def test_the_window_refuses_rather_than_answering_false(_registry_state):
    """The failure direction. `False` here is what opens the write listener."""
    _enter_the_window()

    with pytest.raises(RuntimeError):
        setting_scopes.is_platform_owned(
            SettingDomain.automation, "webhook_allowed_hosts"
        )

    with pytest.raises(RuntimeError):
        setting_scopes.platform_owned_keys()


def test_the_retry_succeeds_once_the_declarations_have_run(_registry_state):
    """Retryable, which is the other half of not latching.

    A check that refused permanently after one bad ordering would be as broken
    as one that latched open, just in the other direction. Registering here
    stands in for `settings_spec` reaching the end of its own module body.
    """
    _enter_the_window()
    with pytest.raises(RuntimeError):
        setting_scopes._ensure_loaded()

    setting_scopes.register_platform_owned(
        SettingDomain.automation, "webhook_allowed_hosts"
    )

    setting_scopes._ensure_loaded()

    assert setting_scopes._loaded is True
    assert setting_scopes.is_platform_owned(
        SettingDomain.automation, "webhook_allowed_hosts"
    )


def test_a_complete_load_still_latches(_registry_state):
    """Non-vacuity: the flag is set on the good path, so the import is not
    repeated on every `DomainSetting` write."""
    _enter_the_window()
    setting_scopes.register_platform_owned(
        SettingDomain.automation, "webhook_allowed_hosts"
    )

    setting_scopes._ensure_loaded()

    assert setting_scopes._loaded is True
