# OrbitMesh Support Assistant

A command-line troubleshooting chatbot for the fictional OrbitMesh home Wi-Fi system.
It ingests the product documents in [`corpus/`](corpus/) into a vector database, holds a multi-turn conversation, asks focused diagnostic questions, remembers what the customer said and tried, retrieves evidence before giving product-specific guidance, gives one safe step at a time with citations, and knows when to stop and escalate.

Companion documents:

- [`DESIGN.md`](DESIGN.md) - architecture, chunking/embedding choices, how quality was measured and what the numbers say, an observed failure, the 100x plan, how the eval could mislead, AI tools used.
- [`DEPLOYMENT.md`](DEPLOYMENT.md) - GCP deployment (Cloud Run, CI/CD, secrets, networking) - implemented with Terraform in [`infra/terraform/`](infra/terraform/).
- [`OBSERVABILITY.md`](OBSERVABILITY.md) - logging and monitoring: what is logged, metrics, dashboard, alerts.
- [`eval/`](eval/) - the machine-readable evaluation suite and runner; [`eval/RESULTS.md`](eval/RESULTS.md) - the recorded result summary and observed failures.

## Quick start (local, no Docker)

Requirements: Python 3.11+, an OpenRouter key.

```bash
cp .env.example .env            # put OPENROUTER_API_KEY=sk-or-... in .env
make setup                      # venv + dependencies
make ingest                     # index corpus/ (embedded Qdrant under data/qdrant, ~5 s)
make chat                       # interactive conversation
make test                       # 58 automated tests, no network, no key
make eval                       # evaluation suite + summary (uses the LLM; ~$0.05 per run)
```

Stable scripts for automated evaluation:

```bash
./scripts/ingest.sh
./scripts/chat.sh --jsonl       # one JSON object per line in, one per line out
python scripts/check_contract.py
```

Example:

```
$ echo '{"session_id":"case-1","message":"My node keeps disconnecting"}' | ./scripts/chat.sh --jsonl 2>/dev/null
{"response": "Which OrbitMesh system do you have: the home R1 router with N1 nodes, or the Pro Series R5 Pro / N5 Pro ...", "citations": [], "action": "ask", "session_id": "case-1", "turn": 1, "guardrails": {"input": {}, "output": []}}
```

`action` is one of `ask`, `instruct`, `resolved`, `escalate`; `citations` is a list of `{source_id, locator}` where `source_id` is the manifest id and `locator` the document section.
Reusing a `session_id` continues that conversation (also across process restarts - sessions are persisted under `data/sessions/`).
Diagnostics are JSON lines on **stderr**; stdout carries only the protocol.

## Run with Docker Compose

The compose file starts Qdrant and the assistant (ingesting on start-up), and exposes an HTTP wrapper on `:8080` with a small browser page for manual testing.

```bash
cp .env.example .env                       # add OPENROUTER_API_KEY (or set LLM_PROVIDER=mock)
docker compose up -d --build --wait        # qdrant + ingest + app
open http://localhost:8080                 # browser chat page
curl -s localhost:8080/health
curl -s -X POST localhost:8080/chat -H 'content-type: application/json' \
     -d '{"session_id":"demo","message":"My N1 is flashing amber"}'

docker compose run --rm chat               # interactive CLI inside the container
echo '{"session_id":"a","message":"R1 shows E31"}' | docker compose run --rm -T --no-deps chat chat --jsonl
docker compose run --rm ingest             # re-index after editing corpus/
docker compose down -v                     # stop and delete the index volume
```

The image bakes the local embedding model and a pre-built index, so the container works with no network access except to OpenRouter.

## Configuration

All settings are environment variables (see [`.env.example`](.env.example)); the defaults work out of the box.

