"""The shared select menu must remain usable across animated form cards."""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def base_url() -> str:
    """This isolated browser check does not use the E2E application server."""
    return "http://unused.invalid"


@pytest.mark.parametrize("top,opens_above", [(120, False), (480, True)])
def test_select_menu_escapes_animated_card_and_uses_available_space(
    page: Page, top: int, opens_above: bool
) -> None:
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        f"""
        <form>
        <section class="card stagger-in" style="position:absolute;top:{top}px;left:20px;width:360px;padding:20px">
            <select aria-label="Account" name="account" class="form-select">
                <option value="">Select account</option>
                {"".join(f'<option value="{i}">Option {i}</option>' for i in range(1, 7))}
            </select>
        </section>
        <section class="card stagger-in" style="position:absolute;top:{top + 100}px;left:20px;width:360px;height:300px">
            Following card
        </section>
        </form>
        <div style="position:absolute;top:1200px;height:1px"></div>
        """
    )
    page.add_style_tag(
        content=(ROOT / "static/css/app.css").read_text(encoding="utf-8")
    )
    page.add_script_tag(
        content=(ROOT / "static/js/typeahead.js").read_text(encoding="utf-8")
    )
    page.wait_for_timeout(600)  # Let the card's entrance transform settle.

    input_field = page.locator(".select-typeahead__input")
    input_field.focus()
    menu = page.get_by_role("listbox")
    expect(menu).to_be_visible()
    assert menu.evaluate(
        "element => element.closest('.typeahead-results').parentElement === document.body"
    )

    field_box = input_field.bounding_box()
    menu_box = menu.bounding_box()
    assert field_box is not None and menu_box is not None
    if opens_above:
        assert menu_box["y"] + menu_box["height"] <= field_box["y"] - 2
        page.set_viewport_size({"width": 800, "height": 1000})
        page.wait_for_function("""() => {
            const field = document.querySelector('.select-typeahead__input').getBoundingClientRect();
            const popup = document.querySelector('.typeahead-menu').getBoundingClientRect();
            return popup.top >= field.bottom + 2;
        }""")
    else:
        assert menu_box["y"] >= field_box["y"] + field_box["height"] + 2
        page.evaluate("window.scrollTo(0, 50)")
        page.wait_for_function("""() => {
            const field = document.querySelector('.select-typeahead__input').getBoundingClientRect();
            const popup = document.querySelector('.typeahead-menu').getBoundingClientRect();
            return Math.abs(popup.top - field.bottom - 4) < 1;
        }""")

    option = menu.get_by_role("option", name="Option 2")
    option_box = option.bounding_box()
    assert option_box is not None
    page.evaluate("""() => {
        window.selectionChanges = 0;
        document.querySelector('select[name="account"]').addEventListener(
            'change', () => window.selectionChanges++
        );
    }""")
    assert option.evaluate("""element => {
        const rect = element.getBoundingClientRect();
        return document.elementFromPoint(
            rect.left + rect.width / 2, rect.top + rect.height / 2
        ) === element;
    }""")
    option.click()
    assert page.locator('select[name="account"]').input_value() == "2"
    assert (
        page.evaluate("new FormData(document.querySelector('form')).get('account')")
        == "2"
    )
    assert page.evaluate("window.selectionChanges") == 1
    assert page.locator(".typeahead-results.is-portaled").count() == 0
