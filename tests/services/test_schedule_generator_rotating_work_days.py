from types import SimpleNamespace
from uuid import uuid4

from app.models.people.scheduling import RotationType
from app.services.people.scheduling.schedule_generator import ScheduleGenerator


class TestGetWorkDaysForShift:
    def test_non_rotating_uses_work_days(self) -> None:
        generator = ScheduleGenerator(db=SimpleNamespace())
        day_shift_id = uuid4()

        pattern = SimpleNamespace(
            rotation_type=RotationType.DAY_ONLY,
            work_days=["MON", "TUE", "WED"],
            day_shift_type_id=day_shift_id,
            night_shift_type_id=None,
            day_work_days=["MON"],
            night_work_days=["SUN"],
        )

        assert generator._get_work_days_for_shift(pattern, day_shift_id) == [
            "MON",
            "TUE",
            "WED",
        ]

    def test_rotating_day_shift_uses_day_work_days(self) -> None:
        generator = ScheduleGenerator(db=SimpleNamespace())
        day_shift_id = uuid4()
        night_shift_id = uuid4()

        pattern = SimpleNamespace(
            rotation_type=RotationType.ROTATING,
            work_days=["MON", "TUE", "WED", "THU", "FRI"],
            day_shift_type_id=day_shift_id,
            night_shift_type_id=night_shift_id,
            day_work_days=["MON", "WED", "FRI"],
            night_work_days=["SAT", "SUN"],
        )

        assert generator._get_work_days_for_shift(pattern, day_shift_id) == [
            "MON",
            "WED",
            "FRI",
        ]

    def test_rotating_night_shift_uses_night_work_days(self) -> None:
        generator = ScheduleGenerator(db=SimpleNamespace())
        day_shift_id = uuid4()
        night_shift_id = uuid4()

        pattern = SimpleNamespace(
            rotation_type=RotationType.ROTATING,
            work_days=["MON", "TUE", "WED", "THU", "FRI"],
            day_shift_type_id=day_shift_id,
            night_shift_type_id=night_shift_id,
            day_work_days=["MON", "WED", "FRI"],
            night_work_days=["SAT", "SUN"],
        )

        assert generator._get_work_days_for_shift(pattern, night_shift_id) == [
            "SAT",
            "SUN",
        ]

    def test_rotating_falls_back_to_work_days_when_specific_days_missing(self) -> None:
        generator = ScheduleGenerator(db=SimpleNamespace())
        day_shift_id = uuid4()

        pattern = SimpleNamespace(
            rotation_type=RotationType.ROTATING,
            work_days=["MON", "TUE", "WED", "THU", "FRI"],
            day_shift_type_id=day_shift_id,
            night_shift_type_id=None,
            day_work_days=None,
            night_work_days=None,
        )

        assert generator._get_work_days_for_shift(pattern, day_shift_id) == [
            "MON",
            "TUE",
            "WED",
            "THU",
            "FRI",
        ]
