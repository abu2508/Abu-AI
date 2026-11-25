"""Merchant normalisation and deterministic categorisation."""

import re
from dataclasses import dataclass
from decimal import Decimal

CATEGORIES = (
    "groceries",
    "dining",
    "transport",
    "fuel",
    "shopping",
    "utilities",
    "rent",
    "emi_loans",
    "insurance",
    "health",
    "education",
    "entertainment",
    "subscriptions",
    "travel",
    "fees_charges",
    "cash_withdrawal",
    "transfers",
    "investments",
    "salary",
    "interest",
    "refunds",
    "other_income",
    "uncategorized",
)
INCOME_CATEGORIES = frozenset({"salary", "interest", "refunds", "other_income"})

_PREFIX = re.compile(r"^(upi|pos|ecom|imps|neft|rtgs|ach|nach|atm|ib|bil|mob)[\s/\-:*]+", re.I)
_VPA = re.compile(r"([A-Za-z0-9._\-]+)@[A-Za-z]{2,}")
_REF = re.compile(r"\b\d{6,}\b|\b[A-Z0-9]{12,}\b")


def normalise_merchant(description: str) -> str:
    """'UPI/402112345678/SWIGGY@icici/Food' -> 'SWIGGY FOOD'. Deterministic; strips references and prefixes."""
    s = description.strip()
    for _ in range(3):
        s2 = _PREFIX.sub("", s)
        if s2 == s:
            break
        s = s2
    s = _VPA.sub(lambda m: m.group(1), s)
    s = _REF.sub(" ", s)
    s = re.sub(r"[^A-Za-z0-9&' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().upper()[:60]


# (category, pattern) evaluated in order; first match wins. Debits and credits use different rule sets.
_DEBIT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("emi_loans", re.compile(r"\b(emi|loan|nach|ecs)\b", re.I)),
    ("cash_withdrawal", re.compile(r"\b(atm|cash wdl|cash withdrawal)\b", re.I)),
    ("fees_charges", re.compile(r"\b(charges?|fee|gst|penalty|annual fee|sms alert)\b", re.I)),
    (
        "subscriptions",
        re.compile(
            r"\b(netflix|spotify|prime|hotstar|youtube premium|icloud|google one|subscription)\b",
            re.I,
        ),
    ),
    (
        "groceries",
        re.compile(
            r"\b(bigbasket|blinkit|zepto|grocer\w*|supermarket|dmart|reliance fresh|vegetables)\b",
            re.I,
        ),
    ),
    (
        "dining",
        re.compile(
            r"\b(swiggy|zomato|restaurant|cafe|coffee|pizza|dominos|mcdonald\w*|bakery|food)\b",
            re.I,
        ),
    ),
    ("fuel", re.compile(r"\b(petrol|diesel|fuel|hpcl|bpcl|iocl|indian oil|shell)\b", re.I)),
    ("transport", re.compile(r"\b(uber|ola|rapido|metro|irctc|fastag|cab|bus|railway)\b", re.I)),
    (
        "utilities",
        re.compile(
            r"\b(electricity|water bill|broadband|airtel|jio|vodafone|recharge|gas|bescom|dth)\b",
            re.I,
        ),
    ),
    ("rent", re.compile(r"\b(rent|landlord)\b", re.I)),
    ("insurance", re.compile(r"\b(insurance|lic|premium)\b", re.I)),
    (
        "health",
        re.compile(r"\b(pharmacy|hospital|clinic|apollo|medplus|diagnostic|doctor)\b", re.I),
    ),
    (
        "education",
        re.compile(r"\b(school|college|tuition|udemy|coursera|course|fees? for)\b", re.I),
    ),
    ("entertainment", re.compile(r"\b(bookmyshow|movie|cinema|pvr|inox|game|steam)\b", re.I)),
    ("travel", re.compile(r"\b(makemytrip|goibibo|airline|indigo|hotel|airbnb|flight)\b", re.I)),
    ("investments", re.compile(r"\b(mutual fund|sip|zerodha|groww|stocks?|ppf|nps)\b", re.I)),
    ("shopping", re.compile(r"\b(amazon|flipkart|myntra|ajio|mall|store|shop)\b", re.I)),
    ("transfers", re.compile(r"\b(self transfer|own account|fund transfer|to self)\b", re.I)),
]
_CREDIT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("salary", re.compile(r"\b(salary|payroll|sal cr|wages)\b", re.I)),
    ("refunds", re.compile(r"\b(refund|reversal|cashback|chargeback)\b", re.I)),
    ("interest", re.compile(r"\b(interest|int\.? pd|int cr)\b", re.I)),
    ("transfers", re.compile(r"\b(self transfer|own account|fund transfer|from self)\b", re.I)),
]


@dataclass(frozen=True)
class Categorised:
    category: str
    kind: str  # expense | income | transfer | emi | fee | refund
    confidence: float


