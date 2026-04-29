#!/usr/bin/env bash
set -euo pipefail

API_URL="${ARIA2_PLUS_HOOK_URL:-http://127.0.0.1:8080/api/aria2/hook}"
HOOK_TOKEN="${ARIA2_PLUS_HOOK_TOKEN:-change-me-hook-token}"
GID="${1:-}"

if [[ -z "$GID" ]]; then
  exit 1
fi

# Use jq if available for safe JSON construction; fall back to printf
if command -v jq >/dev/null 2>&1; then
  PAYLOAD=$(jq -nc --arg gid "$GID" --arg token "$HOOK_TOKEN" '{gid: $gid, token: $token}')
else
  # Escape any special JSON characters in GID
  SAFE_GID=${GID//\\/\\\\}
  SAFE_GID=${SAFE_GID//\"/\\\"}
  PAYLOAD="{\"gid\":\"${SAFE_GID}\",\"token\":\"${HOOK_TOKEN}\"}"
fi

curl -s -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" >/dev/null
