#!/usr/bin/env bash
# Shared launcher so every wrapper resolves python the same way.
# Usage: scripts/run.sh <script.py> [args...]
# Not meant to be called directly.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

SCRIPT=$1
shift

PYTHON=$(command -v python3 || command -v python) || {
  echo "python3 not found. It is preinstalled in CloudShell." >&2
  exit 1
}

exec "$PYTHON" "$HERE/$SCRIPT" "$@"
