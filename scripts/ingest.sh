#!/usr/bin/env bash
# Index the corpus into the vector database. Idempotent: re-run after editing corpus/.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"; [ -x "$PY" ] || PY=python3
exec "$PY" -m orbitmesh.cli ingest "$@"
