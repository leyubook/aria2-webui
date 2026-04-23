#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_CMD="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
else
  echo "[Aria2 Plus] Python 3.10+ not found."
  exit 1
fi

mkdir -p downloads

echo "[Aria2 Plus] starting FastAPI server..."
exec "$PYTHON_CMD" -m server.run
