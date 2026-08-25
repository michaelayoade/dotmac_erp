"""PostgreSQL proof that ERP and shared-module scope stay aligned."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def test_real_session_rearms_both_scope_gucs_after_commit() -> None:
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    assert database_url, "integration lane must provide TEST_DATABASE_URL"

    code = r'''
import os
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import app.db as db_module
from app.db.session_context import session_for_org, tenant_scope_for_session

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
db_module.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
organization_id = uuid4()
query = text("""
    SELECT
        NULLIF(current_setting('app.current_organization_id', true), ''),
        NULLIF(current_setting('app.current_tenant', true), '')
""")

with session_for_org(organization_id) as db:
    first = db.execute(query).one()
    assert first == (str(organization_id), str(organization_id)), first
    db.commit()
    second = db.execute(query).one()
    assert second == first, (first, second)
    db.rollback()

request_db = db_module.SessionLocal()
try:
    with tenant_scope_for_session(request_db, organization_id):
        first = request_db.execute(query).one()
        assert first == (str(organization_id), str(organization_id)), first
        request_db.commit()
        second = request_db.execute(query).one()
        assert second == first, (first, second)
        request_db.rollback()
finally:
    request_db.close()

engine.dispose()
'''
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(  # noqa: S603 - fixed interpreter and test-owned code
        [sys.executable, "-c", code],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
