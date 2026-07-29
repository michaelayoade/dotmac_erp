from __future__ import annotations

import inspect

from app.web.people import self_service as self_service_routes


def test_extended_profile_read_routes_require_profile_read_permission():
    route_functions = [
        self_service_routes.my_qualifications,
        self_service_routes.my_certifications,
        self_service_routes.my_skills,
        self_service_routes.my_dependents,
        self_service_routes.download_my_pending_info_change_evidence,
    ]

    for route in route_functions:
        auth_dependency = inspect.signature(route).parameters["auth"].default
        assert (
            auth_dependency.dependency
            is self_service_routes.require_self_service_profile_read
        )


def test_extended_profile_write_routes_require_profile_update_permission():
    route_functions = [
        self_service_routes.submit_qualification,
        self_service_routes.submit_certification,
        self_service_routes.submit_skill,
        self_service_routes.submit_dependent,
    ]

    for route in route_functions:
        auth_dependency = inspect.signature(route).parameters["auth"].default
        assert (
            auth_dependency.dependency
            is self_service_routes.require_self_service_profile_update
        )


def test_extended_profile_routes_do_not_accept_employee_id():
    route_functions = [
        self_service_routes.my_qualifications,
        self_service_routes.submit_qualification,
        self_service_routes.my_certifications,
        self_service_routes.submit_certification,
        self_service_routes.my_skills,
        self_service_routes.submit_skill,
        self_service_routes.my_dependents,
        self_service_routes.submit_dependent,
    ]

    for route in route_functions:
        assert "employee_id" not in inspect.signature(route).parameters
