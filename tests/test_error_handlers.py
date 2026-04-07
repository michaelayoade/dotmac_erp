from __future__ import annotations

from app.errors import _unwrap_http_exception
from fastapi import HTTPException


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


def test_wrapped_http_exception_returns_original_status(client):
    app = getattr(client, "app", getattr(client, "_app"))

    @app.get("/_test_wrapped_http_exception")
    def _test_wrapped_http_exception():
        raise HTTPException(status_code=400, detail="Invalid CSRF token")

    response = client.get(
        "/_test_wrapped_http_exception",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "http_400",
        "message": "Invalid CSRF token",
        "details": None,
    }


def test_unwrap_http_exception():
    class _WrappedError(Exception):
        def __init__(self) -> None:
            self.exceptions = [
                HTTPException(status_code=400, detail="Invalid CSRF token")
            ]

    wrapped = _WrappedError()
    http_exc = _unwrap_http_exception(wrapped)

    assert http_exc is not None
    assert isinstance(http_exc, HTTPException)
    assert http_exc.status_code == 400
    assert http_exc.detail == "Invalid CSRF token"
