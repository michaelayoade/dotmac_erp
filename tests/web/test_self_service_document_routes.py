from __future__ import annotations

import inspect

from app.web.people import self_service as self_service_routes


def test_document_routes_require_document_permissions():
    read_routes = [
        self_service_routes.my_documents,
        self_service_routes.download_my_document,
        self_service_routes.download_my_pending_document,
    ]
    upload_routes = [
        self_service_routes.new_my_document,
        self_service_routes.submit_my_document,
    ]

    for route in read_routes:
        auth_dependency = inspect.signature(route).parameters["auth"].default
        assert (
            auth_dependency.dependency
            is self_service_routes.require_self_service_documents_read
        )

    for route in upload_routes:
        auth_dependency = inspect.signature(route).parameters["auth"].default
        assert (
            auth_dependency.dependency
            is self_service_routes.require_self_service_documents_upload
        )


def test_document_routes_do_not_accept_employee_id():
    route_functions = [
        self_service_routes.my_documents,
        self_service_routes.new_my_document,
        self_service_routes.submit_my_document,
        self_service_routes.download_my_document,
        self_service_routes.download_my_pending_document,
    ]

    for route in route_functions:
        assert "employee_id" not in inspect.signature(route).parameters
