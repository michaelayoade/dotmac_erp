from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.models.finance.platform.service_hook import HookExecutionMode, HookHandlerType
from app.models.finance.platform.service_hook_execution import ExecutionStatus


def _fake_hook(
    *,
    hook_id: UUID | None = None,
    organization_id: UUID | None,
    event_name: str = "sales.order.confirmed",
    handler_type: HookHandlerType = HookHandlerType.WEBHOOK,
    execution_mode: HookExecutionMode = HookExecutionMode.ASYNC,
    name: str = "Test Hook",
    is_active: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        hook_id=hook_id or uuid4(),
        organization_id=organization_id,
        event_name=event_name,
        handler_type=handler_type,
        execution_mode=execution_mode,
        handler_config={"url": "https://example.com/hooks"}
        if handler_type == HookHandlerType.WEBHOOK
        else {},
        conditions={},
        name=name,
        description=None,
        is_active=is_active,
        priority=10,
        max_retries=3,
        retry_backoff_seconds=60,
        created_by_user_id=uuid4(),
    )


class TestServiceHooksAPI:
    def test_create_list_get_update_toggle_delete(self, client, auth_headers):
        org_id = UUID("00000000-0000-0000-0000-000000000001")
        created = _fake_hook(organization_id=org_id, name="SO Confirmed Webhook")
        updated = _fake_hook(
            hook_id=created.hook_id,
            organization_id=org_id,
            name="SO Confirmed Hook v2",
        )
        disabled = _fake_hook(
            hook_id=created.hook_id,
            organization_id=org_id,
            name="SO Confirmed Hook v2",
            is_active=False,
        )
        execution = SimpleNamespace(
            execution_id=uuid4(),
            hook_id=created.hook_id,
            organization_id=org_id,
            event_name=created.event_name,
            status=ExecutionStatus.FAILED,
            event_payload={"invoice_number": "INV-1"},
            response_body="error body",
            response_status_code=None,
            error_message="boom",
            retry_count=1,
            duration_ms=50,
            created_at=None,
            executed_at=None,
        )

        with (
            patch("app.api.service_hooks.ServiceHookService") as mock_service_cls,
            patch(
                "app.api.service_hooks._get_visible_hook_or_404",
                return_value=created,
            ),
            patch(
                "app.api.service_hooks._get_mutable_hook_or_404",
                return_value=created,
            ),
        ):
            mock_service = mock_service_cls.return_value
            mock_service.create.return_value = created
            mock_service.list_for_org.return_value = [created]
            mock_service.list_executions.return_value = [execution]
            mock_service.count_executions.return_value = 3
            mock_service.get_execution.return_value = execution
            mock_service.retry_execution.return_value = execution
            mock_service.update.return_value = updated
            mock_service.toggle.return_value = disabled

            create_resp = client.post(
                "/service-hooks",
                json={
                    "event_name": "sales.order.confirmed",
                    "handler_type": "WEBHOOK",
                    "name": "SO Confirmed Webhook",
                    "execution_mode": "ASYNC",
                    "handler_config": {"url": "https://example.com/hooks/so"},
                },
                headers=auth_headers,
            )
            assert create_resp.status_code == 201
            assert create_resp.json()["hook_id"] == str(created.hook_id)

            list_resp = client.get("/service-hooks", headers=auth_headers)
            assert list_resp.status_code == 200
            assert list_resp.json()["count"] == 1

            get_resp = client.get(
                f"/service-hooks/{created.hook_id}",
                headers=auth_headers,
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["hook_id"] == str(created.hook_id)

            mock_service.execution_stats.return_value = {
                "SUCCESS": 10,
                "FAILED": 1,
                "DEAD": 0,
            }
            stats_resp = client.get(
                f"/service-hooks/{created.hook_id}/stats?days=14",
                headers=auth_headers,
            )
            assert stats_resp.status_code == 200
            stats_payload = stats_resp.json()
            assert stats_payload["hook_id"] == str(created.hook_id)
            assert stats_payload["days"] == 14
            assert stats_payload["stats"]["SUCCESS"] == 10

            executions_resp = client.get(
                f"/service-hooks/{created.hook_id}/executions?status=FAILED",
                headers=auth_headers,
            )
            assert executions_resp.status_code == 200
            exec_payload = executions_resp.json()
            assert exec_payload["count"] == 3
            assert exec_payload["items"][0]["status"] == "FAILED"

            execution_detail_resp = client.get(
                f"/service-hooks/{created.hook_id}/executions/{execution.execution_id}",
                headers=auth_headers,
            )
            assert execution_detail_resp.status_code == 200
            detail_payload = execution_detail_resp.json()
            assert detail_payload["execution_id"] == str(execution.execution_id)
            assert detail_payload["event_payload"]["invoice_number"] == "INV-1"

            retry_resp = client.post(
                f"/service-hooks/{created.hook_id}/executions/{execution.execution_id}/retry",
                headers=auth_headers,
            )
            assert retry_resp.status_code == 200
            assert retry_resp.json()["execution_id"] == str(execution.execution_id)

            update_resp = client.patch(
                f"/service-hooks/{created.hook_id}",
                json={"name": "SO Confirmed Hook v2", "max_retries": 4},
                headers=auth_headers,
            )
            assert update_resp.status_code == 200
            assert update_resp.json()["name"] == "SO Confirmed Hook v2"

            toggle_resp = client.post(
                f"/service-hooks/{created.hook_id}/toggle",
                json={"enabled": False},
                headers=auth_headers,
            )
            assert toggle_resp.status_code == 200
            assert toggle_resp.json()["is_active"] is False

            delete_resp = client.delete(
                f"/service-hooks/{created.hook_id}",
                headers=auth_headers,
            )
            assert delete_resp.status_code == 204

    def test_list_includes_global_hooks(self, client, auth_headers):
        org_id = UUID("00000000-0000-0000-0000-000000000001")
        global_hook = _fake_hook(
            organization_id=None,
            event_name="inventory.stock.reserved",
            handler_type=HookHandlerType.EVENT_OUTBOX,
            name="Global Hook",
        )
        org_hook = _fake_hook(
            organization_id=org_id,
            event_name="inventory.stock.reserved",
            name="Org Hook",
        )
        with patch("app.api.service_hooks.ServiceHookService") as mock_service_cls:
            mock_service_cls.return_value.list_for_org.return_value = [
                global_hook,
                org_hook,
            ]
            response = client.get(
                "/service-hooks?event_name=inventory.stock.reserved",
                headers=auth_headers,
            )
        assert response.status_code == 200
        names = [item["name"] for item in response.json()["items"]]
        assert "Global Hook" in names
        assert "Org Hook" in names

    def test_list_forwards_handler_and_search_filters(self, client, auth_headers):
        with patch("app.api.service_hooks.ServiceHookService") as mock_service_cls:
            mock_service_cls.return_value.list_for_org.return_value = []
            response = client.get(
                "/service-hooks?q=crm&handler_type=WEBHOOK&is_active=true",
                headers=auth_headers,
            )
        assert response.status_code == 200
        kwargs = mock_service_cls.return_value.list_for_org.call_args.kwargs
        assert kwargs["name_contains"] == "crm"
        assert kwargs["handler_type"].value == "WEBHOOK"
        assert kwargs["is_active"] is True

    def test_cross_tenant_mutation_is_blocked(self, client, auth_headers):
        with patch(
            "app.api.service_hooks._get_mutable_hook_or_404",
            side_effect=HTTPException(status_code=404, detail="Hook not found"),
        ):
            hook_id = uuid4()
            patch_resp = client.patch(
                f"/service-hooks/{hook_id}",
                json={"name": "Hacked"},
                headers=auth_headers,
            )
            assert patch_resp.status_code == 404

            toggle_resp = client.post(
                f"/service-hooks/{hook_id}/toggle",
                json={"enabled": False},
                headers=auth_headers,
            )
            assert toggle_resp.status_code == 404

            delete_resp = client.delete(
                f"/service-hooks/{hook_id}",
                headers=auth_headers,
            )
            assert delete_resp.status_code == 404

    def test_v1_prefix_supported(self, client, auth_headers):
        with patch("app.api.service_hooks.ServiceHookService") as mock_service_cls:
            mock_service_cls.return_value.list_for_org.return_value = []
            response = client.get("/api/v1/service-hooks", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_bulk_toggle_and_delete(self, client, auth_headers):
        hook_ids = [str(uuid4()), str(uuid4())]
        with patch("app.api.service_hooks.ServiceHookService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.bulk_toggle.return_value = {
                "requested": 2,
                "updated": 1,
                "not_found_ids": [hook_ids[1]],
            }
            mock_service.bulk_delete.return_value = {
                "requested": 2,
                "deleted": 1,
                "not_found_ids": [hook_ids[1]],
            }

            toggle_resp = client.post(
                "/service-hooks/actions/bulk/toggle",
                json={"hook_ids": hook_ids, "enabled": False},
                headers=auth_headers,
            )
            delete_resp = client.post(
                "/service-hooks/actions/bulk/delete",
                json={"hook_ids": hook_ids},
                headers=auth_headers,
            )

        assert toggle_resp.status_code == 200
        assert toggle_resp.json()["requested"] == 2
        assert toggle_resp.json()["processed"] == 1
        assert delete_resp.status_code == 200
        assert delete_resp.json()["processed"] == 1

    def test_create_maps_policy_fields_into_handler_config(self, client, auth_headers):
        org_id = UUID("00000000-0000-0000-0000-000000000001")
        created = _fake_hook(organization_id=org_id, name="Policy Hook")

        with (
            patch("app.api.service_hooks.ServiceHookService") as mock_service_cls,
            patch(
                "app.api.service_hooks._get_visible_hook_or_404",
                return_value=created,
            ),
        ):
            mock_service = mock_service_cls.return_value
            mock_service.create.return_value = created

            response = client.post(
                "/service-hooks",
                json={
                    "event_name": "sales.order.confirmed",
                    "handler_type": "WEBHOOK",
                    "name": "Policy Hook",
                    "execution_mode": "ASYNC",
                    "handler_config": {"url": "https://example.com/hooks/so"},
                    "circuit_breaker_failures": 4,
                    "webhook_timeout_seconds": 25,
                },
                headers=auth_headers,
            )

        assert response.status_code == 201
        call_kwargs = mock_service.create.call_args.kwargs
        assert call_kwargs["handler_config"]["circuit_breaker_failures"] == 4
        assert call_kwargs["handler_config"]["timeout_seconds"] == 25

    def test_create_rejects_webhook_timeout_for_non_webhook(self, client, auth_headers):
        with patch("app.api.service_hooks.ServiceHookService") as mock_service_cls:
            response = client.post(
                "/service-hooks",
                json={
                    "event_name": "sales.order.confirmed",
                    "handler_type": "EVENT_OUTBOX",
                    "name": "Bad Timeout",
                    "execution_mode": "ASYNC",
                    "handler_config": {},
                    "webhook_timeout_seconds": 10,
                },
                headers=auth_headers,
            )
        assert response.status_code == 400
        assert "only valid for WEBHOOK" in str(response.json())
        mock_service_cls.return_value.create.assert_not_called()
