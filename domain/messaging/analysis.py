"""Deterministic conversation analysis and reply drafting. Lexicon/rule based, no model.

Everything returned is a SUGGESTION for the user: drafts are never sent (there is no send capability),
they never state facts that are not in the conversation, and they never commit the user to a time,
payment or promise (acceptance drafts leave the specifics to the user)."""

import re
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

_WORD = re.compile(r"[a-z']{3,}")
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "your",
        "with",
        "that",
        "this",
        "have",
        "from",
        "they",
        "will",
        "would",
        "there",
        "their",
        "what",
        "about",
        "when",
        "which",
        "just",
        "like",
        "been",
        "were",
        "has",
        "had",
        "can",
        "could",
        "should",
        "its",
        "it's",
        "i'm",
        "you're",
        "don't",
        "can't",
        "was",
        "she",
        "him",
        "her",
        "his",
        "our",
        "out",
        "all",
        "get",
        "got",
        "how",
        "why",
        "who",
        "yes",
        "yeah",
        "okay",
        "ok",
        "lol",
        "hmm",
    ]
)
_POS = frozenset(
    [
        "great",
        "good",
        "love",
        "happy",
        "glad",
        "awesome",
        "thanks",
        "thank",
        "nice",
        "amazing",
        "wonderful",
        "excited",
        "congrats",
        "proud",
        "sweet",
        "haha",
    ]
)
_NEG = frozenset(
    [
        "sad",
        "upset",
        "angry",
        "annoyed",
        "tired",
        "stressed",
        "anxious",
        "worried",
        "hurt",
        "bad",
        "terrible",
        "awful",
        "sorry",
        "hate",
        "disappointed",
        "lonely",
    ]
)
_URGENT = re.compile(r"\b(urgent|asap|immediately|right now|emergency|quick(ly)?)\b|!{2,}", re.I)
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
_FORMAL = re.compile(r"\b(sir|madam|regards|kindly|dear|sincerely)\b", re.I)

# (intent, pattern) - first match wins, so order is part of the contract.
_INTENTS: list[tuple[str, re.Pattern[str]]] = [
    (
        "emotional_support",
        re.compile(
            r"\b(sad|upset|stressed|anxious|worried|hurt|bad day|feeling down|lonely|cry(ing)?|depressed|scared)\b",
            re.I,
        ),
    ),
    ("apology", re.compile(r"\b(sorry|apolog\w+|my bad|forgive me)\b", re.I)),
    (
        "congratulations",
        re.compile(
            r"\b(got the job|promoted|passed|engaged|got married|new baby|big news|accepted)\b",
            re.I,
        ),
    ),
    ("thanks", re.compile(r"\b(thank(s| you)|thx|appreciate it)\b", re.I)),
    (
        "invitation",
        re.compile(
            r"\b(let'?s|want to|wanna|join us|come over|dinner|lunch|coffee|party|hang ?out|movie|free (on|this|tonight|tomorrow))\b",
            re.I,
        ),
    ),
    (
        "scheduling",
        re.compile(
            r"\b(tomorrow|tonight|this (week|weekend|evening)|next (week|month)|monday|tuesday|wednesday|thursday|friday|saturday|sunday|at \d{1,2}(:\d{2})?\s?(am|pm)?|what time|reschedule)\b",
            re.I,
        ),
    ),
    (
        "request",
        re.compile(
            r"\b(can you|could you|would you|will you|please|need you to|help me|favou?r)\b", re.I
        ),
    ),
    ("farewell", re.compile(r"\b(bye|good ?night|ttyl|talk later|see you|take care|gtg)\b", re.I)),
    (
        "greeting",
        re.compile(r"^\s*(hi|hello|hey|hiya|good (morning|afternoon|evening)|yo)\b", re.I),
    ),
    (
        "question",
        re.compile(
            r"\?|^\s*(what|when|where|who|why|how|are you|do you|did you|is it|have you)\b", re.I
        ),
    ),
]


@dataclass(frozen=True)
class Msg:
    sender: str
    sent_at: datetime
    text: str


def classify_intent(text: str) -> str:
    return next((name for name, rx in _INTENTS if rx.search(text)), "statement")


