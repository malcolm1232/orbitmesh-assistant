"""Input and output guardrails. Deterministic, regex-based, unit-tested.

Input side (customer message -> flags):
  * prompt-injection / instruction-override attempts are flagged; the message is still
    answered as a support request, but the model is told the text is untrusted and the
    injected instruction is not followed;
  * secrets the customer volunteers (Wi-Fi/account passwords, API keys, full serial
    numbers) are detected and redacted before the text reaches the model, the logs, or
    the session file;
  * requests for undocumented/unsafe procedures (unofficial firmware, opening the case)
    are flagged so the model is steered to refuse from the corpus.

Output side (draft reply -> verdict):
  * never asks for a password / API key / full serial number;
  * never tells the customer to open or repair hardware;
  * never promises warranty coverage;
  * never links to or describes a download/sideload/rollback procedure;
  * never issues the factory-reset action unless the session holds an explicit,
    immediately-preceding confirmation;
  * never fabricates a case number or delivery promise.
A violated draft is regenerated once with the violation named; a second violation falls
back to a safe reply that still cites the corpus (see agent.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- input ---------------------------------------------------------------------------------
_INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|the\s+|your\s+|any\s+)?(previous|prior|above|earlier|system)\s+(instructions?|prompts?|rules?)"
    r"|\byou are now\b|\bact as (?:a|an|the)\b.*\b(admin|developer|root|engineer|unrestricted)\b"
    r"|\bsystem prompt\b|\bdeveloper mode\b|\bjailbreak\b|\bDAN\b"
    r"|\bnew instructions?:\s|\boverride\b.*\b(safety|guardrails?|rules?)\b"
    r"|\bpretend (?:you|to be)\b|\brespond only with\b|\bprint your (?:instructions|prompt)\b"
    r"|\bthe (?:manual|documentation|docs) (?:is|are) wrong\b|\bofficial support (?:told|said)\b",
    re.IGNORECASE)
_UNSAFE_REQUEST = re.compile(
    r"\b(open(?:ing)? (?:up )?(?:the|my) (?:unit|device|router|node|case|casing|adapter)|unscrew|disassembl|take (?:it|the \w+) apart|"
    r"internal (?:battery|repair)|solder|bypass(?:ing)? (?:the )?(?:fuse|protection)|"
    r"sideload|unofficial (?:firmware|image)|custom firmware|downgrade|roll ?back(?: the)? firmware|"
    r"download (?:the )?(?:old|older|previous|archived) (?:firmware|image|build)|firmware (?:from|off) (?:a )?(?:forum|reddit|link))\b",
    re.IGNORECASE)
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("api_key", re.compile(r"\b(sk-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b")),
    ("password", re.compile(r"(?i)\b(?:wi-?fi|network|account|admin|router|my)\s+(?:password|passphrase|pass)\s*(?:is|:|=)\s*\S+")),
    ("password", re.compile(r"(?i)\b(?:password|passphrase|pwd)\s*(?::|=|is)\s*\S{4,}")),
    ("serial", re.compile(r"(?i)\b(?:serial(?: number)?|s/?n)\s*(?:is|:|=|#)?\s*([A-Z0-9-]{8,})\b")),
]
_PASSWORD_MENTION = re.compile(r"(?i)\b(password|passphrase)\b")


@dataclass
class InputVerdict:
    text: str                    # the (possibly redacted) message to use downstream
    injection: bool = False
    unsafe_request: bool = False
    secrets: list = field(default_factory=list)   # kinds redacted, e.g. ["password"]
    notes: list = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.injection or self.unsafe_request or bool(self.secrets)


def screen_input(message: str) -> InputVerdict:
    text = message
    verdict = InputVerdict(text=text)
    for kind, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            verdict.secrets.append(kind)
            text = pattern.sub(lambda m: _redact(m.group(0), kind), text)
    verdict.secrets = sorted(set(verdict.secrets))
    verdict.text = text
    if _INJECTION.search(message):
        verdict.injection = True
        verdict.notes.append("customer text contains instruction-override language; treated as untrusted content")
    if _UNSAFE_REQUEST.search(message):
        verdict.unsafe_request = True
        verdict.notes.append("customer asks for a procedure the documentation does not support (repair/opening/unofficial firmware)")
    return verdict


def _redact(matched: str, kind: str) -> str:
    if kind == "serial":
        # Keep the leading label so the sentence still reads; the value goes.
        return re.sub(r"[A-Z0-9-]{8,}$", "[REDACTED-SERIAL]", matched)
    if kind == "api_key":
        return "[REDACTED-KEY]"
    head = re.split(r"(?:is|:|=)", matched, maxsplit=1)[0]
    return f"{head.strip()}: [REDACTED-PASSWORD]"


# --- output --------------------------------------------------------------------------------
_ASKS_SECRET = re.compile(
    r"(?i)\b(what(?:'s| is)|share|send|tell me|provide|enter|give me|type|confirm|paste|let me know)\b[^.?!\n]{0,80}?"
    r"\b(password|passphrase|api key|full serial|serial number|payment|card number|cvv)\b")
_ASKS_SECRET_ALLOW = re.compile(r"(?i)\b(last|final)\s+(four|4)\b|\bdo not (?:share|send|need)\b|\bnever\b|\bno need to\b|\bwithout (?:sharing|your)\b")
_OPEN_HARDWARE = re.compile(
    r"(?i)\b(open(?:ing)?|unscrew(?:ing)?|pry(?:ing)?|disassembl\w*|take apart|remove the (?:cover|casing|shell|back|lid|screws?)|"
    r"crack open|look inside|solder|reseat the (?:battery|board|ram|chip))\b[^.?!\n]{0,60}?"
    r"\b(unit|device|router|node|gateway|adapter|case|casing|enclosure|R1|N1|R5|N5|power supply|charger)\b")
_OPEN_HARDWARE_ALLOW = re.compile(r"(?i)\b(do not|don'?t|never|must not|should not|shouldn'?t|no need to|without)\b[^.?!\n]{0,40}\b(open|unscrew|disassembl|take apart)")
_WARRANTY_PROMISE = re.compile(
    r"(?i)\b(is|are|will be|would be|should be|it'?s|this is|that'?s|definitely|certainly|fully)\s+(covered|replaced|approved|guaranteed|eligible)\b"
    r"|\bcovered (?:by|under) (?:the |your )?warranty\b|\bwarranty (?:will|covers|claim is approved)\b|\bfree replacement\b|\bwe(?:'ll| will) replace\b")
_WARRANTY_ALLOW = re.compile(r"(?i)\b(cannot|can'?t|not able to|unable to|do not|don'?t|no(?:t)? (?:promise|confirm|guarantee)|assessment|"
                             r"support (?:will )?determine|may be|might be|subject to|does not establish|cannot confirm)\b")
_LINK = re.compile(r"(?i)https?://\S+|\bwww\.\S+")
_UNDOCUMENTED = re.compile(
    r"(?i)\bsideload\w*|\bdownload (?:the |an? )?(?:firmware|image|file|\.bin)|\bflash (?:the|an?) (?:firmware|image)|"
    r"\broll(?:ing)? ?back\b|\bdowngrade\b|\bUSB recovery\b|\bTFTP\b|\bopen a (?:case|ticket) number\b|"
    r"\bcase (?:number|id|#)\s*[:#]?\s*[A-Z0-9-]{4,}|\breplacement (?:has been|is) (?:authori[sz]ed|approved|shipped)\b|\bwill (?:arrive|be delivered) (?:on|by|within)\b")
# A refusal that names the thing it refuses ("I cannot help you sideload...") is compliant.
_UNDOCUMENTED_ALLOW = re.compile(
    r"(?i)\b(cannot|can'?t|not able|unable|do not|don'?t|never|must not|not supported|unsupported|not (?:a )?(?:customer|documented)|"
    r"no (?:customer|documented|supported)|is not (?:available|accessible|customer-accessible)|won'?t|will not|refuse|not provide|not (?:be )?possible|"
    r"there is no|isn'?t)\b")
_FACTORY_RESET_STEP = re.compile(
    r"(?i)\b(hold|press(?: and hold)?|keep pressing)\b[^.?!\n]{0,60}?\b(15|fifteen|twenty|20)\b[^.?!\n]{0,40}?\bseconds?\b"
    r"|\bfactory[- ]reset\b[^.?!\n]{0,80}?\b(now|hold|press)\b|\b(perform|do|start|carry out|go ahead with) (?:the |a )?factory[- ]reset\b")
_FACTORY_RESET_MENTION = re.compile(r"(?i)\bfactory[- ]reset\b")
_RESET_CONFIRM_ASK = re.compile(r"(?i)\b(confirm|proceed|go ahead|are you (?:sure|happy)|do you want|would you like|shall i|ready to)\b")
_RESET_ERASES = re.compile(r"(?i)\b(eras\w*|wip\w*|lose|lost|delete\w*)\b")


@dataclass
class OutputVerdict:
    ok: bool
    violations: list = field(default_factory=list)

    def describe(self) -> str:
        return "; ".join(self.violations)


def screen_output(text: str, *, action: str, reset_confirmed: bool) -> OutputVerdict:
    v: list[str] = []
    for sentence in re.split(r"(?<=[.?!])\s+|\n", text):
        if _ASKS_SECRET.search(sentence) and not _ASKS_SECRET_ALLOW.search(sentence):
            v.append("asks for a password/API key/full serial or payment detail")
        if _OPEN_HARDWARE.search(sentence) and not _OPEN_HARDWARE_ALLOW.search(sentence):
            v.append("instructs the customer to open or repair hardware")
        if _WARRANTY_PROMISE.search(sentence) and not _WARRANTY_ALLOW.search(sentence):
            v.append("promises or asserts warranty coverage")
        if _UNDOCUMENTED.search(sentence) and not _UNDOCUMENTED_ALLOW.search(sentence):
            v.append("describes an undocumented procedure or fabricates a case/delivery commitment")
    if _LINK.search(text):
        v.append("contains a link (the documentation never provides download links)")
    if _FACTORY_RESET_STEP.search(text) and not reset_confirmed:
        v.append("gives the factory-reset action without an explicit, immediately-preceding customer confirmation")
    return OutputVerdict(ok=not v, violations=sorted(set(v)))


def is_reset_confirmation_request(text: str) -> bool:
    """Does the draft ask the customer to confirm a factory reset (the pre-reset gate)? The gate question states
    what a reset erases; "before a factory reset, confirm the modem is connected" asks about something else, and
    treating it as the gate would let the customer's next "yes" unlock the reset."""
    return bool(_FACTORY_RESET_MENTION.search(text) and _RESET_CONFIRM_ASK.search(text) and _RESET_ERASES.search(text))


def mentions_factory_reset(text: str) -> bool:
    return bool(_FACTORY_RESET_MENTION.search(text))


def gives_factory_reset_step(text: str) -> bool:
    """Does the draft contain the reset ACTION itself (hold for 15 s, ...)?"""
    return bool(_FACTORY_RESET_STEP.search(text))
