# Traccar Step 2 deployment

This directory defines Traccar as a separate production service. It does not
connect Dotmac ERP to Traccar, create a Traccar user or device, or change a
physical tracker.

## Deployment fit

Dotmac ERP currently runs as a hardened Docker Compose stack on one production
host. Host Nginx terminates TLS, application ports bind to loopback, Docker
services use `unless-stopped`, and bounded `json-file` logs are available to the
existing log collector. PostgreSQL backups are host scripts uploaded by
`rclone`. The Traccar stack follows those conventions but remains a separate
Compose project and has its own PostgreSQL database, login and volumes.

Pinned images:

- Traccar `6.15.3`, image index digest
  `sha256:35346b4af46d57adaeff69a548a7d2cb189a2487fe9c24c99044ee7aa104b5df`
- PostgreSQL `16.15-bookworm`, image index digest
  `sha256:efedf3595f1d6f415c08568ba171029bf54052e754cc9f030e3f2412b21f3d67`

The GPS103 protocol is explicitly configured on TCP port 5001. No other device
protocol port is published.

## Network model

| Surface | Binding | Exposure |
| --- | --- | --- |
| GPS103 ingest | `0.0.0.0:5001/tcp` | Public after host/cloud firewall approval |
| Traccar HTTP/API | `127.0.0.1:8082/tcp` | Host-local only |
| Traccar PostgreSQL | no host port | Private `dotmac_traccar_data` network only |
| Future ERP API path | `http://traccar:8082` | Dormant `dotmac_traccar_api` network |

Do not attach ERP to `dotmac_traccar_api` in Step 2. For temporary operator
access, use an SSH tunnel instead of exposing the UI:

```bash
ssh -L 8082:127.0.0.1:8082 <production-host>
```

Then open `http://127.0.0.1:8082`. Traccar has no default account. Do not
register the Step 3 ERP identity during this deployment.

No DNS record or public TLS route is required for the default Step 2 topology.
If a protected administrative hostname is approved later, create its A/AAAA
record for the production host, proxy it through the existing host Nginx and
Let's Encrypt flow, and restrict it with VPN or an administrative source-IP
allowlist. Never proxy GPS103 traffic through the HTTP virtual host.

## Secret provisioning

The application uses OpenBao references, but a third-party Traccar container
cannot resolve `bao://` values. The infrastructure operator must generate a
dedicated random database password in OpenBao and materialize only that value
to a root-readable host file at deployment time. The value must never be put in
this repository, a Compose environment file, or a command line.

Example operator procedure (adapt paths to the production OpenBao mount):

```bash
sudo install -d -m 0700 /etc/dotmac/secrets
umask 077
# Fetch the database_password field from OpenBao using the host's approved
# authenticated secret helper and write it without a trailing log/terminal copy:
#   <approved-openbao-helper> > /etc/dotmac/secrets/traccar-db-password
sudo chmod 0400 /etc/dotmac/secrets/traccar-db-password
sudo chown root:root /etc/dotmac/secrets/traccar-db-password
sudo install -m 0600 deploy/traccar/traccar.env.example /etc/dotmac/traccar.env
```

The Compose secret is mounted at `/run/secrets/traccar_db_password`. PostgreSQL
uses its `_FILE` interface. The Traccar entrypoint reads the same file without
printing it, exports it only inside the container, and then replaces the shell
with Java. `docker compose config` and `docker inspect` therefore do not contain
the credential value.

Step 2 deliberately creates no bootstrap administrator. On a new Traccar
installation the first registered user becomes administrator, so registration
must only be performed through the protected local interface in the explicitly
approved identity/bootstrap step.

## Deploy

Run these commands from an approved, immutable checkout on the production host:

```bash
docker compose \
  --env-file /etc/dotmac/traccar.env \
  -f deploy/traccar/compose.yml \
  config --quiet

docker compose \
  --env-file /etc/dotmac/traccar.env \
  -f deploy/traccar/compose.yml \
  pull

docker compose \
  --env-file /etc/dotmac/traccar.env \
  -f deploy/traccar/compose.yml \
  up -d --wait
```

Both images are pinned by version and digest. Do not substitute `latest`.

## Firewall actions

Repository code cannot change the provider security group or host firewall.
The infrastructure operator must apply and verify these rules:

1. Allow inbound IPv4 TCP/5001 to the production host for GPS103 pilot traffic.
   Mobile SIM source addresses are normally dynamic, so a stable source-IP
   allowlist is unlikely to work. Do not add UDP/5001.
2. Do not allow inbound TCP/8082 or TCP/5432 at the provider firewall.
3. Verify the host `DOCKER-USER` policy permits only the required published
   TCP/5001 flow. Docker-published ports can bypass uncomplicated host firewall
   rules, so checking only UFW is insufficient.
