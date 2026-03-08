"""Capture full-page screenshots of all static GET routes in dotmac_erp."""
import builtins
import re
import sys
import time
from pathlib import Path

_print = builtins.print


def print(*args, **kw):
    kw["flush"] = True
    _print(*args, **kw)

from playwright.sync_api import sync_playwright

BASE_URL = "http://160.119.127.195:8003"
USERNAME = "admin"
PASSWORD = "admin123"
OUTPUT_DIR = Path("/home/dotmac/projects/dotmac_erp/docs/screenshots")
VIEWPORT = {"width": 1440, "height": 900}
TIMEOUT = 45000  # 45s per page


def extract_routes():
    """Extract all static GET web routes by introspecting the FastAPI app."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.extract_routes import extract_web_routes

    return extract_web_routes()


def route_to_filename(route):
    """Convert route path to a safe filename."""
    if route == "/":
        return "home"
    name = route.strip("/").replace("/", "__")
    # Sanitize
    name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    return name


def get_module(route):
    """Extract module name from route for directory organization."""
    parts = route.strip("/").split("/")
    if not parts or parts[0] == "":
        return "_root"
    module_map = {
        "finance": "finance",
        "people": "people",
        "inventory": "inventory",
        "procurement": "procurement",
        "expense": "expense",
        "support": "support",
        "projects": "projects",
        "tasks": "projects",
        "fleet": "fleet",
        "admin": "admin",
        "automation": "automation",
        "public-sector": "public-sector",
        "coach": "coach",
        "scheduling": "people",
        "account": "account",
        "fixed-assets": "finance",
        "settings": "admin",
        "notifications": "admin",
    }
    return module_map.get(parts[0], "_other")


def launch_browser(pw):
    """Launch a fresh browser + context + page and login."""
    browser = pw.chromium.launch(
        headless=True,
        executable_path="/home/dotmac/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
    )
    context = browser.new_context(viewport=VIEWPORT, ignore_https_errors=True)
    page = context.new_page()

    page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
    time.sleep(1)

    login_result = page.evaluate("""async () => {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
        const resp = await fetch('/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrf || '',
            },
            body: JSON.stringify({username: '""" + USERNAME + """', password: '""" + PASSWORD + """'}),
        });
        const data = await resp.json();
        return {status: resp.status, data: data};
    }""")

    if login_result["status"] != 200:
        raise RuntimeError(f"Login failed: {login_result}")

    page.goto(f"{BASE_URL}/", wait_until="networkidle", timeout=15000)
    time.sleep(1)
    if "/login" in page.url:
        raise RuntimeError(f"Session not established, redirected to {page.url}")

    return browser, page


def main():
    routes = extract_routes()
    print(f"Found {len(routes)} static GET routes to capture")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        print("Logging in...")
        browser, page = launch_browser(p)
        print(f"Login successful, now at {page.url}")

        success = 0
        errors = []

        for i, route in enumerate(routes):
            module = get_module(route)
            module_dir = OUTPUT_DIR / module
            module_dir.mkdir(parents=True, exist_ok=True)

            filename = route_to_filename(route)
            filepath = module_dir / f"{filename}.png"

            try:
                url = f"{BASE_URL}{route}"
                page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
                time.sleep(0.5)

                page.screenshot(path=str(filepath), full_page=True, timeout=30000)
                success += 1
                status = "OK"
            except Exception as e:
                error_msg = str(e)[:100]
                errors.append((route, error_msg))
                status = f"FAIL: {error_msg}"

                # If browser/context crashed, relaunch
                if "has been closed" in error_msg or "crashed" in error_msg:
                    print(f"  Browser crashed, relaunching...")
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser, page = launch_browser(p)
                    print(f"  Re-logged in OK")

            pct = ((i + 1) / len(routes)) * 100
            print(f"[{i+1}/{len(routes)}] ({pct:.0f}%) {route} -> {status}")

        browser.close()

    print(f"\n{'=' * 60}")
    print(f"Capture complete: {success}/{len(routes)} pages captured")
    print(f"Output: {OUTPUT_DIR}")
    if errors:
        print(f"\n{len(errors)} errors:")
        for route, err in errors:
            print(f"  {route}: {err}")

    # Write index
    with open(OUTPUT_DIR / "INDEX.md", "w") as f:
        f.write("# ERP UI Screenshots\n\n")
        f.write(f"Captured: {time.strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Total: {success}/{len(routes)} pages\n\n")
        current_module = None
        for route in routes:
            module = get_module(route)
            if module != current_module:
                f.write(f"\n## {module}\n\n")
                current_module = module
            filename = route_to_filename(route)
            filepath = f"{module}/{filename}.png"
            err = next((e for r, e in errors if r == route), None)
            if err:
                f.write(f"- {route} - FAILED: {err}\n")
            else:
                f.write(f"- [{route}]({filepath})\n")


if __name__ == "__main__":
    main()
