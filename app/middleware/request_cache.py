"""
RequestCacheMiddleware - Middleware for request-scoped cache cleanup.

Ensures the request cache is cleared after each request completes,
preventing memory leaks and cache pollution between requests.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.services.request_cache import request_cache


class RequestCacheMiddleware(BaseHTTPMiddleware):
    """
    Middleware that clears the request cache after each request.

    This middleware should be added early in the middleware chain
    to ensure the cache is always cleaned up.

    Usage in main.py:
        from app.middleware.request_cache import RequestCacheMiddleware
        app.add_middleware(RequestCacheMiddleware)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request and ensure cache cleanup."""
        try:
            response = await call_next(request)
            return response
        finally:
            # Always clear the request cache
            request_cache.clear()
