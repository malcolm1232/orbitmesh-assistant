"""Structured logging (stderr, one JSON object per line) and Prometheus metrics.

Logging policy:
  * stdout is reserved for the JSONL protocol; everything else goes to stderr;
  * customer message text is NOT logged by default (PII); we log lengths, hashes,
    extracted facts, the action, retrieved source ids, latency, tokens and cost.
    ``LOG_CONTENT=1`` turns content logging on for local debugging only;
  * every record carries ``session_id`` and ``turn`` so a case can be reconstructed.
Metrics (``/metrics`` on the HTTP wrapper) cover volume, outcome mix, guardrail activity,
retrieval health, LLM latency/cost and errors - the set OBSERVABILITY.md alerts on.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram

TURNS = Counter("orbitmesh_turns_total", "Turns handled", ["action"])
GUARDRAIL_INPUT = Counter("orbitmesh_guardrail_input_total", "Input guardrail flags", ["kind"])
GUARDRAIL_OUTPUT = Counter("orbitmesh_guardrail_output_total", "Output guardrail outcomes", ["outcome"])
RETRIEVAL_HITS = Histogram("orbitmesh_retrieval_hits", "Evidence chunks per turn", buckets=(0, 1, 2, 3, 4, 6, 8))
RETRIEVAL_EMPTY = Counter("orbitmesh_retrieval_empty_total", "Turns with no evidence")
LLM_LATENCY = Histogram("orbitmesh_llm_latency_seconds", "LLM round-trip", ["provider"],
                        buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32))
LLM_TOKENS = Counter("orbitmesh_llm_tokens_total", "LLM tokens", ["kind"])
LLM_COST = Counter("orbitmesh_llm_cost_usd_total", "LLM spend reported by OpenRouter")
LLM_ERRORS = Counter("orbitmesh_llm_errors_total", "LLM call failures", ["reason"])
TURN_LATENCY = Histogram("orbitmesh_turn_latency_seconds", "End-to-end turn latency",
                         buckets=(0.5, 1, 2, 4, 8, 16, 32))
ERRORS = Counter("orbitmesh_errors_total", "Unhandled turn errors")
INDEX_CHUNKS = Gauge("orbitmesh_index_chunks", "Chunks in the vector index")
SESSIONS_ACTIVE = Gauge("orbitmesh_sessions_cached", "Sessions held in memory")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", *, file: "Path | None" = None) -> None:
    """JSON lines on stderr. With `file`, records at `level` go to that file instead and only
    warnings reach stderr - the interactive CLI uses this so a person at the terminal reads the
    conversation, not the telemetry, while the telemetry is still kept."""
    root = logging.getLogger()
    root.handlers.clear()
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(JsonFormatter())
    root.addHandler(stderr_handler)
    if file is not None:
        file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)
        stderr_handler.setLevel(logging.WARNING)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai", "urllib3", "fastembed", "huggingface_hub",
                  "qdrant_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **data) -> None:
    logger.log(level, event, extra={"data": data})
