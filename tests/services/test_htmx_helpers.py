from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.htmx import htmx_response, htmx_toast, is_htmx_request


def _request(headers: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(headers=headers)


class TestIsHtmxRequest:
    def test_detects_lowercase_true(self) -> None:
        assert is_htmx_request(_request({"HX-Request": "true"})) is True

    def test_detects_mixed_case_true(self) -> None:
        assert is_htmx_request(_request({"HX-Request": "True"})) is True

    def test_false_when_header_missing(self) -> None:
        assert is_htmx_request(_request({})) is False

    def test_false_when_header_is_false(self) -> None:
        assert is_htmx_request(_request({"HX-Request": "false"})) is False


class TestHtmxResponse:
    def test_default_is_empty_200(self) -> None:
        resp = htmx_response()
        assert resp.status_code == 200
        assert resp.body == b""
        assert "HX-Trigger" not in resp.headers
        assert "HX-Redirect" not in resp.headers

    def test_string_trigger_is_passed_through_verbatim(self) -> None:
        resp = htmx_response(trigger="taskCompleted")
        assert resp.headers["HX-Trigger"] == "taskCompleted"

    def test_dict_trigger_is_json_encoded(self) -> None:
        resp = htmx_response(trigger={"showToast": {"message": "hi", "type": "ok"}})
        assert json.loads(resp.headers["HX-Trigger"]) == {
            "showToast": {"message": "hi", "type": "ok"}
        }

    def test_redirect_sets_hx_redirect_header(self) -> None:
        resp = htmx_response(redirect="/finance/ap/payments")
        assert resp.headers["HX-Redirect"] == "/finance/ap/payments"

    def test_refresh_sets_hx_refresh_true(self) -> None:
        resp = htmx_response(refresh=True)
        assert resp.headers["HX-Refresh"] == "true"

    def test_push_url_sets_hx_push_url_header(self) -> None:
        resp = htmx_response(push_url="/x")
        assert resp.headers["HX-Push-Url"] == "/x"

    def test_status_code_is_honoured(self) -> None:
        resp = htmx_response(content="<p>bad</p>", status_code=400)
        assert resp.status_code == 400
        assert resp.body == b"<p>bad</p>"


class TestHtmxToast:
    def test_default_level_is_success(self) -> None:
        assert htmx_toast("saved") == {
            "showToast": {"message": "saved", "type": "success"}
        }

    def test_custom_level(self) -> None:
        assert htmx_toast("oops", "error") == {
            "showToast": {"message": "oops", "type": "error"}
        }
