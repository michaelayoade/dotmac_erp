#!/usr/bin/env bash
# Traccar database backup: password-free globals plus a verified custom dump.
set -euo pipefail

REMOTE="${REMOTE:-Backup:db.backup}"
DB_CONTAINER="${DB_CONTAINER:-dotmac_traccar_db}"
DB_NAME="${DB_NAME:-traccar}"
DB_OS_USER="${DB_OS_USER:-postgres}"
LOCAL_DIR="${LOCAL_DIR:-/var/backups/db}"
REMOTE_DIR="${REMOTE_DIR:-${REMOTE}/traccar}"
KEEP_LAST="${KEEP_LAST:-5}"
SKIP_UPLOAD="${SKIP_UPLOAD:-0}"

timestamp="$(date -u +%Y%m%d_%H%M%S)"
base="traccar_${timestamp}"
globals_path="${LOCAL_DIR}/${base}.globals.sql.gz"
dump_path="${LOCAL_DIR}/${base}.dump"

mkdir -p "${LOCAL_DIR}"

exec_db() {
  docker exec -u "${DB_OS_USER}" -i "${DB_CONTAINER}" "$@"
}

echo "[backup] dumping Traccar cluster globals without password verifiers..."
exec_db pg_dumpall --globals-only --no-role-passwords | gzip -9 >"${globals_path}"
if ! gzip -cd "${globals_path}" | grep -qE '^CREATE ROLE '; then
  echo "[backup] FATAL: globals dump contains no CREATE ROLE statement." >&2
  exit 1
fi
if gzip -cd "${globals_path}" | grep -qiE "PASSWORD '(SCRAM-SHA-256|md5)"; then
  echo "[backup] FATAL: globals dump contains password verifiers." >&2
  rm -f "${globals_path}"
  exit 1
fi

echo "[backup] dumping ${DB_NAME} from ${DB_CONTAINER}..."
exec_db pg_dump -d "${DB_NAME}" -Fc >"${dump_path}"

echo "[backup] validating the custom-format archive..."
toc_entries="$(exec_db pg_restore --list <"${dump_path}" | grep -cvE '^;|^$' || true)"
if ((toc_entries < 1)); then
  echo "[backup] FATAL: archive has no readable table of contents." >&2
  exit 1
fi

chmod 600 "${globals_path}" "${dump_path}"

if [[ "${SKIP_UPLOAD}" != "1" ]]; then
  echo "[backup] uploading to ${REMOTE_DIR}..."
  rclone copy "${globals_path}" "${REMOTE_DIR}" --log-level INFO
  rclone copy "${dump_path}" "${REMOTE_DIR}" --log-level INFO

  mapfile -t runs < <(
    rclone lsf "${REMOTE_DIR}" --files-only |
      sed -nE 's/^traccar_([0-9]{8}_[0-9]{6})\..*$/\1/p' |
      sort -ru
  )
  if ((${#runs[@]} > KEEP_LAST)); then
    for stale in "${runs[@]:KEEP_LAST}"; do
      rclone lsf "${REMOTE_DIR}" --files-only |
        grep -E "^traccar_${stale}\." |
        while read -r file; do rclone deletefile "${REMOTE_DIR}/${file}"; done
    done
  fi
else
  echo "[backup] SKIP_UPLOAD=1 - not uploading."
fi

echo "[backup] done: ${globals_path} and ${dump_path}"
