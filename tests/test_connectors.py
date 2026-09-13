"""Connectors: one shared knowledge base, no duplicates on re-upload, disabled = gone."""
from pathlib import Path

import pytest

from orbitmesh.connectors import CORPUS_CONNECTOR_ID, ConnectorError, ConnectorStore
from orbitmesh.corpus import describe_markdown
from orbitmesh.embeddings import HashingEmbedder
from orbitmesh.retrieval import Retriever
from orbitmesh.sync import needs_sync, sync_all
from orbitmesh.vectorstore import VectorStore

from .conftest import CORPUS

REVISED = """# OrbitMesh Firmware Release Notes

**Document version:** 3.5.0
**Published:** 2026-09-01

## Version 3.5.0 — current stable

Released 2026-09-01 for R1 and N1.

- Adds the Zorblat mesh mode for very large homes.
- Fixes an N1 LED regression from 3.4.2.
"""


@pytest.fixture
def env(tmp_path):
    connectors = ConnectorStore(tmp_path / "connectors", CORPUS)
    store = VectorStore(path=tmp_path / "q", embedder=HashingEmbedder())
    state = tmp_path / "index_state.json"
    return connectors, store, state


def test_corpus_connector_is_seeded_and_read_only(env):
    connectors, _, _ = env
    cs = connectors.list()
    assert cs[0].id == CORPUS_CONNECTOR_ID and cs[0].kind == "corpus" and cs[0].read_only
    assert len(cs[0].documents) == 11
    with pytest.raises(ConnectorError):
        connectors.put_document(CORPUS_CONNECTOR_ID, "x.md", b"# x\n\ntext")
    with pytest.raises(ConnectorError):
        connectors.delete(CORPUS_CONNECTOR_ID)


def test_describe_markdown_reads_the_corpus_conventions():
    assert describe_markdown(REVISED, "fallback") == ("OrbitMesh Firmware Release Notes", "3.5.0", "2026-09-01")
    assert describe_markdown("no headers here", "fallback") == ("fallback", "", "")


def test_upload_connector_feeds_the_shared_index(env):
    connectors, store, state = env
    sync_all(connectors, store, state)
    base = store.count()
    c = connectors.create("Field updates", "upload")
    doc = connectors.put_document(c.id, "field-notes.md", REVISED.encode())
    assert doc.doc_id == "field-notes" and doc.version == "3.5.0"
    report = sync_all(connectors, store, state)
    assert report.total > base and report.connectors[c.id]["chunks"] > 0
    r = Retriever(store)
    hits = r.retrieve("Zorblat mesh mode", product_line="home")
    assert hits and hits[0].chunk.connector_id == c.id and hits[0].chunk.source_id == "field-notes"


def test_reupload_replaces_without_duplicates(env):
    connectors, store, state = env
    c = connectors.create("Uploads", "upload")
    connectors.put_document(c.id, "notes.md", REVISED.encode())
    sync_all(connectors, store, state)
    n = store.count()
    connectors.put_document(c.id, "notes.md", REVISED.replace("Zorblat", "Quexal").encode())
    report = sync_all(connectors, store, state)
    assert store.count() == n and report.deleted >= 1
    texts = [ch.text for ch in store.all_chunks()]
    assert any("Quexal" in t for t in texts) and not any("Zorblat" in t for t in texts)
    assert len(connectors.get(c.id).documents) == 1


def test_disabled_connector_vanishes_and_comes_back(env):
    connectors, store, state = env
    c = connectors.create("Uploads", "upload")
    connectors.put_document(c.id, "notes.md", REVISED.encode())
    sync_all(connectors, store, state)
    connectors.set_enabled(c.id, False)
    assert needs_sync(connectors, store, state)
    sync_all(connectors, store, state)
    assert not any(ch.connector_id == c.id for ch in store.all_chunks())
    assert not needs_sync(connectors, store, state)
    connectors.set_enabled(c.id, True)
    sync_all(connectors, store, state)
    assert any(ch.connector_id == c.id for ch in store.all_chunks())


def test_corpus_connector_can_be_disabled(env):
    connectors, store, state = env
    connectors.set_enabled(CORPUS_CONNECTOR_ID, False)
    assert connectors.get(CORPUS_CONNECTOR_ID).enabled is False
    sync_all(connectors, store, state)
    assert store.count() == 0
    connectors.set_enabled(CORPUS_CONNECTOR_ID, True)
    sync_all(connectors, store, state)
    assert store.count() == 66


def test_validation(env):
    connectors, _, _ = env
    with pytest.raises(ConnectorError):
        connectors.create("", "upload")
    with pytest.raises(ConnectorError):
        connectors.create("x", "corpus")
    with pytest.raises(ConnectorError):
        connectors.create("Drive", "gdrive", "")
    c = connectors.create("Uploads", "upload")
    with pytest.raises(ConnectorError):
        connectors.put_document(c.id, "image.png", b"\x89PNG")
    with pytest.raises(ConnectorError):
        connectors.put_document(c.id, "empty.md", b"   ")
    connectors.delete(c.id)
    with pytest.raises(ConnectorError):
        connectors.get(c.id)


def test_connector_ids_are_unique_slugs(env):
    connectors, _, _ = env
    a = connectors.create("Pro docs", "upload")
    b = connectors.create("Pro docs", "upload")
    assert a.id == "pro-docs" and b.id == "pro-docs-2"
