"""Deterministic privacy helpers: text normalisation, PII redaction, injection scanning and
verbatim-copy detection. Everything here is regex/heuristic and BEST-EFFORT: redaction is not NER
(it cannot find names), so a human approves every embed_text. These checks reduce accidents; they
are not a substitute for that approval."""

import re
import unicodedata

MAX_TEXT = 4000
_ZERO_WIDTH = dict.fromkeys(
    map(
        ord, "\u200b\u200c\u200d\u2060\ufeff\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
    ),
    None,
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class TextRejectedError(ValueError):
    pass


def normalize_text(text: str, *, max_len: int = MAX_TEXT) -> str:
    """NFKC-normalise, strip invisible/bidi control characters, reject control bytes and oversize."""
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH).strip()
    if _CONTROL.search(text):
        raise TextRejectedError("control characters are not allowed")
    if not text:
        raise TextRejectedError("text is empty")
    if len(text) > max_len:
        raise TextRejectedError(f"text exceeds {max_len} characters")
    return text


# --- PII ---------------------------------------------------------------------------------------------

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_UPI = re.compile(r"\b[\w.-]{2,}@[a-zA-Z]{2,}\b")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DIGITS = re.compile(r"(?<![\w])(?:\+?\d[\d\s().-]{7,}\d)(?![\w])")  # phone/card/account-like runs


def _digit_count(s: str) -> int:
    return sum(c.isdigit() for c in s)


def find_pii(text: str) -> list[str]:
    """Kinds of PII-looking content found (empty list = nothing detected)."""
    kinds: list[str] = []
    if _EMAIL.search(text):
        kinds.append("email")
    if _UPI.search(_EMAIL.sub(" ", text)):
        kinds.append("handle")
    if _URL.search(text):
        kinds.append("url")
    if _IPV4.search(text):
        kinds.append("ip_address")
    if any(_digit_count(m.group()) >= 7 for m in _DIGITS.finditer(text)) or re.search(
        r"\d{7,}", text
    ):
        kinds.append("number_sequence")
    return kinds


def redact(text: str) -> str:
    out = _EMAIL.sub("[EMAIL]", text)
    out = _URL.sub("[URL]", out)
    out = _UPI.sub("[HANDLE]", out)
    out = _IPV4.sub("[IP]", out)
    out = _DIGITS.sub(lambda m: "[NUMBER]" if _digit_count(m.group()) >= 7 else m.group(), out)
    return re.sub(r"\d{7,}", "[NUMBER]", out)


# --- prompt-injection heuristics ------------------------------------------------------------------------

_INJECTION = [
    (
        "override_instructions",
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|earlier|all|any|your|the)\b.{0,30}\b(instruction|prompt|rule|guideline|polic)",
    ),
    (
        "new_instructions",
        r"\b(new|updated|additional)\s+(instruction|rule|directive|system prompt)s?\b",
    ),
    ("role_marker", r"(?im)^\s*(system|assistant|developer|user)\s*[:>]"),
    (
        "role_tag",
        r"<\s*/?\s*(system|assistant|developer|instructions?|im_start|im_end|memory|untrusted[\w-]*)\b|\[/?(INST|SYS)\]|<\|",
    ),
    (
        "persona_switch",
        r"\b(you are now|act as|pretend (to be|you are)|from now on|your new (role|task|goal))\b",
    ),
    (
        "prompt_exfiltration",
        r"\b(reveal|print|show|repeat|leak)\b.{0,30}\b(system prompt|instructions|hidden|secret|api key|password|token)",
    ),
    (
        "tool_or_permission",
        r"\b(call|invoke|execute|run|use)\b.{0,20}\b(tool|function|command|shell|api)\b|\b(grant|give|add)\b.{0,25}\b(permission|access|tool|namespace)\b",
    ),
    (
        "concealment",
        r"\b(do not|don't|never)\b.{0,20}\b(tell|inform|mention|show)\b.{0,20}\b(user|owner|human)\b",
    ),
    (
        "action_demand",
        r"\b(always|must|never|should)\s+(respond|reply|answer|send|transfer|pay|delete|approve|ignore)\b",
    ),
]
_COMPILED = [(name, re.compile(pat, re.I | re.S)) for name, pat in _INJECTION]


def injection_flags(text: str) -> list[str]:
    """Names of injection-like patterns present. Heuristic: a hit forces human review, a miss proves nothing."""
    return [name for name, rx in _COMPILED if rx.search(text)]


# --- verbatim copy detection ------------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text).lower())


def _shingles(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def copies_source(candidate: str, source: str) -> bool:
    """True if candidate substantially reproduces the raw source text (so it would retain it).

    Rejects on: any shared run of 8 words, or >= 30% of the candidate's 4-word runs found in the source.
    Short candidates (< 8 words) are judged on the 4-word rule only. A good paraphrase passes.
    """
    cand, src = _words(candidate), _words(source)
    if len(cand) < 4 or len(src) < 4:
        return " ".join(cand) in " ".join(src) and len(cand) >= 2
    if len(cand) >= 8 and _shingles(cand, 8) & _shingles(src, 8):
        return True
    c4 = _shingles(cand, 4)
    return bool(c4) and len(c4 & _shingles(src, 4)) / len(c4) >= 0.30
