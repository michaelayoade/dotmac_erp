#!/usr/bin/env bash
# Traccar database backup: password-free globals plus a verified custom dump.
set -euo pipefail
umask 077

REMOTE="${REMOTE:-Backup:db.backup}"
DB_CONTAINER="${DB_CONTAINER:-dotmac_traccar_db}"
DB_OS_USER="${DB_OS_USER:-postgres}"

read_container_env() {
  local key="$1"
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
    "${DB_CONTAINER}" | sed -n "s/^${key}=//p" | tail -n 1
}

# The official image's operating-system account remains `postgres`, but the
# database login is the value used to initialize the container. Keep those two
# identities separate: running a process as OS user postgres must never make
# libpq silently try a nonexistent database role named postgres.
DB_USER="${DB_USER:-$(read_container_env POSTGRES_USER)}"
DB_NAME="${DB_NAME:-$(read_container_env POSTGRES_DB)}"
if [[ -z "${DB_USER}" || -z "${DB_NAME}" ]]; then
  echo "[backup] FATAL: could not resolve POSTGRES_USER/POSTGRES_DB from ${DB_CONTAINER}." >&2
  echo "[backup] Set DB_USER and DB_NAME explicitly only if the container metadata is unavailable." >&2
  exit 2
fi

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

echo "[backup] checking database access as ${DB_USER}..."
if [[ "$(exec_db psql -U "${DB_USER}" -d "${DB_NAME}" --no-password -Atqc 'SELECT 1')" != "1" ]]; then
  echo "[backup] FATAL: ${DB_USER} cannot connect to ${DB_NAME}." >&2
  exit 1
fi

echo "[backup] dumping Traccar cluster globals without password verifiers..."
exec_db pg_dumpall -U "${DB_USER}" --no-password --globals-only --no-role-passwords | gzip -9 >"${globals_path}"
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
exec_db pg_dump -U "${DB_USER}" -d "${DB_NAME}" --no-password -Fc >"${dump_path}"

echo "[backup] validating the custom-format archive..."
# This is an offline archive parse: pg_restore does not connect to PostgreSQL
# when --list reads from stdin, so no database role is applicable here. The
# isolated restore rehearsal supplies its target role explicitly.
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
