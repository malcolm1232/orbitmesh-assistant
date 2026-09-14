"""HTTP wrapper + web UI around the same Agent, for cloud deployment, monitoring and demos.

    GET  /                   the web app (Ask / Connectors / Dashboard) - no build step, no CDN
    POST /chat               {"session_id", "message"} -> the JSONL response object (+ evidence)
    GET  /health             liveness + index size (Cloud Run / load balancer probes)
    GET  /metrics            Prometheus exposition

    GET  /api/connectors                 list (the corpus connector is always first)
    POST /api/connectors                 {"name", "kind", "source"} -> create (+ fetch for links)
    GET  /api/connectors/{id}
    DELETE /api/connectors/{id}
    POST /api/connectors/{id}/enabled    {"enabled": bool}
    POST /api/connectors/{id}/upload     multipart .md files
    DELETE /api/connectors/{id}/documents/{doc_id}
    POST /api/connectors/{id}/sync       re-fetch (link connectors) + re-index everything
    POST /api/ingest                     re-index everything
    GET  /api/stats                      dashboard numbers (process-lifetime metrics + index)
    GET  /api/sessions                   recent conversations (facts, actions - no message text)

The CLI remains the primary interface; every write here goes through the same ConnectorStore
and sync_all that `make ingest` uses.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from pydantic import BaseModel, Field

from .agent import build_agent, build_retriever
from .config import load_settings
from .connectors import ConnectorError, ConnectorStore
from .observability import configure_logging, log_event
from .sync import index_state, needs_sync, refetch, sync_all

log = logging.getLogger("orbitmesh.server")
STATIC = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=4000)


class ConnectorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern="^(upload|gdrive|sharepoint)$")
    source: str = Field(default="", max_length=2000)


class EnabledBody(BaseModel):
    enabled: bool


def _metric_samples() -> dict:
    """Flatten the in-process Prometheus registry into {name{labels}: value}."""
    out: dict = {}
    for metric in REGISTRY.collect():
        if not metric.name.startswith("orbitmesh_"):
            continue
        for s in metric.samples:
            labels = ",".join(f"{k}={v}" for k, v in sorted(s.labels.items()))
            out[f"{s.name}{{{labels}}}" if labels else s.name] = s.value
    return out


def _histogram(samples: dict, name: str) -> dict:
    """Bucket counts + estimated p50/p95 from a Prometheus histogram's cumulative buckets.
    Buckets are summed across any other labels (e.g. provider); +Inf is reported as None."""
    cum: dict[float, float] = {}
    for k, v in samples.items():
        if not k.startswith(f"{name}_bucket{{"):
            continue
        m = re.search(r"le=([^,}]+)", k)
        if not m:
            continue
        le = float(m.group(1))
        cum[le] = cum.get(le, 0.0) + v
    buckets = sorted(cum.items())
    total = sum(v for k, v in samples.items() if k.startswith(f"{name}_count"))
    total_sum = sum(v for k, v in samples.items() if k.startswith(f"{name}_sum"))

    def pct(p: float) -> float | None:
        """Linear interpolation inside the bucket that holds the target rank, as Prometheus's
        histogram_quantile does; past the last finite bucket, that bucket's upper edge."""
        if not total:
            return None
        target = p * total
        lower, below = 0.0, 0.0
        for le, c in buckets:
            if c >= target:
                if math.isinf(le):
                    return lower if lower else None
                return round(lower + (le - lower) * (target - below) / (c - below), 2) if c > below else le
            lower, below = le, c
        return None

    prev = 0.0
    dist = []
    for le, c in buckets:
        if not math.isinf(le):
            dist.append({"le": le, "count": c - prev})
        prev = c
    return {"count": total, "mean": (total_sum / total) if total else None, "p50": pct(0.5), "p95": pct(0.95),
            "buckets": dist}


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="OrbitMesh Support Assistant", version=os.environ.get("APP_VERSION", "dev"))
    connectors = ConnectorStore(settings.connectors_dir, settings.corpus_dir)

    # Start-up: make the index match the enabled connectors, but only if it does not already.
    from .embeddings import build_embedder
    from .vectorstore import VectorStore

    embedder = build_embedder(settings.embedding_provider, settings.embedding_model, str(settings.model_cache_dir))
    store = VectorStore(url=settings.qdrant_url, api_key=settings.qdrant_api_key, path=settings.qdrant_path,
                        collection=settings.collection, embedder=embedder)
    if needs_sync(connectors, store, settings.index_state_path):
        log_event(log, "startup.sync", reason="index fingerprint differs from the enabled connectors")
        sync_all(connectors, store, settings.index_state_path)
    agent = build_agent(settings, store=store)
    started = time.time()

    def resync() -> dict:
        report = sync_all(connectors, store, settings.index_state_path)
        agent.reload(build_retriever(settings, store))
        return report.as_dict()

    def connector_view(c) -> dict:
        d = c.to_dict()
        d["read_only"] = c.read_only
        d["chunks"] = sum(1 for ch in agent.retriever._chunks if ch.connector_id == c.id)
        return d

    # --- pages ------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "app.html", media_type="text/html")

    @app.get("/static/{name}", include_in_schema=False)
    def static(name: str) -> FileResponse:
        path = (STATIC / name).resolve()
        if path.parent != STATIC.resolve() or not path.exists():
            raise HTTPException(404)
        return FileResponse(path)

    # --- ops --------------------------------------------------------------------------
    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "index_chunks": agent.retriever.size, "llm": settings.llm_provider,
                "model": settings.llm_model, "version": app.version,
                "index_current": not needs_sync(connectors, store, settings.index_state_path)}

    @app.get("/metrics")
    def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # --- chat -------------------------------------------------------------------------
    @app.post("/chat")
    def chat(req: ChatRequest) -> dict:
        try:
            return agent.handle(req.session_id, req.message).as_ui()
        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed")
            raise HTTPException(status_code=500, detail=type(exc).__name__) from exc

    # --- connectors -------------------------------------------------------------------
    def _wrap(fn):
        try:
            return fn()
        except ConnectorError as exc:
            raise HTTPException(status_code=404 if "unknown" in str(exc) else 400, detail=str(exc)) from exc

    @app.get("/api/connectors")
    def list_connectors() -> dict:
        return {"connectors": [connector_view(c) for c in connectors.list()],
                "index_current": not needs_sync(connectors, store, settings.index_state_path),
                "index": index_state(settings.index_state_path)}

    @app.post("/api/connectors", status_code=201)
    def create_connector(body: ConnectorCreate) -> dict:
        def go():
            c = connectors.create(body.name, body.kind, body.source)
            fetched = 0
            error = ""
            if c.kind in ("gdrive", "sharepoint"):
                try:
                    fetched = refetch(connectors, c.id, google_api_key=settings.google_api_key)
                except ConnectorError as exc:
                    error = str(exc)
            report = resync() if fetched else None
            return {"connector": connector_view(connectors.get(c.id)), "fetched": fetched, "error": error,
                    "sync": report}
        return _wrap(go)

    @app.get("/api/connectors/{connector_id}")
    def get_connector(connector_id: str) -> dict:
        return _wrap(lambda: connector_view(connectors.get(connector_id)))

    @app.delete("/api/connectors/{connector_id}")
    def delete_connector(connector_id: str) -> dict:
        def go():
            connectors.delete(connector_id)
            return {"deleted": connector_id, "sync": resync()}
        return _wrap(go)

    @app.post("/api/connectors/{connector_id}/enabled")
    def set_enabled(connector_id: str, body: EnabledBody) -> dict:
        def go():
            connectors.set_enabled(connector_id, body.enabled)
            return {"connector": connector_view(connectors.get(connector_id)), "sync": resync()}
        return _wrap(go)

    @app.post("/api/connectors/{connector_id}/upload")
    async def upload(connector_id: str, files: list[UploadFile] = File(...)) -> dict:
        added = []
        for f in files:
            data = await f.read()
            try:
                doc = connectors.put_document(connector_id, f.filename or "document.md", data)
            except ConnectorError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            added.append(doc.doc_id)
        return {"added": added, "connector": connector_view(connectors.get(connector_id)), "sync": resync()}

    @app.delete("/api/connectors/{connector_id}/documents/{doc_id}")
    def delete_document(connector_id: str, doc_id: str) -> dict:
        def go():
            connectors.remove_document(connector_id, doc_id)
            return {"removed": doc_id, "sync": resync()}
        return _wrap(go)

    @app.post("/api/connectors/{connector_id}/sync")
    def sync_connector(connector_id: str) -> dict:
        def go():
            fetched = refetch(connectors, connector_id, google_api_key=settings.google_api_key)
            return {"fetched": fetched, "connector": connector_view(connectors.get(connector_id)), "sync": resync()}
        return _wrap(go)

    @app.post("/api/ingest")
    def ingest() -> dict:
        return {"sync": resync()}

    # --- dashboard --------------------------------------------------------------------
    @app.get("/api/stats")
    def stats() -> dict:
        s = _metric_samples()
        by_action = {a: s.get(f"orbitmesh_turns_total{{action={a}}}", 0.0) for a in ("ask", "instruct", "resolved", "escalate")}
        guard_in = {k.split("kind=")[1].rstrip("}"): v for k, v in s.items() if k.startswith("orbitmesh_guardrail_input_total{")}
        guard_out = {k.split("outcome=")[1].rstrip("}"): v for k, v in s.items() if k.startswith("orbitmesh_guardrail_output_total{")}
        per_connector = {}
        for c in connectors.list():
            per_connector[c.id] = {"name": c.name, "kind": c.kind, "enabled": c.enabled, "documents": len(c.documents),
                                   "chunks": sum(1 for ch in agent.retriever._chunks if ch.connector_id == c.id)}
        return {
            "uptime_s": int(time.time() - started), "llm": f"{settings.llm_provider}:{settings.llm_model}",
            "embedder": store.embedder.name, "index_chunks": agent.retriever.size,
            "index_current": not needs_sync(connectors, store, settings.index_state_path),
            "turns": {"total": sum(by_action.values()), "by_action": by_action},
            "turn_latency": _histogram(s, "orbitmesh_turn_latency_seconds"),
            "llm_latency": _histogram(s, "orbitmesh_llm_latency_seconds"),
            "llm": {"cost_usd": s.get("orbitmesh_llm_cost_usd_total", 0.0),
                    "prompt_tokens": s.get("orbitmesh_llm_tokens_total{kind=prompt}", 0.0),
                    "completion_tokens": s.get("orbitmesh_llm_tokens_total{kind=completion}", 0.0),
                    "errors": sum(v for k, v in s.items() if k.startswith("orbitmesh_llm_errors_total{")),
                    "model": settings.llm_model, "provider": settings.llm_provider},
            "guardrails": {"input": guard_in, "output": guard_out},
            "retrieval": {"empty": s.get("orbitmesh_retrieval_empty_total", 0.0),
                          "hits": _histogram(s, "orbitmesh_retrieval_hits")},
            "errors": s.get("orbitmesh_errors_total", 0.0),
            "connectors": per_connector,
        }

    @app.get("/api/sessions")
    def sessions(limit: int = 20) -> dict:
        items = []
        d = settings.session_dir
        if d.exists():
            for p in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
                try:
                    st = json.loads(p.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                last = st.get("history", [])[-1] if st.get("history") else {}
                items.append({"session_id": st.get("session_id"), "turns": st.get("turns", 0),
                              "facts": st.get("facts", {}), "resolved": st.get("resolved", False),
                              "escalated": st.get("escalated", False), "last_action": last.get("action", ""),
                              "updated_at": p.stat().st_mtime})
        return {"sessions": items}

    return app
