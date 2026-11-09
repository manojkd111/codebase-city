"""Money helpers. Amounts are stored as integer cents everywhere in the database."""

from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount: float) -> int:
    """Convert a user-entered amount such as 19.99 to cents."""
    return int(amount * 100)


def from_cents(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


def split_evenly(total_cents: int, parts: int) -> list[int]:
    """Split an amount into `parts` shares that add up exactly to the total."""
    if parts <= 0:
        raise ValueError("parts must be positive")
    base, extra = divmod(total_cents, parts)
    return [base + 1 if i < extra else base for i in range(parts)]


def percent_of(cents: int, pct: Decimal) -> int:
    value = Decimal(cents) * pct / 100
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
