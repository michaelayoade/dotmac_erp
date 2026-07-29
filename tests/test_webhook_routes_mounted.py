"""
Inbound webhook receivers must actually be mounted on the app.

A webhook router that exists in app/api/ but is never included in
app/main.py fails silently: the sender gets 404/502 and the "working"
receiver never sees a single delivery. This bit /dotmac-sub/webhook —
the dotmac_sub finance-sync receiver was fully implemented and tested
but unreachable in prod.
"""

from app.main import app


def _route_paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_inbound_webhook_receivers_are_mounted():
    paths = _route_paths()
    for expected in (
        "/dotmac-sub/webhook",
        "/dotmac-academy/webhook",
        "/crm/webhook",
    ):
        assert any(p == expected or p.endswith(expected) for p in paths), (
            f"webhook receiver {expected} is not mounted on the app — "
            "include its router in app/main.py"
        )
