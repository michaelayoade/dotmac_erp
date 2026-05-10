import importlib.util
from pathlib import Path


def _load_real_db_module():
    # app/db is a package (app/db/__init__.py); load the real package's
    # __init__ directly to bypass the test conftest's mock for fork-safety
    # tests that need to exercise the real engine/sessionmaker lifecycle.
    module_path = Path(__file__).resolve().parents[1] / "app" / "db" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "app_db_fork_safety_real", module_path
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeEngine:
    def __init__(self, name: str) -> None:
        self.name = name
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def test_get_engine_reuses_engine_within_same_process(monkeypatch) -> None:
    db_module = _load_real_db_module()
    created: list[_FakeEngine] = []

    def _fake_create_engine(*args, **kwargs):
        engine = _FakeEngine(f"engine-{len(created) + 1}")
        created.append(engine)
        return engine

    monkeypatch.setitem(
        db_module.get_engine.__globals__, "create_engine", _fake_create_engine
    )
    monkeypatch.setitem(db_module.get_engine.__globals__, "_current_pid", lambda: 101)
    monkeypatch.setattr(db_module, "_engine", None, raising=False)
    monkeypatch.setattr(db_module, "_engine_pid", None, raising=False)

    engine1 = db_module.get_engine()
    engine2 = db_module.get_engine()

    assert engine1 is engine2
    assert len(created) == 1


def test_get_engine_recreates_engine_after_fork(monkeypatch) -> None:
    db_module = _load_real_db_module()
    created: list[_FakeEngine] = []
    pids = iter((101, 202))

    def _fake_create_engine(*args, **kwargs):
        engine = _FakeEngine(f"engine-{len(created) + 1}")
        created.append(engine)
        return engine

    monkeypatch.setitem(
        db_module.get_engine.__globals__, "create_engine", _fake_create_engine
    )
    monkeypatch.setitem(
        db_module.get_engine.__globals__, "_current_pid", lambda: next(pids)
    )
    monkeypatch.setattr(db_module, "_engine", None, raising=False)
    monkeypatch.setattr(db_module, "_engine_pid", None, raising=False)

    engine1 = db_module.get_engine()
    engine2 = db_module.get_engine()

    assert engine1 is not engine2
    assert engine1.disposed is True
    assert engine2.disposed is False
    assert len(created) == 2


def test_session_local_recreates_sessionmaker_after_fork(monkeypatch) -> None:
    db_module = _load_real_db_module()
    calls: list[str] = []
    pids = iter((303, 404))

    monkeypatch.setitem(
        db_module._get_session_local.__globals__,
        "_current_pid",
        lambda: next(pids),
    )
    monkeypatch.setattr(db_module, "_session_local", None, raising=False)
    monkeypatch.setattr(db_module, "_session_local_pid", None, raising=False)
    monkeypatch.setitem(
        db_module._get_session_local.__globals__, "get_engine", lambda: object()
    )

    def _fake_sessionmaker(*args, **kwargs):
        maker_id = f"maker-{len(calls) + 1}"
        calls.append(maker_id)

        def _open_session():
            return maker_id

        return _open_session

    monkeypatch.setitem(
        db_module._get_session_local.__globals__, "sessionmaker", _fake_sessionmaker
    )

    session1 = db_module.SessionLocal()
    session2 = db_module.SessionLocal()

    assert session1 == "maker-1"
    assert session2 == "maker-2"
    assert calls == ["maker-1", "maker-2"]
