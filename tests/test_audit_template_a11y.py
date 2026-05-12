"""Unit tests for the template a11y scanner heuristic.

Guards against false-positives that historically tripped up the scan
(Jinja expressions, Alpine.js x-text bindings, sr-only text).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the script as a module without making it importable elsewhere.
_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "audit_template_a11y.py"
spec = importlib.util.spec_from_file_location("audit_template_a11y", _SCRIPT_PATH)
audit_module = importlib.util.module_from_spec(spec)
sys.modules["audit_template_a11y"] = audit_module
spec.loader.exec_module(audit_module)


# Inner-text detection — false positives this scan historically generated
def test_has_inner_text_recognizes_jinja_expression() -> None:
    assert audit_module.has_inner_text("<span>{{ label }}</span>") is True


def test_has_inner_text_recognizes_x_text_binding() -> None:
    assert audit_module.has_inner_text('<p x-text="user.name"></p>') is True


def test_has_inner_text_recognizes_plain_text() -> None:
    assert audit_module.has_inner_text("Submit") is True


def test_has_inner_text_recognizes_nested_jinja() -> None:
    assert audit_module.has_inner_text("<div><span>{{ count }}</span></div>") is True


# Inner-text detection — true negatives
def test_has_inner_text_rejects_only_svg() -> None:
    svg = '<svg viewBox="0 0 24 24"><path d="..."/></svg>'
    assert audit_module.has_inner_text(svg) is False


def test_has_inner_text_rejects_whitespace_only() -> None:
    assert audit_module.has_inner_text("   \n  ") is False


def test_has_inner_text_rejects_nbsp_only() -> None:
    assert audit_module.has_inner_text("&nbsp;&nbsp;") is False


# Control-detection
def test_input_with_aria_label_is_labeled() -> None:
    html = '<form><input type="text" name="q" aria-label="Search"></form>'
    assert audit_module.find_unlabeled_controls(html) == []


def test_input_with_explicit_label_is_labeled() -> None:
    html = '<label for="q">Search</label><input type="text" name="q" id="q">'
    assert audit_module.find_unlabeled_controls(html) == []


def test_input_without_label_is_flagged() -> None:
    html = '<form><input type="text" name="q"></form>'
    results = audit_module.find_unlabeled_controls(html)
    assert len(results) == 1


def test_hidden_input_is_not_flagged() -> None:
    html = '<input type="hidden" name="csrf_token" value="x">'
    assert audit_module.find_unlabeled_controls(html) == []


def test_input_nested_in_label_is_implicit_labeled() -> None:
    """Implicit labeling: input inside a <label> element is accessible without for=."""
    html = """
    <label class="flex items-center gap-3">
      <input type="checkbox" name="agree">
      <span>I agree to terms</span>
    </label>
    """
    assert audit_module.find_unlabeled_controls(html) == []


def test_input_outside_label_block_is_flagged() -> None:
    """An input that follows a closed <label> is NOT implicitly labeled."""
    html = """
    <label class="block">Section heading</label>
    <input type="text" name="foo">
    """
    results = audit_module.find_unlabeled_controls(html)
    assert len(results) == 1


def test_strip_jinja_comments_preserves_line_numbers(tmp_path) -> None:
    """Jinja `{# ... #}` blocks contain doc/example HTML that's never rendered.

    The replacement should preserve line breaks so violation line numbers
    in the rest of the file remain accurate.
    """
    f = tmp_path / "t.html"
    f.write_text(
        "{# Usage example:\n"
        '   <input type="text" name="example">\n'
        "#}\n"
        '<input type="text" name="real_violation">\n'
    )
    findings = audit_module.scan_file(f)
    # Only the real input outside the comment should be flagged
    assert len(findings["unlabeled_controls"]) == 1
    line, _ = findings["unlabeled_controls"][0]
    assert line == 4  # the real input is on line 4


# Icon-action detection
def test_button_with_visible_text_is_not_flagged() -> None:
    html = "<button><svg></svg> Save</button>"
    assert audit_module.find_nameless_icon_actions(html) == []


def test_button_with_jinja_label_is_not_flagged() -> None:
    html = "<button><svg></svg> {{ label }}</button>"
    assert audit_module.find_nameless_icon_actions(html) == []


def test_button_with_x_text_label_is_not_flagged() -> None:
    html = '<button><svg></svg> <span x-text="action.label"></span></button>'
    assert audit_module.find_nameless_icon_actions(html) == []


def test_button_with_aria_label_is_not_flagged() -> None:
    html = '<button aria-label="Close"><svg></svg></button>'
    assert audit_module.find_nameless_icon_actions(html) == []


def test_button_with_sr_only_text_is_not_flagged() -> None:
    html = '<button><svg></svg><span class="sr-only">Close</span></button>'
    assert audit_module.find_nameless_icon_actions(html) == []


def test_icon_only_button_is_flagged() -> None:
    html = "<button><svg></svg></button>"
    results = audit_module.find_nameless_icon_actions(html)
    assert len(results) == 1


def test_icon_only_anchor_is_flagged() -> None:
    html = '<a href="/x"><svg></svg></a>'
    results = audit_module.find_nameless_icon_actions(html)
    assert len(results) == 1
