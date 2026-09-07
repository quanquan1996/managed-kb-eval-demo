#!/usr/bin/env bash
# Ask the knowledge base one question.
#   ./ask.sh "计量芯片保修几年？"
#   ./ask.sh "错误码 E207 是什么" --mode plain --show-chunks
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/scripts/run.sh" ask.py "$@"
