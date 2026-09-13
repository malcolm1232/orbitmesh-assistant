# Connectors + web UI design (2026-09-14)

Scope: strictly inside `orbitmesh-assistant`, for the Dextech submission. The CLI and the JSONL
contract are unchanged; the UI is an addition that shows the connector model carried over from
DBSearch.AI.

## Connectors
- One shared knowledge base. Every chunk carries `connector_id`; `source_id` stays the document id
  (manifest id for the corpus, filename slug otherwise) so citations keep their shape.
- Kinds: `corpus` (supplied OrbitMesh corpus, seeded from `corpus/manifest.json`, read-only, always
  present), `upload` (.md files via the UI), `gdrive` (public Drive file link; public folder link
  only with `GOOGLE_API_KEY`), `sharepoint` ("Anyone with the link" file link via `download=1`, or folder link crawled
  anonymously through the FedAuth badge + classic REST, ported from DBSearch.AI).
- Storage: `data/connectors/<id>/connector.json` + `docs/*.md`. Document metadata (title, version,
  effective date, product line, archived) derived from the file exactly like the corpus.
- `sync_all()` chunks every ENABLED connector and runs the existing idempotent vector-store sync;
  disabled connectors lose their chunks on the next sync; re-uploading a revised file replaces its
  chunks. `make ingest`, the UI Sync button and container start all call it. An `index_state.json`
  fingerprint (connector ids, doc shas, enabled flags, embedder) tells the UI whether the index is
  current and lets start-up skip a redundant sync.

## UI (no build step, no CDN)
Sidebar: Ask (multi-turn agent, action chip, citations `connector / document / section`, new
conversation), Connectors (list, enable toggle, sync, delete, add form: upload / gdrive / sharepoint,
documents table), Dashboard (turns by action, latency, guardrail blocks, LLM cost, index size,
per-connector counts, recent conversations; canvas charts).

## API
`GET/POST/DELETE /api/connectors[/{id}]`, `POST /api/connectors/{id}/enabled`,
`POST /api/connectors/{id}/upload` (multipart), `POST /api/connectors/{id}/sync` (re-fetch + sync),
`POST /api/ingest` (sync all), `GET /api/stats`, `GET /api/sessions`. Existing `/chat`, `/health`,
`/metrics` unchanged. Errors: 400 with a plain message (bad link, HTML login page, oversize, not .md).

## Tests
Connector CRUD, upload then re-upload = zero duplicates, disabled connector vanishes from retrieval,
Drive/SharePoint link parsing and fetch with mocked HTTP, TestClient smoke of every page/endpoint.
CI stays credential-free. Full eval must remain 37/37 after the change.

## Plan
1. `connectors.py` (store + document metadata), `fetchers.py` (gdrive/sharepoint), `sync.py`.
2. Thread `connector_id` through `corpus.py`/`vectorstore.py`/`retrieval.py`; CLI ingest -> sync_all.
3. `server.py` API + static UI (`static/app.html|js|css`), `/api/stats` from the Prometheus registry.
4. Tests, eval re-run, Docker (`/data/connectors` volume), redeploy, Chrome verification, docs.
