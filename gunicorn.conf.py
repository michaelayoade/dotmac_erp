# Gunicorn configuration file for production

import multiprocessing
import os

# Server socket
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8002")
backlog = 2048

# Worker processes
workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
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