def categorise(description: str, direction: str, user_rule: str | None = None) -> Categorised:
    """User rule first (explicit choice), then keyword rules, else uncategorized (confidence 0).
    The first matching keyword rule wins, so rule ORDER is part of the contract (tested)."""
    if user_rule:
        cat = user_rule
        conf = 1.0
    else:
        rules = _DEBIT_RULES if direction == "debit" else _CREDIT_RULES
        cat = next((c for c, rx in rules if rx.search(description)), None) or (
            "other_income" if direction == "credit" else "uncategorized"
        )
        conf = 0.0 if cat in ("uncategorized", "other_income") else 0.8
    kind = {
        "transfers": "transfer",
        "emi_loans": "emi",
        "fees_charges": "fee",
        "refunds": "refund",
    }.get(cat, "income" if direction == "credit" else "expense")
    return Categorised(cat, kind, conf)


# ---------------------------------------------------------------------------------- classification safety (S2.1A)
# ONE central threshold. Today's scale only produces 0.00 (no rule matched), 0.80 (a keyword rule) and 1.00 (the user's
# own choice), so any value in (0.00, 0.80] behaves identically; 0.50 is therefore not a tuning knob. Strictly "less
# than": exactly 0.50 is NOT low. 1.00 only ever means "decided by the user" (an explicit category or remembered rule).
CLASSIFICATION_REVIEW_THRESHOLD = Decimal("0.50")
REVIEW_REASONS = ("unknown_credit", "unlinked_transfer", "ambiguous_transfer", "emi_unverified")
CONF_RULE = Decimal("0.80")
CONF_USER = Decimal("1.00")
CONF_NONE = Decimal("0.00")


def is_low_confidence(confidence: Decimal) -> bool:
    return confidence < CLASSIFICATION_REVIEW_THRESHOLD


def _conf(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


_CARD_NAME = re.compile(r"\bcredit\s*card\b", re.I)
_CARD_BILL_WORDS = re.compile(r"\b(pay(ment|mt)?|pymt|bill|due|outstanding|autopay)\b", re.I)
_CARD_SHORT = re.compile(r"\b(cc|card)\s*(bill|pay(ment|mt)?|pymt)\b", re.I)
_PHRASE_DEBIT = re.compile(r"\b(self transfer|own account|fund transfer|to self)\b", re.I)
_PHRASE_CREDIT = re.compile(r"\b(self transfer|own account|fund transfer|from self)\b", re.I)
_CARD_RECEIPT = re.compile(
    r"(\bpayment\b.*\b(received|recd|rcvd|thank)\b|\b(received|recd|rcvd)\b.*\bpayment\b"
    r"|\bthank\s*you\b|\bbbps\b|\bautopay\b)",
    re.I,
)
_REFUND_WORDS = re.compile(r"\b(refund|reversal|cashback|chargeback)\b", re.I)


@dataclass(frozen=True)
class Classification:
    category: str
    kind: str  # expense | income | transfer | emi | fee | refund
    confidence: Decimal
    # True only when a deterministic DESCRIPTION pattern (card payment / own-account phrase) says "transfer". A user's
    # choice of 'transfers' has confidence 1.00 and is an assertion, not pattern evidence.
    transfer_evidence: bool = False


def classify(
    description: str, direction: str, account_kind: str, user_rule: str | None = None
) -> Classification:
    """Deterministic classification with account awareness. `categorise` (unchanged) supplies merchant rules.

    - non-card debit: card-payment / own-account patterns are checked BEFORE merchant rules (so 'SELF TRANSFER ... STORE
      CREDIT' is a transfer candidate, not shopping). 'RENT TRANSFER' matches none of them and stays rent;
    - card debit: ordinary merchant rules, never a transfer by description;
    - card credit: payment receipt -> transfer candidate; refund -> refund; both words -> unknown; neither -> unknown;
    - 'payment received' wording means a card payment ONLY on a card account; on a bank it is an unknown credit.
    """
    card = account_kind == "credit_card"
    if user_rule:
        c = categorise(description, direction, user_rule)
        return Classification(c.category, c.kind, CONF_USER)
    if direction == "debit":
        if not card and (
            _PHRASE_DEBIT.search(description)
            or _CARD_SHORT.search(description)
            or (_CARD_NAME.search(description) and _CARD_BILL_WORDS.search(description))
        ):
            return Classification("transfers", "transfer", CONF_RULE, True)
    elif card:
        refund, receipt = (
            bool(_REFUND_WORDS.search(description)),
            bool(_CARD_RECEIPT.search(description)),
        )
        if refund and receipt:
            return Classification("other_income", "income", CONF_NONE)
        if receipt:
            return Classification("transfers", "transfer", CONF_RULE, True)
        if not refund:
            return Classification("other_income", "income", CONF_NONE)
    elif _PHRASE_CREDIT.search(description):
        return Classification("transfers", "transfer", CONF_RULE, True)
    c = categorise(description, direction, None)
    conf = _conf(c.confidence)
    return Classification(c.category, c.kind, conf, c.kind == "transfer" and conf < CONF_USER)
