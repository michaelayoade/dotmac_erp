# ERP metrics collection

## Runtime contract

The root Compose topology has two metric targets:

- `app:8002/metrics` aggregates all Gunicorn workers and requires the
  deployment `METRICS_TOKEN` as a bearer token;
- `worker:8004/metrics` aggregates Celery prefork children and is reachable
  only from the Compose network. It is not published on the host.

Each application container uses its own
`/tmp/dotmac-erp-prometheus` directory. The identical path is safe because it
is container-local; it must never be replaced by a volume shared between the
app and worker, whose PID namespaces may reuse filenames. The app clears its
directory before Gunicorn preloads collectors. The worker entrypoint clears its
directory before importing Celery or Prometheus. Live gauge files are retired
when managed child processes exit.

## Validation before enabling the observability profile

1. Set a high-entropy `METRICS_TOKEN` in deployment material and enable only
   the intended observability profile.
2. Run `docker compose config` and confirm both targets remain on the private
   project network and no worker port is published.
3. From vmagent, confirm both `dotmac-erp-app` and `dotmac-erp-worker` targets
   are up. An app 404 means the bearer material does not match.
4. Run one named non-production Celery task and confirm
   `job_runs_total{instance="dotmac-erp-worker"}` increases once and its
   `job_duration_seconds` histogram receives one observation.
5. Restart one Celery child and one Gunicorn worker. Confirm counters remain
   readable and no duplicate PID-labelled live-gauge series remains.
6. Confirm the existing finance job alert expressions evaluate against the
   worker instance before enabling their notification routes.

Scraping performs no database, Redis, ledger, or external-integration work.
The metric endpoints only serialize process-local mmap-backed collectors.
