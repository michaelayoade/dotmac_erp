from app.services.people.perf.web.cycle_web import CycleWebService


def test_extract_pms_template_config_returns_none_when_disabled() -> None:
    svc = CycleWebService()
    form_data = {"pms_config_enabled": "false"}

    assert svc._extract_pms_template_config(form_data) is None


def test_extract_pms_template_config_parses_enabled_values() -> None:
    svc = CycleWebService()
    form_data = {
        "pms_config_enabled": "true",
        "objective_weight_pct": "70",
        "process_weight_pct": "10",
        "competency_weight_pct": "20",
        "required_competency_count": "5",
        "required_development_focus_count": "3",
        "evidence_required": "on",
    }

    config = svc._extract_pms_template_config(form_data)
    assert config is not None
    assert config["objective_weight_pct"] == 70
    assert config["process_weight_pct"] == 10
    assert config["competency_weight_pct"] == 20
    assert config["required_competency_count"] == 5
    assert config["required_development_focus_count"] == 3
    assert config["evidence_required"] is True
