#!/usr/bin/env bash
# Interactive chat, or the JSONL transport with --jsonl (stdout = JSON only, logs -> stderr).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"; [ -x "$PY" ] || PY=python3
exec "$PY" -m orbitmesh.cli chat "$@"
