"""Embedder guards that do not need the ONNX model."""
from orbitmesh import embeddings


class _FakeModel:
    def __init__(self):
        self.batch_sizes = []

    def embed(self, texts, batch_size=256):
        self.batch_sizes.append(batch_size)
        return [[1.0, 0.0] for _ in texts]


def test_fastembed_embeds_in_small_batches_so_long_chunks_fit_in_memory():
    """Peak memory grows with batch x sequence length. Measured on a 45 KB notes file (30 chunks
    of ~2.5 KB): batch 32 added 568 MB and pushed the 1 GiB Cloud Run instance over its limit
    (every 'add Drive folder' answered 503); batch 2 added 58 MB."""
    e = object.__new__(embeddings.FastEmbedEmbedder)
    e._model = _FakeModel()
    out = e.embed(["x" * 3000] * 10)
    assert len(out) == 10
    assert e._model.batch_sizes and max(e._model.batch_sizes) <= embeddings.EMBED_BATCH_SIZE <= 4


def test_onnxruntime_telemetry_is_disabled_before_the_model_loads(monkeypatch):
    import sys
    import types

    calls = []
    fake_ort = types.SimpleNamespace(disable_telemetry_events=lambda: calls.append("disabled"))

    class _FakeTextEmbedding:
        def __init__(self, *a, **k):
            calls.append("model")

        def embed(self, texts, batch_size=1):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=_FakeTextEmbedding))
    embeddings.FastEmbedEmbedder("any-model", "/tmp/unused")
    assert calls[:2] == ["disabled", "model"]
