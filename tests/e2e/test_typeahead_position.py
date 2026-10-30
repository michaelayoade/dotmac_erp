"""The shared typeahead menu stays attached to its field in animated panels."""

from pathlib import Path

import pytest


TYPEAHEAD_SCRIPT = Path(__file__).resolve().parents[2] / "static/js/typeahead.js"


@pytest.mark.parametrize("transform", ["none", "translate(85px, 60px) scale(0.95)"])
def test_select_menu_tracks_its_field_inside_transformed_panel(page, transform):
    page.set_content(
        f"""
        <style>
          .panel {{ margin: 100px 0 0 180px; transform: {transform};
                    transform-origin: top left; }}
          .select-typeahead {{ position: relative; width: 180px; }}
          .select-typeahead__native {{ position: absolute; width: 1px; height: 1px; }}
          .select-typeahead__control {{ position: relative; }}
          .select-typeahead__input {{ box-sizing: border-box; width: 180px;
                                        height: 40px; }}
          .typeahead-results {{ position: relative; }}
          .typeahead-menu {{ position: fixed; min-width: 224px; }}
          .typeahead-option {{ height: 40px; }}
        </style>
        <div class="panel">
          <select aria-label="Source Type">
            <option value="">All Types</option>
            <option value="journal">Journal</option>
            <option value="payment">Payment</option>
          </select>
        </div>
        """
    )
    page.add_script_tag(path=str(TYPEAHEAD_SCRIPT))

    field = page.locator(".select-typeahead__input")
    field.click()
    menu = page.locator(".typeahead-menu")
    menu.wait_for(state="visible")
    field_box = field.bounding_box()
    menu_box = menu.bounding_box()
    assert field_box is not None
    assert menu_box is not None
    assert abs(menu_box["x"] - field_box["x"]) <= 2
    assert abs(menu_box["y"] - field_box["y"] - field_box["height"] - 4) <= 2
    assert menu_box["width"] >= 224


def test_remote_typeahead_status_tracks_its_field_inside_transformed_panel(page):
    page.set_content(
        """
        <style>
          .panel { margin: 100px 0 0 180px; transform: translate(85px, 60px)
                   scale(0.95); transform-origin: top left; }
          .typeahead-results { position: relative; }
          .typeahead-status { position: fixed; min-width: 224px; padding: 12px; }
          [data-typeahead-input] { box-sizing: border-box; width: 180px;
                                   height: 40px; }
        </style>
        <div class="panel" data-typeahead-url="/unused" data-typeahead-min="2">
          <input aria-label="Supplier" data-typeahead-input>
          <input type="hidden" data-typeahead-hidden>
          <div data-typeahead-results></div>
        </div>
        """
    )
    page.add_script_tag(path=str(TYPEAHEAD_SCRIPT))

    field = page.get_by_role("combobox", name="Supplier")
    field.fill("a")
    status = page.locator(".typeahead-status")
    status.wait_for(state="visible")
    field_box = field.bounding_box()
    status_box = status.bounding_box()
    assert field_box is not None
    assert status_box is not None
    assert abs(status_box["x"] - field_box["x"]) <= 2
    assert abs(status_box["y"] - field_box["y"] - field_box["height"] - 4) <= 2
