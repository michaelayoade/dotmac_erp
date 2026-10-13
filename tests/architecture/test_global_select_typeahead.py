from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TYPEAHEAD_JS = ROOT / "static" / "js" / "typeahead.js"


def test_base_template_loads_shared_typeahead() -> None:
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    assert '<script defer src="{{ typeahead_script_url }}"></script>' in base


def test_every_native_select_is_progressively_enhanced() -> None:
    script = TYPEAHEAD_JS.read_text(encoding="utf-8")

    assert 'scope.querySelectorAll("select")' in script
    assert "initSelectTypeahead" in script
    assert "new MutationObserver" in script
    assert 'document.body.addEventListener("htmx:afterSwap"' in script

    opted_out = []
    for template in (ROOT / "templates").rglob("*.html"):
        if "data-no-typeahead" in template.read_text(encoding="utf-8"):
            opted_out.append(template.relative_to(ROOT).as_posix())
    assert opted_out == []


def test_typeahead_supports_remote_loading_and_accessible_keyboard_use() -> None:
    script = TYPEAHEAD_JS.read_text(encoding="utf-8")

    assert "data-typeahead-url" in script
    assert "AbortController" in script
    assert "Loading options…" in script
    assert "aria-autocomplete" in script
    assert "aria-activedescendant" in script
    assert 'event.key === "ArrowDown"' in script
    assert 'event.key === "Enter"' in script
    assert 'event.key === "Escape"' in script
    assert "aria-multiselectable" in script


def test_typeahead_component_styles_are_built_from_source() -> None:
    entrypoint = (ROOT / "src" / "css" / "main.css").read_text(encoding="utf-8")
    component = (ROOT / "src" / "css" / "components" / "_typeahead.css").read_text(
        encoding="utf-8"
    )

    assert "@import 'components/_typeahead.css';" in entrypoint
    assert ".select-typeahead__native" in component
    assert ".typeahead-option.is-active" in component
    assert "min-height: 44px" in component
    assert "position: fixed" in component
