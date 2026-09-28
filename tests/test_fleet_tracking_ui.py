from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_vehicle_detail_extends_existing_page_with_tracking_placeholders():
    template = (ROOT / "templates" / "fleet" / "vehicle_detail.html").read_text(
        encoding="utf-8"
    )

    assert "{% if can_read_tracking %}" in template
    assert "Tracking" in template
    assert "tracker.unique_id" in template
    assert "tracker.provider" in template
    assert "tracker.tracker_model" in template
    assert "tracker_connection_status" in template
    assert "tracker_last_update" in template
    assert "leaflet" not in template.lower()
