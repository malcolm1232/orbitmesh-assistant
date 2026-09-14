"""Embedding providers behind one tiny interface.

* ``fastembed`` (default): BAAI/bge-small-en-v1.5 via ONNX, runs on CPU, ~30 MB, no API
  key and no per-call cost. Quality is sufficient for a 4k-word corpus and it keeps the
  whole ingest/retrieval path free of paid calls (the assignment asks for that mode).
* ``hash``: a deterministic token-hashing embedder (adapted from DBSearch.AI's
  ``HashingEmbedding``, Apache-2.0). No model download at all - used by unit tests and the
  CI "no network" job. Retrieval remains meaningful because the hybrid ranker's lexical
  half carries it.

Vectors are L2-normalised so cosine similarity is a dot product in Qdrant.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_WORD = re.compile(r"[a-z0-9]+")

# ONNX attention memory grows with batch x sequence length, and the runtime keeps its arena at
# the peak. Long chunks (a 45 KB notes file cut into ~2.5 KB pieces) at batch 32 added 568 MB and
# killed the 1 GiB Cloud Run instance; batch 4 keeps the same file around +110 MB. Small corpora
# barely notice the throughput difference on CPU.
EMBED_BATCH_SIZE = 4


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class HashingEmbedder:
    """Bag of hashed unigrams + bigrams. Deterministic across processes and platforms."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.name = f"hash-{dim}"

    @staticmethod
    def _slot(token: str, dim: int) -> int:
        return int(hashlib.md5(token.encode("utf-8")).hexdigest()[:8], 16) % dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            words = _WORD.findall(text.lower())
            for i, w in enumerate(words):
                vec[self._slot(w, self.dim)] += 1.0
                if i + 1 < len(words):
                    vec[self._slot(w + "_" + words[i + 1], self.dim)] += 0.5
            out.append(_normalise(vec))
        return out


class FastEmbedEmbedder:
    def __init__(self, model_name: str, cache_dir: str) -> None:
        import onnxruntime
        from fastembed import TextEmbedding  # lazy: keeps the hash mode import-free

        # onnxruntime's built-in telemetry dispatches an event while the interpreter shuts down; on
        # macOS that once aborted a finished `chat --jsonl` process (SIGABRT in
        # Events::DebugEventSource::DispatchEvent -> recursive_mutex::lock) and raised the
        # "Python quit unexpectedly" dialog during scripts/check_contract.py. No telemetry, no dispatch.
        onnxruntime.disable_telemetry_events()
        self._model = TextEmbedding(model_name, cache_dir=cache_dir)
        self.name = f"fastembed:{model_name}"
        probe = list(self._model.embed(["dimension probe"]))[0]
        self.dim = int(len(probe))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_normalise([float(x) for x in vec]) for vec in self._model.embed(texts, batch_size=EMBED_BATCH_SIZE)]


def build_embedder(provider: str, model_name: str, cache_dir: str) -> Embedder:
    if provider == "hash":
        return HashingEmbedder()
    if provider == "fastembed":
        return FastEmbedEmbedder(model_name, cache_dir)
    raise ValueError(f"unknown EMBEDDING_PROVIDER={provider!r} (expected 'fastembed' or 'hash')")
