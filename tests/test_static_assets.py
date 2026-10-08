"""Regression coverage for immutable typeahead asset URLs."""

from urllib.parse import parse_qs, urlsplit

from app import static_assets


def test_typeahead_url_is_stable_for_unchanged_contents(tmp_path, monkeypatch):
    script = tmp_path / "typeahead.js"
    script.write_bytes(b"const version = 1;")
    monkeypatch.setattr(static_assets, "_TYPEAHEAD_SCRIPT", script)

    url = static_assets.typeahead_script_url()

    assert static_assets.typeahead_script_url() == url
    parsed = urlsplit(url)
    assert parsed.path == "/static/js/typeahead.js"
    assert parse_qs(parsed.query)["v"]


def test_typeahead_url_changes_when_contents_change(tmp_path, monkeypatch):
    script = tmp_path / "typeahead.js"
    script.write_bytes(b"const version = 1;")
    monkeypatch.setattr(static_assets, "_TYPEAHEAD_SCRIPT", script)
    previous_url = static_assets.typeahead_script_url()

    script.write_bytes(b"const version = 2;")
    current_url = static_assets.typeahead_script_url()

    assert current_url != previous_url
    assert urlsplit(current_url).path == urlsplit(previous_url).path
