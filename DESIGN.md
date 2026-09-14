# Design note

## Architecture and key trade-offs

One Python package and one turn pipeline (`agent.py`):

```
message -> input guardrails -> memory extraction -> reset gate -> hybrid retrieval
        -> LLM draft (JSON) -> citation validation -> output guardrails -> reply + state
```

The trade-off that shaped everything: **the model proposes, deterministic code disposes.**
The LLM writes the prose and picks an action; code (`guardrails.py`, `conversation.py`, unit-tested against scripted bad drafts) decides whether a reset was confirmed, whether a citation points at evidence actually shown, whether a safety report escalates, and whether a reply asks for a secret.
The cost is rigidity, so a blocked draft is regenerated once with the violated rule named before a corpus-cited fallback replaces it.

- **Product facts stay in the corpus.** The system prompt holds behavioural rules only; even the canned fallbacks look up their sentence in the index at run time.
- **Memory is structured, not a transcript.** Device, LED pattern, error code, firmware, backhaul and the steps offered and tried are extracted by code on every message; the model may add facts but cannot override what code extracted from the same message, or set the flags code owns (safety, reset gate, resolution).
- **A real mock.** `LLM_PROVIDER=mock` applies the same rules to the retrieved evidence, so CI exercises transport, retrieval, memory and guardrails without a paid call.
- **Connectors, one knowledge base.** The supplied corpus is a read-only connector next to upload, Google Drive and SharePoint public-link connectors; enable, disable and re-upload all run through one idempotent `sync_all()`.
- **Model.** `openai/gpt-4.1-mini` through OpenRouter: cheap (about $0.0007 per call), reliable at JSON output, and fast enough for chat; `google/gemini-2.5-flash` and `anthropic/claude-haiku-4.5` were verified as drop-in alternatives.
- **Reuse.** Reciprocal Rank Fusion of dense and lexical ranking, a relevance floor relative to the best hit, and content-hash chunk ids for idempotent re-ingest are adapted from my open-source project DBSearch.AI (Apache-2.0).

## Chunking and embedding

**One chunk per document section.** Each `##`/`###` heading (and each bold FAQ question) becomes a chunk with its heading path prepended (`Troubleshooting Guide > N1 node disconnects intermittently > Wireless N1`).
Sections over 1,800 characters split on paragraph boundaries with one paragraph of overlap, and an over-long paragraph splits by line, then by word, so no chunk exceeds the embedder window; the 11 documents yield 66 chunks.
Fixed windows would cut LED tables and procedures in half, and "flashing amber" means a weak link under `N1 node LEDs` but a firmware download under `N5 Pro node LEDs`: the heading path lets embedding and citation tell them apart.

Each chunk carries `product_line` (explicit from an "Applies to" line, or inferred from model names), `archived`, version and effective date.
Once the customer's hardware is known, retrieval excludes explicitly scoped chunks of the other product line and demotes inferred mismatches; archived chunks are scored ×0.35 and labelled ARCHIVED in the prompt.

**Embeddings: `BAAI/bge-small-en-v1.5`, local ONNX via fastembed.** Zero per-call cost, no credentials in CI, a 30 MB model baked into the image, reproducible vectors.
It is enough because retrieval is hybrid: BM25 is fused with the dense ranking by RRF (k=60), and exact tokens such as `E17`, `3.4.1` and `N5 Pro` carry much of the signal.

## How quality was measured, and what the numbers say

`eval/cases.jsonl` holds 38 scripted conversations (47 turns) in nine categories: retrieval, product-line isolation, freshness, memory, ordered steps and the reset gate, escalation, abstention, input guardrails and output guardrails.
Every turn has deterministic expectations (actions, sources that must or must not be cited or retrieved, top-ranked source, reply regexes, guardrail flags); `eval/run_eval.py` reports pass rates, Recall@k, MRR and an optional LLM judge (1-5: grounded, helpful, safe).

| | final run (`eval/RESULTS.md`) |
|---|---|
| cases / checks | **38/38**, 141/141 |
| retrieval (25 turns) | Recall@8 = 1.00, MRR = 1.00 |
| judge (n=47) | grounded 4.68, helpful 4.45, safe 5.00 |
| no-credentials mode | 14/14 cases on the model-independent subset |
| cost | about $0.05 per eval run; $0.65 of the $10 budget for the whole project |

Retrieval here is effectively solved by section chunking plus hybrid ranking; the real misses were the right document for the wrong product line or a superseded version, hence the explicit `retrieved_none` and `cite_none` checks.
The judge's lowest axis is helpfulness ("correct, but could have asked the more specific question"): conversation design, not grounding.
The first live run scored 31/37; those failures, and the ones found later by driving the deployed site in a browser, are recorded in `eval/RESULTS.md`.

## One observed failure

**"Yes, I confirm. I can set the network up again." did not count as a yes.**
The reset gate is state: the assistant states what a reset erases, and only an explicit confirmation on the next turn opens it.
The first parser accepted only a bare affirmative, so this more explicit answer left the gate pending, the output guardrail blocked the model's correct reset step, and the customer was asked again.
The fix separates "does it start affirmatively?" from "is it hedged?" (`but`, `wait`, a question mark); only a clean affirmative opens the gate (`test_confirmed_factory_reset_is_allowed_once`, eval case `reset-01`).
The mirror image surfaced later: "before a factory reset, please confirm the modem is connected" armed the gate, so a later "yes" could have unlocked the reset; the gate question must now also state what is erased.

## At ~100x corpus size and real customer load

- **Retrieval.** 6,600 chunks still fit one Qdrant node, but the in-process BM25 does not scale: move the lexical side into Qdrant sparse vectors with native hybrid queries, pre-filter by product line server-side, and add a cross-encoder reranker over the top 30.
- **Ingestion.** Re-sync already embeds only new chunks; at 100x it becomes an async job fed by change events, with manifest versions driving "supersedes" explicitly.
- **Load.** Sessions (a Cloud Storage bucket today) move to Redis or Firestore with a TTL so Cloud Run can scale past one instance. The LLM call dominates latency (about 3 s p50, 6 s p95 on the live demo) and cost: 100k turns a day is about $70 of model against a few dollars of Cloud Run. Levers: prompt caching, a smaller model for `ask` turns, caching identical first messages.

## One way my evaluation suite could be misleading

The expectations are regexes written alongside the guardrails, so the suite tests failure modes already imagined: `response_not_regex: "move (it|the node) closer"` catches the product-line confusion it was built for and passes a reply wrong in an unanticipated way.
It cuts the other way too: that regex once failed a correct "do not move it closer", and tuning against such a suite pushes prose toward the regex rather than the customer.
The judge grades against the retrieved evidence, so a retrieval miss can still score "grounded".
The private suite, written by someone else against corpus updates, is the real test.

## AI tools used

- **Claude Code (Anthropic)** did most of the implementation: the package, tests, eval cases and their expectations, the web UI, Terraform and deploy scripts, and drafts of these documents. It also ran the evaluations, reproduced bugs, and verified behaviour end to end in a browser (Claude in Chrome, Playwright) against the local stack and the live Cloud Run deployment.
- **My part:** setting the goals and constraints (reuse what I built in DBSearch.AI, a DBSearch-style connectors canvas, deployment on GCP verified in a browser, a clean-slate redeploy before submission), deciding what to fix and what to defer, and reviewing the results.
- **OpenRouter** serves the chat model and the eval judge (`openai/gpt-4.1-mini`).
- **DBSearch.AI**, my own open-source retrieval project, is the source of the hybrid ranking and idempotent-ingest design.
- The corpus is the supplied one, unmodified.
