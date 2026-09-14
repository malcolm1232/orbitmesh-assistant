"""Hybrid retrieval: dense vectors + BM25, fused by Reciprocal Rank Fusion, then made
product- and recency-aware, then cut by a relevance floor.

The ranking design is lifted from DBSearch.AI's ``query/rerank.py`` (Apache-2.0):
RRF is scale-free, so it combines a cosine score and a sparse keyword score without
normalisation games, and the relevance floor is *relative to the best hit* rather than an
absolute threshold, so it does not need re-tuning per embedder.

What this module adds for this corpus:
  * ``product_line`` awareness - documents that explicitly scope themselves to the other
    product line are excluded at the vector-store level; inferred mismatches are demoted.
  * archive demotion - a chunk from a document that declares itself superseded keeps its
    place only if nothing current answers the question; it is always labelled ARCHIVED.
  * query enrichment - the caller passes the conversation's known facts (device, LED,
    error code) so a terse follow-up like "it's flashing amber now" still retrieves the
    right LED table.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from .corpus import Chunk
from .vectorstore import VectorStore

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be been but by can could do does for from had has have he her his how i in is it "
    "its me my of on or our she that the their them they this to was we were what when where which who why "
    "will with would you your it's im i'm my the please help hi hello orbitmesh set up use using get got "
    "keeps keep still just also any some one two".split()
)

ARCHIVE_PENALTY = 0.35        # multiply fused score for archived chunks
PRODUCT_MISMATCH_PENALTY = 0.5  # inferred (not explicit) product mismatch
PRODUCT_MATCH_BONUS = 1.15


def _terms(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _content_terms(text: str) -> set[str]:
    return set(_terms(text)) - _STOP


@dataclass
class Hit:
    chunk: Chunk
    score: float
    vector_rank: int | None
    lexical_rank: int | None

    @property
    def citation(self) -> dict:
        return {"source_id": self.chunk.source_id, "locator": self.chunk.locator}


def evidence_is_weak(query: str, hits: list["Hit"]) -> bool:
    """No hit shares a single content term with the question: the dense side found the
    nearest neighbours of an off-topic question. The agent tells the model so it can
    abstain instead of forcing an answer out of the wrong section."""
    qterms = _content_terms(query)
    if not hits or not qterms:
        return False
    best = max(len(qterms & set(_terms(h.chunk.text))) for h in hits)
    return best < 2 and best / len(qterms) < 0.5


class Retriever:
    def __init__(self, store: VectorStore, *, candidates: int = 24, top_k: int = 6) -> None:
        self.store = store
        self.candidates = candidates
        self.top_k = top_k
        self._chunks: list[Chunk] = store.all_chunks()
        self._by_id = {c.chunk_id: c for c in self._chunks}
        self._bm25 = BM25Okapi([_terms(c.text) for c in self._chunks]) if self._chunks else None

    @property
    def size(self) -> int:
        return len(self._chunks)

    def chunk(self, chunk_id: str) -> Chunk | None:
        return self._by_id.get(chunk_id)

    def find(self, source_id: str, locator_contains: str = "") -> list[Chunk]:
        """Deterministic lookup by document/section - used by guardrail fallbacks so even a
        canned safety message cites the corpus rather than restating it from code."""
        want = locator_contains.lower()
        matches = [c for c in self._chunks if c.source_id == source_id and want in c.locator.lower()]
        # An exact section name first: "Safety" must not resolve to the document-title preamble
        # "OrbitMesh Warranty, Safety, and Escalation Policy" just because it comes first.
        return sorted(matches, key=lambda c: (c.locator.lower() != want, not c.locator.lower().startswith(want)))

    def retrieve(self, query: str, *, product_line: str | None = None, top_k: int | None = None) -> list[Hit]:
        if not self._chunks:
            return []
        top_k = top_k or self.top_k
        exclude = {"home": "pro", "pro": "home"}.get(product_line or "")

        # Dense candidates.
        qvec = self.store.embedder.embed([query])[0]
        dense = self.store.search(qvec, limit=self.candidates, exclude_product=exclude)
        vec_rank = {c.chunk_id: r for r, (c, _) in enumerate(dense, start=1)}

        # Lexical candidates over the whole corpus (small), same exclusion rule.
        scores = self._bm25.get_scores(_terms(query))  # type: ignore[union-attr]
        lex_order = sorted(range(len(self._chunks)), key=lambda i: scores[i], reverse=True)
        lex_rank: dict[str, int] = {}
        for i in lex_order:
            c = self._chunks[i]
            if scores[i] <= 0:
                break
            if exclude and c.product_line == exclude and c.product_explicit:
                continue
            lex_rank[c.chunk_id] = len(lex_rank) + 1
            if len(lex_rank) >= self.candidates:
                break

        # RRF fusion (k=60, the standard constant).
        k = 60.0
        fused: dict[str, float] = {}
        for cid in set(vec_rank) | set(lex_rank):
            s = 0.0
            if cid in vec_rank:
                s += 1.0 / (k + vec_rank[cid])
            if cid in lex_rank:
                s += 1.0 / (k + lex_rank[cid])
            fused[cid] = s

        # Product and archive adjustments.
        hits: list[Hit] = []
        for cid, s in fused.items():
            c = self._by_id.get(cid)
            if c is None:
                continue
            if c.archived:
                s *= ARCHIVE_PENALTY
            if product_line in ("home", "pro"):
                if c.product_line == product_line:
                    s *= PRODUCT_MATCH_BONUS
                elif c.product_line not in ("all", product_line):
                    s *= PRODUCT_MISMATCH_PENALTY
            hits.append(Hit(chunk=c, score=s, vector_rank=vec_rank.get(cid), lexical_rank=lex_rank.get(cid)))
        hits.sort(key=lambda h: h.score, reverse=True)
        hits = self._relevance_floor(query, hits[: top_k * 2])
        return hits[:top_k]

    @staticmethod
    def _relevance_floor(query: str, hits: list[Hit], rel_lexical: float = 0.5) -> list[Hit]:
        """Drop filler that a fixed top-k would otherwise force into the evidence. A hit stays
        if it shares at least ceil(rel_lexical * best_shared) of the query's content terms
        with the best hit, or if it came from the dense side in the top 3 (a semantic match
        that shares no keywords). Strictly subtractive."""
        if not hits:
            return hits
        qterms = _content_terms(query)
        shared = [len(qterms & set(_terms(h.chunk.text))) for h in hits]
        best = max(shared)
        need = max(1, math.ceil(rel_lexical * best))
        kept = [h for h, sh in zip(hits, shared)
                if sh >= need or (h.vector_rank is not None and h.vector_rank <= 3)]
        return kept or hits[:1]
