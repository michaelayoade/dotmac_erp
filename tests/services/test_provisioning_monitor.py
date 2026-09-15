from types import SimpleNamespace

from app.services.people.hr.provisioning_monitor import record_stage, stage_rows


def test_record_stage_tracks_attempt_and_failure() -> None:
    employee = SimpleNamespace(
        workforce_provisioning_state={}, workforce_provisioning_status=None
    )

    record_stage(employee, "nextcloud", "running")
    record_stage(employee, "nextcloud", "failed", error="OCS request failed")

    stage = employee.workforce_provisioning_state["nextcloud"]
    assert stage["attempts"] == 1
    assert stage["error"] == "OCS request failed"
    assert employee.workforce_provisioning_status == "failed"


def test_stage_rows_returns_all_relay_stages() -> None:
    employee = SimpleNamespace(workforce_provisioning_state={})

    rows = stage_rows(employee)

    assert [row["key"] for row in rows] == [
        "mailcow",
        "activation_email",
        "nextcloud",
        "selfcare",
        "talk",
    ]
    assert {row["status"] for row in rows} == {"not_started"}
