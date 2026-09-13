"""The HTTP wrapper + web UI, end to end through FastAPI's TestClient (mock LLM, hash embedder)."""
import os

import pytest
from fastapi.testclient import TestClient

from .conftest import ROOT


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("srv")
    os.environ.update({"LLM_PROVIDER": "mock", "EMBEDDING_PROVIDER": "hash", "QDRANT_URL": "",
                       "QDRANT_PATH": str(d / "q"), "SESSION_DIR": str(d / "s"), "CONNECTORS_DIR": str(d / "c"),
                       "INDEX_STATE_PATH": str(d / "index_state.json"), "CORPUS_DIR": str(ROOT / "corpus")})
    from orbitmesh.server import create_app

    with TestClient(create_app()) as c:
        yield c


def test_pages_and_ops(client):
    assert client.get("/").status_code == 200 and "Connectors" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/../pyproject.toml").status_code in (404, 400)
    h = client.get("/health").json()
    assert h["ok"] and h["index_chunks"] == 66 and h["index_current"] is True
    assert "orbitmesh_turns_total" in client.get("/metrics").text


def test_every_element_the_ui_script_looks_up_exists_in_the_page(client):
    """app.js grabs its mount points with $("#id") at start-up; a renamed or removed element in
    app.html throws there and blanks every view. Ids the script creates itself (the inspector's
    p-* fields) are rendered on demand and are excluded."""
    import re

    html = client.get("/").text
    js = client.get("/static/app.js").text
    looked_up = set(re.findall(r'\$\("#([A-Za-z][\w-]*)"\)', js))
    rendered_by_js = set(re.findall(r'id="([A-Za-z][\w-]*)"', js))
    missing = sorted(i for i in looked_up - rendered_by_js if f'id="{i}"' not in html)
    assert looked_up and not missing, missing
    for mount in ("cv-canvas", "cv-world", "cv-edges", "cv-hub", "cv-kinds", "cv-panel", "cv-status"):
        assert f'id="{mount}"' in html


def test_chat_returns_contract_plus_evidence(client):
    j = client.post("/chat", json={"session_id": "web-1", "message": "N1 flashing amber on wireless"}).json()
    assert j["action"] in {"ask", "instruct", "resolved", "escalate"} and isinstance(j["citations"], list)
    assert j["evidence"] and j["evidence"][0]["connector_id"] == "orbitmesh-corpus"
    assert client.post("/chat", json={"session_id": "", "message": "x"}).status_code == 422


def test_connector_lifecycle_through_the_api(client):
    r = client.post("/api/connectors", json={"name": "Field notes", "kind": "upload"})
    assert r.status_code == 201
    cid = r.json()["connector"]["id"]
    md = b"# Field notes\n\n**Document version:** 9.9\n\n## Wobble fix\n\nIf the R1 wobbles, tighten the Zorblat bracket.\n"
    r = client.post(f"/api/connectors/{cid}/upload", files=[("files", ("field-notes.md", md, "text/markdown"))])
    assert r.status_code == 200 and r.json()["added"] == ["field-notes"]
    total = r.json()["sync"]["total"]
    assert total > 66
    # Re-upload: same total, one stale chunk set deleted.
    r = client.post(f"/api/connectors/{cid}/upload", files=[("files", ("field-notes.md", md.replace(b"tighten", b"loosen"), "text/markdown"))])
    assert r.json()["sync"]["total"] == total and r.json()["sync"]["deleted"] >= 1
    # The new document is retrievable through the agent.
    j = client.post("/chat", json={"session_id": "web-2", "message": "R1 wobbles, Zorblat bracket?"}).json()
    assert any(e["connector_id"] == cid for e in j["evidence"])
    # Disable -> gone from the index; the listing says so.
    r = client.post(f"/api/connectors/{cid}/enabled", json={"enabled": False})
    assert r.json()["sync"]["total"] == 66
    listing = client.get("/api/connectors").json()
    assert listing["index_current"] and any(c["id"] == cid and not c["enabled"] for c in listing["connectors"])
    # Bad uploads are 400 with a message; unknown connector is 404.
    assert client.post(f"/api/connectors/{cid}/upload", files=[("files", ("x.png", b"\x89PNG", "image/png"))]).status_code == 400
    assert client.get("/api/connectors/nope").status_code == 404
    assert client.delete("/api/connectors/orbitmesh-corpus").status_code == 400
    r = client.delete(f"/api/connectors/{cid}")
    assert r.status_code == 200 and r.json()["sync"]["total"] == 66


def test_link_connector_with_unreachable_link_reports_the_error(client, monkeypatch):
    import orbitmesh.fetchers as f

    def boom(*a, **k):
        raise f.LinkNotPublic("the link is not publicly accessible (HTTP 403)")

    monkeypatch.setattr(f, "fetch_sharepoint", boom)
    r = client.post("/api/connectors", json={"name": "SP", "kind": "sharepoint", "source": "https://x.sharepoint.com/:t:/s/a/b"})
    assert r.status_code == 201 and "not publicly accessible" in r.json()["error"]
    cid = r.json()["connector"]["id"]
    assert "not publicly accessible" in client.get(f"/api/connectors/{cid}").json()["last_error"]
    client.delete(f"/api/connectors/{cid}")


def test_stats_and_sessions(client):
    s = client.get("/api/stats").json()
    assert s["index_chunks"] == 66 and s["turns"]["total"] >= 1 and "orbitmesh-corpus" in s["connectors"]
    assert set(s["turns"]["by_action"]) == {"ask", "instruct", "resolved", "escalate"}
    ss = client.get("/api/sessions").json()["sessions"]
    assert any(x["session_id"] == "web-1" for x in ss)
    assert all("message" not in x for x in ss)   # no customer text on the dashboard


def test_stats_histograms_survive_labels_and_inf(client):
    s = client.get("/api/stats").json()
    lat = s["turn_latency"]
    assert lat["count"] >= 1 and lat["p95"] is not None and lat["p95"] < float("inf")
    assert all(b["le"] < float("inf") for b in lat["buckets"]) and sum(b["count"] for b in lat["buckets"]) <= lat["count"]
    assert s["llm_latency"]["count"] == 0 and s["llm_latency"]["p50"] is None   # mock LLM: labelled histogram, no samples
