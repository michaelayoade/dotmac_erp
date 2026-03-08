"""Retry timed-out pages with longer timeout and capture error details."""
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
TIMEOUT = 45000  # 45s — triple the original

FAILED_ROUTES = [
    "/support/dashboard",
    "/support/reports/aging",
    "/support/reports/breaches",
    "/support/teams",
    "/support/teams/new",
    "/support/tickets",
    "/support/tickets/archived",
    "/support/tickets/new",
    "/tasks",
    "/tax/codes",
    "/tax/codes/new",
    "/tax/jurisdictions",
    "/tax/periods",
    "/tax/periods/overdue",
    "/tax/returns",
    "/tax/returns/new",
]


def route_to_filename(route):
    if route == "/":
        return "home"
    name = route.strip("/").replace("/", "__")
    name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    return name


def get_module(route):
    parts = route.strip("/").split("/")
    if not parts or parts[0] == "":
        return "_root"
    module_map = {
        "support": "support", "tasks": "projects", "tax": "finance",
    }
    return module_map.get(parts[0], "_other")


def main():
    print(f"Retrying {len(FAILED_ROUTES)} timed-out pages (timeout={TIMEOUT}ms)")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path="/home/dotmac/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
        )
        context = browser.new_context(viewport=VIEWPORT, ignore_https_errors=True)
        page = context.new_page()

        # Enable console logging to catch JS errors
        js_errors = []
        page.on("console", lambda msg: js_errors.append(f"[{msg.type}] {msg.text}") if msg.type in ("error", "warning") else None)
        page.on("pageerror", lambda err: js_errors.append(f"[PAGE_ERROR] {err.message}"))

        # Login
        print("Logging in...")
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
            print(f"ERROR: Login failed: {login_result}")
            browser.close()
            sys.exit(1)

        page.goto(f"{BASE_URL}/", wait_until="networkidle", timeout=15000)
        time.sleep(1)
        print(f"Login OK, now at {page.url}")

        success = 0
        still_failing = []

        for i, route in enumerate(FAILED_ROUTES):
            js_errors.clear()
            module = get_module(route)
            module_dir = OUTPUT_DIR / module
            module_dir.mkdir(parents=True, exist_ok=True)
            filename = route_to_filename(route)
            filepath = module_dir / f"{filename}.png"

            url = f"{BASE_URL}{route}"
            print(f"\n[{i+1}/{len(FAILED_ROUTES)}] {route}")

            try:
                resp = page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
                time.sleep(1)

                status_code = resp.status if resp else "no response"
                page_title = page.title()
                current_url = page.url

                print(f"  HTTP {status_code} | title: {page_title} | url: {current_url}")

                # Check for server errors in page content
                body_text = page.evaluate("() => document.body?.innerText?.substring(0, 500) || ''")
                if "Internal Server Error" in body_text or "500" in str(status_code):
                    print("  SERVER ERROR detected")
                    # Capture the error page screenshot anyway
                    error_path = module_dir / f"{filename}_ERROR.png"
                    page.screenshot(path=str(error_path), full_page=True)
                    still_failing.append((route, f"HTTP {status_code}", body_text[:200]))
                    continue

                if "/login" in current_url and route != "/login":
                    print("  REDIRECTED TO LOGIN — session expired?")
                    still_failing.append((route, "redirect_to_login", ""))
                    continue

                page.screenshot(path=str(filepath), full_page=True)
                success += 1
                print("  OK — screenshot saved")

                if js_errors:
                    print(f"  JS warnings/errors: {len(js_errors)}")
                    for err in js_errors[:3]:
                        print(f"    {err[:120]}")

            except Exception as e:
                error_msg = str(e)[:200]
                print(f"  FAIL: {error_msg}")

                # Try to get whatever loaded
                try:
                    error_path = module_dir / f"{filename}_ERROR.png"
                    page.screenshot(path=str(error_path), full_page=True)
                    print("  Captured partial screenshot as _ERROR")

                    # Check response status from network
                    body_text = page.evaluate("() => document.body?.innerText?.substring(0, 500) || ''")
                    print(f"  Page content preview: {body_text[:150]}")
                    still_failing.append((route, "timeout", body_text[:200]))
                except Exception:
                    still_failing.append((route, "timeout+screenshot_fail", error_msg))

        browser.close()

    print(f"\n{'='*60}")
    print(f"Retry complete: {success}/{len(FAILED_ROUTES)} succeeded")

    if still_failing:
        print(f"\n{len(still_failing)} still failing:")
        for route, reason, detail in still_failing:
            print(f"\n  {route}")
            print(f"    Reason: {reason}")
            if detail:
                print(f"    Detail: {detail[:200]}")


if __name__ == "__main__":
    main()
