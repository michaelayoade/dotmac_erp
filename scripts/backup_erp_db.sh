#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"

load_env_default() {
  local key="$1"
  if [[ -n "${!key:-}" || ! -f "${ENV_FILE}" ]]; then
    return
  fi

  local value
  value="$(sed -n "s/^${key}=//p" "${ENV_FILE}" | head -n 1)"
  if [[ -n "${value}" ]]; then
    export "${key}=${value}"
  fi
}

load_env_default "POSTGRES_USER"
load_env_default "POSTGRES_PASSWORD"
load_env_default "POSTGRES_DB"

REMOTE="${REMOTE:-Backup:db.backup}"
DB_CONTAINER="${DB_CONTAINER:-dotmac_pg_local}"
DB_NAME="${DB_NAME:-${POSTGRES_DB:-dotmac_erp}}"
DB_USER="${DB_USER:-${POSTGRES_USER:-postgres}}"
DB_OS_USER="${DB_OS_USER:-postgres}"
PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}"
LOCAL_DIR="${LOCAL_DIR:-/var/backups/db}"
REMOTE_DIR="${REMOTE_DIR:-${REMOTE}/dotmac_erp}"
KEEP_LAST="${KEEP_LAST:-5}"

timestamp="$(date +%Y%m%d_%H%M%S)"
backup_file="dotmac_erp_${timestamp}.sql.gz"
local_path="${LOCAL_DIR}/${backup_file}"

mkdir -p "${LOCAL_DIR}"

echo "[backup] dumping ${DB_NAME} from ${DB_CONTAINER}..."
if [[ -n "${PGPASSWORD}" ]]; then
  docker exec -e PGPASSWORD="${PGPASSWORD}" "${DB_CONTAINER}" \
    pg_dump -U "${DB_USER}" -d "${DB_NAME}" | gzip -9 > "${local_path}"
else
  docker exec -u "${DB_OS_USER}" "${DB_CONTAINER}" \
    pg_dump -d "${DB_NAME}" | gzip -9 > "${local_path}"
fi

echo "[backup] uploading to ${REMOTE_DIR}..."
rclone copy "${local_path}" "${REMOTE_DIR}" --log-level INFO

echo "[backup] enforcing retention (keep last ${KEEP_LAST})..."
mapfile -t remote_files < <(
  rclone lsf "${REMOTE_DIR}" --files-only --format "tp" --separator "|" |
    sort -r |
    cut -d "|" -f 2-
)
if (( ${#remote_files[@]} > KEEP_LAST )); then
  for f in "${remote_files[@]:KEEP_LAST}"; do
    rclone deletefile "${REMOTE_DIR}/${f}"
  done
fi

echo "[backup] done: ${local_path}"
