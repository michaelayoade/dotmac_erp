"""Browser contract for organization-formatted native calendar fields."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).parents[1]
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def test_calendar_uses_organization_display_and_iso_form_value() -> None:
    with sync_playwright() as playwright:
        executable = (
            CHROME if CHROME.exists() else Path(playwright.chromium.executable_path)
        )
        if not executable.exists():
            pytest.skip("No installed Chromium browser")
        browser = playwright.chromium.launch(
            executable_path=str(executable), headless=True
        )
        page = browser.new_page(locale="en-US")
        page.set_content(
            '<html data-date-format="DD/MM/YYYY"><body><form>'
            '<label for="dob">Date of Birth</label>'
            '<input type="date" id="dob" name="date_of_birth" '
            'required min="1900-01-01" max="2026-12-31" value="2000-11-13">'
            '<label for="hearing">Hearing Date &amp; Time</label>'
            '<input type="datetime-local" id="hearing" name="hearing_at" '
            'value="2026-11-13T09:30">'
            "</form></body></html>"
        )
        page.add_style_tag(path=str(ROOT / "static/css/date-picker.css"))
        page.add_script_tag(path=str(ROOT / "static/js/date-picker.js"))
        original = page.locator("#dob")
        visible = original.locator("..").locator(".erp-date-picker__value")
        page.wait_for_function(
            "document.querySelector('.erp-date-picker__value').textContent === '13/11/2000'"
        )
        assert original.get_attribute("type") == "date"
        assert visible.inner_text() == "13/11/2000"
        assert original.evaluate("element => element.checkValidity()")
        page.get_by_role("button", name="Date of Birth").click()
        calendar = page.get_by_role("dialog", name="Choose date")
        assert calendar.is_visible()
        calendar.get_by_role("combobox", name="Year").select_option("2024")
        calendar.get_by_role("combobox", name="Month").select_option("2")
        calendar.get_by_role("button", name="29/02/2024").click()
        assert visible.inner_text() == "29/02/2024"
        assert original.input_value() == "2024-02-29"
        assert (
            page.evaluate(
                "new FormData(document.querySelector('form')).get('date_of_birth')"
            )
            == "2024-02-29"
        )
        original.evaluate("element => { element.max = '2024-02-28'; }")
        page.get_by_role("button", name="Date of Birth").click()
        assert calendar.get_by_role("button", name="29/02/2024").is_disabled()
        page.keyboard.press("Escape")
        original.evaluate("element => { element.max = '2026-12-31'; }")
        page.evaluate("document.documentElement.dataset.dateFormat = 'MM/DD/YYYY'")
        original.dispatch_event("change")
        assert visible.inner_text() == "02/29/2024"
        original.evaluate("element => { element.value = '2025-03-02'; }")
        assert visible.inner_text() == "03/02/2025"
        original.fill("")
        assert not original.evaluate("element => element.checkValidity()")

        hearing = page.locator("#hearing")
        hearing_display = hearing.locator("..").locator(".erp-date-picker__value")
        assert hearing_display.inner_text() == "13/11/2026 09:30"
        hearing.dispatch_event("change")
        assert hearing_display.inner_text() == "11/13/2026 09:30"
        page.get_by_role("button", name="Hearing Date & Time").click()
        date_time_calendar = page.get_by_role("dialog", name="Choose date and time")
        date_time_calendar.get_by_role("button", name="11/14/2026").click()
        date_time_calendar.locator('input[type="time"]').fill("14:45")
        date_time_calendar.get_by_role("button", name="Apply").click()
        assert hearing.input_value() == "2026-11-14T14:45"
        assert hearing_display.inner_text() == "11/14/2026 14:45"
        page.evaluate("document.documentElement.dataset.dateFormat = 'DD MMM YYYY'")
        hearing.dispatch_event("change")
        assert hearing_display.inner_text() == "14 Nov 2026 14:45"
        browser.close()
