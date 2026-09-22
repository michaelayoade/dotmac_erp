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
   agent's already-loaded configuration.
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
