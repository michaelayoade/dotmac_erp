#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE="${REMOTE:-origin}"
DEV_BRANCH="${DEV_BRANCH:-dev}"
MAIN_BRANCH="${MAIN_BRANCH:-main}"
RHI_BRANCH="${RHI_BRANCH:-RHI}"
BUILD_DIR="${BUILD_DIR:-/tmp/dotmac-rhi-build}"
IMAGE_TAG="${IMAGE_TAG:-dotmac-erp-hardened:local}"

RHI_OWNED_FILES=(
  "scripts/build_rhi.py"
  "Dockerfile.hardened"
  ".github/workflows/release-hardened.yml"
)

ORIGINAL_DIR="$(pwd)"

log() {
  echo
  echo "==> $*"
}

fail() {
  echo
  echo "ERROR: $*" >&2
  exit 1
}

require_repo() {
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "Not inside a git repository."
}

require_clean_tree() {
  if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git status --porcelain)" ]]; then
    git status --short
    fail "Working tree is dirty. Commit or stash changes before continuing."
  fi
}

require_branch_ref() {
  local ref="$1"
  git show-ref --verify --quiet "$ref" || fail "Missing required ref: $ref"
}

tree_blob() {
  local ref="$1"
  local path="$2"
  git rev-parse "${ref}:${path}" 2>/dev/null || true
}

protect_rhi_owned_files_before_merge() {
  local upstream_ref="$1"
  local base_ref="$2"

  log "Checking RHI-owned files before merge"
  local path current_blob upstream_blob base_blob
  for path in "${RHI_OWNED_FILES[@]}"; do
    current_blob="$(tree_blob HEAD "$path")"
    upstream_blob="$(tree_blob "$upstream_ref" "$path")"
    base_blob="$(tree_blob "$base_ref" "$path")"

    if [[ -n "$upstream_blob" && "$upstream_blob" != "$base_blob" && "$upstream_blob" != "$current_blob" ]]; then
      fail "$path changed on $upstream_ref since $base_ref. Resolve RHI-owned file ownership manually before merging."
    fi
  done
}

snapshot_rhi_owned_files() {
  local path blob
  for path in "${RHI_OWNED_FILES[@]}"; do
    blob="$(tree_blob HEAD "$path")"
    printf '%s\t%s\n' "$path" "$blob"
  done
}

verify_rhi_owned_files_unchanged() {
  local snapshot_file="$1"
  local path before after

  log "Verifying RHI-owned files were preserved"
  while IFS=$'\t' read -r path before; do
    after="$(tree_blob HEAD "$path")"
    if [[ "$after" != "$before" ]]; then
      fail "$path changed during merge. Review and repair the RHI branch manually."
    fi
  done < "$snapshot_file"
}

cleanup_build_worktree() {
  cd "$ORIGINAL_DIR" || return 0
  if git worktree list --porcelain | grep -Fxq "worktree $BUILD_DIR"; then
    git worktree remove --force "$BUILD_DIR" >/dev/null 2>&1 || true
  fi
  git worktree prune >/dev/null 2>&1 || true
  if [[ -d "$BUILD_DIR" ]]; then
    rm -rf "$BUILD_DIR"
  fi
}

trap cleanup_build_worktree EXIT

require_repo
require_clean_tree

log "Fetching latest refs from $REMOTE"
git fetch --prune "$REMOTE"

require_branch_ref "refs/remotes/${REMOTE}/${DEV_BRANCH}"
require_branch_ref "refs/remotes/${REMOTE}/${MAIN_BRANCH}"
require_branch_ref "refs/remotes/${REMOTE}/${RHI_BRANCH}"

log "Stage 1: promote ${DEV_BRANCH} -> ${MAIN_BRANCH}"
require_clean_tree
git checkout "$MAIN_BRANCH"
git pull --ff-only "$REMOTE" "$MAIN_BRANCH"
git merge --no-ff "${REMOTE}/${DEV_BRANCH}" -m "Promote ${DEV_BRANCH} to ${MAIN_BRANCH}"
git push "$REMOTE" "$MAIN_BRANCH"

log "Stage 2: promote ${MAIN_BRANCH} -> ${RHI_BRANCH}"
require_clean_tree
git checkout "$RHI_BRANCH"
git pull --ff-only "$REMOTE" "$RHI_BRANCH"

snapshot_file="$(mktemp)"
snapshot_rhi_owned_files > "$snapshot_file"
merge_base="$(git merge-base HEAD "$MAIN_BRANCH")"
protect_rhi_owned_files_before_merge "$MAIN_BRANCH" "$merge_base"
git merge --no-ff "$MAIN_BRANCH" -m "Merge ${MAIN_BRANCH} into ${RHI_BRANCH} for RHI"
verify_rhi_owned_files_unchanged "$snapshot_file"
rm -f "$snapshot_file"

log "Stage 3: build hardened image in disposable worktree"
require_clean_tree
cleanup_build_worktree
git worktree add --detach "$BUILD_DIR" "$RHI_BRANCH"

cd "$BUILD_DIR"
docker build -f Dockerfile.hardened -t "$IMAGE_TAG" .

log "Stage 4: push verified ${RHI_BRANCH}"
cd "$ORIGINAL_DIR"
require_clean_tree
git push "$REMOTE" "$RHI_BRANCH"

log "Promotion complete"
