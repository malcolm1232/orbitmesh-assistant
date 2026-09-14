"""Per-session conversation state.

The assistant has to *remember*: which hardware, which LED pattern, which error code, what
the customer has already tried, and whether it is mid-way through a factory-reset
confirmation. The LLM is not trusted to carry that in free text; it lives here, and the
deterministic extractors below update it on every customer message regardless of what the
model does. The model may add facts (``facts`` in its JSON reply) but never overwrite one
the extractors found in the same turn.

State is persisted as one JSON file per session so the JSONL transport can continue a
session across process invocations (the private suite may run one process per case or
many cases per process - both work).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

MAX_HISTORY = 12

_PRO = re.compile(r"\b(R5|N5)\s*-?\s*Pro\b|\bPro\s+Series\b|\bPro\s+Console\b", re.IGNORECASE)
_HOME = re.compile(r"\bR1\b|\bN1\b", re.IGNORECASE)
_DEVICE = re.compile(r"\b(R5\s*Pro|N5\s*Pro|R1|N1)\b", re.IGNORECASE)
_LED = re.compile(
    r"\b(solid|steady|flashing|blinking|pulsing|breathing)\s+(white|amber|orange|blue|red)\b"
    r"|\b(white|amber|orange|blue|red)\b\s+(?:light|led)?\s*(?:is|and)?\s*(solid|steady|flashing|blinking|pulsing)\b",
    re.IGNORECASE)
_NO_LIGHT = re.compile(r"\b(no light|no led|not lit|unlit|no lights|dark|won'?t (?:light|power|turn) (?:up|on))\b", re.IGNORECASE)
_ERROR = re.compile(r"\bE\s?(\d{2})\b", re.IGNORECASE)
_FIRMWARE = re.compile(r"\b(3\.\d\.\d)\b")
_WIRED = re.compile(r"\b(ethernet|wired|cable[d]?\s+(?:to|into)|backhaul cable)\b", re.IGNORECASE)
_WIRELESS = re.compile(r"\b(wireless(?:ly)?|wi-?fi backhaul|over wi-?fi|no cable)\b", re.IGNORECASE)
_SAFETY = re.compile(r"\b(smoke|smoking|burnt|burning smell|smell(?:s|ing)? (?:hot|burnt|of burning)|very hot|too hot|"
                     r"overheat(?:s|ing|ed)?|melted|liquid|water|spill(?:ed)?|wet|cracked|crack|damaged|sparks?)\b", re.IGNORECASE)
_YES = re.compile(r"^\s*(yes|yep|yeah|ok(?:ay)?|sure|go ahead|proceed|confirm(?:ed)?|do it|i confirm|i'?m sure|"
                  r"i understand|please proceed|fine|affirmative|let'?s do it|reset it)\b", re.IGNORECASE)
_NO = re.compile(r"^\s*(no|nope|not yet|don'?t|cancel|stop|wait|hold on|i'?d rather not|never ?mind|actually)\b", re.IGNORECASE)
_HEDGE = re.compile(r"\b(but|not|don'?t|wait|cancel|unless|before that|first|hold on|what (?:will|would|about)|\?)", re.IGNORECASE)
_RESOLVED = re.compile(r"\b(that (?:fixed|solved|worked)|it'?s (?:working|fixed|back|online|solid white)( now)?|"
                       r"working now|fixed now|all good|problem solved|solved it|that did it|back online)\b", re.IGNORECASE)
_TRIED = re.compile(r"\b(already|i'?ve (?:tried|done|restarted|rebooted|moved|checked)|tried that|did that|"
                    r"(?:restarted|rebooted|reset|moved|checked|reseated|replaced)\s+(?:it|the|both|my))\b", re.IGNORECASE)


@dataclass
class Turn:
    role: str
    content: str
    action: str = ""


@dataclass
class SessionState:
    session_id: str
    created_at: float = field(default_factory=time.time)
    turns: int = 0
    facts: dict = field(default_factory=dict)       # product_line, device, led, error_code, firmware, backhaul, ...
    steps_offered: list = field(default_factory=list)   # instructions we have given, in order
    steps_tried: list = field(default_factory=list)     # what the customer reports having done
    history: list = field(default_factory=list)         # last MAX_HISTORY Turn dicts
    reset_confirm_pending: bool = False
    reset_confirmed: bool = False
    safety_condition: bool = False
    resolved: bool = False
    escalated: bool = False
    flags: dict = field(default_factory=dict)          # last-turn guardrail notes

    # --- extraction -------------------------------------------------------------------
    def observe_customer(self, message: str, *, keep_wording: bool = True) -> dict:
        """Update facts from a customer message. Returns the facts set this turn."""
        found: dict = {}
        if _PRO.search(message):
            found["product_line"] = "pro"
        elif _HOME.search(message):
            found["product_line"] = "home"
        m = _DEVICE.search(message)
        if m:
            found["device"] = re.sub(r"\s+", " ", m.group(1).upper()).replace("PRO", "Pro")
        m = _LED.search(message)
        if m:
            motion = (m.group(1) or m.group(4) or "").lower()
            colour = (m.group(2) or m.group(3) or "").lower()
            motion = {"steady": "solid", "blinking": "flashing", "breathing": "pulsing"}.get(motion, motion)
            colour = {"orange": "amber"}.get(colour, colour)
            found["led"] = f"{motion} {colour}".strip()
        elif _NO_LIGHT.search(message):
            found["led"] = "no light"
        m = _ERROR.search(message)
        if m:
            found["error_code"] = f"E{m.group(1)}"
        m = _FIRMWARE.search(message)
        if m:
            found["firmware"] = m.group(1)
        if _WIRED.search(message):
            found["backhaul"] = "ethernet"
        elif _WIRELESS.search(message):
            found["backhaul"] = "wireless"
        if _SAFETY.search(message):
            self.safety_condition = True
            found["safety_condition"] = True
        if _TRIED.search(message):
            # The customer's own sentence is replayed to the model on later turns; a message flagged as
            # an injection keeps its structured facts but not its wording.
            self.steps_tried.append(message.strip()[:200] if keep_wording else "(a step reported in a withheld message)")
        if _RESOLVED.search(message):
            found["customer_reports_resolved"] = True
        self.facts.update(found)
        return found

    def customer_confirms(self, message: str) -> bool | None:
        """True / False / None(unclear) - only meaningful while reset_confirm_pending."""
        if _NO.search(message):
            return False
        if _YES.search(message):
            # "Yes, I confirm. I can set it up again." is a yes; "yes but what will I lose?" is not.
            return None if _HEDGE.search(message) else True
        return None

    def merge_model_facts(self, facts: dict, protected: dict) -> None:
        for key, value in (facts or {}).items():
            if key in protected or not isinstance(key, str):
                continue
            if isinstance(value, (str, int, float, bool)) and value not in ("", None):
                self.facts[key] = value
            elif isinstance(value, list) and key == "tried":
                for item in value:
                    if isinstance(item, str) and item and item not in self.steps_tried:
                        self.steps_tried.append(item[:200])

    def remember(self, role: str, content: str, action: str = "") -> None:
        self.history.append(asdict(Turn(role=role, content=content, action=action)))
        self.history = self.history[-MAX_HISTORY:]

    # --- summary for prompts ------------------------------------------------------------
    def summary(self) -> str:
        parts = []
        for key in ("product_line", "device", "led", "error_code", "firmware", "backhaul", "symptom",
                    "clients_affected", "connection_type"):
            if key in self.facts:
                parts.append(f"{key}={self.facts[key]}")
        for key, value in self.facts.items():
            if key not in ("product_line", "device", "led", "error_code", "firmware", "backhaul", "symptom",
                           "clients_affected", "connection_type", "safety_condition"):
                parts.append(f"{key}={value}")
        if self.safety_condition:
            parts.append("SAFETY_CONDITION_REPORTED=yes")
        if self.reset_confirm_pending:
            parts.append("factory_reset_confirmation=PENDING")
        if self.reset_confirmed:
            parts.append("factory_reset_confirmation=CONFIRMED")
        return "; ".join(parts) if parts else "(nothing established yet)"

    def retrieval_context(self) -> str:
        bits = []
        for key in ("device", "product_line", "led", "error_code", "firmware", "backhaul", "symptom"):
            if key in self.facts:
                bits.append(str(self.facts[key]))
        return " ".join(bits)

    # --- persistence ----------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SessionState":
        return SessionState(**{k: v for k, v in d.items() if k in SessionState.__dataclass_fields__})


class SessionStore:
    def __init__(self, directory: Path | None) -> None:
        self.directory = directory
        self._cache: dict[str, SessionState] = {}
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path | None:
        if self.directory is None:
            return None
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:120] or "session"
        return self.directory / f"{safe}.json"

    def get(self, session_id: str) -> SessionState:
        if session_id in self._cache:
            return self._cache[session_id]
        path = self._path(session_id)
        if path is not None and path.exists():
            state = SessionState.from_dict(json.loads(path.read_text(encoding="utf-8")))
        else:
            state = SessionState(session_id=session_id)
        self._cache[session_id] = state
        return state

    def save(self, state: SessionState) -> None:
        self._cache[state.session_id] = state
        path = self._path(state.session_id)
        if path is not None:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)

    def reset(self, session_id: str) -> None:
        self._cache.pop(session_id, None)
        path = self._path(session_id)
        if path is not None and path.exists():
            path.unlink()
