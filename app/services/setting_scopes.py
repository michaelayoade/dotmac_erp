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

_PLATFORM_OWNED: set[tuple[str, str]] = set()

# Import state for `_ensure_loaded`. `_loading` covers exactly one case: a
# query arriving underneath the import THIS module started, which must answer
# from whatever has registered so far rather than recurse forever. It does not
# cover a query arriving while `settings_spec` is mid-import from some other
# entry point — see `_ensure_loaded` for that case and why `_loaded` may not be
# set until the registry has been validated.
_loaded = False
_loading = False


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
    still empty. ``_loading`` does not cover that: the import was begun by some
    other entry point, not by us, so ``_loading`` is ``False`` here.

    An earlier version then set ``_loaded = True`` regardless. That LATCHED the
    empty registry for the life of the process: ``is_platform_owned`` answered
    ``False`` for every key forever, disabling the write listener and all four
    read overrides at once, from one unlucky import ordering, silently.

    So the flag is set only after the registry is VALIDATED non-empty, and a
    failure re-raises. A caller that arrives during the window gets a loud
    refusal it can retry — the next call re-imports (cheap: ``sys.modules``
    lookup), re-validates, and succeeds as soon as ``settings_spec`` has
    finished. Failing closed and retryable is the correct direction for a
    security check; latching open is not. No lock is taken: every step here is
    idempotent, so the worst a race does is repeat a no-op import, whereas a
    lock around an ``import`` invites deadlock against the import machinery's
    own.
    """
    global _loaded, _loading
    if _loaded or _loading:
        return
    _loading = True
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
        _loading = False
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
