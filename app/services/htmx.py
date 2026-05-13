"""HTMX response helpers used by web routes and web services.

See `PRD.md` §3.1 for the call-site inventory this module replaces.
"""

from __future__ import annotations

import json
from typing import Union

from fastapi import Request
from fastapi.responses import HTMLResponse


def is_htmx_request(request: Request) -> bool:
    """True when the current request was issued by HTMX (HX-Request: true)."""
    return request.headers.get("HX-Request", "").lower() == "true"


def htmx_response(
    content: str = "",
    *,
    trigger: Union[dict, str, None] = None,
    push_url: Union[str, None] = None,
    redirect: Union[str, None] = None,
    refresh: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    """Build an HTMX-aware HTMLResponse.

    `trigger` accepts either a bare event name ("taskCompleted") or a dict
    payload ({"showToast": {...}}) — dicts are JSON-encoded for HX-Trigger.
    """
    headers: dict[str, str] = {}
    if trigger:
        headers["HX-Trigger"] = (
            trigger if isinstance(trigger, str) else json.dumps(trigger)
        )
    if push_url:
        headers["HX-Push-Url"] = push_url
    if redirect:
        headers["HX-Redirect"] = redirect
    if refresh:
        headers["HX-Refresh"] = "true"
    return HTMLResponse(content=content, status_code=status_code, headers=headers)


def htmx_toast(message: str, level: str = "success") -> dict:
    """Build an HX-Trigger payload that fires the global showToast event."""
    return {"showToast": {"message": message, "type": level}}