| variable | default | meaning |
|---|---|---|
| `OPENROUTER_API_KEY` | - | required unless `LLM_PROVIDER=mock` |
| `LLM_MODEL` | `openai/gpt-4.1-mini` | any OpenRouter chat model; `google/gemini-2.5-flash` and `anthropic/claude-haiku-4.5` were also verified |
| `LLM_PROVIDER` | `openrouter` | `mock` = deterministic, evidence-driven responder; no key, no network (used by CI and tests) |
| `EMBEDDING_PROVIDER` | `fastembed` | local ONNX `BAAI/bge-small-en-v1.5` (free, downloaded once to `data/models`); `hash` = deterministic hashing embedder with no download (tests) |
| `QDRANT_URL` | empty | set to use a Qdrant server (compose: `http://qdrant:6333`); empty = embedded Qdrant at `QDRANT_PATH` |
| `LLM_CACHE` | `1` | cache LLM replies on disk so eval re-runs and replays are free |
| `LOG_CONTENT` | `0` | log customer/assistant text (development only) |

### No-credentials mode (used by CI)

```bash
LLM_PROVIDER=mock EMBEDDING_PROVIDER=hash make ingest test eval
```

`mock` is not a stub returning a constant: it reads the retrieved evidence and the session state and applies the same rules (ask which product when the evidence spans both lines, escalate on a safety report, give the reset step only after confirmation, ...), so retrieval, guardrails, memory and the transport are exercised without a paid call.
In mock mode the eval runner scores only the model-independent expectations (actions, retrieval isolation and ranking, guardrail flags).

## How it works

```
customer message
  -> input guardrails      redact volunteered secrets; flag injection and unsafe requests
  -> conversation memory   extract device / LED / error code / firmware / backhaul / tried steps
  -> factory-reset gate    confirmation must be explicit and immediately preceding
  -> hybrid retrieval      dense (bge-small) + BM25 fused by RRF; product-line filter; archived docs demoted
  -> LLM (JSON mode)       response, action, citations (evidence numbers), new facts, step label
  -> citation validation   only evidence that was actually shown can be cited
  -> output guardrails     no secrets requested, no opening hardware, no warranty promises,
                           no undocumented procedures/links, no unconfirmed factory reset;
                           one regeneration with the violation named, then a corpus-cited safe fallback
  -> state update, structured log, metrics
```

Product facts, procedures and safety rules live only in the corpus: the system prompt states behavioural rules, and every canned fallback cites a corpus section it looks up at run time.

Layout:

```
src/orbitmesh/
  corpus.py        manifest + markdown -> section chunks with product/archive metadata
  embeddings.py    fastembed | hash
  vectorstore.py   Qdrant (server or embedded), idempotent sync that deletes stale chunks
  retrieval.py     hybrid retrieval, RRF, product/archive awareness, relevance floor
  conversation.py  session state + deterministic fact extraction + persistence
  guardrails.py    input/output guardrails
  llm.py           OpenRouter client (JSON mode, disk cache) and the mock
  agent.py         the turn pipeline
  cli.py           `orbitmesh ingest|chat [--jsonl]|serve`
  server.py        HTTP wrapper: POST /chat, GET /health, GET /metrics, GET /
  observability.py JSON logging to stderr + Prometheus metrics
eval/              cases.jsonl (37 scripted conversations) + run_eval.py (+ optional LLM judge)
tests/             58 tests: chunking, idempotent re-ingest, retrieval isolation, guardrails, memory, agent, JSONL contract
infra/terraform/   GCP: Cloud Run, Artifact Registry, Secret Manager, uptime check, alerts, dashboard
.github/workflows/ ci.yml (ingest + retrieval + eval checks, no paid credentials), deploy.yml (WIF -> Cloud Run)
```

## Updating the corpus

Edit or add files under `corpus/` and the entry in `corpus/manifest.json`, then `make ingest` (or `docker compose run --rm ingest`).
Chunk ids are content hashes, so unchanged sections are rewritten in place, edited sections replace their old chunk, and documents removed from the manifest lose all their chunks - re-ingestion never accumulates duplicates (`tests/test_vectorstore.py` proves it).
The CI workflow runs the same check on every corpus change: it ingests twice into an ephemeral Qdrant and asserts the second run deletes nothing.

## Cloud deployment

See [`DEPLOYMENT.md`](DEPLOYMENT.md).
The service in this submission was deployed to Cloud Run with `terraform apply` and verified in a browser:

```bash
cd infra/terraform && cp terraform.tfvars.example terraform.tfvars   # edit project/region/image
terraform init && terraform apply
gcloud builds submit --tag "$(terraform output -raw image 2>/dev/null || echo <image>)"   # or cloudbuild.yaml
terraform output service_url
```
