"""OrbitMesh Support Assistant.

Layout (one module per concern, each importable on its own):

    config.py        environment -> Settings
    corpus.py        manifest + markdown -> section-level Chunks (with product/archive metadata)
    embeddings.py    EmbeddingPort: local fastembed (default) or deterministic hashing (CI)
    vectorstore.py   Qdrant (server or embedded) with idempotent, de-duplicating upsert
    retrieval.py     hybrid vector + BM25 retrieval, RRF fusion, product/archive aware
    conversation.py  per-session state: facts, LED/error extraction, steps, reset gate
    guardrails.py    input + output guardrails (deterministic, testable)
    llm.py           OpenRouter client (JSON mode) and a deterministic mock for CI
    agent.py         one turn: guard -> remember -> retrieve -> decide -> guard -> respond
    cli.py           interactive REPL and the --jsonl transport
    server.py        optional HTTP wrapper (/chat, /healthz, /metrics) for cloud deploys
    observability.py structured JSON logging to stderr + Prometheus metrics
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
