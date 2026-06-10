"""
Tests for FF-1 mobile push: /me/devices endpoints + PushService.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.push import PushService

ORG_ID = uuid4()
PERSON_ID = uuid4()


def _auth() -> dict:
    return {
        "organization_id": str(ORG_ID),
        "person_id": str(PERSON_ID),
        "roles": [],
        "scopes": [],
    }


@pytest.fixture()
def me_client():
    from app.api.deps import get_db_with_org, require_tenant_auth
    from app.api.me import router as me_router

    app = FastAPI()
    app.include_router(me_router, prefix="/api/v1")
    app.dependency_overrides[require_tenant_auth] = _auth
    app.dependency_overrides[get_db_with_org] = lambda: MagicMock()
    return TestClient(app)


# =============================================================================
# Endpoints
# =============================================================================


class TestDeviceEndpoints:
    @patch("app.api.me.PushService")
    def test_register_device(self, svc_cls, me_client) -> None:
        device_id = uuid4()
        svc_cls.return_value.register_device.return_value = SimpleNamespace(
            device_token_id=device_id
        )

        resp = me_client.post(
            "/api/v1/me/devices",
            json={"token": "fcm-token-abc123", "platform": "android"},
        )

        assert resp.status_code == 201
        assert resp.json() == {"device_token_id": str(device_id)}
        args, kwargs = svc_cls.return_value.register_device.call_args
        assert args == (ORG_ID, PERSON_ID)
        assert kwargs == {"token": "fcm-token-abc123", "platform": "android"}

    @patch("app.api.me.PushService")
    def test_register_rejects_unknown_platform(self, svc_cls, me_client) -> None:
        resp = me_client.post(
            "/api/v1/me/devices",
            json={"token": "fcm-token-abc123", "platform": "windows"},
        )
        assert resp.status_code == 422
        svc_cls.return_value.register_device.assert_not_called()

    @patch("app.api.me.PushService")
    def test_unregister_device(self, svc_cls, me_client) -> None:
        svc_cls.return_value.unregister_device.return_value = True

        resp = me_client.post(
            "/api/v1/me/devices/unregister",
            json={"token": "fcm-token-abc123"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"revoked": True}

    @patch("app.api.me.PushService")
    def test_unregister_unknown_token_is_not_error(self, svc_cls, me_client) -> None:
        svc_cls.return_value.unregister_device.return_value = False

        resp = me_client.post(
            "/api/v1/me/devices/unregister",
            json={"token": "never-registered"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"revoked": False}


# =============================================================================
# PushService
# =============================================================================


class TestPushServiceRegistration:
    def test_register_new_token_adds_row(self) -> None:
        db = MagicMock()
        db.scalar.return_value = None

        device = PushService(db).register_device(
            ORG_ID, PERSON_ID, token="tok-1", platform="android"
        )

        db.add.assert_called_once()
        db.flush.assert_called_once()
        assert device.token == "tok-1"
        assert device.person_id == PERSON_ID

    def test_register_existing_token_reassigns_and_reactivates(self) -> None:
        existing = SimpleNamespace(
            organization_id=uuid4(),
            person_id=uuid4(),
            platform="ios",
            last_seen_at=datetime(2026, 1, 1),
            revoked_at=datetime(2026, 2, 1),
        )
        db = MagicMock()
        db.scalar.return_value = existing

        PushService(db).register_device(
            ORG_ID, PERSON_ID, token="tok-1", platform="android"
        )

        assert existing.person_id == PERSON_ID
        assert existing.organization_id == ORG_ID
        assert existing.platform == "android"
        assert existing.revoked_at is None
        db.add.assert_not_called()

    def test_unregister_is_idempotent(self) -> None:
        already_revoked = SimpleNamespace(revoked_at=datetime(2026, 6, 1))
        db = MagicMock()
        db.scalar.return_value = already_revoked

        assert PushService(db).unregister_device(PERSON_ID, token="tok-1") is True
        db.flush.assert_not_called()  # no double-write

    def test_unregister_unknown_token_returns_false(self) -> None:
        db = MagicMock()
        db.scalar.return_value = None
        assert PushService(db).unregister_device(PERSON_ID, token="x") is False


class TestPushServiceDelivery:
    def test_send_is_noop_when_unconfigured(self) -> None:
        db = MagicMock()
        with patch("app.services.push.settings") as mock_settings:
            mock_settings.fcm_service_account_json = ""
            sent = PushService(db).send_to_person(PERSON_ID, title="t", body="b")
        assert sent == 0
        db.scalars.assert_not_called()

    def _service_with_devices(self, devices):
        db = MagicMock()
        db.scalars.return_value.all.return_value = devices
        return PushService(db), db

    @patch("app.services.push.httpx.post")
    def test_send_success_counts_and_dead_token_revoked(self, mock_post) -> None:
        good = SimpleNamespace(token="tok-good", revoked_at=None)
        dead = SimpleNamespace(token="tok-dead", revoked_at=None)
        svc, db = self._service_with_devices([good, dead])

        mock_post.side_effect = [
            SimpleNamespace(status_code=200),
            SimpleNamespace(status_code=404),  # UNREGISTERED
        ]
        fake_creds = SimpleNamespace(
            project_id="proj", token="oauth-token", refresh=lambda req: None
        )
        with (
            patch("app.services.push.settings") as mock_settings,
            patch.object(PushService, "_load_credentials", return_value=fake_creds),
        ):
            mock_settings.fcm_service_account_json = "/path/sa.json"
            sent = svc.send_to_person(PERSON_ID, title="t", body="b")

        assert sent == 1
        assert dead.revoked_at is not None
        assert good.revoked_at is None

    def test_send_credential_failure_returns_zero(self) -> None:
        svc, db = self._service_with_devices(
            [SimpleNamespace(token="tok", revoked_at=None)]
        )
        with (
            patch("app.services.push.settings") as mock_settings,
            patch.object(
                PushService, "_load_credentials", side_effect=ValueError("bad json")
            ),
        ):
            mock_settings.fcm_service_account_json = "{not json"
            sent = svc.send_to_person(PERSON_ID, title="t", body="b")
        assert sent == 0
