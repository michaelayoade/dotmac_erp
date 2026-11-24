from starlette.requests import Request

from app.web.csrf import _csrf_error


def test_employee_create_csrf_error_returns_to_form(caplog):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/people/hr/employees/new",
            "headers": [(b"accept", b"text/html")],
            "query_string": b"",
        }
    )
    request.state.request_id = "csrf-request-id"

    response = _csrf_error("Invalid CSRF token", request)

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/people/hr/employees/new?form_error=session_expired"
    )
    assert any(
        record.field == "csrf_token"
        and record.reason == "Invalid CSRF token"
        and record.request_id == "csrf-request-id"
        for record in caplog.records
        if record.getMessage() == "Employee creation form security check failed"
    )
