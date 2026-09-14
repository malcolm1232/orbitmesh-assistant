# Evaluation results

Runner: `make eval` (`python -m eval.run_eval [--judge]`). Cases: `eval/cases.jsonl` (38 conversations, 47 turns, 141 deterministic checks). Full per-turn transcripts of every run are written to `eval_results/run-<id>.json`.

## Final run - 2026-09-14, `openai/gpt-4.1-mini`, local `bge-small-en-v1.5`

| category | cases |
|---|---|
| retrieval | 5/5 |
| product-line | 4/4 |
| freshness (archived vs current) | 3/3 |
| memory | 4/4 |
| steps (ordered, least destructive) | 6/6 |
| escalation | 4/4 |
| abstention | 3/3 |
| guardrails-input | 4/4 |
| guardrails-output | 5/5 |
| **total** | **38/38 cases, 141/141 checks** |

- Retrieval over the 25 turns with a declared target: **Recall@8 = 1.00, MRR = 1.00**.
- LLM judge (`openai/gpt-4.1-mini`, 1-5, n=47 turns): **grounded 4.74, helpful 4.43, safe 5.00** (re-run after adding `mem-04`; the previous run, n=45, scored 4.78 / 4.42 / 4.98). Lowest-scoring axis is helpfulness; the judge's notes are mostly "correct, could have asked the more targeted question".
- Wall time under 20 s with the LLM cache warm, about 140 s uncached (250 s with the judge); cost about $0.05 per uncached run.
- No-credentials mode (`LLM_PROVIDER=mock`, real local embedder, model-independent checks only): 14/14 cases, Recall@8 1.00, MRR 1.00. With the hashing embedder (unit-test mode): 12/14 - the two misses are `retrieved_top_any` ranking, expected for a bag-of-hashed-words embedder.

## Observed failures (first live run: 31/37, 125/134 checks)

1. **Product-line coin flip.** "my node is flashing amber, what do I do?" (no hardware stated) was answered with the home N1 procedure (move it closer) although the same LED on an N5 Pro means a firmware download that must *not* be interrupted. Fix: when the product line is unknown and the top evidence spans both lines, an `instruct` draft is rejected and the assistant asks which system the customer has (`agent._product_ambiguous`). Cases `prod-03`, `prod-04`.
2. **Confirmation not recognised.** "Yes, I confirm. I can set the network up again." was not accepted as the factory-reset confirmation because the parser wanted a bare "yes"; the guardrail then blocked the (correct) reset step and the customer was asked again. Fix: affirmative-start + no-hedge parsing (`conversation.customer_confirms`). Case `reset-01`, test `test_confirmed_factory_reset_is_allowed_once`.
3. **Escalation trigger already met.** An N1 with no light, supplied adapter, known-working outlet - the documented "stop and escalate" condition - got one more diagnostic question. Fix: the escalation chunks were outside top-6 (now top-8) and the prompt states that an already-satisfied trigger means escalate now. Case `esc-02`.
4. **Off-topic stalling.** "How do I set up port forwarding?" produced a clarifying question instead of "the documentation does not cover this". Fix: a weak-evidence signal (no chunk shares two content terms with the question) tells the model to abstain. Case `abs-01`.
5. **Guardrail blocked a compliant refusal.** "I cannot help with sideloading firmware…" tripped the undocumented-procedure rule because it named the procedure. Fix: negation-aware allow patterns on every output rule. Test `test_refusing_an_undocumented_procedure_is_allowed`.
6. **Bad eval regexes** (two): the suite failed correct replies that said "do **not** move it closer" and "do **not** share your password". Fixed with negative lookbehinds - and recorded here because a suite that fails good answers pushes prose toward the regex (see DESIGN.md, "one way my evaluation suite could be misleading").

## Found after the final run

7. **Uncited warranty answer mid-conversation.** A Pro customer who first described a rebooting N5 Pro and then asked "will the warranty definitely cover a replacement?" was told the documentation does not specify warranty coverage, with no citation. The question alone retrieves `warranty-safety-policy > Limited warranty` first, but the retrieval query appends the session context ("N5 Pro", "rebooting"), so Pro manuals filled the evidence. Fix: a message about warranty, coverage, an RMA or a replacement pins the Limited warranty section, the same way a safety report pins the Safety section (`agent._WARRANTY`). Case `mem-04` (0/1, 3/7 checks on the previous agent), test `test_a_warranty_question_mid_conversation_is_answered_from_the_warranty_section`.

## Reproducing

```bash
make ingest
make eval                      # deterministic checks
EVAL_ARGS=--judge make eval    # + LLM judge
LLM_PROVIDER=mock make eval    # no credentials
```
