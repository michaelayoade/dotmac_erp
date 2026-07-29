#!/usr/bin/env bash
set -euo pipefail

IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-ghcr.io/michaelayoade/dotmac_erp}"
KEEP_LAST="${KEEP_LAST:-5}"
DRY_RUN=1

usage() {
  cat <<USAGE
Usage: $0 [--execute] [--dry-run] [--keep N] [--repository IMAGE_REPOSITORY]

Keep the newest N unique local Docker image IDs for a repository and remove
older tags. Images currently used by any container are always protected.

Defaults:
  IMAGE_REPOSITORY=${IMAGE_REPOSITORY}
  KEEP_LAST=${KEEP_LAST}
  mode=dry-run
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute)
      DRY_RUN=0
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --keep)
      KEEP_LAST="${2:-}"
      shift 2
      ;;
    --repository)
      IMAGE_REPOSITORY="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[docker-retention] unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${IMAGE_REPOSITORY}" ]]; then
  echo "[docker-retention] IMAGE_REPOSITORY cannot be empty" >&2
  exit 2
fi

if ! [[ "${KEEP_LAST}" =~ ^[0-9]+$ ]] || (( KEEP_LAST < 1 )); then
  echo "[docker-retention] KEEP_LAST must be a positive integer" >&2
  exit 2
fi

echo "[docker-retention] repository=${IMAGE_REPOSITORY} keep_last=${KEEP_LAST} dry_run=${DRY_RUN}"

mapfile -t image_rows < <(
  docker image ls --no-trunc "${IMAGE_REPOSITORY}" \
    --format '{{.CreatedAt}}|{{.ID}}|{{.Repository}}:{{.Tag}}' |
    sort -r
)

if (( ${#image_rows[@]} == 0 )); then
  echo "[docker-retention] no local images found for ${IMAGE_REPOSITORY}"
  exit 0
fi

declare -A retain_ids=()
declare -A seen_ids=()
retain_count=0

for row in "${image_rows[@]}"; do
  image_id="$(cut -d '|' -f 2 <<< "${row}")"
  if [[ -n "${seen_ids[$image_id]:-}" ]]; then
    continue
  fi
  seen_ids["$image_id"]=1
  if (( retain_count < KEEP_LAST )); then
    retain_ids["$image_id"]=1
    ((retain_count += 1))
  fi
done

declare -A protected_ids=()
while IFS= read -r image_id; do
  if [[ -n "${image_id}" ]]; then
    protected_ids["$image_id"]=1
  fi
done < <(docker ps -aq | xargs -r docker inspect --format '{{.Image}}')

remove_count=0
for row in "${image_rows[@]}"; do
  image_id="$(cut -d '|' -f 2 <<< "${row}")"
  image_ref="$(cut -d '|' -f 3- <<< "${row}")"

  if [[ "${image_ref}" == *":<none>" ]]; then
    continue
  fi

  if [[ -n "${retain_ids[$image_id]:-}" ]]; then
    echo "[docker-retention] keep ${image_ref} (${image_id})"
    continue
  fi

  if [[ -n "${protected_ids[$image_id]:-}" ]]; then
    echo "[docker-retention] protect in-use ${image_ref} (${image_id})"
    continue
  fi

  ((remove_count += 1))
  if (( DRY_RUN == 1 )); then
    echo "[docker-retention] would remove ${image_ref} (${image_id})"
  else
    echo "[docker-retention] removing ${image_ref} (${image_id})"
    docker image rm "${image_ref}"
  fi
done

if (( DRY_RUN == 1 )); then
  echo "[docker-retention] dry run complete: ${remove_count} tag(s) would be removed"
else
  echo "[docker-retention] complete: removed ${remove_count} tag(s)"
fi
