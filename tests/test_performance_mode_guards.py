from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models.finance.core_org import PerformanceMode
from app.web.deps import (
    WebAuthContext,
    require_government_pms_mode,
    require_private_performance_mode,
)


def _auth_context() -> WebAuthContext:
    return WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
        employee_id=uuid4(),
        user_name="Mode Tester",
        user_initials="MT",
        roles=["admin"],
        scopes=["hr:access"],
    )


def _request(path: str = "/people/perf/appraisals") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": ("testclient", 123),
            "server": ("testserver", 80),
            "scheme": "http",
            "http_version": "1.1",
        }
    )


def test_private_guard_allows_private_mode() -> None:
    auth = _auth_context()
    db = SimpleNamespace(
        get=lambda *_: SimpleNamespace(
            performance_mode=PerformanceMode.PRIVATE,
            pms_ohcsf_enabled=False,
        )
    )

    result = require_private_performance_mode(request=_request(), auth=auth, db=db)
    assert result is auth


def test_private_guard_blocks_government_mode() -> None:
    auth = _auth_context()
    db = SimpleNamespace(
        get=lambda *_: SimpleNamespace(
            performance_mode=PerformanceMode.GOVERNMENT_PMS,
            pms_ohcsf_enabled=True,
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        require_private_performance_mode(request=_request(), auth=auth, db=db)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Private performance mode required"


def test_government_guard_allows_hybrid_mode() -> None:
    auth = _auth_context()
    db = SimpleNamespace(
        get=lambda *_: SimpleNamespace(
            performance_mode=PerformanceMode.HYBRID,
            pms_ohcsf_enabled=True,
        )
    )

    result = require_government_pms_mode(auth=auth, db=db)
    assert result is auth


def test_government_guard_blocks_private_mode() -> None:
    auth = _auth_context()
    db = SimpleNamespace(
        get=lambda *_: SimpleNamespace(
            performance_mode=PerformanceMode.PRIVATE,
            pms_ohcsf_enabled=False,
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        require_government_pms_mode(auth=auth, db=db)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Government PMS mode required"


def test_private_guard_skips_pms_subpaths() -> None:
    auth = _auth_context()
    db = SimpleNamespace(
        get=lambda *_: SimpleNamespace(
            performance_mode=PerformanceMode.GOVERNMENT_PMS,
            pms_ohcsf_enabled=True,
        )
    )

    result = require_private_performance_mode(
        request=_request("/people/perf/pms/dashboard"), auth=auth, db=db
    )
    assert result is auth
