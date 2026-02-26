from __future__ import annotations


def test_unknown_web_route_renders_html_404_template(client):
    response = client.get("/mimi", headers={"accept": "text/html"})

    assert response.status_code == 404
    assert "text/html" in response.headers.get("content-type", "")
    assert "Page Not Found" in response.text


def test_unknown_api_route_returns_json_404_payload(client):
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "code": "http_404",
        "message": "Not Found",
        "details": None,
    }
