"""
Every beat-scheduled task must be registered with the Celery app.

Celery's ``autodiscover_tasks(["app.tasks"])`` only imports
``app.tasks.tasks`` (which doesn't exist); real registration happens when
``app/tasks/__init__.py`` imports each task module. A module that is beat-
scheduled but missing from that import list is silently dead: beat enqueues
it on schedule and every worker discards it with "Received unregistered
task". This bit ``app.tasks.dotmac_sub`` (the entire dotmac_sub finance
sync never ran in prod), plus ``exchange_rates`` and ``license``.
"""

from app.services.scheduler_config import _builtin_beat_schedule


def test_all_builtin_beat_tasks_are_registered():
    import app.tasks  # noqa: F401 — imports every task module, registering each task
    from app.celery_app import celery_app

    scheduled = {entry["task"] for entry in _builtin_beat_schedule().values()}
    missing = sorted(scheduled - set(celery_app.tasks))

    assert not missing, (
        "Beat-scheduled tasks not registered with the Celery app "
        f"(import their modules in app/tasks/__init__.py): {missing}"
    )
