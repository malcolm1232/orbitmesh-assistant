# Evaluation results

Runner: `make eval` (`python -m eval.run_eval [--judge]`). Cases: `eval/cases.jsonl` (37 conversations, 45 turns, 134 deterministic checks). Full per-turn transcripts of every run are written to `eval_results/run-<id>.json`.

## Final run - 2026-09-13, `openai/gpt-4.1-mini`, local `bge-small-en-v1.5`

| category | cases |
|---|---|
| retrieval | 5/5 |
| product-line | 4/4 |
| freshness (archived vs current) | 3/3 |
| memory | 3/3 |
| steps (ordered, least destructive) | 6/6 |
| escalation | 4/4 |
| abstention | 3/3 |
| guardrails-input | 4/4 |
| guardrails-output | 5/5 |
| **total** | **37/37 cases, 134/134 checks** |

- Retrieval over the 23 turns with a declared target: **Recall@8 = 1.00, MRR = 1.00**.
- LLM judge (`openai/gpt-4.1-mini`, 1-5, n=45 turns): **grounded 4.78, helpful 4.42, safe 4.98** (re-run after the connectors/UI change; the previous run scored 4.76 / 4.40 / 4.98). Lowest-scoring axis is helpfulness; the judge's notes are mostly "correct, could have asked the more targeted question".
- Wall time 19 s with the LLM cache warm (90 s cold); cost about $0.05 per uncached run.
- No-credentials mode (`LLM_PROVIDER=mock`, real local embedder, model-independent checks only): 13/13 cases, Recall@8 1.00, MRR 1.00. With the hashing embedder (unit-test mode): 11/13 - the two misses are `retrieved_top_any` ranking, expected for a bag-of-hashed-words embedder.

## Observed failures (first live run: 31/37, 125/134 checks)

1. **Product-line coin flip.** "my node is flashing amber, what do I do?" (no hardware stated) was answered with the home N1 procedure (move it closer) although the same LED on an N5 Pro means a firmware download that must *not* be interrupted. Fix: when the product line is unknown and the top evidence spans both lines, an `instruct` draft is rejected and the assistant asks which system the customer has (`agent._product_ambiguous`). Cases `prod-03`, `prod-04`.
2. **Confirmation not recognised.** "Yes, I confirm. I can set the network up again." was not accepted as the factory-reset confirmation because the parser wanted a bare "yes"; the guardrail then blocked the (correct) reset step and the customer was asked again. Fix: affirmative-start + no-hedge parsing (`conversation.customer_confirms`). Case `reset-01`, test `test_confirmed_factory_reset_is_allowed_once`.
3. **Escalation trigger already met.** An N1 with no light, supplied adapter, known-working outlet - the documented "stop and escalate" condition - got one more diagnostic question. Fix: the escalation chunks were outside top-6 (now top-8) and the prompt states that an already-satisfied trigger means escalate now. Case `esc-02`.
4. **Off-topic stalling.** "How do I set up port forwarding?" produced a clarifying question instead of "the documentation does not cover this". Fix: a weak-evidence signal (no chunk shares two content terms with the question) tells the model to abstain. Case `abs-01`.
5. **Guardrail blocked a compliant refusal.** "I cannot help with sideloading firmware…" tripped the undocumented-procedure rule because it named the procedure. Fix: negation-aware allow patterns on every output rule. Test `test_refusing_an_undocumented_procedure_is_allowed`.
6. **Bad eval regexes** (two): the suite failed correct replies that said "do **not** move it closer" and "do **not** share your password". Fixed with negative lookbehinds - and recorded here because a suite that fails good answers pushes prose toward the regex (see DESIGN.md, "one way my evaluation suite could be misleading").

## Reproducing

```bash
make ingest
make eval                      # deterministic checks
EVAL_ARGS=--judge make eval    # + LLM judge
LLM_PROVIDER=mock make eval    # no credentials
```
