# Logging and monitoring

Implemented: structured JSON logs (`src/orbitmesh/observability.py`), a Prometheus `/metrics` endpoint, and in `infra/terraform/monitoring.tf` four log-based metrics, an uptime check, four alert policies and a Cloud Monitoring dashboard.
Described only: the tracing and the eval-in-production loop at the end.

## What is logged

Every turn emits one JSON object to **stderr** (stdout is reserved for the JSONL protocol; on Cloud Run stderr becomes a `jsonPayload` in Cloud Logging).
The interactive `make chat` writes the same records to `data/logs/chat.log` instead and shows only warnings on the terminal, so a person reads the conversation rather than the telemetry:

```json
{"ts":"2026-09-13T15:20:43.240Z","level":"INFO","logger":"orbitmesh.agent","event":"turn",
 "session_id":"live-2","turn":1,"action":"escalate","latency_ms":4952,"model":"openai/gpt-4.1-mini",
 "cached":false,"msg_len":159,"msg_sha":"c6a88c21ec13",
 "facts":{"product_line":"home","device":"R1","firmware":"3.3.6"},
 "guardrails":{"input":{"injection":true,"unsafe_request":true,"secrets_redacted":["password"]},
               "output":["draft 1 blocked: describes an undocumented procedure ..."]},
 "evidence":["firmware-release-notes:Version 3.4.2 — current stable", "..."],
 "citations":["warranty-safety-policy:When to escalate"]}
```

Other events: `ingest.done` (chunk counts, stale deletions, embedder name), `guardrail.output` (WARNING, the violation text and attempt number), `llm.error` (WARNING, exception class, never the prompt), `turn.error` (ERROR with traceback).

### Logging considerations

- **No customer text by default.** Messages contain network names, addresses and, as the eval shows, volunteered passwords. We log the message length and a short SHA-256 so a case can be correlated with a customer's own transcript, plus the *extracted facts* (device, LED, error code), which are what an engineer needs to reproduce a bad answer. `LOG_CONTENT=1` enables full content for local debugging only; the deploy sets it off.
- **Secrets are redacted before anything else runs.** The input guardrail rewrites `password: …`, `sk-…` keys and serial numbers to `[REDACTED-…]` before the text reaches the model, the session file or any log line, so a debug log cannot leak them either.
- **Every record carries `session_id` and `turn`** so one conversation can be reassembled with a single Logs Explorer query: `jsonPayload.session_id="case-1"`.
- **The evidence and citations are logged as `source_id:locator`.** A wrong answer is almost always a retrieval miss or a citation of the wrong product line; having both lists next to the action makes that a one-look diagnosis and feeds the "corpus update broke retrieval" alert idea below.
- **Cost and cache are first-class fields.** `cached` tells you whether the disk cache answered (eval reruns) and OpenRouter's `usage.cost` is exported per call.
- **Levels are meaningful.** INFO = a turn; WARNING = a guardrail block or an LLM error that was handled (the customer still got a safe reply); ERROR = the turn failed. Alerting keys off those levels.
- Retention: 30 days in Cloud Logging is enough for support cases; a BigQuery sink of the `turn` events (without content) is the cheap way to get funnel analytics (ask -> instruct -> resolved rates by symptom) without building a warehouse.

## Metrics

Exposed at `GET /metrics` in Prometheus format (scraped by Managed Service for Prometheus / any Prometheus), and the same signals are derived from the logs on GCP so no scraper is required:

| metric | why it matters |
|---|---|
| `orbitmesh_turns_total{action}` | volume, and the **outcome mix** - a rising `escalate` share or a falling `resolved` share is the earliest sign a prompt, model or corpus change made the bot less useful |
| `orbitmesh_turn_latency_seconds` (histogram) | what the customer feels; p95 is the SLO metric |
| `orbitmesh_llm_latency_seconds{provider}`, `orbitmesh_llm_tokens_total{kind}`, `orbitmesh_llm_cost_usd_total` | provider health and spend; cost per turn is the number to watch after a model swap |
| `orbitmesh_llm_errors_total{reason}` | timeouts, rate limits, auth failures, bad JSON - each a different runbook |
| `orbitmesh_guardrail_input_total{kind}`, `orbitmesh_guardrail_output_total{outcome}` | attack/abuse volume (injection, unsafe requests) and, more importantly, **how often the model drafted something the output guard had to block or fall back on** - a regression detector for prompt and model changes |
| `orbitmesh_retrieval_hits` (histogram), `orbitmesh_retrieval_empty_total` | retrieval health; an empty-evidence rate that jumps after a corpus deploy means the ingest or chunker broke |
| `orbitmesh_errors_total` | turns that raised |
| `orbitmesh_index_chunks`, `orbitmesh_sessions_cached` | sanity gauges (a deploy with the wrong chunk count is caught at a glance) |

Cloud Run adds request count by status class, instance count, CPU/memory and container start latency for free, and those are on the dashboard next to the application metrics.

## Dashboard

`google_monitoring_dashboard.main` (Terraform) shows: turns/min stacked by action, turn latency p50/p95, guardrail output blocks, errors, Cloud Run request status classes, instance count, and the uptime-check pass fraction.
That is the "is it up, is it fast, is it safe, is it useful" view on one screen.

## Alerts (implemented in Terraform)

| alert | condition | why this threshold |
|---|---|---|
| `/health failing` | uptime check fails from 2+ regions for 5 min | the service is down or the index failed to load (`/health` reports `index_chunks`) |
| `5xx / turn errors` | Cloud Run 5xx > 5% over 10 min **or** > 3 `turn.error`/`llm.error` logs in 5 min | a handful of LLM errors is normal weather; a sustained rate means the provider or the key is broken |
| `p95 turn latency > 15 s` | over 10 min | the JSONL contract checker allows 60 s but a customer will not wait 15; this fires before that |
| `guardrail blocks spiking` | > 10 blocked drafts in 15 min | either an attack (injection volume) or a prompt/model regression producing unsafe drafts - both need a human the same day |

Described only:

- **Budget alert** on the billing account plus a `orbitmesh_llm_cost_usd_total` rate alert (spend per hour above N× the 7-day baseline) - the single most likely way this service hurts you is a retry loop against a paid API.
- **Empty-evidence rate** alert (`orbitmesh_retrieval_empty_total / turns > 10%` for 30 min) scoped to the first hour after a corpus deploy.
- **Escalation-rate drift**: `escalate` share of turns above its 7-day baseline + 2σ for 6 h - the "the bot got worse" alert that no infrastructure metric will show.
- **Tracing**: OpenTelemetry spans per turn (`guard_input`, `retrieve`, `llm`, `guard_output`) exported to Cloud Trace, so a slow p95 can be attributed to the vector store vs. the model without reading logs.
- **Eval in production**: replay the private eval suite against the staging service nightly and publish the pass rate as a metric; alert on any drop. The eval runner already writes `eval_results/summary.json` in a shape that can be pushed as a gauge.
