"""Shared fixtures. Everything here runs offline: hashing embedder, embedded Qdrant in a
temp directory, deterministic mock LLM."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("EMBEDDING_PROVIDER", "hash")
os.environ["QDRANT_URL"] = ""

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"


@pytest.fixture(scope="session")
def chunks():
    from orbitmesh.corpus import load_corpus

    return load_corpus(CORPUS)


@pytest.fixture(scope="session")
def store(tmp_path_factory, chunks):
    from orbitmesh.embeddings import HashingEmbedder
    from orbitmesh.vectorstore import VectorStore

    vs = VectorStore(path=tmp_path_factory.mktemp("qdrant"), embedder=HashingEmbedder())
    vs.sync(chunks)
    return vs


@pytest.fixture(scope="session")
def retriever(store):
    from orbitmesh.retrieval import Retriever

    return Retriever(store, candidates=24, top_k=6)


@pytest.fixture
def agent(retriever, tmp_path):
    from orbitmesh.agent import Agent
    from orbitmesh.conversation import SessionStore
    from orbitmesh.llm import MockLLM

    return Agent(retriever, MockLLM(), SessionStore(tmp_path / "sessions"))


class ScriptedLLM:
    """Returns canned drafts in order - for testing what the agent does AROUND the model."""

    provider = "scripted"
    model = "scripted"

    def __init__(self, drafts):
        from orbitmesh.llm import Draft

        self._drafts = [d if not isinstance(d, dict) else Draft(**d) for d in drafts]
        self.prompts: list[list[dict]] = []

    def complete(self, messages, **_):
        self.prompts.append(messages)
        if not self._drafts:
            raise AssertionError("ScriptedLLM ran out of drafts")
        return self._drafts.pop(0)


@pytest.fixture
def scripted(retriever, tmp_path):
    from orbitmesh.agent import Agent
    from orbitmesh.conversation import SessionStore

    def make(drafts):
        llm = ScriptedLLM(drafts)
        return Agent(retriever, llm, SessionStore(tmp_path / "sessions")), llm

    return make
