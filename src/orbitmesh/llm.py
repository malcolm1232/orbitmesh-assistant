"""LLM providers.

* ``OpenRouterLLM`` - any OpenRouter model through the OpenAI-compatible endpoint, JSON
  mode, with an on-disk response cache (keyed by model + messages) so re-running the eval
  suite or replaying a conversation during development costs nothing.
* ``MockLLM`` - a deterministic, evidence-driven responder for CI and unit tests. It is
  *not* a stub that returns a constant: it reads the retrieved evidence and the session
  state and produces the action the rules would demand, so the transport, guardrails,
  memory and retrieval paths are all exercised without a paid call.

Both return a ``Draft``: the model's proposed reply before the output guardrails run.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .observability import LLM_COST, LLM_ERRORS, LLM_LATENCY, LLM_TOKENS

log = logging.getLogger("orbitmesh.llm")

ACTIONS = ("ask", "instruct", "resolved", "escalate")


@dataclass
class Draft:
    response: str
    action: str
    citations: list = field(default_factory=list)   # evidence indexes (1-based) or {source_id, locator}
    facts: dict = field(default_factory=dict)
    step: str = ""
    raw: str = ""
    usage: dict = field(default_factory=dict)
    cached: bool = False


class LLMError(RuntimeError):
    pass


def _parse_draft(raw: str) -> Draft:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LLMError("model did not return JSON")
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise LLMError("model JSON is not an object")
    action = str(obj.get("action", "")).strip().lower()
    if action not in ACTIONS:
        action = "ask"
    response = str(obj.get("response", "")).strip()
    if not response:
        raise LLMError("model returned an empty response")
    citations = obj.get("citations") or []
    if not isinstance(citations, list):
        citations = []
    facts = obj.get("facts") if isinstance(obj.get("facts"), dict) else {}
    return Draft(response=response, action=action, citations=citations, facts=facts,
                 step=str(obj.get("step", "") or ""), raw=raw)


class OpenRouterLLM:
    provider = "openrouter"

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: float = 60.0,
                 cache_dir: Path | None = None) -> None:
        if not api_key:
            raise LLMError("OPENROUTER_API_KEY is not set. Put it in .env (see .env.example) or run with "
                           "LLM_PROVIDER=mock for the no-credentials mode.")
        from openai import OpenAI  # lazy so the mock path never imports the SDK

        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2,
                             default_headers={"HTTP-Referer": "https://github.com/orbitmesh-assistant",
                                              "X-Title": "OrbitMesh Support Assistant"})
        self.model = model
        self.cache_dir = cache_dir
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, messages: list[dict], temperature: float) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(json.dumps({"m": self.model, "t": temperature, "msgs": messages},
                                        sort_keys=True).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def complete(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 700) -> Draft:
        content, usage, cached = self.raw(messages, temperature=temperature, max_tokens=max_tokens)
        try:
            draft = _parse_draft(content)
        except LLMError:
            LLM_ERRORS.labels(reason="bad_json").inc()
            raise
        draft.usage = usage
        draft.cached = cached
        return draft

    def raw(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 700) -> tuple[str, dict, bool]:
        """One JSON-mode completion: (content, usage, served_from_cache). Used by ``complete``
        and by the eval judge, which has its own JSON shape."""
        path = self._cache_path(messages, temperature)
        if path is not None and path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            return cached["content"], cached.get("usage", {}), True
        t0 = time.perf_counter()
        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=temperature, max_tokens=max_tokens,
                response_format={"type": "json_object"},
                extra_body={"usage": {"include": True}},
            )
        except Exception as exc:  # noqa: BLE001 - every SDK failure is one metric label
            LLM_ERRORS.labels(reason=type(exc).__name__).inc()
            raise LLMError(f"LLM call failed: {type(exc).__name__}: {exc}") from exc
        finally:
            LLM_LATENCY.labels(provider=self.provider).observe(time.perf_counter() - t0)
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = {}
        if resp.usage is not None:
            usage = {"prompt_tokens": resp.usage.prompt_tokens, "completion_tokens": resp.usage.completion_tokens,
                     "cost": float(getattr(resp.usage, "cost", 0.0) or 0.0)}
            LLM_TOKENS.labels(kind="prompt").inc(resp.usage.prompt_tokens or 0)
            LLM_TOKENS.labels(kind="completion").inc(resp.usage.completion_tokens or 0)
            LLM_COST.inc(usage["cost"])
        if path is not None and content:
            path.write_text(json.dumps({"content": content, "usage": usage}), encoding="utf-8")
        return content, usage, False


class MockLLM:
    """Deterministic responder for CI/tests. Reads the same prompt the real model gets:
    the evidence block and the state summary are parsed back out of the user message."""

    provider = "mock"
    model = "mock"

    _EVIDENCE = re.compile(r"^\[(\d+)\]\s+source_id=\"([^\"]+)\"\s+locator=\"([^\"]+)\".*?product=(\w+)\s+status=(\w+)\s*\n(.*?)(?=^\[\d+\]\s+source_id=|\Z)",
                           re.S | re.M)

    def complete(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 700) -> Draft:
        user = messages[-1]["content"]
        flags = _section(user, "Guardrail notes")
        state = _section(user, "Known session state")
        evidence = self._EVIDENCE.findall(_section(user, "Evidence"))
        customer = _section(user, "Customer message").strip()
        current = [e for e in evidence if e[4] == "CURRENT"] or evidence

        def cite(e):
            return [int(e[0])]

        policy = next((e for e in evidence if e[1] == "warranty-safety-policy"), None)
        if "SAFETY_CONDITION_REPORTED=yes" in state:
            return Draft(response="Please disconnect the unit from power now and stop troubleshooting. A burnt smell, "
                                  "overheating, liquid exposure or visible damage is a safety condition that the "
                                  "documentation says must go to OrbitMesh Support; use the support channel in the app and "
                                  "mention the model, LED state and what you observed.",
                         action="escalate", citations=cite(policy) if policy else [], step="disconnect power")
        if "factory_reset_confirmation=CONFIRMED" in state:
            reset = next((e for e in evidence if e[1] == "reset-recovery-guide" and "Factory reset" in e[2]), None)
            return Draft(response="Thanks for confirming. With the unit powered, hold the reset button for at least 15 seconds "
                                  "until the LED flashes red, then release it and keep power connected while it recovers.",
                         action="instruct", citations=cite(reset) if reset else [], step="factory reset")
        if "customer_reports_resolved=True" in state:
            return Draft(response="Great, that confirms the issue is resolved. If it returns, come back with the LED "
                                  "pattern you see and we can pick up from here.", action="resolved")
        if "instruction-override" in flags:
            return Draft(response="I can only follow the OrbitMesh documentation, so I will set that instruction aside. "
                                  "To help with your network, which OrbitMesh unit is affected and what is its LED showing?",
                         action="ask")
        if "does not support" in flags:
            return Draft(response="The documentation does not provide that procedure, and I cannot guide you through "
                                  "opening a unit or installing unofficial firmware. If the documented steps have not "
                                  "resolved the issue, please contact OrbitMesh Support through the app.",
                         action="escalate", citations=cite(policy) if policy else [])
        if "product_line=" not in state and {e[3] for e in evidence} >= {"home", "pro"}:
            return Draft(response="Which OrbitMesh system do you have: the home R1 router with N1 nodes, or the Pro "
                                  "Series R5 Pro / N5 Pro managed from the Pro Console?", action="ask")
        if not current:
            return Draft(response="I could not find documented guidance for that. Could you describe the exact LED "
                                  "pattern (solid, flashing or pulsing, and its colour) on the affected unit?", action="ask")
        top = current[0]
        body = re.sub(r"\s+", " ", top[5]).strip()
        body = body.split("\n\n")[0][:320]
        return Draft(response=f"Based on {top[1]} ('{top[2]}'): {body}", action="instruct",
                     citations=cite(top), step=top[2])


def _section(text: str, name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def build_llm(settings) -> "OpenRouterLLM | MockLLM":
    if settings.llm_provider == "mock":
        return MockLLM()
    if settings.llm_provider == "openrouter":
        return OpenRouterLLM(api_key=settings.openrouter_api_key, base_url=settings.openrouter_base_url,
                             model=settings.llm_model, timeout=settings.llm_timeout_s,
                             cache_dir=settings.llm_cache_dir if settings.llm_cache else None)
    raise LLMError(f"unknown LLM_PROVIDER={settings.llm_provider!r} (expected 'openrouter' or 'mock')")
