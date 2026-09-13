"""Connectors: where documents come from.

The model is carried over from DBSearch.AI's connector layer (one shared, permission-aware
knowledge base fed by many sources), cut down to what a single-product support corpus needs:

    corpus      the supplied OrbitMesh corpus, seeded from corpus/manifest.json, read-only
    upload      .md files added through the UI
    gdrive      a public Google Drive file (or folder, with a free API key) link
    sharepoint  an "Anyone with the link" SharePoint / OneDrive file link

Every connector owns a folder under ``data/connectors/<id>/`` holding ``connector.json`` and
the fetched markdown under ``docs/``. Documents are described by the same header conventions
the corpus uses (title from the H1, ``**Document version:**``, ``**Published:**``), so an
uploaded revision of ``firmware-release-notes.md`` carries its own version and date.

A connector is *enabled* or not; ``sync.sync_all`` indexes only enabled connectors, and the
vector store's reconcile step drops everything else - disabling is a sync away from
"invisible", re-enabling a sync away from "back", with no duplicates either way.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .corpus import CORPUS_CONNECTOR_ID, DocumentMeta, describe_markdown, load_manifest

KINDS = ("corpus", "upload", "gdrive", "sharepoint")
MAX_DOC_BYTES = 5 * 1024 * 1024


class ConnectorError(ValueError):
    """A user-facing problem (bad kind, bad file, unknown id). Maps to HTTP 400/404."""


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:80] or "document"


@dataclass
class ConnectorDocument:
    doc_id: str
    title: str
    filename: str
    sha: str
    version: str = ""
    effective_date: str = ""
    fetched_at: float = 0.0
    bytes: int = 0
    origin: str = ""       # the URL or upload name this came from


@dataclass
class Connector:
    id: str
    name: str
    kind: str
    source: str = ""       # link for gdrive/sharepoint; empty for upload/corpus
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_sync: float = 0.0
    last_error: str = ""
    documents: list = field(default_factory=list)   # ConnectorDocument dicts

    @property
    def read_only(self) -> bool:
        return self.kind == "corpus"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Connector":
        c = Connector(**{k: v for k, v in d.items() if k in Connector.__dataclass_fields__})
        c.documents = [x if isinstance(x, dict) else asdict(x) for x in c.documents]
        return c


class ConnectorStore:
    def __init__(self, root: Path, corpus_dir: Path) -> None:
        self.root = root
        self.corpus_dir = corpus_dir
        root.mkdir(parents=True, exist_ok=True)

    # --- persistence ----------------------------------------------------------------------
    def _dir(self, connector_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", connector_id):
            raise ConnectorError(f"invalid connector id {connector_id!r}")
        return self.root / connector_id

    def _save(self, c: Connector) -> None:
        d = self._dir(c.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "docs").mkdir(exist_ok=True)
        tmp = d / "connector.json.tmp"
        tmp.write_text(json.dumps(c.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(d / "connector.json")

    def _corpus_connector(self) -> Connector:
        docs = []
        for m in load_manifest(self.corpus_dir):
            raw = m.path.read_bytes()
            docs.append(asdict(ConnectorDocument(
                doc_id=m.source_id, title=m.title, filename=m.path.name, sha=hashlib.sha1(raw).hexdigest()[:16],
                version=m.version, effective_date=m.effective_date, fetched_at=m.path.stat().st_mtime,
                bytes=len(raw), origin=str(m.path.name))))
        return Connector(id=CORPUS_CONNECTOR_ID, name="OrbitMesh product corpus", kind="corpus",
                         source=str(self.corpus_dir), enabled=self._corpus_enabled(), documents=docs)

    def _corpus_enabled(self) -> bool:
        p = self._dir(CORPUS_CONNECTOR_ID) / "connector.json"
        if p.exists():
            return bool(json.loads(p.read_text(encoding="utf-8")).get("enabled", True))
        return True

    def list(self) -> list[Connector]:
        out = [self._corpus_connector()]
        for p in sorted(self.root.glob("*/connector.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("kind") != "corpus":       # the corpus file only stores the enabled flag
                out.append(Connector.from_dict(d))
        return out

    def get(self, connector_id: str) -> Connector:
        for c in self.list():
            if c.id == connector_id:
                return c
        raise ConnectorError(f"unknown connector {connector_id!r}")

    # --- mutations ------------------------------------------------------------------------
    def create(self, name: str, kind: str, source: str = "") -> Connector:
        name = name.strip()
        if not name:
            raise ConnectorError("a connector needs a name")
        if kind not in KINDS or kind == "corpus":
            raise ConnectorError(f"kind must be one of upload, gdrive, sharepoint (got {kind!r})")
        if kind in ("gdrive", "sharepoint") and not source.strip():
            raise ConnectorError(f"a {kind} connector needs a link")
        base = slugify(name) or kind
        cid = base
        n = 2
        while (self.root / cid).exists() or cid == CORPUS_CONNECTOR_ID:
            cid = f"{base}-{n}"
            n += 1
        c = Connector(id=cid, name=name, kind=kind, source=source.strip())
        self._save(c)
        return c

    def delete(self, connector_id: str) -> None:
        c = self.get(connector_id)
        if c.read_only:
            raise ConnectorError("the supplied corpus connector cannot be deleted (disable it instead)")
        d = self._dir(connector_id)
        for p in sorted(d.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        d.rmdir()

    def set_enabled(self, connector_id: str, enabled: bool) -> Connector:
        c = self.get(connector_id)
        c.enabled = bool(enabled)
        if c.read_only:
            # Only the flag is persisted for the corpus; its documents always come from corpus/.
            d = self._dir(CORPUS_CONNECTOR_ID)
            d.mkdir(parents=True, exist_ok=True)
            (d / "connector.json").write_text(json.dumps({"id": c.id, "kind": "corpus", "enabled": c.enabled}),
                                              encoding="utf-8")
            return c
        self._save(c)
        return c

    def put_document(self, connector_id: str, filename: str, data: bytes, origin: str = "") -> ConnectorDocument:
        """Add or replace one markdown document. Same filename = same doc_id = replacement."""
        c = self.get(connector_id)
        if c.read_only:
            raise ConnectorError("the supplied corpus connector is read-only")
        filename = Path(filename).name
        if not filename.lower().endswith((".md", ".markdown", ".txt")):
            raise ConnectorError(f"{filename!r}: only markdown (.md) documents are accepted")
        if len(data) > MAX_DOC_BYTES:
            raise ConnectorError(f"{filename!r}: larger than {MAX_DOC_BYTES // 1024 // 1024} MB")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConnectorError(f"{filename!r}: not UTF-8 text") from exc
        if not text.strip():
            raise ConnectorError(f"{filename!r}: empty document")
        stem = slugify(Path(filename).stem)
        safe_name = f"{stem}.md"
        title, version, date = describe_markdown(text, Path(filename).stem)
        doc = ConnectorDocument(doc_id=stem, title=title, filename=safe_name,
                                sha=hashlib.sha1(data).hexdigest()[:16], version=version, effective_date=date,
                                fetched_at=time.time(), bytes=len(data), origin=origin or filename)
        (self._dir(c.id) / "docs" / safe_name).write_text(text, encoding="utf-8")
        c.documents = [d for d in c.documents if d["doc_id"] != stem] + [asdict(doc)]
        c.documents.sort(key=lambda d: d["doc_id"])
        self._save(c)
        return doc

    def remove_document(self, connector_id: str, doc_id: str) -> None:
        c = self.get(connector_id)
        if c.read_only:
            raise ConnectorError("the supplied corpus connector is read-only")
        keep = [d for d in c.documents if d["doc_id"] != doc_id]
        if len(keep) == len(c.documents):
            raise ConnectorError(f"unknown document {doc_id!r}")
        for d in c.documents:
            if d["doc_id"] == doc_id:
                (self._dir(c.id) / "docs" / d["filename"]).unlink(missing_ok=True)
        c.documents = keep
        self._save(c)

    def record_sync(self, connector_id: str, error: str = "") -> None:
        c = self.get(connector_id)
        if c.read_only:
            return
        c.last_sync = time.time()
        c.last_error = error
        self._save(c)

    # --- for ingestion ----------------------------------------------------------------------
    def document_metas(self, c: Connector) -> list[DocumentMeta]:
        if c.kind == "corpus":
            return list(load_manifest(self.corpus_dir))
        base = self._dir(c.id) / "docs"
        return [DocumentMeta(source_id=d["doc_id"], title=d["title"], path=base / d["filename"],
                             version=d.get("version", ""), effective_date=d.get("effective_date", ""),
                             connector_id=c.id)
                for d in c.documents if (base / d["filename"]).exists()]

    def fingerprint(self, embedder_name: str) -> str:
        """Changes whenever the set of (enabled connector, document sha) changes - the UI's
        "index is current / needs sync" signal and start-up's "skip the sync" check."""
        parts = [embedder_name]
        for c in self.list():
            if c.enabled:
                parts.append(c.id)
                parts.extend(f"{d['doc_id']}:{d['sha']}" for d in sorted(c.documents, key=lambda d: d["doc_id"]))
        return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
