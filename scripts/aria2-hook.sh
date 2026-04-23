#!/usr/bin/env bash
set -euo pipefail

API_URL="${ARIA2_PLUS_HOOK_URL:-http://127.0.0.1:8080/api/aria2/hook}"
HOOK_TOKEN="${ARIA2_PLUS_HOOK_TOKEN:-change-me-hook-token}"
GID="${1:-}"

if [[ -z "$GID" ]]; then
  exit 1
fi

curl -s -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d "{\"gid\":\"$GID\",\"token\":\"$HOOK_TOKEN\"}" >/dev/null
