import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_gunicorn_conf():
    module_path = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location(
        "dotmac_gunicorn_conf_test", module_path
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_post_worker_init_bootstraps_runtime_observability(monkeypatch) -> None:
    gunicorn_conf = _load_gunicorn_conf()
    calls: list[object] = []
    fake_app = object()

    fake_main = ModuleType("app.main")
    fake_main.app = fake_app
    fake_main.bootstrap_runtime_observability = lambda app: calls.append(app)

    monkeypatch.setitem(sys.modules, "app.main", fake_main)

    gunicorn_conf.post_worker_init(worker=object())

    assert calls == [fake_app]


def test_child_exit_retires_live_metrics_gauges(monkeypatch) -> None:
    gunicorn_conf = _load_gunicorn_conf()
    calls: list[int] = []
    fake_metrics = ModuleType("app.metrics")
    fake_metrics.mark_metrics_process_dead = lambda pid: calls.append(pid)
    monkeypatch.setitem(sys.modules, "app.metrics", fake_metrics)

    class Worker:
        pid = 4242

    gunicorn_conf.child_exit(server=object(), worker=Worker())

    assert calls == [4242]
