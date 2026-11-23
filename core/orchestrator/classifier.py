"""Deterministic, rule-based intent classifier.

Why rules and not a model: with only the mock model available, a model-based classifier would prove
nothing, and a deterministic one is free, instant, auditable and easy to test. It implements the
`Classifier` protocol, so an LLM classifier can replace it later; whatever implements it is held to the
same `Classification` model (an ENUM of intents), so no classifier can invent an intent, an agent or a
model name.

Limits (honest): keyword rules are brittle. They misclassify unusual phrasing, ignore context from earlier
turns and handle English only. Misses degrade to AMBIGUOUS / CLARIFICATION_NEEDED / GENERAL, which are all
safe outcomes: classification can never cause an action, only choose which governed, bounded workflow answers.
"""

import re
import unicodedata
from dataclasses import dataclass

from core.memory.privacy import injection_flags
from core.orchestrator.types import Classification, Intent


@dataclass(frozen=True)
class Rule:
    code: str
    pattern: re.Pattern[str]
    weight: int


def _r(code: str, pattern: str, weight: int = 2) -> Rule:
    return Rule(code, re.compile(pattern, re.I), weight)


FINANCE = [
    _r(
        "money_words",
        r"\b(budget|expenses?|spending|spent|income|salary|savings?|invest(ment|ing)?s?|tax(es)?|net worth)\b",
        3,
    ),
    _r(
        "banking",
        r"\b(bank|account balance|credit card|debit card|loan|emi|mortgage|upi|neft|imps|statement|overdraft)\b",
        3,
    ),
    _r(
        "transactions",
        r"\b(transactions?|invoice|receipts?|subscriptions?|bills?( due)?|payments? history|cash ?flow)\b",
        2,
    ),
    _r("currency", r"(₹|\binr\b|\brupees?\b|\brs\.?\s?\d|\$\s?\d|\busd\b)", 2),
    _r(
        "finance_verbs",
        r"\b(how much (did|do|have) i (spend|spent|save|owe)|categori[sz]e|reconcile)\b",
        2,
    ),
]
MESSAGING = [
    _r(
        "reply_words",
        r"\b(reply|respond|answer)\b.{0,25}\b(to|him|her|them|message|text|dm)\b|\bwhat (should|do) i (say|reply|write)\b",
        4,
    ),
    _r("draft", r"\b(draft|write|compose|suggest)\b.{0,20}\b(reply|response|message|text|dm)\b", 4),
    _r(
        "platforms",
        r"\b(instagram|telegram|bumble|grindr|dm|direct message|chat|conversation)\b",
        2,
    ),
    _r(
        "message_nouns",
        r"\b(message|text) (from|to|back)\b|\btext back\b|\bthey (said|texted|messaged)\b|\bhe (said|texted)\b|\bshe (said|texted)\b",
        3,
    ),
]
COACHING = [
    _r(
        "planning",
        r"\b(plan|schedule|organi[sz]e|prioriti[sz]e)\b.{0,20}\b(day|week|morning|evening|tasks?|time|work)\b",
        4,
    ),
    _r(
        "goals_habits", r"\b(goals?|habits?|routine|streak|productivity|focus|procrastinat\w*)\b", 3
    ),
    _r(
        "tasks_calendar", r"\b(to-?do|tasks?|reminders?|calendar|deadline|agenda|meeting prep)\b", 3
    ),
    _r(
        "briefings",
        r"\b(morning briefing|evening review|daily (plan|review|briefing)|weekly review)\b",
        4,
    ),
]
GENERAL_META = [
    _r(
        "greeting",
        r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you)\b[\s!.,?]*$",
        3,
    ),
    _r(
        "capabilities",
        r"\b(what can you do|who are you|what are you|how do you work|help me understand how)\b",
        3,
    ),
]

UNSUPPORTED = [
    # Irreversible actions the system will not perform (and has no capability to perform).
    (
        "irreversible_action",
        re.compile(
            r"^\s*(please\s+)?(pay|transfer|wire|remit)\b|\b(can|could|will|would) you (please )?(pay|transfer|wire|send money)\b|\b(pay|transfer|wire) (it |this |that )?(now|immediately|for me)\b|\bsend (me |them |him |her )?(some |the |my )?(money|funds|cash|payment)\b",
            re.I,
        ),
    ),
    (
        "irreversible_action",
        re.compile(
            r"\b(send|post|forward)\b.{0,20}\b(it|this|that|the (message|reply))\b.{0,20}\b(for me|automatically|directly|right now|now)\b|\b(auto-?(send|reply)|reply (automatically|on my behalf))\b|\bsend (it|this|that) to\b",
            re.I,
        ),
    ),
    (
        "irreversible_action",
        re.compile(
            r"\b(delete|erase|wipe)\b.{0,20}\b(all|everything|my (data|memory|account))\b", re.I
        ),
    ),
    # Attempts to change the system's own limits, permissions or routing.
    (
        "governance_bypass",
        re.compile(
            r"\b(kill ?switch|spending limit|budget limit|governance|guardrails?)\b.{0,30}\b(off|disable|bypass|ignore|override|remove)\b|\b(disable|bypass|ignore|override|remove)\b.{0,30}\b(kill ?switch|safety|governance|guardrails?|limits?|permissions?|restrictions?)\b",
            re.I,
        ),
    ),
    (
        "governance_bypass",
        re.compile(
            r"\b(admin|root|developer|god) mode\b|\bsudo\b|\bunlimited (budget|spend\w*)\b|\buse (a |the )?paid model\b|\bgrant (me|yourself|the \w+ agent)\b",
            re.I,
        ),
    ),
    (
        "cross_user",
        re.compile(
            r"\b(another|someone else'?s|other) (user|person)'?s? (data|memory|messages|account)\b",
            re.I,
        ),
    ),
]