def analyse(msgs: list[Msg], now: datetime, has_real_timestamps: bool = True) -> dict[str, object]:
    if not msgs:
        return {"message_count": 0}
    msgs = sorted(msgs, key=lambda m: m.sent_at)
    mine = [m for m in msgs if m.sender == "me"]
    theirs = [m for m in msgs if m.sender == "them"]
    last = msgs[-1]
    last_them = theirs[-1] if theirs else None
    needs_reply = last.sender == "them"
    out: dict[str, object] = {
        "message_count": len(msgs),
        "from_me": len(mine),
        "from_them": len(theirs),
        "first_message_at": msgs[0].sent_at,
        "last_message_at": last.sent_at,
        "last_sender": last.sender,
        "needs_reply": needs_reply,
        "waiting_hours": round((now - last.sent_at).total_seconds() / 3600, 1)
        if needs_reply and has_real_timestamps
        else None,
        "last_message_intent": classify_intent(last_them.text)
        if last_them and needs_reply
        else None,
    }
    if has_real_timestamps:
        gaps_me: list[float] = []
        gaps_them: list[float] = []
        for a, b in zip(msgs, msgs[1:], strict=False):
            gap = (b.sent_at - a.sent_at).total_seconds() / 60
            if a.sender != b.sender and gap <= 7 * 24 * 60:
                (gaps_me if b.sender == "me" else gaps_them).append(gap)
        out["median_my_response_minutes"] = (
            round(statistics.median(gaps_me), 1) if gaps_me else None
        )
        out["median_their_response_minutes"] = (
            round(statistics.median(gaps_them), 1) if gaps_them else None
        )
    words = Counter(w for m in theirs for w in _WORD.findall(m.text.lower()) if w not in _STOP)
    out["topics"] = [w for w, _ in words.most_common(5)]
    blob = " ".join(m.text for m in theirs[-10:]).lower()
    toks = set(_WORD.findall(blob))
    pos, neg = len(toks & _POS), len(toks & _NEG)
    out["their_tone"] = {
        "sentiment": "positive" if pos > neg else "negative" if neg > pos else "neutral",
        "urgent": bool(_URGENT.search(blob)),
        "formal": bool(_FORMAL.search(blob)),
        "uses_emoji": bool(_EMOJI.search(blob)),
        "brief": bool(theirs[-10:])
        and statistics.mean(len(m.text.split()) for m in theirs[-10:]) <= 4,
    }
    out["my_style"] = {
        "avg_words": round(statistics.mean(len(m.text.split()) for m in mine), 1) if mine else None,
        "uses_emoji": bool(mine)
        and sum(bool(_EMOJI.search(m.text)) for m in mine) / len(mine) >= 0.2,
    }
    return out


# ------------------------------------------------------------------------------------------ drafts
# intent -> option -> tone -> text. No names, times, amounts or promises are ever invented.
_DRAFTS: dict[str, dict[str, dict[str, str]]] = {
    "greeting": {
        "reply": {
            "casual": "Hey! How's it going?",
            "warm": "Hi! So good to hear from you. How are you?",
            "formal": "Hello, I hope you're well.",
            "brief": "Hey!",
        }
    },
    "question": {
        "answer_later": {
            "casual": "Good question - let me think and get back to you.",
            "warm": "Thanks for asking! Let me think about it and get back to you properly.",
            "formal": "Thank you for your question. I will look into it and respond shortly.",
            "brief": "Let me check and get back to you.",
        },
        "ask_detail": {
            "casual": "Can you tell me a bit more about what you mean?",
            "warm": "I'd love to help - could you share a little more detail?",
            "formal": "Could you please provide some additional detail?",
            "brief": "More detail?",
        },
    },
    "invitation": {
        "accept": {
            "casual": "Yes, I'd love to! Count me in.",
            "warm": "That sounds lovely - I'd really like that. Count me in!",
            "formal": "Thank you for the invitation. I would be glad to attend.",
            "brief": "Sounds good, I'm in.",
        },
        "decline": {
            "casual": "Thanks for thinking of me, but I can't make it this time.",
            "warm": "Thank you so much for inviting me - I can't make it this time, but I'd love to catch up soon.",
            "formal": "Thank you for the invitation. Unfortunately I am unable to attend.",
            "brief": "Can't make it, sorry. Thanks!",
        },
        "defer": {
            "casual": "Sounds fun! Let me check my schedule and get back to you.",
            "warm": "That sounds lovely! Let me check my schedule and I'll let you know soon.",
            "formal": "Thank you for the invitation. Let me check my availability and confirm shortly.",
            "brief": "Let me check and confirm.",
        },
    },
    "scheduling": {
        "defer": {
            "casual": "Let me check my schedule and confirm.",
            "warm": "Thanks for suggesting that - let me check my schedule and get right back to you.",
            "formal": "Let me confirm my availability and respond shortly.",
            "brief": "Let me check and confirm.",
        },
        "ask_options": {
            "casual": "Does another time work too?",
            "warm": "Would another time work for you as well? I'd like to find something that suits us both.",
            "formal": "Would an alternative time be possible?",
            "brief": "Another time possible?",
        },
    },
    "request": {
        "agree_check": {
            "casual": "Sure - let me see what I can do and get back to you.",
            "warm": "Of course, I'd be happy to help. Let me see what I can do and get back to you.",
            "formal": "Certainly. Let me look into it and revert.",
            "brief": "Sure, let me check.",
        },
        "decline": {
            "casual": "I'm sorry, I can't help with that right now.",
            "warm": "I'm really sorry - I can't help with that right now, but I hope it works out.",
            "formal": "I am sorry, I am unable to assist with this at present.",
            "brief": "Sorry, can't right now.",
        },
    },
    "thanks": {
        "reply": {
            "casual": "Anytime!",
            "warm": "You're so welcome - happy to help!",
            "formal": "You are most welcome.",
            "brief": "Anytime!",
        }
    },
    "apology": {
        "reply": {
            "casual": "No worries, thanks for saying that.",
            "warm": "Thank you for telling me - I appreciate it, and it's okay.",
            "formal": "Thank you for your apology. I appreciate it.",
            "brief": "It's okay, thanks.",
        }
    },
    "emotional_support": {
        "support": {
            "casual": "I'm really sorry you're going through that. I'm here if you want to talk.",
            "warm": "I'm so sorry you're dealing with this. I'm here for you - tell me whatever you need to, or we can just talk.",
            "formal": "I am sorry to hear this. Please let me know if there is anything I can do.",
            "brief": "I'm here for you. Want to talk?",
        }
    },
    "congratulations": {
        "reply": {
            "casual": "That's amazing news - congratulations!",
            "warm": "Congratulations! I'm so happy for you - you deserve this!",
            "formal": "Congratulations on this achievement.",
            "brief": "Congrats!",
        }
    },
    "farewell": {
        "reply": {
            "casual": "Talk soon!",
            "warm": "Take care - talk soon!",
            "formal": "Goodbye, take care.",
            "brief": "Bye!",
        }
    },
    "statement": {
        "acknowledge": {
            "casual": "Got it, thanks for letting me know.",
            "warm": "Thanks for sharing that with me!",
            "formal": "Understood, thank you for informing me.",
            "brief": "Got it.",
        }
    },
}
_CAUTION = {
    "emotional_support": "personal_topic",
    "apology": "relationship_sensitive",
    "request": "commitment",
    "invitation": "commitment",
    "scheduling": "commitment",
}
_EMOJI_ADD = {"greeting": " 🙂", "thanks": " 🙂", "congratulations": " 🎉"}


