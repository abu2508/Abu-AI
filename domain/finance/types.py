import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class RawTxn:
    """One transaction as read from a notification, statement row or manual entry - before storage."""

    txn_date: date
    amount: Decimal  # always positive
    direction: str  # debit | credit
    description: str
    reference: str | None = None
    account_last4: str | None = None
    balance: Decimal | None = None


@dataclass
class ImportResult:
    rows: list[RawTxn] = field(default_factory=list)
    rejected: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


class ImportFailed(Exception):
    """The file could not be read at all (corrupt, encrypted, too large, unsupported). Message is a safe code."""


LAST4 = re.compile(r"^[0-9]{4}$")
