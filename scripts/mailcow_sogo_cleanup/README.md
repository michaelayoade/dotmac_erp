# Mailcow SOGo forwarding cleanup

These scripts remove forwarding references to an offboarded employee from
other SOGo profiles. The ERP sends only the employee email to a private HTTP
receiver. The receiver adds it to a local SQLite queue, and a systemd timer
processes the queue every 15 minutes.

The receiver never accesses SOGo. The processor reaches MariaDB only through
Mailcow's local `mysql-mailcow` Docker Compose service; no database port or
credentials need to be exposed to the ERP or public network.

## Files

- `sogo_cleanup_queue.py`: shared SQLite schema, queue functions, and CLI.
- `sogo_cleanup_receiver.py`: authenticated private HTTP receiver.
- `sogo_forward_cleanup.py`: queued SOGo forwarding cleanup processor.
- `dotmac-sogo-cleanup.service` and `.timer`: 15-minute processor schedule.
- `dotmac-sogo-cleanup-receiver.service`: long-running HTTP receiver.
- `sogo_cleanup_config.env.sample`: shared configuration example.

The receiver and processor import their queue operations from
`sogo_cleanup_queue.py`. This makes the `cleanup_queue` table and
`cleanup_queue_pending_email_idx` partial unique index a single source of
truth. Duplicate pending requests are accepted without creating duplicate
rows.

## Install on Mailcow

Copy this directory to the Mailcow server, change into it, and run as root:

```sh
install -d -m 0755 /opt/dotmac-mailcow-offboarding
install -m 0755 \
  sogo_cleanup_queue.py \
  sogo_cleanup_receiver.py \
  sogo_forward_cleanup.py \
  /opt/dotmac-mailcow-offboarding/
install -m 0644 \
  dotmac-sogo-cleanup.service \
  dotmac-sogo-cleanup.timer \
  dotmac-sogo-cleanup-receiver.service \
  /etc/systemd/system/
install -d -m 0700 /var/lib/dotmac-mailcow-offboarding
install -m 0600 sogo_cleanup_config.env.sample /etc/dotmac-sogo-cleanup.env
```

Edit `/etc/dotmac-sogo-cleanup.env`. Confirm `MAILCOW_DIR` points to the
Mailcow Docker Compose directory, and generate a long random receiver token:

```sh
openssl rand -hex 32
editor /etc/dotmac-sogo-cleanup.env
```

Set the generated value as `CLEANUP_RECEIVER_TOKEN`. Configure the ERP's
`MAILCOW_SOGO_CLEANUP_TOKEN` with the same value. Protect the configuration:

```sh
chown root:root /etc/dotmac-sogo-cleanup.env
chmod 0600 /etc/dotmac-sogo-cleanup.env
```

Initialize and inspect the queue:

```sh
/opt/dotmac-mailcow-offboarding/sogo_cleanup_queue.py \
  --config /etc/dotmac-sogo-cleanup.env init
/opt/dotmac-mailcow-offboarding/sogo_cleanup_queue.py \
  --config /etc/dotmac-sogo-cleanup.env list
```

## Cleanup processor and timer

For a safe manual test, enqueue an address and run the processor in dry-run
mode. Dry-run does not update SOGo or complete the queue entry:

```sh
/opt/dotmac-mailcow-offboarding/sogo_cleanup_queue.py \
  --config /etc/dotmac-sogo-cleanup.env \
  enqueue john@dotmac.ng --created-by manual_test
/opt/dotmac-mailcow-offboarding/sogo_forward_cleanup.py \
  --config /etc/dotmac-sogo-cleanup.env --dry-run
```

After reviewing the output, apply the cleanup. An apply run updates matching
`sogo_user_profile.c_defaults`, restarts `sogo-mailcow` and
`memcached-mailcow`, and marks successful queue entries completed:

```sh
/opt/dotmac-mailcow-offboarding/sogo_forward_cleanup.py \
  --config /etc/dotmac-sogo-cleanup.env --apply
```

Enable the 15-minute timer:

```sh
systemctl daemon-reload
systemctl enable --now dotmac-sogo-cleanup.timer
systemctl status dotmac-sogo-cleanup.timer
systemctl list-timers dotmac-sogo-cleanup.timer
journalctl -u dotmac-sogo-cleanup.service -n 100 --no-pager
```

When no work is waiting, the service logs `No pending cleanup requests`.
Failed requests remain pending with `last_error` populated so a later timer run
can retry them.

## Private receiver

The default receiver listens on `127.0.0.1:8765` and accepts only
`POST /cleanup` with `Authorization: Bearer <token>`. Start it with:

```sh
systemctl enable --now dotmac-sogo-cleanup-receiver.service
systemctl status dotmac-sogo-cleanup-receiver.service
journalctl -u dotmac-sogo-cleanup-receiver.service -n 100 --no-pager
```

Test locally on the Mailcow server, replacing the token with the value from
`/etc/dotmac-sogo-cleanup.env`:

```sh
curl --fail-with-body \
  -X POST http://127.0.0.1:8765/cleanup \
  -H 'Authorization: Bearer replace-with-the-configured-token' \
  -H 'Content-Type: application/json' \
  -d '{"email":"john@dotmac.ng","event":"employee_offboarding"}'
```

The successful response is:

```json
{"ok":true,"email":"john@dotmac.ng","queued":true}
```

Confirm that the processor can see the receiver-created request:

```sh
/opt/dotmac-mailcow-offboarding/sogo_cleanup_queue.py \
  --config /etc/dotmac-sogo-cleanup.env list
```

Keep `CLEANUP_RECEIVER_HOST=127.0.0.1` when ERP reaches it through a private
tunnel or local proxy. If direct ERP access is required, bind only to a private
Mailcow interface and restrict the port at the firewall to the ERP host. Never
expose the receiver or Mailcow database publicly.
