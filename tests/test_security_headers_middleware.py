try:
    from builtins import ExceptionGroup  # py311+
except ImportError:  # py310 support
    from exceptiongroup import BaseExceptionGroup as ExceptionGroup

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import Response
import anyio

from app.main import csp_middleware


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
    call_next = AsyncMock(
        side_effect=ExceptionGroup("group", [anyio.EndOfStream()])
    )

    response = await csp_middleware(request, call_next)

    assert response.status_code == 204
