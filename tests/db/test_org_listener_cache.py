"""Execute cached tenant queries against real rows, not mocked SQL states."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Integer, Uuid, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, aliased, mapped_column

from app.db.multi_tenant import MissingOrgContextError
from app.db.org_listener import _add_org_filter


class TenantCacheBase(DeclarativeBase):
    pass


class TenantCacheRow(TenantCacheBase):
    __tablename__ = "tenant_cache_row"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


@pytest.fixture
def tenant_cache_store():
    engine = create_engine("sqlite://")
    TenantCacheBase.metadata.create_all(engine)
    first, second = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            TenantCacheRow.__table__.insert(),
            [
                {"id": 1, "organization_id": first},
                {"id": 2, "organization_id": second},
            ],
        )

    class ScopedSession(Session):
        pass

    event.listen(ScopedSession, "do_orm_execute", _add_org_filter)
    try:
        yield engine, ScopedSession, first, second
    finally:
        event.remove(ScopedSession, "do_orm_execute", _add_org_filter)
        engine.dispose()


@pytest.mark.parametrize("shape", ["entity", "column", "alias", "primary_key"])
def test_cached_queries_bind_each_sessions_current_tenant(tenant_cache_store, shape):
    engine, scoped_session, first, second = tenant_cache_store
    row_alias = aliased(TenantCacheRow)
    for organization_id, expected_id in [
        (first, 1),
        (second, 2),
        (first, 1),
        (second, 2),
    ]:
        with scoped_session(engine, info={"organization_id": organization_id}) as db:
            if shape == "primary_key":
                assert db.get(TenantCacheRow, 3 - expected_id) is None
                own_row = db.get(TenantCacheRow, expected_id)
                assert own_row is not None
                assert own_row.organization_id == organization_id
            elif shape == "column":
                assert list(db.scalars(select(TenantCacheRow.id))) == [expected_id]
            else:
                entity = row_alias if shape == "alias" else TenantCacheRow
                rows = list(db.scalars(select(entity)))
                assert [row.id for row in rows] == [expected_id]
                assert all(row.organization_id == organization_id for row in rows)


def test_cached_query_does_not_authorize_an_unprimed_session(tenant_cache_store):
    engine, scoped_session, first, _ = tenant_cache_store
    with scoped_session(engine, info={"organization_id": first}) as db:
        assert list(db.scalars(select(TenantCacheRow.id))) == [1]
    with scoped_session(engine) as db, pytest.raises(MissingOrgContextError):
        db.scalars(select(TenantCacheRow.id)).all()