SUBTYPES: dict[Intent, list[tuple[str, re.Pattern[str]]]] = {
    Intent.FINANCE: [
        ("statements", re.compile(r"\bstatement\b", re.I)),
        ("budgets", re.compile(r"\bbudget\b", re.I)),
        ("subscriptions", re.compile(r"\bsubscriptions?\b", re.I)),
        ("transactions", re.compile(r"\b(transactions?|spent|spending|expenses?)\b", re.I)),
    ],
    Intent.MESSAGING: [
        ("reply_assist", re.compile(r"\b(reply|respond|draft|say)\b", re.I)),
        ("conversation_summary", re.compile(r"\b(summari[sz]e|summary|catch me up)\b", re.I)),
    ],
    Intent.COACHING: [
        ("briefing", re.compile(r"\b(briefing|review)\b", re.I)),
        ("goals", re.compile(r"\bgoals?\b", re.I)),
        ("habits", re.compile(r"\b(habits?|streak|routine)\b", re.I)),
        ("planning", re.compile(r"\b(plan|schedule|tasks?|to-?do)\b", re.I)),
    ],
}

ROUTING_OVERRIDE = re.compile(
    r"\b(route|send|forward|switch|hand) (me|this|it)?\s*(to|over to)\b.{0,25}\b(agent|assistant)\b|\b(use|talk to|ask) the \w+[_ ]agent\b|\b\w+_agent\b",
    re.I,
)

MIN_SCORE = 3  # a pillar needs at least this much evidence to be chosen
AMBIGUITY_RATIO = 0.6  # second place within 60% of first => ambiguous
MIN_WORDS_FOR_GENERAL = 4


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _score(rules: list[Rule], text: str) -> tuple[int, list[str]]:
    hits = [r for r in rules if r.pattern.search(text)]
    return sum(r.weight for r in hits), [r.code for r in hits]


class RuleBasedClassifier:
    def classify(self, message: str) -> Classification:
        text = _normalise(message)
        flags = sorted(set(injection_flags(text)))
        if ROUTING_OVERRIDE.search(text):
            flags.append("routing_override_attempt")  # informational: it has NO effect on routing

        for code, rx in UNSUPPORTED:
            if rx.search(text):
                return Classification(
                    intent=Intent.UNSUPPORTED, confidence=0.9, reasons=[code], flags=flags
                )

        scored = {
            Intent.FINANCE: _score(FINANCE, text),
            Intent.MESSAGING: _score(MESSAGING, text),
            Intent.COACHING: _score(COACHING, text),
        }
        ranked = sorted(scored.items(), key=lambda kv: kv[1][0], reverse=True)
        (top, (top_score, top_reasons)), (second, (second_score, _)) = ranked[0], ranked[1]

        if top_score >= MIN_SCORE:
            if second_score >= MIN_SCORE and second_score >= AMBIGUITY_RATIO * top_score:
                return Classification(
                    intent=Intent.AMBIGUOUS,
                    confidence=0.4,
                    candidates=[top, second],
                    reasons=["multiple_pillars"],
                    flags=flags,
                )
            subtype = next((name for name, rx in SUBTYPES.get(top, []) if rx.search(text)), None)
            confidence = round(min(0.99, top_score / (top_score + second_score + 1)), 2)
            return Classification(
                intent=top, subtype=subtype, confidence=confidence, reasons=top_reasons, flags=flags
            )

        meta_score, meta_reasons = _score(GENERAL_META, text)
        if meta_score >= 3:
            return Classification(
                intent=Intent.GENERAL, confidence=0.8, reasons=meta_reasons, flags=flags
            )
        if len(text.split()) >= MIN_WORDS_FOR_GENERAL and top_score == 0:
            return Classification(
                intent=Intent.GENERAL, confidence=0.5, reasons=["no_pillar_signal"], flags=flags
            )
        return Classification(
            intent=Intent.CLARIFICATION_NEEDED,
            confidence=0.3,
            reasons=["too_little_signal"],
            flags=flags,
        )
