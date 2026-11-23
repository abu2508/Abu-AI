"""Messaging parsers, analysis and drafts. Synthetic conversations only."""

from datetime import UTC, datetime, timedelta

import pytest

from domain.messaging import analysis as an
from domain.messaging import importers as imp

NOW = datetime(2024, 3, 12, 22, 0, tzinfo=UTC)

WA = """12/03/24, 9:41 pm - Asha: Hey! Are you free tomorrow evening?
12/03/24, 9:50 pm - Me: Hi! Maybe, what's the plan
12/03/24, 9:52 pm - Asha: Dinner at the new place,
let's go together
[12/03/2024, 21:55:10] Asha: Let me know!!
not a header line at the start is ignored until a header exists"""


def test_whatsapp_export_with_multiline_and_both_styles() -> None:
    m = imp.parse("whatsapp_export", WA, my_name="me", now=NOW)
    assert [(x.sender, x.sent_at.hour, x.sent_at.minute) for x in m] == [
        ("them", 21, 41),
        ("me", 21, 50),
        ("them", 21, 52),
        ("them", 21, 55),
    ]
    assert m[2].text == "Dinner at the new place,\nlet's go together"


def test_labelled_and_json() -> None:
    m = imp.parse(
        "labelled", "me: hi\nthem: hello\ncontinued line\nYou: bye", my_name=None, now=NOW
    )
    assert (
        [x.sender for x in m] == ["me", "them", "me"]
        and m[1].text == "hello\ncontinued line"
        and m[-1].sent_at == NOW - timedelta(minutes=1)
    )
    j = '[{"sender":"them","text":"hi","sent_at":"2024-03-12T10:00:00Z"},{"sender":"me","text":"yo","sent_at":"2024-03-12T10:05:00"}]'
    assert len(imp.parse("json", j, my_name=None, now=NOW)) == 2


@pytest.mark.parametrize(
    ("fmt", "text", "my"),
    [
        ("whatsapp_export", "12/03/24, 9:41 pm - A: hi", None),
        ("whatsapp_export", "nothing parseable", "me"),
        ("labelled", "no labels here", None),
        ("json", "not json", None),
        ("json", '[{"sender":"bot","text":"x","sent_at":"2024-01-01T00:00:00Z"}]', None),
        ("json", '[{"sender":"me","text":"x","sent_at":"2999-01-01T00:00:00Z"}]', None),
        ("yaml", "x", None),
        ("json", "[]", None),
        ("labelled", "me: " + "x" * 1_100_000, None),
    ],
)
def test_bad_imports_fail_safely(fmt: str, text: str, my: str | None) -> None:
    with pytest.raises(imp.ImportError_):
        imp.parse(fmt, text, my_name=my, now=NOW)


def test_control_and_bidi_characters_are_stripped() -> None:
    assert imp.clean("hi‮ there\x00​!") == "hi there!"


def convo() -> list[an.Msg]:
    t = datetime(2024, 3, 12, 20, 0, tzinfo=UTC)
    return [
        an.Msg("them", t, "Hey! Are you free tomorrow evening for dinner?"),
        an.Msg("me", t + timedelta(minutes=10), "yes!! 😀"),
        an.Msg(
            "them", t + timedelta(minutes=12), "Great, sorry for the late notice, thanks so much"
        ),
        an.Msg("me", t + timedelta(minutes=30), "np"),
        an.Msg("them", t + timedelta(minutes=40), "Can you bring the documents please? urgent!!"),
    ]


def test_analysis_numbers() -> None:
    a = an.analyse(convo(), NOW)
    assert (
        a["message_count"],
        a["from_me"],
        a["from_them"],
        a["last_sender"],
        a["needs_reply"],
    ) == (5, 2, 3, "them", True)
    assert a["median_my_response_minutes"] == 14.0  # gaps 10 and 18
    assert a["median_their_response_minutes"] == 6.0  # gaps 2 and 10
    assert a["last_message_intent"] == "request" and a["their_tone"]["urgent"] is True  # type: ignore[index]
    assert a["my_style"]["uses_emoji"] is True  # type: ignore[index]
    assert a["waiting_hours"] == round((NOW - convo()[-1].sent_at).total_seconds() / 3600, 1)


def test_no_reply_needed_when_i_spoke_last() -> None:
    a = an.analyse(convo()[:4], NOW)
    assert (
        a["needs_reply"] is False
        and a["last_message_intent"] is None
        and a["waiting_hours"] is None
    )


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("I'm so stressed and sad today", "emotional_support"),
        ("sorry about yesterday", "apology"),
        ("I got the job!", "congratulations"),
        ("thanks a lot", "thanks"),
        ("Let's grab coffee", "invitation"),
        ("What time tomorrow", "scheduling"),
        ("Can you help me move", "request"),
        ("good night", "farewell"),
        ("hey", "greeting"),
        ("Where are you?", "question"),
        ("the sky is blue", "statement"),
    ],
)
def test_intent_rules(text: str, intent: str) -> None:
    assert an.classify_intent(text) == intent


def test_drafts_are_drafts_and_never_commit_the_user() -> None:
    for intent in ("invitation", "scheduling", "request", "emotional_support", "question"):
        for tone in ("casual", "warm", "formal", "brief"):
            out = an.suggest_replies({"last_message_intent": intent, "needs_reply": True}, tone)
            assert out["draft_only"] is True and out["send_supported"] is False and out["drafts"]
            for d in out["drafts"]:  # type: ignore[attr-defined]
                assert not any(ch.isdigit() for ch in d["text"]), (
                    d
                )  # no invented times, amounts or dates
    assert (
        an.suggest_replies({"last_message_intent": "invitation"}, "casual")["caution"]
        == "commitment"
    )
    # formal counterpart and a colleague relationship make a casual preference formal
    assert (
        an.suggest_replies(
            {"last_message_intent": "greeting", "their_tone": {"formal": True}}, "casual"
        )["tone"]
        == "formal"
    )


def test_summary_contains_no_message_text() -> None:
    a = an.analyse(convo(), NOW)
    s = an.conversation_summary(a)
    assert "Hey! Are you free" not in s and s.startswith("5 messages")