def suggest_replies(
    analysis: dict[str, object], tone_pref: str, relationship: str = "other"
) -> dict[str, object]:
    intent = str(analysis.get("last_message_intent") or "statement")
    their: dict[str, object] = analysis.get("their_tone") or {}  # type: ignore[assignment]
    style: dict[str, object] = analysis.get("my_style") or {}  # type: ignore[assignment]
    tone = tone_pref if tone_pref in ("casual", "warm", "formal", "brief") else "casual"
    if relationship in ("colleague",) and tone == "casual":
        tone = "formal" if their.get("formal") else tone
    if their.get("formal") and tone == "casual":
        tone = "formal"
    avg = style.get("avg_words")
    if isinstance(avg, int | float) and avg <= 4 and tone == "casual":
        tone = "brief"
    options = _DRAFTS.get(intent, _DRAFTS["statement"])
    use_emoji = bool(style.get("uses_emoji")) and tone in ("casual", "warm")
    drafts = []
    for option, by_tone in options.items():
        text = by_tone.get(tone) or by_tone["casual"]
        if use_emoji and intent in _EMOJI_ADD:
            text += _EMOJI_ADD[intent]
        drafts.append({"option": option, "tone": tone, "text": text})
    return {
        "intent": intent,
        "tone": tone,
        "drafts": drafts,
        "caution": _CAUTION.get(intent),
        "needs_reply": bool(analysis.get("needs_reply")),
        "draft_only": True,
        "send_supported": False,
        "note": "These are drafts for you to edit and send yourself. Nothing is sent for you.",
    }


def conversation_summary(analysis: dict[str, object]) -> str:
    """Neutral, content-free-as-possible summary suitable for a (user-approved) memory: counts and topic words,
    never message text."""
    topics = ", ".join(analysis.get("topics") or []) or "none"  # type: ignore[arg-type]
    return (
        f"{analysis.get('message_count', 0)} messages ({analysis.get('from_me', 0)} yours, {analysis.get('from_them', 0)} theirs); "
        f"recent topics: {topics}; last message intent: {analysis.get('last_message_intent') or 'n/a'}."
    )


__all__ = [
    "Msg",
    "analyse",
    "classify_intent",
    "conversation_summary",
    "suggest_replies",
    "timedelta",
]
