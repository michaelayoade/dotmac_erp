# vmagent environment-label verification

## Contract

The metrics scrape configuration is bind-mounted from
`config/vmagent/config.yml`. Docker Compose interpolates its own environment
mapping, but does not interpolate this file. The pinned vmagent version
(v1.96.0) expands `%{ENV_VAR}` placeholders. Its `lib/envtemplate` implementation
leaves `${ENV_VAR}` unchanged.

The canonical deployment label is `environment: '%{DEPLOY_ENV}'`, alongside
`app: dotmac_erp`. The app and worker keep their existing distinct job/instance
labels and targets. Promtail has a different expansion mechanism and must not
be changed as part of this correction.

## Config is mounted as a directory, and still needs a restart

`config/vmagent/` and `config/promtail/` are bind-mounted as DIRECTORIES
(`./config/vmagent:/etc/vmagent:ro`, `./config/promtail:/etc/promtail:ro`),
never as single files. A single-file bind mount pins the inode that existed
when the container was created. `git pull` writes a changed tracked file as a
new inode, so the running container kept reading the old content: after the
`%{DEPLOY_ENV}` fix was merged and pulled, vmagent on both production and
staging kept the two-week-old literal `${DEPLOY_ENV}` label, and their metrics
stayed merged into the same series. A SIGHUP reload re-read the same stale
inode; only recreating the container picked up the change. With a directory
mount the container resolves the file name at open time and sees the pulled
file. `tests/architecture/test_observability_config_mounts.py` refuses a
single-file mount of checkout config, and pins the files allowed in those
directories, because every file there is readable by the agent container.

A directory mount makes the new file VISIBLE; it does not make the agent load
it. vmagent and promtail read their config at start, and `docker compose up -d`
does not recreate a container whose Compose definition did not change. So
`scripts/deploy.sh` compares `config/vmagent` and `config/promtail` between the
previous and the deployed commit, and runs `docker compose restart <agent>`
for a running agent whose config changed (and again on automatic rollback).
If config is changed outside `scripts/deploy.sh`, restart the agent by hand:
`docker compose restart vmagent` (or `promtail`).

Switching an existing host from the old single-file mount to the directory
mount changes the Compose definition, so the first deploy carrying it
recreates vmagent through `docker compose up -d worker beat vmagent`. promtail
is not in that command: recreate it once with `docker compose up -d promtail`
on hosts running the `observability` profile.

## Evidence and limitations

The 21–22 September 2026 ERP export contains rejected remote-write blocks in
production and staging: duplicate timestamps with conflicting values, and
out-of-order samples. The former literal environment label could make these
deployments share metric identities at the same receiver. The repository bug
is confirmed; proving every observed rejection has this one cause requires
inspection of the effective deployed labels and receiver-side evidence.

Log stream environment labels are not proof of the metric labels being sent.
The regression tests validate the tracked configuration and the documented
placeholder contract; they do not execute the vmagent binary.

## Separately authorized operational verification

1. Verify the checked-out configuration and the vmagent container's effective
   `DEPLOY_ENV` on each deployment. Staging must explicitly use `staging`; the
   Compose default remains `production` for compatibility.
2. Roll out/reload the corrected configuration only under the normal authorized
   deployment process. A merged source change alone does not update a running
   agent's already-loaded configuration; the agent container must be
   restarted or recreated (see "Config is mounted as a directory" above).
3. Verify received app/worker series have the correct concrete environment
   label, and no new series carry a literal placeholder.
4. Check remote-write rejection counters/logs and current sample timestamps.
   If errors remain, inspect duplicate writers, receiver relabeling, scrape
   timestamps and host clock alignment rather than assuming this fixed every
   cause.
5. Preserve evidence of already-rejected blocks. This patch cannot recover
   discarded monitoring samples or rewrite historical mixed-environment data.

Do not clear remote-write buffers, remove authentication, weaken receiver
validation or rewrite historical series as part of this change. No production
configuration, infrastructure or data is modified by preparing this PR.
