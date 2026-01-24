#!/usr/bin/env bash
set -euo pipefail

PLAN_FILE="${PLAN_FILE:-/root/dotmac/PRD.md}"
WORKDIR="${WORKDIR:-/root/dotmac}"
LOOP_LIMIT="${LOOP_LIMIT:-10}"
RUN_CHECKS="${RUN_CHECKS:-1}"
RUN_TESTS="${RUN_TESTS:-1}"
STATE_FILE="${STATE_FILE:-/root/dotmac/.ralph_state}"

detect_build_cmd() {
  if [ -n "${BUILD_CMD:-}" ]; then
    echo "$BUILD_CMD"
    return
  fi
  if [ -f "$WORKDIR/package.json" ] && command -v npm >/dev/null 2>&1; then
    if rg -q "\"build:css\"" "$WORKDIR/package.json" && [ -d "$WORKDIR/node_modules" ]; then
      echo "npm run build:css"
      return
    fi
  fi
  echo ""
}

detect_test_cmd() {
  if [ -n "${TEST_CMD:-}" ]; then
    echo "$TEST_CMD"
    return
  fi
  if [ -f "$WORKDIR/pytest.ini" ] && command -v pytest >/dev/null 2>&1; then
    echo "pytest -q"
    return
  fi
  echo ""
}

if ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI not found in PATH" >&2
  exit 1
fi

if [ ! -f "$PLAN_FILE" ]; then
  echo "Plan file not found: $PLAN_FILE" >&2
  exit 1
fi

for i in $(seq 1 "$LOOP_LIMIT"); do
  echo "\n--- Ralph loop iteration $i/$LOOP_LIMIT ---"
  MUST_FIX_ONLY="false"
  if [ -f "$STATE_FILE" ] && rg -q "checks_failed=1" "$STATE_FILE"; then
    MUST_FIX_ONLY="true"
  fi
  PROMPT=$(cat <<'PROMPT_EOF'
You are an autonomous coding agent. Use the plan in PRD.md as the source of truth.

Rules:
- Implement the smallest next slice of the plan.
- Update PRD.md by marking completed checklist items inline (e.g., add [x]).
- If tests/build fail, the next slice must fix them before new features.
- Provide a short diff summary and remaining items at the end.

Start now.
PROMPT_EOF
)

  if [ "$MUST_FIX_ONLY" = "true" ]; then
    PROMPT="$PROMPT

Previous iteration failed checks. Fix checks only; do not add new features."
  fi

  CLAUDE_ARGS=(--print --permission-mode dontAsk --no-session-persistence)
  if [ "${EUID:-0}" -ne 0 ]; then
    CLAUDE_ARGS+=(--dangerously-skip-permissions)
  else
    echo "Running as root: skipping --dangerously-skip-permissions (Claude disallows it for root)."
  fi
  (cd "$WORKDIR" && printf "%s" "$PROMPT" | claude "${CLAUDE_ARGS[@]}")

  if [ "$RUN_CHECKS" -eq 1 ]; then
    BUILD_CMD_DETECTED="$(detect_build_cmd)"
    TEST_CMD_DETECTED="$(detect_test_cmd)"
    CHECKS_FAILED=0

    if [ -n "$BUILD_CMD_DETECTED" ]; then
      echo "\nRunning build command: $BUILD_CMD_DETECTED"
      (cd "$WORKDIR" && bash -lc "$BUILD_CMD_DETECTED") || CHECKS_FAILED=1
    else
      echo "\nNo build command detected. Set BUILD_CMD to force."
    fi

    if [ "$RUN_TESTS" -eq 1 ] && [ -n "$TEST_CMD_DETECTED" ]; then
      echo "\nRunning test command: $TEST_CMD_DETECTED"
      (cd "$WORKDIR" && bash -lc "$TEST_CMD_DETECTED") || CHECKS_FAILED=1
    elif [ "$RUN_TESTS" -eq 1 ]; then
      echo "\nNo test command detected. Set TEST_CMD to force."
    fi

    if [ "$CHECKS_FAILED" -eq 1 ]; then
      echo "checks_failed=1" > "$STATE_FILE"
      echo "Checks failed. Next iteration will focus on fixes."
    else
      echo "checks_failed=0" > "$STATE_FILE"
    fi
  fi

  if command -v rg >/dev/null 2>&1; then
    HAS_UNCHECKED=$(rg -n "\[ \]" "$PLAN_FILE" 2>/dev/null || true)
  else
    HAS_UNCHECKED=$(grep -n "\[ \]" "$PLAN_FILE" 2>/dev/null || true)
  fi
  if [ -z "$HAS_UNCHECKED" ]; then
    echo "No unchecked items detected in PRD.md. Stopping."
    break
  fi

done
