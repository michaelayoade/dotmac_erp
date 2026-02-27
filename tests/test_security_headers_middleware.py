from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import Response

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
