# Static-asset sync safety net

Nginx serves `/static/` from `/var/www/dotmac/static/` (it runs as `www-data`
and cannot read `/root`, mode `0700`). The project source lives in
`/root/dotmac/static/`, so assets must be rsync'd to the serving directory by
`scripts/sync-static.sh`.

`scripts/deploy.sh` already runs that sync (Step 4). These units are a
**belt-and-suspenders** for updates that bypass `deploy.sh` — a bare
`docker restart`, or live bind-mount edits — which update the app container
instantly but leave Nginx's static copy stale. Combined with Nginx's
`expires 30d; ... immutable` headers on `/static/`, a missed sync can serve
stale JS/CSS for up to 30 days.

## Install

```bash
sudo cp deploy/systemd/dotmac-static-sync.service /etc/systemd/system/
sudo cp deploy/systemd/dotmac-static-sync.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dotmac-static-sync.timer
```

## Verify

```bash
systemctl status dotmac-static-sync.timer      # next/last run
systemctl list-timers dotmac-static-sync.timer
journalctl -u dotmac-static-sync.service --since "10 min ago"
tail -f /var/log/dotmac/static-sync.log
```

## Notes

- The `.service` is `Type=oneshot`; the `.timer` triggers it every ~2 minutes.
- `sync-static.sh` takes a `flock` on `/var/lock/dotmac-static-sync.lock`, so a
  timer run and a manual `deploy.sh` sync serialize instead of racing.
- This does **not** replace the cache-buster version bump (`?v=...` in
  `templates/base.html`). The timer fixes *what bytes* Nginx serves; the bump
  forces browsers holding an `immutable` copy to refetch the new URL. Both are
  needed for a JS/CSS change to reach users already on the site.
