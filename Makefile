# OrbitMesh Support Assistant
PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
# Interpreter used to CREATE the venv: first 3.11+ on PATH (override: make setup SYS_PY=python3.12)
SYS_PY ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)

.PHONY: setup ingest chat test eval contract serve clean

setup:            ## create the venv and install the package + dev deps
	$(SYS_PY) -m venv .venv
	$(PIP) install --upgrade pip >/dev/null
	$(PIP) install -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo "setup done - put your OPENROUTER_API_KEY in .env (or use LLM_PROVIDER=mock)"

ingest:           ## index corpus/ into the vector database (idempotent)
	./scripts/ingest.sh

chat:             ## interactive CLI conversation
	./scripts/chat.sh

test:             ## unit + integration tests (no network, no key)
	LLM_PROVIDER=mock EMBEDDING_PROVIDER=hash QDRANT_URL= $(PY) -m pytest

eval:             ## run the evaluation suite and print a summary (uses the configured LLM)
	$(PY) -m eval.run_eval $(EVAL_ARGS)

contract:         ## verify the JSONL transport contract
	$(PY) scripts/check_contract.py

serve:            ## HTTP wrapper on :8080 (POST /chat, /healthz, /metrics)
	$(PY) -m orbitmesh.cli serve --port 8080

clean:
	rm -rf data/qdrant data/sessions data/llm_cache eval_results .pytest_cache
