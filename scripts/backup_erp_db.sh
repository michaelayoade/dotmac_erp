#!/usr/bin/env bash
set -euo pipefail

REMOTE="Backup:db.backup"
DB_CONTAINER="dotmac_erp_db"
DB_NAME="dotmac_erp"
DB_USER="postgres"
PGPASSWORD="/EtoBko8fSfeXiX9IDWO7es+uDI82fs/"
LOCAL_DIR="/var/backups/db"
REMOTE_DIR="${REMOTE}/dotmac_erp"
KEEP_LAST=5

timestamp="$(date +%Y%m%d_%H%M%S)"
backup_file="dotmac_erp_${timestamp}.sql.gz"
local_path="${LOCAL_DIR}/${backup_file}"

mkdir -p "${LOCAL_DIR}"

echo "[backup] dumping ${DB_NAME} from ${DB_CONTAINER}..."
docker exec -e PGPASSWORD="${PGPASSWORD}" "${DB_CONTAINER}" \
  pg_dump -U "${DB_USER}" -d "${DB_NAME}" | gzip -9 > "${local_path}"

echo "[backup] uploading to ${REMOTE_DIR}..."
rclone copy "${local_path}" "${REMOTE_DIR}" --log-level INFO

echo "[backup] enforcing retention (keep last ${KEEP_LAST})..."
mapfile -t remote_files < <(rclone lsf "${REMOTE_DIR}" --format "tp" | sort -r | awk '{print $2}')
if (( ${#remote_files[@]} > KEEP_LAST )); then
  for f in "${remote_files[@]:KEEP_LAST}"; do
    rclone deletefile "${REMOTE_DIR}/${f}"
  done
fi

echo "[backup] done: ${local_path}"
