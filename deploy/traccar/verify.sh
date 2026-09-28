#!/usr/bin/env bash
# Verify a running Step 2 Traccar stack without creating users or devices.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${SCRIPT_DIR}/compose.yml}"
ENV_FILE="${ENV_FILE:?Set ENV_FILE to the host-only Traccar Compose env file}"
PUBLIC_HOST="${PUBLIC_HOST:-127.0.0.1}"

compose() {
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

echo "[verify] checking Compose service health..."
db_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' dotmac_traccar_db)"
traccar_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' dotmac_traccar)"
[[ "${db_health}" == "healthy" ]] || { echo "[verify] database is ${db_health}" >&2; exit 1; }
[[ "${traccar_health}" == "healthy" ]] || { echo "[verify] Traccar is ${traccar_health}" >&2; exit 1; }

echo "[verify] checking HTTP health through the loopback binding..."
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8082/api/health >/dev/null

echo "[verify] checking database readiness and GPS103 listener in-container..."
compose exec -T traccar-db pg_isready -U traccar -d traccar >/dev/null
compose exec -T traccar sh -ec 'nc -z 127.0.0.1 5001'

echo "[verify] checking host port exposure..."
http_binding="$(docker port dotmac_traccar 8082/tcp)"
[[ "${http_binding}" == 127.0.0.1:* ]] || {
  echo "[verify] Traccar HTTP is not loopback-only: ${http_binding}" >&2
  exit 1
}
if docker port dotmac_traccar_db 5432/tcp 2>/dev/null | grep -q .; then
  echo "[verify] PostgreSQL unexpectedly has a published host port" >&2
  exit 1
fi

echo "[verify] checking GPS103 reachability at ${PUBLIC_HOST}:5001..."
if command -v nc >/dev/null 2>&1; then
  nc -z -w 5 "${PUBLIC_HOST}" 5001
else
  timeout 5 bash -c "</dev/tcp/${PUBLIC_HOST}/5001"
fi

echo "[verify] checking recent logs for repeated startup/database failures..."
recent_logs="$(compose logs --since 5m --no-color traccar traccar-db 2>&1)"
if grep -Eiq '(authentication failed|connection refused|database.*(failed|error)|liquibase.*failed|startup failed)' <<<"${recent_logs}"; then
  echo "[verify] suspicious startup/database failures found in recent logs" >&2
  grep -Ei '(authentication failed|connection refused|database.*(failed|error)|liquibase.*failed|startup failed)' <<<"${recent_logs}" >&2
  exit 1
fi

echo "[verify] Step 2 runtime checks passed. No user, device, or ERP connection was created."
