"""Corpus loading and chunking.

The manifest is the source of truth for which documents exist and their versions. Each
markdown file is split on its headings, so a chunk is one *section* of one document: the
unit a support agent would cite ("Troubleshooting Guide, 'N1 node disconnects
intermittently'"). Tables and numbered procedures therefore never get cut in half, which
matters for a corpus whose value is in ordered steps and LED tables.

Metadata derived here (never hard-coded product facts):
  * ``product_line`` - "pro" / "home", read from the document's own "Applies to:" line when
    it has one (``product_explicit=True``), otherwise inferred from which model names the
    text mentions (``product_explicit=False``, so retrieval treats it as a soft signal).
  * ``archived`` - the document declares itself superseded.
  * ``version`` / ``effective_date`` - from the manifest, so "current vs. old" is decidable.

Chunk ids are content hashes of (source_id, locator, text). Re-ingesting an unchanged
document rewrites the same ids; a changed section gets a new id and the old one is
deleted by the vector store's reconcile step. That is what keeps re-ingestion free of
duplicates (see ``vectorstore.VectorStore.sync``).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADING = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
_APPLIES = re.compile(r"^\*\*Applies to:\*\*\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_PRO_MODELS = re.compile(r"\b(R5|N5)\s*Pro\b|\bPro Series\b|\bPro Console\b", re.IGNORECASE)
_HOME_MODELS = re.compile(r"\b(R1|N1)\b")
_ARCHIVED = re.compile(r"\barchived reference\b|\bsuperseded by\b", re.IGNORECASE)
_FAQ_QUESTION = re.compile(r"^\*\*([^*]+\?)\*\*\s*$")

# Long sections are split into windows so no chunk exceeds the embedder's useful context.
# Adapted from DBSearch.AI's sliding-window chunker (Apache-2.0), but the split unit here
# is a paragraph boundary, because a troubleshooting bullet cut mid-sentence is worse than
# a slightly uneven chunk.
MAX_CHUNK_CHARS = 1800
OVERLAP_PARAGRAPHS = 1


CORPUS_CONNECTOR_ID = "orbitmesh-corpus"

_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_VERSION_LINE = re.compile(r"^\*\*(?:Document|Policy) version:\*\*\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_DATE_LINE = re.compile(r"^\*\*(?:Published|Effective):\*\*\s*(\S+)", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class DocumentMeta:
    source_id: str
    title: str
    path: Path
    version: str
    effective_date: str
    connector_id: str = CORPUS_CONNECTOR_ID


def describe_markdown(text: str, fallback_title: str) -> tuple[str, str, str]:
    """(title, version, effective_date) read from a markdown document's own header lines -
    the same conventions the supplied corpus uses - so an uploaded document gets the same
    freshness metadata as a manifest entry. Missing values are empty strings."""
    h1, ver, date = _H1.search(text), _VERSION_LINE.search(text), _DATE_LINE.search(text)
    return ((h1.group(1) if h1 else fallback_title).strip(),
            (ver.group(1) if ver else "").strip(),
            (date.group(1) if date else "").strip())


@dataclass
class Chunk:
    chunk_id: str
    source_id: str
    title: str
    version: str
    effective_date: str
    locator: str            # the H2 section heading (or document title for the preamble)
    subsection: str         # the H3 heading, if any
    text: str
    product_line: str       # "home" | "pro" | "all"
    product_explicit: bool
    archived: bool
    doc_hash: str
    part: int = 0           # window index within the section, 0 for the whole section
    connector_id: str = CORPUS_CONNECTOR_ID
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        loc = self.locator if not self.subsection else f"{self.locator} > {self.subsection}"
        return f"{self.source_id} | {loc}"

    def payload(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "title": self.title,
            "version": self.version,
            "effective_date": self.effective_date,
            "locator": self.locator,
            "subsection": self.subsection,
            "text": self.text,
            "product_line": self.product_line,
            "product_explicit": self.product_explicit,
            "archived": self.archived,
            "doc_hash": self.doc_hash,
            "part": self.part,
            "connector_id": self.connector_id,
        }

    @staticmethod
    def from_payload(p: dict) -> "Chunk":
        return Chunk(
            chunk_id=p["chunk_id"], source_id=p["source_id"], title=p.get("title", ""),
            version=p.get("version", ""), effective_date=p.get("effective_date", ""),
            locator=p.get("locator", ""), subsection=p.get("subsection", ""), text=p["text"],
            product_line=p.get("product_line", "all"), product_explicit=bool(p.get("product_explicit")),
            archived=bool(p.get("archived")), doc_hash=p.get("doc_hash", ""), part=int(p.get("part", 0)),
            connector_id=p.get("connector_id", CORPUS_CONNECTOR_ID),
        )


def load_manifest(corpus_dir: Path) -> list[DocumentMeta]:
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    docs = []
    for entry in manifest["documents"]:
        docs.append(DocumentMeta(
            source_id=entry["id"], title=entry["title"], path=corpus_dir / entry["path"],
            version=str(entry.get("version", "")), effective_date=str(entry.get("effective_date", "")),
        ))
    return docs


def classify_product(text: str) -> tuple[str, bool]:
    """Return (product_line, explicit). Explicit comes from an "Applies to:" line."""
    m = _APPLIES.search(text)
    if m:
        line = m.group(1)
        if _PRO_MODELS.search(line):
            return "pro", True
        if _HOME_MODELS.search(line):
            return "home", True
    pro = bool(_PRO_MODELS.search(text))
    home = bool(_HOME_MODELS.search(text))
    if pro and not home:
        return "pro", False
    if home and not pro:
        return "home", False
    if pro and home:
        # A Pro document that name-checks the home line to exclude it is still a Pro doc.
        return ("pro", False) if _PRO_MODELS.search(text[:600]) else ("all", False)
    return "all", False


def _split_sections(markdown: str, doc_title: str) -> list[tuple[str, str, str]]:
    """Yield (h2, h3, body) triples. Text before the first H2 becomes the preamble section,
    keyed by the document title, so the version banner and "Applies to" line stay citable."""
    sections: list[tuple[str, str, list[str]]] = []
    h2, h3 = doc_title, ""
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append((h2, h3, buf.copy()))
        buf.clear()

    for line in markdown.splitlines():
        m = _HEADING.match(line)
        q = _FAQ_QUESTION.match(line)
        if q and not m:
            # FAQ-style documents use bold questions instead of headings; each question is
            # its own citable section ("customer-faq", "What does a factory reset do?").
            flush()
            h2, h3 = q.group(1).strip(), ""
            continue
        if m:
            level, heading = len(m.group(1)), m.group(2).strip()
            if level == 1:
                continue  # the H1 is the document title; the manifest already carries it
            flush()
            if level == 2:
                h2, h3 = heading, ""
            else:
                h3 = heading
            continue
        buf.append(line)
    flush()
    return [(a, b, "\n".join(c).strip()) for a, b, c in sections]


def _windows(body: str) -> list[str]:
    if len(body) <= MAX_CHUNK_CHARS:
        return [body]
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for p in paras:
        if cur and size + len(p) > MAX_CHUNK_CHARS:
            out.append("\n\n".join(cur))
            cur = cur[-OVERLAP_PARAGRAPHS:]
            size = sum(len(x) for x in cur)
        cur.append(p)
        size += len(p)
    if cur:
        out.append("\n\n".join(cur))
    return out


def _chunk_id(connector_id: str, source_id: str, locator: str, subsection: str, part: int, text: str) -> str:
    key = f"{connector_id}\x1f{source_id}\x1f{locator}\x1f{subsection}\x1f{part}\x1f{text}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


def chunk_document(meta: DocumentMeta) -> list[Chunk]:
    raw = meta.path.read_text(encoding="utf-8")
    doc_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    product_line, explicit = classify_product(raw)
    archived = bool(_ARCHIVED.search(raw[:800]))
    chunks: list[Chunk] = []
    for h2, h3, body in _split_sections(raw, meta.title):
        for part, window in enumerate(_windows(body)):
            # The heading path is embedded with the body: a table row like "Flashing amber"
            # means something different under "N1 node LEDs" than under "R5 Pro gateway LEDs".
            heading_path = " > ".join(x for x in (meta.title, h2 if h2 != meta.title else "", h3) if x)
            text = f"{heading_path}\n\n{window}"
            chunks.append(Chunk(
                chunk_id=_chunk_id(meta.connector_id, meta.source_id, h2, h3, part, text),
                source_id=meta.source_id, title=meta.title, version=meta.version,
                effective_date=meta.effective_date, locator=h2, subsection=h3, text=text,
                product_line=product_line, product_explicit=explicit, archived=archived,
                doc_hash=doc_hash, part=part, connector_id=meta.connector_id,
            ))
    return chunks


def load_corpus(corpus_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for meta in load_manifest(corpus_dir):
        chunks.extend(chunk_document(meta))
    return chunks
