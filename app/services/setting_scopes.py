"""Which ``(domain, key)`` pairs are PLATFORM-owned — the one answer to that.

A PLATFORM-owned setting is one no organization may hold a row for. It is not a
preference an organization tunes; it is a control whose whole purpose is to
CONSTRAIN organizations, and a per-organization row for it would let the
constrained party rewrite its own constraint. The outbound-webhook SSRF
allowlist is the founding example.

## Why this is its own module

Exactly the reason ``app.services.setting_domains`` is its own module: the ORM
listener on ``DomainSetting`` has to ask this question on every write, and
``app.services.settings_spec`` imports ``app.services.domain_settings`` which
imports the model — so the model cannot import ``settings_spec``. A leaf module
both sides may import breaks the cycle, and after the first call the answer is
a set lookup rather than an import.

## Why a registry rather than a column

The authority answer is deliberately NOT persisted in ``domain_settings``.
Putting it there would store the rule inside the table the rule governs, where
a write could change it — the same reason settings-encryption keys are not kept
in the database they protect. ``SettingSpec.scope`` is the declaration;
this module is its index.

## Scope is not inheritance

``inherits=False`` means a LESS specific row is not a valid answer.
``scope=PLATFORM`` means a MORE specific row may not exist at all. They are
orthogonal, and conflating them is how a platform control quietly acquires a
tenant override.
"""

from __future__ import annotations

import threading

_PLATFORM_OWNED: set[tuple[str, str]] = set()

# Import state for `_ensure_loaded`. The re-entry guard covers exactly one
# case: a query arriving underneath the import THIS CALL started, on THIS
# thread, which must answer from whatever has registered so far rather than
# recurse forever. It does not cover a query arriving while `settings_spec` is
# mid-import from some other entry point — see `_ensure_loaded` for that case
# and why `_loaded` may not be set until the registry has been validated.
#
# It is THREAD-LOCAL, and that is load-bearing rather than tidy. As a plain
# global it was read by threads that had not set it: this app serves sync
# routes from a threadpool, so while one thread held `_loading = True` every
# other thread's `is_platform_owned` returned at the guard and answered from a
# registry that may still be empty — `False` for every key, silently, which is
# the fail-open this module exists to prevent. Per-thread, a second thread
# instead performs its own import; CPython's per-module import lock makes it
# WAIT for the in-flight import and then see the completed module, so the
# cross-thread race resolves into a correct answer instead of a quiet one.
_loaded = False
_import_state = threading.local()


def register_platform_owned(domain: object, key: str) -> None:
    """Declare ``(domain, key)`` platform-owned. Called by ``settings_spec``."""
    _PLATFORM_OWNED.add((str(domain), key))


def _ensure_loaded() -> None:
    """Make sure the declarations have actually been executed.

    ``settings_spec`` registers at import time, but nothing guarantees anything
    in a given process has imported it before the first ``DomainSetting``
    write — a Celery task, a seed script or an admin route could reach the ORM
    listener first. An unpopulated registry would answer "not platform-owned"
    for every key, which is a security check that fails open. So the first
    question imports the declarations.

    The import alone is not proof the declarations ran
    ---------------------------------------------------
    ``import`` is a no-op when the module is already in ``sys.modules``, and a
    module goes into ``sys.modules`` at the START of its execution. So if a
    ``DomainSetting`` is constructed while ``settings_spec`` is itself mid-import
    — its registration loop runs at the END of that module — this function's
    import returns immediately, having executed nothing, and the registry is
    still empty. The re-entry guard does not cover that: the import was begun
    by some other entry point, not by this call, so ``_import_state.loading``
    is ``False`` here.

    An earlier version then set ``_loaded = True`` regardless. That LATCHED the
    empty registry for the life of the process: ``is_platform_owned`` answered
    ``False`` for every key forever, disabling the write listener and all four
    read overrides at once, from one unlucky import ordering, silently.

    So the flag is set only after the registry is VALIDATED non-empty, and a
    failure re-raises. A caller that arrives during the window gets a loud
    refusal it can retry — the next call re-imports (cheap: ``sys.modules``
    lookup), re-validates, and succeeds as soon as ``settings_spec`` has
    finished. Failing closed and retryable is the correct direction for a
    security check; latching open is not.

    No lock is taken here, and the re-entry flag is per-thread
    ------------------------------------------------------------
    An earlier note claimed "the worst a race does is repeat a no-op import".
    That was true of the import and false of the flag: as a plain global, the
    guard was READ by threads that had not set it, so a concurrent caller
    short-circuited on another thread's in-flight import and answered from a
    registry that might still be empty — ``False`` for every key, no
    exception, which is precisely the fail-open direction. Per-thread, the
    concurrent caller runs its own ``import`` and blocks on CPython's
    per-module import lock until the in-flight one finishes, then validates a
    populated registry. Every step remains idempotent, and no lock of ours
    wraps an ``import`` — that is what would deadlock against the import
    machinery's own.
    """
    global _loaded
    if _loaded or getattr(_import_state, "loading", False):
        return
    _import_state.loading = True
    try:
        import app.services.settings_spec  # noqa: F401  (import for its side effect)

        if not _PLATFORM_OWNED:
            raise RuntimeError(
                "app.services.settings_spec imported but registered no "
                "platform-owned settings. Its registration runs at the end of "
                "the module, so this means the import was a no-op against a "
                "partially-initialised module — the registry is not yet usable "
                "and answering from it would fail open. Retry: this resolves "
                "once that import completes."
            )
    finally:
        _import_state.loading = False
    # Reached only on a complete, validated load. Never move this above the
    # validation, and never put it in the `finally`.
    _loaded = True


def is_platform_owned(domain: object, key: str) -> bool:
    _ensure_loaded()
    return (str(domain), key) in _PLATFORM_OWNED


def platform_owned_keys() -> frozenset[tuple[str, str]]:
    """Every declared platform-owned ``(domain, key)``. For tests and audits."""
    _ensure_loaded()
    return frozenset(_PLATFORM_OWNED)


__all__ = [
    "is_platform_owned",
    "platform_owned_keys",
    "register_platform_owned",
]
