"""One entry point for (re)indexing: every enabled connector -> chunks -> vector store.

Called by `make ingest`, by the UI's Sync buttons, and at server start-up. The vector
store's sync is a full reconcile (everything not produced this run is deleted), so this is
the only place that needs to decide *what should exist* - which is exactly "the documents of
the enabled connectors".
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .connectors import ConnectorError, ConnectorStore
from .corpus import Chunk, chunk_document
from .vectorstore import VectorStore

log = logging.getLogger("orbitmesh.sync")


@dataclass
class SyncReport:
    written: int = 0
    deleted: int = 0
    total: int = 0
    recreated: bool = False
    connectors: dict = field(default_factory=dict)   # id -> {"documents": n, "chunks": n, "error": str}
    fingerprint: str = ""
    elapsed_s: float = 0.0

    def as_dict(self) -> dict:
        return {"written": self.written, "deleted": self.deleted, "total": self.total, "recreated": self.recreated,
                "connectors": self.connectors, "fingerprint": self.fingerprint, "elapsed_s": round(self.elapsed_s, 2)}


def collect_chunks(connectors: ConnectorStore) -> tuple[list[Chunk], dict]:
    chunks: list[Chunk] = []
    per: dict = {}
    for c in connectors.list():
        if not c.enabled:
            per[c.id] = {"documents": len(c.documents), "chunks": 0, "enabled": False}
            continue
        n_before = len(chunks)
        metas = connectors.document_metas(c)
        for meta in metas:
            chunks.extend(chunk_document(meta))
        per[c.id] = {"documents": len(metas), "chunks": len(chunks) - n_before, "enabled": True}
    return chunks, per


def refetch(connectors: ConnectorStore, connector_id: str, *, google_api_key: str = "") -> int:
    """Re-download a link connector's documents. Returns the number of documents fetched.
    Upload/corpus connectors have nothing to fetch and return 0."""
    from .fetchers import fetch

    c = connectors.get(connector_id)
    if c.kind not in ("gdrive", "sharepoint"):
        return 0
    try:
        items = fetch(c.kind, c.source, google_api_key=google_api_key)
    except ConnectorError as exc:
        connectors.record_sync(connector_id, error=str(exc))
        raise
    for item in items:
        connectors.put_document(connector_id, item.filename, item.data, origin=item.origin)
    connectors.record_sync(connector_id)
    return len(items)


def sync_all(connectors: ConnectorStore, store: VectorStore, state_path: Path | None = None) -> SyncReport:
    t0 = time.perf_counter()
    chunks, per = collect_chunks(connectors)
    vs = store.sync(chunks)
    report = SyncReport(written=vs.written, deleted=vs.deleted, total=vs.total, recreated=vs.recreated,
                        connectors=per, fingerprint=connectors.fingerprint(store.embedder.name),
                        elapsed_s=time.perf_counter() - t0)
    if state_path is not None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"fingerprint": report.fingerprint, "synced_at": time.time(),
                                          "total": report.total, "connectors": per}), encoding="utf-8")
    log.info("sync.done", extra={"data": report.as_dict()})
    return report


def index_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def needs_sync(connectors: ConnectorStore, store: VectorStore, state_path: Path) -> bool:
    return index_state(state_path).get("fingerprint") != connectors.fingerprint(store.embedder.name) or not store.ready()