4. From a host outside the production network, confirm TCP/5001 connects and
   TCP/8082 and TCP/5432 do not.

An open TCP/5001 listener accepts untrusted internet traffic by design. Traccar
must keep unknown-device registration disabled in normal operation, and only a
pre-registered device identifier should be accepted in the later pilot step.

## Validate and restart test

Run the non-mutating runtime verifier after deployment:

```bash
ENV_FILE=/etc/dotmac/traccar.env \
PUBLIC_HOST=<production-public-ip-or-name> \
bash deploy/traccar/verify.sh
```

It requires both container health checks, an HTTP response, database readiness,
the GPS103 listener, loopback-only HTTP, no published database port, external
TCP/5001 reachability, and clean recent startup/database logs.

Test restart persistence without creating a user or device:

```bash
before_db_oid="$(docker exec -u postgres dotmac_traccar_db \
  psql -U traccar -At -d traccar -c "select oid from pg_database where datname='traccar'")"

docker restart dotmac_traccar_db dotmac_traccar

ENV_FILE=/etc/dotmac/traccar.env \
PUBLIC_HOST=<production-public-ip-or-name> \
bash deploy/traccar/verify.sh

after_db_oid="$(docker exec -u postgres dotmac_traccar_db \
  psql -U traccar -At -d traccar -c "select oid from pg_database where datname='traccar'")"
test -n "${before_db_oid}" && test "${before_db_oid}" = "${after_db_oid}"
```

For the host-restart check, reboot the host in an approved maintenance window,
then run `verify.sh` again. `restart: unless-stopped` restores both services,
while the named volumes preserve the database, logs and media.

Inspect logs with:

```bash
docker compose --env-file /etc/dotmac/traccar.env \
  -f deploy/traccar/compose.yml logs --since 30m traccar traccar-db
```

The bounded `json-file` configuration follows the current stack's logging
convention and keeps logs available to Docker and host collection. Production
logging stays at `info`; do not enable trace logging during normal operation.

## Backups and restoration

The database named volume is durable storage, not a backup. Install a root cron
entry beside the existing ERP backup job, but at a non-overlapping time:

```cron
30 18 * * * cd <immutable-release-checkout> && /usr/bin/bash scripts/backup_traccar_db.sh >> /var/log/dotmac-traccar-backup.log 2>&1
```

`scripts/backup_traccar_db.sh` writes two mode-0600 artifacts under
`/var/backups/db` and uploads them to `Backup:db.backup/traccar`:

- `traccar_<timestamp>.globals.sql.gz`: roles and grants, without password
  verifiers;
- `traccar_<timestamp>.dump`: PostgreSQL custom-format database archive.

The script reads `POSTGRES_USER` and `POSTGRES_DB` from the running database
container and passes that database identity explicitly to `psql`, `pg_dumpall`
and `pg_dump`. The container process still runs as the image's `postgres`
operating-system account, but backup connections use the configured `traccar`
database role. The official PostgreSQL image created that configured role with
the privileges needed for `pg_dumpall` when it initialized this dedicated
cluster; the backup does not require or create a database role named `postgres`.

The script validates the archive before upload and retains five complete runs.
Run it once with `SKIP_UPLOAD=1`, once against the configured remote, and verify
the two remote objects before enabling cron. A restore rehearsal is required
before describing the backup as recovery-tested.

Restore to an empty, version-compatible PostgreSQL instance by restoring the
globals first, reinstalling the `traccar` login password from OpenBao, creating
the database if the dump does not do so, and then using `pg_restore`:

```bash
gzip -cd traccar_<timestamp>.globals.sql.gz | psql -U postgres -d postgres
# Reinstall the login secret from OpenBao through a non-logging operator flow.
createdb -U postgres -O traccar traccar
pg_restore -U postgres --clean --if-exists -d traccar traccar_<timestamp>.dump
```

Here `postgres` is the administrator of the fresh, isolated restore target; it
is not expected to exist in the production Traccar cluster. The restored
`traccar` role's login secret must be reinstalled from OpenBao before Traccar is
started against the recovered database.

After restoration, start Traccar and run `verify.sh`. The named volumes
`dotmac_traccar_logs` and `dotmac_traccar_media` may also contain operational
diagnostics or uploaded media; include them in host volume snapshots if media
use is enabled later. GPS history and Traccar configuration are in PostgreSQL.

## Scope boundary

This step intentionally leaves all of the following for later work:

- Traccar bootstrap/final ERP identity and credentials;
- ERP network attachment and `TRACCAR_BASE_URL` activation;
- REST/WebSocket integration, maps, telemetry and history in ERP;
- Traccar device registration;
- COBAN SMS commands or migration away from AutoTracker;
- a production fleet rollout.
