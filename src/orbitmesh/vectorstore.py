"""Qdrant-backed vector store with an idempotent, de-duplicating sync.

Two deployment shapes behind one class:
  * ``QDRANT_URL`` set  -> a Qdrant server (the docker-compose service, or a managed cluster);
  * otherwise           -> Qdrant's embedded local mode at ``QDRANT_PATH`` (no daemon, used
                           by tests, CI and the zero-dependency quick start).

``sync`` is the "handle updated documents without accumulating duplicate chunks"
requirement. Point ids are derived from chunk content hashes, so:
  * unchanged section  -> same id, upsert overwrites in place (no duplicate);
  * edited section     -> new id written, stale id deleted by the reconcile pass;
  * removed document   -> all of its points deleted (it is no longer in the manifest).
The reconcile pass is a set difference between the ids Qdrant holds and the ids the corpus
produces, so it is exact regardless of how many times ingest runs.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from .corpus import Chunk
from .embeddings import Embedder

log = logging.getLogger("orbitmesh.vectorstore")

_NAMESPACE = uuid.UUID("2f1b5b0c-5b42-4b0f-9b6d-0f4c6d7a1e11")


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


@dataclass
class SyncReport:
    written: int
    deleted: int
    total: int
    recreated: bool

    def as_dict(self) -> dict:
        return {"written": self.written, "deleted": self.deleted, "total": self.total, "recreated": self.recreated}


class VectorStore:
    def __init__(self, *, url: str = "", api_key: str = "", path: Path | None = None,
                 collection: str = "orbitmesh_chunks", embedder: Embedder) -> None:
        self.is_server = bool(url)
        if url:
            self.client = QdrantClient(url=url, api_key=api_key or None, timeout=30)
            self.location = url
        else:
            assert path is not None
            path.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(path))
            self.location = f"embedded:{path}"
        self.collection = collection
        self.embedder = embedder

    # --- schema -----------------------------------------------------------------------
    def _ensure_collection(self) -> bool:
        """Create the collection if missing, or recreate it if the embedder changed (a
        different dimension or model would make old vectors meaningless). Returns True when
        a fresh collection was created."""
        if self.client.collection_exists(self.collection):
            info = self.client.get_collection(self.collection)
            size = info.config.params.vectors.size  # type: ignore[union-attr]
            stamp = self._embedder_stamp()
            if size == self.embedder.dim and stamp in (None, self.embedder.name):
                return False
            log.warning("recreating collection %s: embedder changed (%s/%s -> %s/%s)",
                        self.collection, size, stamp, self.embedder.dim, self.embedder.name)
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            self.collection,
            vectors_config=qm.VectorParams(size=self.embedder.dim, distance=qm.Distance.COSINE),
        )
        if self.is_server:
            # Payload indexes only exist on a Qdrant server; embedded mode filters by scan.
            for field_name, schema in (("source_id", qm.PayloadSchemaType.KEYWORD),
                                       ("product_line", qm.PayloadSchemaType.KEYWORD),
                                       ("product_explicit", qm.PayloadSchemaType.BOOL),
                                       ("archived", qm.PayloadSchemaType.BOOL)):
                self.client.create_payload_index(self.collection, field_name=field_name, field_schema=schema)
        return True

    _STAMP_ID = str(uuid.uuid5(_NAMESPACE, "__embedder_stamp__"))

    def _embedder_stamp(self) -> str | None:
        got = self.client.retrieve(self.collection, ids=[self._STAMP_ID], with_payload=True, with_vectors=False)
        return got[0].payload.get("embedder") if got else None

    def _write_stamp(self) -> None:
        self.client.upsert(self.collection, points=[qm.PointStruct(
            id=self._STAMP_ID, vector=[0.0] * self.embedder.dim,
            payload={"embedder": self.embedder.name, "chunk_id": "__embedder_stamp__", "source_id": "__meta__",
                     "text": "", "product_line": "none", "archived": False},
        )])

    # --- ingest -----------------------------------------------------------------------
    def existing_ids(self) -> set[str]:
        ids: set[str] = set()
        offset = None
        while True:
            points, offset = self.client.scroll(self.collection, limit=512, offset=offset,
                                                with_payload=False, with_vectors=False)
            ids.update(str(p.id) for p in points)
            if offset is None:
                break
        ids.discard(self._STAMP_ID)
        return ids

    def sync(self, chunks: list[Chunk], batch_size: int = 64) -> SyncReport:
        recreated = self._ensure_collection()
        before = set() if recreated else self.existing_ids()
        wanted = {point_id(c.chunk_id): c for c in chunks}
        to_write = [c for pid, c in wanted.items()]  # overwrite everything: cheap, and exact
        written = 0
        for i in range(0, len(to_write), batch_size):
            batch = to_write[i:i + batch_size]
            vectors = self.embedder.embed([c.text for c in batch])
            self.client.upsert(self.collection, points=[
                qm.PointStruct(id=point_id(c.chunk_id), vector=v, payload=c.payload())
                for c, v in zip(batch, vectors)
            ])
            written += len(batch)
        stale = sorted(before - set(wanted))
        if stale:
            self.client.delete(self.collection, points_selector=qm.PointIdsList(points=stale))
        self._write_stamp()
        return SyncReport(written=written, deleted=len(stale), total=len(wanted), recreated=recreated)

    # --- read -------------------------------------------------------------------------
    def all_chunks(self) -> list[Chunk]:
        out: list[Chunk] = []
        offset = None
        while True:
            points, offset = self.client.scroll(self.collection, limit=512, offset=offset,
                                                with_payload=True, with_vectors=False)
            for p in points:
                if str(p.id) != self._STAMP_ID:
                    out.append(Chunk.from_payload(p.payload))
            if offset is None:
                break
        return out

    def search(self, vector: list[float], limit: int, exclude_product: str | None = None) -> list[tuple[Chunk, float]]:
        must_not = [qm.FieldCondition(key="source_id", match=qm.MatchValue(value="__meta__"))]
        if exclude_product:
            # Hard exclusion only for documents that EXPLICITLY scope themselves to the other
            # product line ("Do not apply this document to the home R1 and N1 system").
            must_not.append(qm.Filter(must=[
                qm.FieldCondition(key="product_line", match=qm.MatchValue(value=exclude_product)),
                qm.FieldCondition(key="product_explicit", match=qm.MatchValue(value=True)),
            ]))
        res = self.client.query_points(
            self.collection, query=vector, limit=limit, with_payload=True,
            query_filter=qm.Filter(must_not=must_not),
        )
        return [(Chunk.from_payload(p.payload), float(p.score)) for p in res.points]

    def count(self) -> int:
        if not self.client.collection_exists(self.collection):
            return 0
        n = self.client.count(self.collection, exact=True).count
        return max(0, n - 1)  # minus the embedder stamp

    def ready(self) -> bool:
        return self.client.collection_exists(self.collection) and self.count() > 0
