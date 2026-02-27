from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.people.scheduling import RotationType
from app.services.people.scheduling.schedule_generator import ScheduleGenerator
from app.services.people.scheduling.scheduling_service import SchedulingService


def test_create_pattern_rotating_forces_cycle_weeks_two() -> None:
    db = MagicMock()
    svc = SchedulingService(db)

    pattern = svc.create_pattern(
        org_id=uuid4(),
        pattern_code="ROT-A",
        pattern_name="Rotating A",
        rotation_type=RotationType.ROTATING,
        day_shift_type_id=uuid4(),
        night_shift_type_id=uuid4(),
        cycle_weeks=1,
        day_work_days=["MON", "TUE"],
        night_work_days=["WED", "THU"],
    )

    assert pattern.cycle_weeks == 2


def test_pattern_lines_drive_weekend_alternation() -> None:
    generator = ScheduleGenerator(db=SimpleNamespace())
    day_shift_id = uuid4()
    night_shift_id = uuid4()

    pattern = SimpleNamespace(
        rotation_type=RotationType.ROTATING,
        day_shift_type_id=day_shift_id,
        night_shift_type_id=night_shift_id,
        cycle_weeks=2,
        work_days=["MON", "TUE", "WED", "THU", "FRI"],
        day_work_days=["MON", "TUE", "WED", "THU", "FRI"],
        night_work_days=["MON", "TUE", "WED", "THU", "FRI"],
        pattern_lines=[
            {"week_index": 1, "day": "SAT", "shift_slot": "DAY"},
            {"week_index": 1, "day": "SUN", "shift_slot": "OFF"},
            {"week_index": 2, "day": "SAT", "shift_slot": "OFF"},
            {"week_index": 2, "day": "SUN", "shift_slot": "DAY"},
        ],
    )
    assignment = SimpleNamespace(
        effective_from=date(2026, 3, 2), rotation_week_offset=0
    )

    week1_sat = generator._determine_shift_type(
        pattern, assignment, date(2026, 3, 7), date(2026, 3, 1)
    )
    week1_sun = generator._determine_shift_type(
        pattern, assignment, date(2026, 3, 8), date(2026, 3, 1)
    )
    week2_sun = generator._determine_shift_type(
        pattern, assignment, date(2026, 3, 15), date(2026, 3, 1)
    )

    assert week1_sat == day_shift_id
    assert week1_sun is None
    assert week2_sun == day_shift_id
