# OrbitMesh Support Assistant - one image for ingest, chat (CLI/JSONL) and the HTTP wrapper.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    MODEL_CACHE_DIR=/models QDRANT_PATH=/data/qdrant SESSION_DIR=/data/sessions LLM_CACHE_DIR=/data/llm_cache \
    CONNECTORS_DIR=/data/connectors INDEX_STATE_PATH=/data/index_state.json \
    CORPUS_DIR=/app/corpus HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . && rm -rf /root/.cache

# Bake the local embedding model so the container needs no network at run time.
RUN python -c "from orbitmesh.embeddings import build_embedder; build_embedder('fastembed', 'BAAI/bge-small-en-v1.5', '/models')"

COPY corpus ./corpus
COPY scripts ./scripts
COPY eval ./eval
COPY tests ./tests

# Index the corpus at build time (embedded Qdrant under /data). `make ingest` inside the
# container re-syncs after a corpus change; with QDRANT_URL set it targets the server instead.
RUN python -m orbitmesh.cli ingest

RUN useradd -m app && chown -R app:app /app /data /models
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status==200 else 1)" || exit 1

ENTRYPOINT ["python", "-m", "orbitmesh.cli"]
CMD ["serve", "--port", "8080"]
