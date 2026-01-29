import logging
import time

from celery.beat import ScheduleEntry, Scheduler

from app.services.scheduler_config import build_beat_schedule

logger = logging.getLogger(__name__)


class DbScheduler(Scheduler):
    """Custom Celery beat scheduler that refreshes schedule from database."""

    def __init__(self, *args, **kwargs):
        self._last_refresh_at = 0.0  # Must be set before super().__init__()
        self._schedule_dict: dict = {}  # Track raw dict for comparison
        super().__init__(*args, **kwargs)

    def setup_schedule(self):
        """Initialize the schedule from database."""
        self._refresh_schedule(force=True)

    def tick(self):
        """Called by beat on each iteration - refresh schedule periodically."""
        self._refresh_schedule()
        return super().tick()

    def _refresh_schedule(self, force: bool = False):
        """Refresh schedule from database if refresh interval has passed."""
        refresh_seconds = int(self.app.conf.get("beat_refresh_seconds", 30))
        now = time.monotonic()
        if not force and (now - self._last_refresh_at < max(refresh_seconds, 1)):
            return

        try:
            new_schedule_dict = build_beat_schedule()
        except Exception:
            logger.exception("Failed to refresh beat schedule from database")
            return

        # Only update if schedule has changed
        if new_schedule_dict != self._schedule_dict:
            self._schedule_dict = new_schedule_dict
            self._update_schedule_entries(new_schedule_dict)
            logger.info("Beat schedule refreshed with %d tasks", len(new_schedule_dict))

        self._last_refresh_at = now

    def _update_schedule_entries(self, schedule_dict: dict):
        """Convert schedule dict to ScheduleEntry objects and update scheduler."""
        new_entries = {}
        for name, entry_dict in schedule_dict.items():
            new_entries[name] = ScheduleEntry(
                name=name,
                task=entry_dict["task"],
                schedule=entry_dict["schedule"],
                args=entry_dict.get("args", ()),
                kwargs=entry_dict.get("kwargs", {}),
                options=entry_dict.get("options", {}),
                app=self.app,
            )
        self.schedule.clear()
        self.schedule.update(new_entries)
