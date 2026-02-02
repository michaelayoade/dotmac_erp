#!/usr/bin/env bash
set -euo pipefail

# Simple, idempotent VPS optimization script for Ubuntu + Postgres 16 + Redis.
# Adjust defaults via env vars if needed.

PG_TUNE_FILE=${PG_TUNE_FILE:-/etc/postgresql/16/main/conf.d/99-tuning.conf}
PG_SHARED_BUFFERS=${PG_SHARED_BUFFERS:-2GB}
PG_WORK_MEM=${PG_WORK_MEM:-16MB}
PG_MAINTENANCE_WORK_MEM=${PG_MAINTENANCE_WORK_MEM:-256MB}
PG_EFFECTIVE_CACHE_SIZE=${PG_EFFECTIVE_CACHE_SIZE:-6GB}

REDIS_CONF=${REDIS_CONF:-/etc/redis/redis.conf}
REDIS_MAXMEMORY=${REDIS_MAXMEMORY:-512mb}
REDIS_MAXMEMORY_POLICY=${REDIS_MAXMEMORY_POLICY:-allkeys-lru}

SERVICES_TO_DISABLE=(ModemManager multipathd udisks2)

require_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "This script must be run as root." >&2
    exit 1
  fi
}

backup_file() {
  local f="$1"
  if [[ -f "$f" ]]; then
    cp -n "$f" "$f.bak" || true
  fi
}

write_pg_tuning() {
  echo "Writing Postgres tuning to $PG_TUNE_FILE"
  mkdir -p "$(dirname "$PG_TUNE_FILE")"
  cat > "$PG_TUNE_FILE" <<PGEOF
# tuned for 11Gi RAM VPS with Postgres + app containers
shared_buffers = $PG_SHARED_BUFFERS
work_mem = $PG_WORK_MEM
maintenance_work_mem = $PG_MAINTENANCE_WORK_MEM
effective_cache_size = $PG_EFFECTIVE_CACHE_SIZE
PGEOF
}

ensure_redis_setting() {
  local key="$1" value="$2"
  if rg -q "^${key} " "$REDIS_CONF"; then
    # replace existing
    sed -i "s/^${key} .*/${key} ${value}/" "$REDIS_CONF"
  else
    printf "\n# tuning\n%s %s\n" "$key" "$value" >> "$REDIS_CONF"
  fi
}

write_redis_tuning() {
  echo "Updating Redis config at $REDIS_CONF"
  backup_file "$REDIS_CONF"
  ensure_redis_setting "maxmemory" "$REDIS_MAXMEMORY"
  ensure_redis_setting "maxmemory-policy" "$REDIS_MAXMEMORY_POLICY"
}

disable_services() {
  for svc in "${SERVICES_TO_DISABLE[@]}"; do
    if systemctl list-unit-files | rg -q "^${svc}\.service"; then
      echo "Disabling $svc"
      systemctl disable --now "$svc" || true
    fi
  done
  if systemctl list-unit-files | rg -q '^multipathd\.socket'; then
    echo "Disabling multipathd.socket"
    systemctl disable --now multipathd.socket || true
  fi
}

restart_services() {
  echo "Restarting postgresql and redis"
  systemctl restart postgresql
  systemctl restart redis-server
}

verify() {
  echo "Postgres settings:"
  sudo -u postgres psql -tAc "show shared_buffers; show work_mem; show maintenance_work_mem; show effective_cache_size;" || true
  echo "Redis settings:"
  redis-cli CONFIG GET maxmemory maxmemory-policy || true
}

main() {
  require_root
  write_pg_tuning
  write_redis_tuning
  disable_services
  restart_services
  verify
  echo "Done."
}

main "$@"
