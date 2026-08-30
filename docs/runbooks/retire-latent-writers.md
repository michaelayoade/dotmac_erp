# Runbook — retiring ERP's latent parallel writers

Two paths on `149.102.158.167` can put a **second ERP writer** on the production
database without going through the image, the migration gate, Compose, or any
deployment controller. Both were measured on 2026-08-30 and are recorded in
`docs/inventories/2026-08-30-erp-production-infrastructure-preflight.md` § 4 B.

The repository half of this work has landed. **The host half has not**, and is
listed here as exact commands so it can be executed by someone holding the
permission, and verified afterwards.

Do **not** remove `/root/dotmac/docker-compose.yml`. It is retained as rollback
evidence until the legacy executor is retired.

## 1. `dotmac-books.service` — host-only, still present

An installed systemd unit at `/etc/systemd/system/dotmac-books.service`,
currently `disabled` and `inactive`. It runs:

```
ExecStart=/root/.local/bin/poetry run gunicorn app.main:app -c gunicorn.conf.py
User=root
WorkingDirectory=/root/dotmac
EnvironmentFile=/root/dotmac/.env      # production DSN and every secret
Restart=always
Requires=postgresql.service redis-server.service
```

One `systemctl start dotmac-books` is all that stands between the current state
and an uncontainerised ERP application, running as root, writing to the
production database from whatever the checkout happens to contain. `disabled`
prevents boot activation; it does **not** prevent a manual or dependency-driven
start.

```
systemctl mask dotmac-books.service      # mask, not just disable: refuses manual start too
systemctl stop dotmac-books.service      # no-op if inactive, harmless
rm /etc/systemd/system/dotmac-books.service
systemctl daemon-reload
```

Verify:

```
systemctl status dotmac-books.service    # expect: could not be found / masked
```

Its `Requires=` is also the reason the host carries `postgresql.service` and
`redis-server.service` on loopback. Whether those host services are retired with
it is a separate decision — they are not ERP's data plane (that is the
`dotmac_pg_local` container) but removing them is out of scope here.

## 2. `app-dev` — Compose service removed; the container object remains

The service definition is gone from `docker-compose.yml`. The **exited container
object `dotmac_erp_app_dev` still exists on the host**, and it retains its
original configuration: the production `DATABASE_URL`, the whole checkout
mounted over `/app`, and a short-syntax `8002:8002` publish on both address
families that no `DOCKER-USER` rule covers.

Removing the service definition does not remove the object. A single
`docker start dotmac_erp_app_dev` would still revive it.

```
docker rm dotmac_erp_app_dev
```

Verify:

```
docker ps -a --filter name=dotmac_erp_app_dev   # expect: no rows
```

## 3. What the repository change already closed

- The four checkout bind mounts (`./static`, `./templates`, `./license`,
  `./gunicorn.conf.py`) are removed, so the image digest identifies the runtime.
- `scripts/sync-static.sh` now copies product static **out of the container**
  rather than out of `/root/dotmac/static/`. This mattered more than the mounts:
  nginx intercepts `/static/` before FastAPI and serves the rsynced copy, so the
  checkout — not the image — decided what browsers received.
- The `app-dev` service definition is gone, and with it the only remaining
  `build:` stanza — which closes the in-place host image build path
  (`docker compose up -d --build app`, run roughly twelve times historically).
- A `css-drift` CI job proves the committed stylesheet equals a fresh build, so
  serving the image's compiled asset is provably behaviour-preserving.

## 4. Ordering

The host steps above are safe in either order and are independent of a
deployment. They should be done **before** any controller cutover, because a
controller that owns deployment while these paths remain open does not actually
own the runtime.
