"""Re-ingestion must never accumulate duplicates and must drop stale chunks."""
import json
import shutil
from pathlib import Path

from orbitmesh.corpus import load_corpus
from orbitmesh.embeddings import HashingEmbedder
from orbitmesh.vectorstore import VectorStore, point_id

from .conftest import CORPUS


def _copy_corpus(tmp_path: Path) -> Path:
    dst = tmp_path / "corpus"
    shutil.copytree(CORPUS, dst)
    return dst


def test_sync_is_idempotent(tmp_path):
    vs = VectorStore(path=tmp_path / "q", embedder=HashingEmbedder())
    chunks = load_corpus(CORPUS)
    first = vs.sync(chunks)
    second = vs.sync(chunks)
    assert first.recreated and not second.recreated
    assert vs.count() == len(chunks) == first.total == second.total
    assert second.deleted == 0


def test_edited_document_replaces_its_chunks_without_duplicates(tmp_path):
    corpus = _copy_corpus(tmp_path)
    vs = VectorStore(path=tmp_path / "q", embedder=HashingEmbedder())
    before = load_corpus(corpus)
    vs.sync(before)
    n = vs.count()

    guide = corpus / "troubleshooting-guide.md"
    guide.write_text(guide.read_text().replace("Wait two minutes.", "Wait five minutes."))
    after = load_corpus(corpus)
    report = vs.sync(after)

    assert vs.count() == n, "an edit must not grow the index"
    old_ids = {c.chunk_id for c in before} - {c.chunk_id for c in after}
    new_ids = {c.chunk_id for c in after} - {c.chunk_id for c in before}
    assert len(old_ids) == 1 and len(new_ids) == 1 and report.deleted == 1
    live = {c.chunk_id for c in vs.all_chunks()}
    assert new_ids <= live and not (old_ids & live)
    assert any("Wait five minutes" in c.text for c in vs.all_chunks())
    assert not any("Wait two minutes" in c.text for c in vs.all_chunks())


def test_document_removed_from_manifest_is_deleted(tmp_path):
    corpus = _copy_corpus(tmp_path)
    vs = VectorStore(path=tmp_path / "q", embedder=HashingEmbedder())
    vs.sync(load_corpus(corpus))
    manifest = json.loads((corpus / "manifest.json").read_text())
    manifest["documents"] = [d for d in manifest["documents"] if d["id"] != "firmware-archive"]
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    report = vs.sync(load_corpus(corpus))
    assert report.deleted > 0
    assert not any(c.source_id == "firmware-archive" for c in vs.all_chunks())


def test_changing_the_embedder_recreates_the_collection(tmp_path):
    chunks = load_corpus(CORPUS)
    VectorStore(path=tmp_path / "q", embedder=HashingEmbedder(dim=128)).sync(chunks)
    vs = VectorStore(path=tmp_path / "q", embedder=HashingEmbedder(dim=256))
    report = vs.sync(chunks)
    assert report.recreated and vs.count() == len(chunks)


def test_point_ids_are_stable():
    assert point_id("abc") == point_id("abc") != point_id("abd")


class _CountingEmbedder(HashingEmbedder):
    def __init__(self):
        super().__init__()
        self.embedded = 0

    def embed(self, texts):
        self.embedded += len(texts)
        return super().embed(texts)


def test_a_resync_embeds_only_new_chunks(tmp_path):
    """Every UI action (toggle, upload, re-fetch) and every container start runs a full sync. Re-embedding
    unchanged chunks made adding a 45 KB Drive folder take ~40 s on Cloud Run and grows with the index."""
    corpus = _copy_corpus(tmp_path)
    emb = _CountingEmbedder()
    vs = VectorStore(path=tmp_path / "q", embedder=emb)
    chunks = load_corpus(corpus)
    assert vs.sync(chunks).written == len(chunks) and emb.embedded == len(chunks)

    emb.embedded = 0
    again = vs.sync(chunks)
    assert again.written == 0 and emb.embedded == 0 and again.deleted == 0 and vs.count() == len(chunks)

    guide = corpus / "troubleshooting-guide.md"
    guide.write_text(guide.read_text().replace("Wait two minutes.", "Wait five minutes."))
    report = vs.sync(load_corpus(corpus))
    assert report.written == 1 and emb.embedded == 1 and report.deleted == 1


def test_metadata_change_without_text_change_still_reaches_the_index(tmp_path):
    """Chunk ids hash the text, not the manifest fields, so skipping the embed must not skip the payload:
    a manifest version/date bump (what freshness ranking reads) has to reach the index on the next sync."""
    corpus = _copy_corpus(tmp_path)
    vs = VectorStore(path=tmp_path / "q", embedder=HashingEmbedder())
    vs.sync(load_corpus(corpus))
    manifest = json.loads((corpus / "manifest.json").read_text())
    target = next(d for d in manifest["documents"] if d["id"] == "firmware-release-notes")
    target["version"], target["effective_date"] = "9.9", "2030-01-01"
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    report = vs.sync(load_corpus(corpus))
    stored = [c for c in vs.all_chunks() if c.source_id == "firmware-release-notes"]
    assert stored and all(c.version == "9.9" and c.effective_date == "2030-01-01" for c in stored)
    assert report.deleted == 0 and vs.count() == len(load_corpus(corpus))


def test_process_exit_is_quiet_after_using_the_embedded_store(tmp_path):
    """The embedded client's __del__ ran during interpreter teardown and printed 'Exception ignored ...
    ImportError: sys.meta_path is None' on every exit - after pytest's summary, after every CLI ingest,
    and in the Cloud Run logs on each shutdown."""
    import subprocess
    import sys

    code = (
        "from pathlib import Path\n"
        "from orbitmesh.embeddings import HashingEmbedder\n"
        "from orbitmesh.vectorstore import VectorStore\n"
        "from orbitmesh.corpus import load_corpus\n"
        f"vs = VectorStore(path=Path({str(tmp_path / 'q')!r}), embedder=HashingEmbedder())\n"
        f"vs.sync(load_corpus(Path({str(CORPUS)!r}))[:5])\n"
        "import orbitmesh.corpus as held\n"
        "held._store_kept_alive_by_a_module = vs\n"   # like the server app holding the store until teardown
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "Exception ignored" not in proc.stderr and "Traceback" not in proc.stderr, proc.stderr
