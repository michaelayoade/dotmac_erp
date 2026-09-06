# Gunicorn configuration file for production

import multiprocessing
import os

from app.prometheus_multiprocess import prepare_configured_multiprocess_directory


# Gunicorn preloads the application. Clear stale mmap files before that import
# creates any Prometheus collectors inherited by the worker processes.
prepare_configured_multiprocess_directory()


def _int_env(name: str, default: int) -> int:
    """Parse integer environment variables with safe fallback."""
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


# Server socket
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8002")
backlog = 2048

# Worker processes
workers = _int_env("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1)
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
timeout = 120
keepalive = 5

# Graceful restart - workers are restarted after this many requests (prevents memory leaks)
max_requests = 1000
max_requests_jitter = 50

# Logging - use stdout/stderr for Docker compatibility
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")

# Access log format with timing info
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "dotmac_erp"

# Misc
daemon = False

# Graceful shutdown timeout
graceful_timeout = 30

# Preload app for faster worker spawning (shares memory)
preload_app = True

# Defer per-process observability bootstrap until Gunicorn forks workers.
os.environ.setdefault(
    "DOTMAC_DEFER_RUNTIME_OBSERVABILITY",
    "1" if preload_app else "0",
)


def post_worker_init(worker):
    from app.main import app, bootstrap_runtime_observability

    bootstrap_runtime_observability(app)


def child_exit(server, worker):
    from app.metrics import mark_metrics_process_dead

    mark_metrics_process_dead(worker.pid)
