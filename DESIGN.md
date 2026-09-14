# Design note

## Architecture and key trade-offs

One Python package, one turn pipeline (`agent.py`), everything else a replaceable module behind a small interface:

```
message -> input guardrails -> memory extraction -> reset gate -> hybrid retrieval
        -> LLM draft (JSON) -> citation validation -> output guardrails -> reply + state
```

The trade-off that shaped everything: **the model proposes, deterministic code disposes.**
The LLM writes the prose and picks an action, but it does not get to decide whether a factory reset was confirmed, whether a citation points at evidence it was actually shown, whether a safety report escalates, or whether a password is worth asking for.
Those are regexes and state machines in `guardrails.py` and `conversation.py`, and they are unit-tested against the model's worst drafts (`tests/test_agent.py` feeds scripted bad drafts through the pipeline).
The cost is some rigidity - a rule can block a compliant draft, which happened during development (see "Observed failure") - and the mitigation is a regeneration step that tells the model exactly which rule it broke before falling back to a canned reply that itself cites the corpus.

Other decisions:

- **Product facts stay in the corpus.** The system prompt contains behavioural rules only. Even the canned fallbacks (reset confirmation, escalation) look up their sentence in the indexed corpus at run time rather than restating it.
- **Session memory is structured, not a transcript.** Device, LED pattern, error code, firmware, backhaul, steps offered and steps tried are extracted deterministically on every customer message; the model may add facts but cannot overwrite an extracted one. The prompt then shows the model an authoritative state summary plus a short history, so a terse "yes" or "it's flashing amber now" still lands on the right procedure.
- **Sessions persist to disk** so the JSONL adapter continues a conversation whether the evaluator runs one process or many.
- **Qdrant in both modes.** Server mode for docker-compose/CI, embedded mode (no daemon) for tests and the zero-dependency quick start - same class, one environment variable.
- **A real mock.** `LLM_PROVIDER=mock` reads the evidence and state and applies the rules, so CI proves the transport, retrieval, memory and guardrails end to end without a paid call; only prose quality needs the real model.
- **Connectors, one knowledge base.** The web UI adds DBSearch.AI's connector model: the supplied corpus is a read-only connector, and upload / Google Drive / SharePoint connectors (public links - including anonymous SharePoint folder crawling - no OAuth) feed the same index. Chunks carry `connector_id`, citations keep the assignment's `{source_id, locator}` shape, and enable/disable/re-upload all go through the one idempotent `sync_all()` - so "corpus updates without duplicate chunks" is something a reviewer can click through, not only read about.
- **Reuse.** The retrieval design (Reciprocal Rank Fusion of dense + lexical, a relevance floor relative to the best hit, content-hash ids with delete-before-upsert for idempotent re-ingest) is adapted from my open-source project DBSearch.AI (Apache-2.0), where it was tuned against a 120-document real corpus. This repository is standalone; nothing there was modified.

## Chunking and embedding

**Chunking is by document section.** Each `##`/`###` heading (and each bold question in the FAQ) becomes one chunk, with the heading path prepended to the text (`Troubleshooting Guide > N1 node disconnects intermittently > Wireless N1`).
Sections over 1,800 characters split on paragraph boundaries with one paragraph of overlap; in this corpus none do, so the 11 documents yield 66 chunks.
Why sections and not fixed windows: the value of this corpus is in ordered procedures and LED tables.
A 500-token window that cuts the N1 LED table in half produces a chunk in which "Flashing amber" has no meaning, and a citation `{"source_id": "led-reference", "locator": "N1 node LEDs"}` is exactly what a support agent would write.
The heading prefix matters for the same reason: "Flashing amber" under `N5 Pro node LEDs` means firmware download, under `N1 node LEDs` weak signal, and the embedding must see that.

Each chunk carries metadata derived from the document itself: `product_line` (`pro`/`home`) and whether it was **explicit** (an "Applies to:" line) or inferred from model names; `archived` (the document declares itself superseded); version and effective date from the manifest.
Retrieval hard-excludes explicitly-scoped chunks of the other product line once the customer's hardware is known, soft-demotes inferred mismatches, multiplies archived chunks by 0.35 and labels them ARCHIVED in the prompt.

**Embeddings: `BAAI/bge-small-en-v1.5` locally (384-d, ONNX via fastembed).** At 4k words the corpus fits in memory a hundred times over; the deciding factors were zero per-call cost, no credentials in CI, a 30 MB model that bakes into the Docker image, and reproducible vectors.
Quality is adequate because retrieval is hybrid: BM25 over the same chunks is fused with the dense ranking by RRF (k=60), and on this corpus the exact tokens - `E17`, `3.4.1`, `N5 Pro`, `band steering` - carry most of the signal.
The `hash` embedder (unigram+bigram hashing) exists so unit tests need no download at all.

## How quality was measured, and what the numbers say

`eval/cases.jsonl` holds 38 scripted conversations (47 turns) across nine categories: retrieval, product-line isolation, freshness (archived vs. current), memory, ordered steps, the factory-reset gate, escalation, abstention, input guardrails, output guardrails.
Every turn has deterministic expectations - allowed actions, sources that must/must not be cited or retrieved, the top-ranked source, regexes the reply must and must not match, guardrail flags that must fire.
`eval/run_eval.py` runs them through the real pipeline, reports pass rates per category, Recall@k and MRR over the turns that declare a retrieval target, and optionally scores each reply with an LLM judge (1-5 on grounded / helpful / safe against the retrieved evidence).

