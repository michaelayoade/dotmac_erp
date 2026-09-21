from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute

from app.main import app


def _matching_routes(path: str, method: str = "GET") -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and method in (route.methods or set())
    ]


def test_people_web_employee_list_route_is_not_shadowed_by_api():
    routes = _matching_routes("/people/hr/employees")

    assert len(routes) == 1
    assert routes[0].response_class is HTMLResponse


def test_people_web_applicant_list_route_is_not_shadowed_by_api():
    routes = _matching_routes("/people/recruit/applicants")

    assert len(routes) == 1
    assert routes[0].response_class is HTMLResponse


def test_people_web_job_applicant_report_route_is_registered():
    routes = _matching_routes("/people/recruit/jobs/{job_opening_id}/report")

    assert len(routes) == 1
    assert routes[0].response_class is HTMLResponse


def test_organization_calendar_route_is_registered_without_duplicate_prefix():
    routes = _matching_routes("/people/calendar")

    assert len(routes) == 1
    assert routes[0].response_class is HTMLResponse
    assert not _matching_routes("/people/people/calendar")
