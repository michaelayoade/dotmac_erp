"""Extract all static GET web routes by introspecting the FastAPI app."""

import sys
from pathlib import Path

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402


def extract_web_routes() -> list[str]:
    """Get all static GET routes that serve HTML pages (not API endpoints)."""
    routes = []
    for route in app.routes:
        # Only look at actual Route objects with GET method
        if not hasattr(route, "methods") or "GET" not in route.methods:
            continue

        path = route.path

        # Skip API endpoints by path prefix
        if path.startswith("/api/"):
            continue

        # Skip parameterized routes (e.g. /finance/ar/invoices/{invoice_id})
        if "{" in path:
            continue

        # Only include routes served by web modules (app.web.*)
        endpoint = getattr(route, "endpoint", None)
        if endpoint:
            mod = getattr(endpoint, "__module__", "")
            if not mod.startswith("app.web"):
                continue

        # Skip non-page endpoints
        skip_suffixes = (
            "/callback",
            "/logout",
            "/health",
            "/metrics",
            "/favicon.ico",
        )
        if any(path.endswith(s) for s in skip_suffixes):
            continue

        # Skip static/file serving
        skip_prefixes = ("/static/", "/files/", "/uploads/")
        if any(path.startswith(s) for s in skip_prefixes):
            continue

        routes.append(path)

    return sorted(set(routes))


if __name__ == "__main__":
    routes = extract_web_routes()
    print(f"Total static GET web routes: {len(routes)}")
    for r in routes:
        print(r)