Final run (`openai/gpt-4.1-mini`, local bge-small, `eval/RESULTS.md`):

| | |
|---|---|
| cases / checks | **38/38**, 141/141 |
| retrieval (25 turns) | Recall@8 = 1.00, MRR = 1.00 |
| judge (n=47) | grounded 4.74, helpful 4.43, safe 5.00 |
| cost | ~$0.0007 per LLM call, ~$0.05 per full eval run; $0.16 spent over the whole project including development |

What the numbers say, honestly: retrieval on this corpus is effectively solved by section chunking plus hybrid ranking - the interesting misses were never "wrong document" but "right document, wrong product line" or "right document, superseded version", which is why the eval has explicit `retrieved_none` and `cite_none` checks.
The judge's lowest axis is *helpful* (4.43), and reading the notes it is mostly "correct but could have asked the more specific question" - conversation design, not grounding.
The first live run scored 31/37; the six failures are the useful part and are listed in `eval/RESULTS.md`.
The no-credentials CI mode scores 14/14 on the model-independent subset with the real embedder.

## One observed failure

**"Yes, I confirm. I can set the network up again." did not count as a yes.**
The factory-reset gate requires an explicit, immediately-preceding confirmation, tracked as state (`reset_confirm_pending -> reset_confirmed`).
The first confirmation parser matched only a bare affirmative (`^yes\b…$`), so a natural, *more* explicit answer left the gate pending; the output guardrail then blocked the model's (correct) reset instruction twice and the fallback asked for confirmation a second time.
Deterministic code was being safe in the least useful way.
The fix separates two questions - "does this start affirmatively?" and "is it hedged?" (`but`, `not`, `wait`, a question mark) - and only a clean affirmative flips the gate; hedged answers stay pending and the model is told to clarify.
Added as `test_confirmed_factory_reset_is_allowed_once` and eval case `reset-01`.
The general lesson applied elsewhere: every guardrail got an *allow* pattern for negations, because the same session showed the "undocumented procedure" rule blocking a compliant refusal that named the thing it refused ("I cannot help with sideloading…").

## At ~100x corpus size and real customer load

- **Retrieval.** 6,600 chunks still fit one Qdrant node, but the in-process BM25 (built from a full scroll at start-up) does not: move the lexical side into Qdrant's sparse vectors and use its native hybrid query, add payload indexes on `product_line`/`archived`, and pre-filter by product line server-side. Add a cross-encoder reranker over the top 30 - cheap at this scale and the biggest quality lever once the corpus has near-duplicate sections across product generations.
- **Ingestion.** Content-hash ids already make re-ingest idempotent and a re-sync embeds only chunks the index has not seen (unchanged chunks only get their metadata refreshed); at 100x it becomes an async job (queue + workers) fed by change events instead of a full manifest scan, with the manifest's versions driving "supersedes" relationships explicitly instead of the archived-banner heuristic.
- **Serving.** Cloud Run scales horizontally, but session state has to leave the instance disk: Redis/Firestore keyed by `session_id` with a TTL. The LLM call dominates latency (p50 ~2 s, p95 ~5 s) and cost (~$0.0007/turn). Levers in order: prompt caching of the system prompt, a smaller model for the *ask* turns with escalation to the larger one only when an *instruct* needs drafting, and semantic caching of the (state, question) -> reply pairs for the long tail of identical first messages.
- **Evaluation and safety.** The private-suite style regression run moves into CI against a staging deployment; guardrail hit rates and the escalate/resolve mix become alerts (see `OBSERVABILITY.md`).
- **Cost at load.** 100k turns/day is ~$70/day of model, ~$5/day of Cloud Run; the embedding model stays local and free.

## One way my evaluation suite could be misleading

The cases were written by the person who wrote the guardrails, and the expectations are regexes.
Both bias the suite toward the failure modes I already imagined: a check like `response_not_regex: "move (it|the node) closer"` catches the Pro/home confusion I designed for, and passes a reply that is wrong in a way I did not enumerate.
Concretely, `prod-01` passed for a run in which the reply was *correct* only after I fixed my own regex (it had matched "Do **not** move it closer"), which is the mirror image: the suite can also fail good answers, and a developer tuning against it may tune the prose toward the regex rather than toward the customer.
The LLM judge partly compensates, but it grades against the *retrieved* evidence, so a retrieval miss that leaves the model with the wrong section can be scored "grounded".
The private suite, written by someone else against corpus updates I have not seen, is the real test; the mitigation in this repo is that the deterministic checks are cheap to extend and every failure is recorded with the full reply, so new cases come from observed conversations rather than imagination.

## AI tools used

- **Claude Code (Anthropic)** as a pair programmer throughout: scaffolding the package, writing the first versions of the tests, the eval runner, the Terraform and the drafts of these documents, and driving the browser and cloud verification. I directed the design (the deterministic-gate architecture, section chunking, the mock strategy, the eval categories), reviewed every module, and did the failure analysis on the eval runs; the fixes described above came out of reading the failing transcripts.
- **OpenRouter** for the chat model (`openai/gpt-4.1-mini`; `google/gemini-2.5-flash` and `anthropic/claude-haiku-4.5` verified as drop-in alternatives) and for the eval judge.
- **DBSearch.AI**, my own open-source retrieval project, for the hybrid ranking and idempotent-ingest design.
- No AI-generated content is in the corpus or the eval expectations.
