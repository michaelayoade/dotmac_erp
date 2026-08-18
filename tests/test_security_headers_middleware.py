try:
    from builtins import ExceptionGroup  # py311+
except ImportError:  # py310 support
    from exceptiongroup import BaseExceptionGroup as ExceptionGroup

from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import Response
import anyio

from app.main import _is_no_response_runtime_error, csp_middleware


@pytest.mark.asyncio
async def test_csp_middleware_sets_security_headers():
    request = MagicMock(spec=Request)
    call_next = AsyncMock(return_value=Response(status_code=200))

    with patch("app.main.add_unsafe_eval_to_csp", return_value="default-src 'self'"):
        response = await csp_middleware(request, call_next)

    call_next.assert_awaited_once_with(request)
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert (
        response.headers["Strict-Transport-Security"]
        == "max-age=31536000; includeSubDomains"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_name", "marker", "expected"),
    [
        ("sla_policy_document_view", True, "SAMEORIGIN"),
        ("sla_policy_document_view", False, "DENY"),
        ("another_route", True, "DENY"),
    ],
)
async def test_frame_exception_is_limited_to_successful_sla_document_route(
    route_name, marker, expected
):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/sla-policies/id/document",
            "headers": [],
            "route": SimpleNamespace(name=route_name),
        }
    )
    if marker:
        request.state.allow_sla_document_frame = True
    call_next = AsyncMock(return_value=Response(status_code=200))

    with patch("app.main.add_unsafe_eval_to_csp", return_value="default-src 'self'"):
        response = await csp_middleware(request, call_next)

    assert response.headers["X-Frame-Options"] == expected


@pytest.mark.asyncio
async def test_csp_middleware_returns_204_for_no_response_runtime_error():
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.url.path = "/operations/dashboard"
    call_next = AsyncMock(side_effect=RuntimeError("No response returned."))

    response = await csp_middleware(request, call_next)

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_csp_middleware_returns_204_for_no_response_exception_group():
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.url.path = "/operations/dashboard"
    call_next = AsyncMock(
        side_effect=ExceptionGroup("group", [RuntimeError("No response returned.")])
    )

    response = await csp_middleware(request, call_next)

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_csp_middleware_returns_204_for_end_of_stream():
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.url.path = "/api/v1/anyio"
    call_next = AsyncMock(side_effect=anyio.EndOfStream())

    response = await csp_middleware(request, call_next)

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_csp_middleware_returns_204_for_end_of_stream_exception_group():
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.url.path = "/api/v1/anyio"
    call_next = AsyncMock(side_effect=ExceptionGroup("group", [anyio.EndOfStream()]))

    response = await csp_middleware(request, call_next)

    assert response.status_code == 204


def test_application_error_with_end_of_stream_context_is_not_a_disconnect():
    """A real endpoint failure must not be hidden by Starlette stream context."""
    application_error: ValueError | None = None
    try:
        raise anyio.EndOfStream
    except anyio.EndOfStream:
        try:
            raise ValueError("database query failed")
        except ValueError as exc:
            application_error = exc

    assert application_error is not None
    assert application_error.__context__ is not None
    assert _is_no_response_runtime_error(application_error) is False


@pytest.mark.asyncio
async def test_csp_middleware_does_not_swallow_mixed_exception_group():
    request = MagicMock(spec=Request)
    request.method = "POST"
    request.url.path = "/api/v1/payments/transfers/id/initiate"
    failure = ExceptionGroup(
        "request failed",
        [anyio.EndOfStream(), ValueError("database query failed")],
    )
    call_next = AsyncMock(side_effect=failure)

    with pytest.raises(ExceptionGroup) as exc_info:
        await csp_middleware(request, call_next)

    assert exc_info.value is failure
