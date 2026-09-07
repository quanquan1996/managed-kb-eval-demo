#!/usr/bin/env bash
# Score the three retrieval modes against sample-data/questions.json.
# Any extra arguments are passed through, for example:
#   ./eval.sh --only M1,M4
#   ./eval.sh --modes agentic --json results.json
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/scripts/run.sh" eval.py "$@"
